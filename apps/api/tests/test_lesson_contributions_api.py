from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore


AUTHOR = UserView(
    id="contribution_author",
    email="author@example.com",
    role="user",
    display_name="Author",
    created_at="2026-01-01T00:00:00+00:00",
)
CONTRIBUTOR = UserView(
    id="contribution_learner",
    email="learner@example.com",
    role="user",
    display_name="Learner",
    created_at="2026-01-02T00:00:00+00:00",
)
GUEST = UserView(
    id="guest_contribution",
    email="guest@local.invalid",
    role="guest",
    created_at="2026-01-03T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: AUTHOR
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _as(user: UserView) -> None:
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: user


def _document_with_text(document: dict, text: str) -> dict:
    result = deepcopy(document)
    result["content_text"] = text
    result["content_html"] = f"<p>{text}</p>"
    result["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return result


def _save(api_client: TestClient, lesson: dict, text: str) -> dict:
    response = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": _document_with_text(lesson["board_document"], text),
            "label": text,
            "message": text,
        },
    )
    assert response.status_code == 200
    return response.json()["lessons"][0]


def _forked_lesson(api_client: TestClient) -> tuple[dict, dict]:
    _as(AUTHOR)
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Public source", "start_blank": True},
    )
    source = generated.json()["lessons"][0]
    source = _save(api_client, source, "Public baseline")
    assert api_client.post(
        f"/api/lessons/{source['id']}/visibility", json={"visibility": "public"}
    ).status_code == 200

    _as(CONTRIBUTOR)
    forked = api_client.post(f"/api/public/lessons/{source['id']}/fork")
    assert forked.status_code == 200
    personal = next(
        lesson for lesson in forked.json()["lessons"] if lesson["id"] == forked.json()["active_lesson_id"]
    )
    return source, _save(api_client, personal, "Public baseline\n\nLearner improvement")


def test_contribution_snapshot_comments_and_visibility(api_client: TestClient) -> None:
    source, personal = _forked_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Clarify the lesson", "description": "A reusable improvement"},
    )
    assert created.status_code == 200
    contribution = created.json()
    assert contribution["status"] == "open"
    assert contribution["revision"]["proposed_document"]["content_text"].endswith("Learner improvement")
    assert "contributor_lesson_id" not in contribution

    public_view = api_client.get(f"/api/public/contributions/{contribution['id']}")
    assert public_view.status_code == 200
    assert public_view.json()["viewer_permissions"]["can_comment"] is False

    stale = api_client.post(
        f"/api/contributions/{contribution['id']}/comments",
        json={"expected_version": contribution["version"] + 1, "body": "Review note"},
    )
    assert stale.status_code == 409
    commented = api_client.post(
        f"/api/contributions/{contribution['id']}/comments",
        json={"expected_version": contribution["version"], "body": "Review note"},
    )
    assert commented.status_code == 200
    contribution = commented.json()
    comment = next(event for event in contribution["events"] if event["kind"] == "commented")
    edited = api_client.patch(
        f"/api/contributions/{contribution['id']}/comments/{comment['id']}",
        json={"expected_version": contribution["version"], "body": "Updated review note"},
    )
    assert edited.status_code == 200
    contribution = edited.json()
    assert next(event for event in contribution["events"] if event["id"] == comment["id"])["body"] == "Updated review note"

    personal = _save(api_client, personal, "A later private edit")
    updated = api_client.post(
        f"/api/contributions/{contribution['id']}/revisions",
        json={"expected_version": contribution["version"]},
    )
    assert updated.status_code == 200
    contribution = updated.json()
    assert contribution["current_revision"] == 2
    assert contribution["revision"]["proposed_document"]["content_text"] == "A later private edit"

    _as(AUTHOR)
    assert api_client.post(
        f"/api/lessons/{source['id']}/visibility", json={"visibility": "private"}
    ).status_code == 200
    assert api_client.get(f"/api/public/contributions/{contribution['id']}").status_code == 404
    participant_view = api_client.get(f"/api/contributions/{contribution['id']}")
    assert participant_view.status_code == 200
    assert participant_view.json()["source_is_public"] is False


def test_guest_and_unrelated_lesson_cannot_submit(api_client: TestClient) -> None:
    _, personal = _forked_lesson(api_client)
    _as(GUEST)
    denied_guest = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Guest proposal"},
    )
    assert denied_guest.status_code in {403, 404}

    _as(CONTRIBUTOR)
    generated = api_client.post(
        "/api/lessons/generate", json={"topic": "Unrelated", "start_blank": True}
    ).json()
    unrelated = next(
        lesson for lesson in generated["lessons"] if lesson["id"] == generated["active_lesson_id"]
    )
    denied_unrelated = api_client.post(
        f"/api/lessons/{unrelated['id']}/contributions",
        json={"title": "Unrelated proposal"},
    )
    assert denied_unrelated.status_code == 403


def test_author_starts_merge_without_changing_live_lesson_and_can_return(
    api_client: TestClient,
) -> None:
    source, personal = _forked_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Merge-ready proposal"},
    ).json()

    _as(AUTHOR)
    started = api_client.post(
        f"/api/contributions/{created['id']}/merge/start",
        json={"expected_version": created["version"]},
    )
    assert started.status_code == 200
    contribution = started.json()
    assert contribution["status"] == "merge_draft"
    assert contribution["merge_session_id"]

    workspace = api_client.get("/api/workspace").json()
    persisted_source = next(
        lesson
        for package in workspace["packages"]
        for lesson in package["lessons"]
        if lesson["id"] == source["id"]
    )
    assert persisted_source["board_document"]["content_text"] == "Public baseline"
    active = api_client.get(f"/api/lessons/{source['id']}/merge-sessions/active")
    assert active.status_code == 200
    session = active.json()
    assert session["id"] == contribution["merge_session_id"]
    assert session["audit"]["lesson_contribution_id"] == contribution["id"]
    assert session["source_branch_name"].startswith("contribution/")

    returned = api_client.post(
        f"/api/contributions/{contribution['id']}/merge/return",
        json={"expected_version": contribution["version"]},
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "open"
    assert returned.json()["merge_session_id"] is None

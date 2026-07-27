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
    assert contribution["viewer_project_lesson_id"] == personal["id"]
    assert "contributor_lesson_id" not in contribution

    public_view = api_client.get(f"/api/public/contributions/{contribution['id']}")
    assert public_view.status_code == 200
    assert public_view.json()["viewer_project_lesson_id"] is None
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
    assert participant_view.json()["viewer_project_lesson_id"] == source["id"]


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


def test_contribution_merge_commits_and_updates_status_atomically(api_client: TestClient) -> None:
    source, personal = _forked_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Merge this proposal"},
    ).json()
    _as(AUTHOR)
    contribution = api_client.post(
        f"/api/contributions/{created['id']}/merge/start",
        json={"expected_version": created["version"]},
    ).json()
    session = api_client.get(
        f"/api/lessons/{source['id']}/merge-sessions/{contribution['merge_session_id']}"
    ).json()
    assert session["status"] == "ready"

    submitted = api_client.post(
        f"/api/lessons/{source['id']}/merge-sessions/{session['id']}/submit",
        json={"expected_version": session["version"]},
    )
    assert submitted.status_code == 200
    merged = api_client.get(f"/api/contributions/{contribution['id']}")
    assert merged.status_code == 200
    merged_view = merged.json()
    assert merged_view["status"] == "merged"
    assert merged_view["merged_commit_id"]
    lesson = next(item for item in submitted.json()["lessons"] if item["id"] == source["id"])
    assert lesson["board_document"]["content_text"].endswith("Learner improvement")
    merge_commit = next(
        commit
        for commit in lesson["history_graph"]["commits"]
        if commit["id"] == merged_view["merged_commit_id"]
    )
    assert len(merge_commit["parent_ids"]) == 2
    assert merge_commit["metadata"]["lesson_contribution_id"] == contribution["id"]


def test_closing_merge_draft_abandons_linked_session(api_client: TestClient) -> None:
    source, personal = _forked_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Close during review"},
    ).json()
    _as(AUTHOR)
    contribution = api_client.post(
        f"/api/contributions/{created['id']}/merge/start",
        json={"expected_version": created["version"]},
    ).json()
    session_id = contribution["merge_session_id"]
    closed = api_client.post(
        f"/api/contributions/{contribution['id']}/close",
        json={"expected_version": contribution["version"]},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["merge_session_id"] is None
    session = api_client.get(f"/api/lessons/{source['id']}/merge-sessions/{session_id}")
    assert session.status_code == 200
    assert session.json()["status"] == "abandoned"


def test_stale_contribution_merge_recomputes_and_relinks(api_client: TestClient) -> None:
    source, personal = _forked_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Recompute proposal"},
    ).json()
    _as(AUTHOR)
    contribution = api_client.post(
        f"/api/contributions/{created['id']}/merge/start",
        json={"expected_version": created["version"]},
    ).json()
    old_session = api_client.get(
        f"/api/lessons/{source['id']}/merge-sessions/{contribution['merge_session_id']}"
    ).json()
    _save(api_client, source, "Author changed after review started")
    stale = api_client.post(
        f"/api/lessons/{source['id']}/merge-sessions/{old_session['id']}/submit",
        json={"expected_version": old_session["version"]},
    )
    assert stale.status_code == 409
    stale_session = api_client.get(
        f"/api/lessons/{source['id']}/merge-sessions/{old_session['id']}"
    ).json()
    assert stale_session["status"] == "stale"

    recomputed = api_client.post(
        f"/api/lessons/{source['id']}/merge-sessions/{old_session['id']}/recompute",
        json={"expected_version": stale_session["version"]},
    )
    assert recomputed.status_code == 200
    assert recomputed.json()["id"] != old_session["id"]
    relinked = api_client.get(f"/api/contributions/{contribution['id']}").json()
    assert relinked["merge_session_id"] == recomputed.json()["id"]

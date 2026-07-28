from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore


OWNER = UserView(
    id="governance_owner",
    email="owner@example.com",
    role="user",
    display_name="Owner",
    created_at="2026-01-01T00:00:00+00:00",
)
EDITOR = UserView(
    id="governance_editor",
    email="editor@example.com",
    role="user",
    display_name="Editor",
    created_at="2026-01-02T00:00:00+00:00",
)
REVIEWER = UserView(
    id="governance_reviewer",
    email="reviewer@example.com",
    role="user",
    display_name="Reviewer",
    created_at="2026-01-03T00:00:00+00:00",
)
REVIEWER_TWO = UserView(
    id="governance_reviewer_two",
    email="reviewer-two@example.com",
    role="user",
    display_name="Reviewer Two",
    created_at="2026-01-04T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: OWNER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _as(user: UserView) -> None:
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: user


def _create_lesson(api_client: TestClient) -> dict:
    response = api_client.post("/api/lessons/generate", json={"topic": "Shared project", "start_blank": True})
    assert response.status_code == 200
    return response.json()["lessons"][0]


def _document_with_text(document: dict, text: str) -> dict:
    result = deepcopy(document)
    result["content_text"] = text
    result["content_html"] = f"<p>{text}</p>"
    result["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return result


def _save_owned_lesson(api_client: TestClient, lesson: dict, text: str) -> dict:
    saved = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={"document": _document_with_text(lesson["board_document"], text), "label": text, "message": text},
    )
    assert saved.status_code == 200
    return next(item for item in saved.json()["lessons"] if item["id"] == lesson["id"])


def test_member_invitation_roles_and_collaborative_save(api_client: TestClient) -> None:
    lesson = _create_lesson(api_client)
    project_path = f"/api/projects/lesson/{lesson['id']}"

    governance = api_client.get(f"{project_path}/governance")
    assert governance.status_code == 200
    assert governance.json()["viewer_role"] == "owner"
    assert governance.json()["capabilities"]["manage_members"] is True

    invited = api_client.post(
        f"{project_path}/invitations",
        json={"email": EDITOR.email, "role": "editor"},
    )
    assert invited.status_code == 200
    invitation = next(item for item in invited.json()["invitations"] if item["status"] == "pending")

    _as(EDITOR)
    incoming = api_client.get("/api/project-invitations")
    assert incoming.status_code == 200
    assert incoming.json()[0]["project_title"] == lesson["title"]
    accepted = api_client.post(f"/api/project-invitations/{invitation['id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["viewer_role"] == "editor"
    assert accepted.json()["capabilities"]["edit_project"] is True
    assert accepted.json()["capabilities"]["manage_members"] is False
    collaborative_lesson = api_client.get(f"{project_path}/lessons/{lesson['id']}")
    assert collaborative_lesson.status_code == 200
    assert collaborative_lesson.json()["id"] == lesson["id"]

    saved = api_client.post(
        f"{project_path}/lessons/{lesson['id']}/document/save",
        json={
            "document": _document_with_text(lesson["board_document"], "Edited by an invited member"),
            "label": "Collaborative edit",
            "message": "Editor updated the shared project",
        },
    )
    assert saved.status_code == 200

    _as(OWNER)
    owner_workspace = api_client.get("/api/workspace")
    current_lesson = next(
        item
        for package in owner_workspace.json()["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert current_lesson["board_document"]["content_text"] == "Edited by an invited member"
    assert current_lesson["history_graph"]["commits"][-1]["metadata"]["actor_user_id"] == EDITOR.id
    audit = api_client.get(f"{project_path}/governance").json()["audit_events"]
    assert any(item["actor_user_id"] == EDITOR.id and item["kind"] == "document.saved" for item in audit)

    assert api_client.put(
        f"{project_path}/policy",
        json={
            "protect_default_branch": True,
            "required_approvals": 0,
            "require_passing_checks": False,
            "dismiss_stale_approvals": True,
        },
    ).status_code == 200
    _as(EDITOR)
    blocked = api_client.post(
        f"{project_path}/lessons/{lesson['id']}/document/save",
        json={
            "document": _document_with_text(lesson["board_document"], "Protected edit"),
            "label": "Protected edit",
            "message": "This direct edit must be blocked",
        },
    )
    assert blocked.status_code == 409
    assert "受保护" in blocked.json()["detail"]


def test_policy_checks_work_items_and_integrations(api_client: TestClient) -> None:
    lesson = _create_lesson(api_client)
    project_path = f"/api/projects/lesson/{lesson['id']}"

    policy = api_client.put(
        f"{project_path}/policy",
        json={
            "protect_default_branch": True,
            "required_approvals": 1,
            "require_passing_checks": True,
            "dismiss_stale_approvals": True,
        },
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["required_approvals"] == 1

    assert api_client.post(f"{project_path}/labels", json={"name": "priority", "color": "#7c3aed"}).status_code == 200
    assert api_client.post(f"{project_path}/milestones", json={"title": "First release"}).status_code == 200
    board = api_client.put(f"{project_path}/board", json={"columns": ["待办", "进行中", "审查中", "已完成"]})
    assert board.status_code == 200
    work_item = api_client.post(
        f"{project_path}/work-items",
        json={"title": "Review shared lesson", "body": "Track the reusable collaboration work."},
    )
    assert work_item.status_code == 200
    assert work_item.json()["work_items"][0]["number"] == 1
    assert len(work_item.json()["board_columns"]) == 4

    checks = api_client.post(f"{project_path}/checks/run")
    assert checks.status_code == 200
    assert {item["name"] for item in checks.json()} == {
        "course_integrity",
        "source_access",
        "publication_safety",
        "export_readiness",
    }

    blocked = api_client.post(
        f"{project_path}/webhooks",
        json={"url": "https://127.0.0.1/events", "events": ["project.updated"], "secret": "0123456789abcdef"},
    )
    assert blocked.status_code == 422
    webhook = api_client.post(
        f"{project_path}/webhooks",
        json={"url": "https://example.com/openclass-events", "events": ["project.updated"], "secret": "0123456789abcdef"},
    )
    assert webhook.status_code == 200
    assert webhook.json()["webhooks"][0]["url"] == "https://example.com/openclass-events"
    deliveries = api_client.get(f"{project_path}/webhooks/deliveries")
    assert deliveries.status_code == 200
    assert deliveries.json()[0]["event"] == "webhook.created"
    assert deliveries.json()[0]["status"] == "queued"
    assert deliveries.json()[0]["attempts"] == 0

    app = api_client.post(
        "/api/integration-apps",
        json={"name": "Course QA", "permissions": ["checks:write"]},
    )
    assert app.status_code == 200
    installed = api_client.post(f"{project_path}/integrations", json={"app_id": app.json()["id"]})
    assert installed.status_code == 200
    assert installed.json()["integrations"][0]["installed"] is True


def test_organization_team_can_receive_project_role(api_client: TestClient) -> None:
    lesson = _create_lesson(api_client)
    organization = api_client.post("/api/organizations", json={"name": "Learning Lab", "slug": "learning-lab"})
    assert organization.status_code == 200
    team = api_client.post(
        f"/api/organizations/{organization.json()['id']}/teams",
        json={"name": "Reviewers", "slug": "reviewers"},
    )
    assert team.status_code == 200
    assert api_client.post(
        f"/api/organizations/{organization.json()['id']}/teams/{team.json()['id']}/members",
        json={"user_id": EDITOR.id},
    ).status_code == 200
    assert api_client.post(
        f"/api/organizations/{organization.json()['id']}/teams/{team.json()['id']}/projects",
        json={"project_kind": "lesson", "project_id": lesson["id"], "role": "reviewer"},
    ).status_code == 200

    _as(EDITOR)
    governance = api_client.get(f"/api/projects/lesson/{lesson['id']}/governance")
    assert governance.status_code == 200
    assert governance.json()["viewer_role"] == "reviewer"
    assert governance.json()["capabilities"]["review_changes"] is True


def test_required_review_and_checks_gate_contribution_merge(api_client: TestClient) -> None:
    source = _save_owned_lesson(api_client, _create_lesson(api_client), "Reviewed baseline")
    assert api_client.post(
        f"/api/lessons/{source['id']}/visibility",
        json={"visibility": "public"},
    ).status_code == 200

    _as(EDITOR)
    forked = api_client.post(f"/api/public/lessons/{source['id']}/fork")
    assert forked.status_code == 200
    personal = next(
        item for item in forked.json()["lessons"] if item["id"] == forked.json()["active_lesson_id"]
    )
    personal = _save_owned_lesson(api_client, personal, "Reviewed baseline\n\nProposed improvement")
    created = api_client.post(
        f"/api/lessons/{personal['id']}/contributions",
        json={"title": "Improve explanation", "description": "A review-gated change."},
    )
    assert created.status_code == 200
    contribution = created.json()

    _as(OWNER)
    project_path = f"/api/projects/lesson/{source['id']}"
    assert api_client.put(
        f"{project_path}/policy",
        json={
            "protect_default_branch": True,
            "required_approvals": 2,
            "require_passing_checks": True,
            "dismiss_stale_approvals": True,
        },
    ).status_code == 200
    blocked = api_client.post(
        f"/api/contributions/{contribution['id']}/merge/start",
        json={"expected_version": contribution["version"]},
    )
    assert blocked.status_code == 409
    assert "需要 2 个批准" in str(blocked.json()["detail"])

    invited = api_client.post(
        f"{project_path}/invitations",
        json={"email": REVIEWER.email, "role": "reviewer"},
    )
    invitation_id = next(item["id"] for item in invited.json()["invitations"] if item["status"] == "pending")
    _as(REVIEWER)
    assert api_client.post(f"/api/project-invitations/{invitation_id}/accept").status_code == 200
    checks = api_client.post(f"{project_path}/checks/run?contribution_id={contribution['id']}")
    assert checks.status_code == 200
    assert all(item["status"] == "passed" for item in checks.json())
    review = api_client.post(
        f"{project_path}/contributions/{contribution['id']}/reviews",
        json={"decision": "approve", "body": "Ready to merge.", "revision_number": contribution["current_revision"]},
    )
    assert review.status_code == 200

    _as(OWNER)
    one_approval = api_client.post(
        f"/api/contributions/{contribution['id']}/merge/start",
        json={"expected_version": contribution["version"]},
    )
    assert one_approval.status_code == 409
    assert "已有 1 个" in str(one_approval.json()["detail"])
    second_invite = api_client.post(
        f"{project_path}/invitations",
        json={"email": REVIEWER_TWO.email, "role": "reviewer"},
    )
    second_invitation_id = next(
        item["id"] for item in second_invite.json()["invitations"]
        if item["email"] == REVIEWER_TWO.email and item["status"] == "pending"
    )
    _as(REVIEWER_TWO)
    assert api_client.post(f"/api/project-invitations/{second_invitation_id}/accept").status_code == 200
    assert api_client.post(
        f"{project_path}/contributions/{contribution['id']}/reviews",
        json={"decision": "approve", "body": "Second approval.", "revision_number": contribution["current_revision"]},
    ).status_code == 200

    _as(OWNER)
    started = api_client.post(
        f"/api/contributions/{contribution['id']}/merge/start",
        json={"expected_version": contribution["version"]},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "merge_draft"

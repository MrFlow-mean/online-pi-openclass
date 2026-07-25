from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.services import pi_agent_runtime


MEMBER = UserView(
    id="user_api_key",
    email="key-owner@example.com",
    role="user",
    created_at="2026-07-25T00:00:00+00:00",
)
GUEST = UserView(
    id="guest_api_key",
    email="guest@openclass.local",
    role="guest",
    created_at="2026-07-25T00:00:00+00:00",
)


def test_member_can_save_inspect_and_remove_a_private_provider_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(pi_agent_runtime, "pi_runtime_root", lambda: tmp_path)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: MEMBER
    try:
        client = TestClient(main_module.app)

        initial = client.get("/api/model-credentials")
        assert initial.status_code == 200
        assert initial.json() == [
            {
                "provider": "deepseek",
                "label": "DeepSeek",
                "configured": False,
                "manageable": True,
            }
        ]

        saved = client.put(
            "/api/model-credentials/deepseek",
            json={"api_key": "sk-member-private"},
        )
        assert saved.status_code == 200
        assert saved.json()["configured"] is True
        assert "sk-member-private" not in saved.text
        assert "api_key" not in saved.text

        listed = client.get("/api/model-credentials")
        assert listed.json()[0]["configured"] is True

        removed = client.delete("/api/model-credentials/deepseek")
        assert removed.status_code == 200
        assert removed.json()["configured"] is False
    finally:
        main_module.app.dependency_overrides.clear()


def test_guest_can_inspect_but_cannot_persist_a_provider_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(pi_agent_runtime, "pi_runtime_root", lambda: tmp_path)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: GUEST
    try:
        client = TestClient(main_module.app)
        status = client.get("/api/model-credentials")
        assert status.status_code == 200
        assert status.json()[0]["manageable"] is False

        saved = client.put(
            "/api/model-credentials/deepseek",
            json={"api_key": "sk-guest-private"},
        )
        assert saved.status_code == 403
        assert "sk-guest-private" not in saved.text
    finally:
        main_module.app.dependency_overrides.clear()


def test_model_credentials_reject_unsupported_providers(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(pi_agent_runtime, "pi_runtime_root", lambda: tmp_path)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: MEMBER
    try:
        response = TestClient(main_module.app).put(
            "/api/model-credentials/openai",
            json={"api_key": "sk-openai-private"},
        )
        assert response.status_code == 400
        assert "sk-openai-private" not in response.text
    finally:
        main_module.app.dependency_overrides.clear()

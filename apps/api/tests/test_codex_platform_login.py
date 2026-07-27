from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.models import CodexAccountView, CodexLoginStartResponse
from app.routers import codex_provider
from app.services.auth_service import AUTH_COOKIE_NAME, AuthService
from app.services.codex_app_server import CodexAppServerError
from app.services.course_store import SqliteCourseStore


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/codex/login/complete",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode("utf-8"))],
        }
    )


def _formal_cookie_token(response: Response) -> str:
    prefix = f"{AUTH_COOKIE_NAME}="
    for key, value in response.raw_headers:
        if key == b"set-cookie":
            first_part = value.decode("latin-1").split(";", 1)[0]
            if first_part.startswith(prefix):
                return first_part.removeprefix(prefix)
    raise AssertionError("formal auth cookie was not set")


@pytest.fixture(autouse=True)
def _enable_chatgpt_platform_login(monkeypatch) -> None:
    monkeypatch.setattr(codex_provider, "chatgpt_platform_login_enabled", lambda: True)
    monkeypatch.setattr(codex_provider, "complete_codex_platform_login_claim", lambda *_args: None)
    monkeypatch.setattr(codex_provider, "release_codex_platform_login_claim", lambda *_args: None)
    monkeypatch.setattr(codex_provider, "remove_codex_auth", lambda *_args: None)


def test_completed_chatgpt_login_promotes_guest_bearer_atomically(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    guest_token, guest = auth.start_guest_session()
    workspace = store.load_for_user(guest.id)
    workspace.packages[0].title = "游客课程"
    store.save_for_user(guest.id, workspace)

    monkeypatch.setattr(codex_provider, "auth_service", auth)
    monkeypatch.setattr(
        codex_provider,
        "claim_completed_codex_platform_login",
        lambda login_id, user_id: CodexAccountView(
            type="chatgpt",
            email="student@example.com",
            plan_type="plus",
        ),
    )
    copied: list[tuple[str, str]] = []
    monkeypatch.setattr(
        codex_provider,
        "copy_codex_auth",
        lambda source_user_id, target_user_id: copied.append((source_user_id, target_user_id)),
    )

    route_response = Response()
    response = codex_provider.complete_platform_login(
        request=_request(guest_token),
        response=route_response,
        login_id="login_1",
        user=guest,
    )

    formal_token = _formal_cookie_token(route_response)
    assert response.token is None
    assert formal_token != guest_token
    assert response.user.id == guest.id
    assert response.user.role in {"user", "admin"}
    assert response.user.email == "student@example.com"
    assert response.user.auth_identities[0].provider == "chatgpt"
    with pytest.raises(HTTPException):
        auth.get_user_by_token(guest_token)
    assert auth.get_user_by_token(formal_token).id == response.user.id
    assert store.load_for_user(response.user.id).packages[0].title == "游客课程"
    assert copied == []


def test_chatgpt_login_links_existing_email_and_moves_bearer_and_codex_auth(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    _, existing_member = auth.register("student@example.com", "correct-password")
    guest_token, guest = auth.start_guest_session()

    monkeypatch.setattr(codex_provider, "auth_service", auth)
    monkeypatch.setattr(
        codex_provider,
        "claim_completed_codex_platform_login",
        lambda login_id, user_id: CodexAccountView(type="chatgpt", email="student@example.com"),
    )
    copied: list[tuple[str, str]] = []
    removed: list[str] = []
    monkeypatch.setattr(
        codex_provider,
        "copy_codex_auth",
        lambda source_user_id, target_user_id: copied.append((source_user_id, target_user_id)),
    )
    monkeypatch.setattr(codex_provider, "remove_codex_auth", lambda user_id: removed.append(user_id))

    route_response = Response()
    response = codex_provider.complete_platform_login(
        request=_request(guest_token),
        response=route_response,
        login_id="login_2",
        user=guest,
    )

    formal_token = _formal_cookie_token(route_response)
    assert response.token is None
    assert response.user.id == existing_member.id
    with pytest.raises(HTTPException):
        auth.get_user_by_token(guest_token)
    assert auth.get_user_by_token(formal_token).id == existing_member.id
    assert copied == [(guest.id, existing_member.id)]
    assert removed == [guest.id]
    assert {identity.provider for identity in response.user.auth_identities} == {"email", "chatgpt"}


def test_chatgpt_platform_login_rejects_unfinished_or_unidentified_account(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    guest_token, guest = auth.start_guest_session()
    monkeypatch.setattr(codex_provider, "auth_service", auth)

    def unfinished_account(_login_id: str, _user_id: str) -> CodexAccountView:
        raise CodexAppServerError("ChatGPT login has not completed")

    monkeypatch.setattr(codex_provider, "claim_completed_codex_platform_login", unfinished_account)
    with pytest.raises(HTTPException) as unfinished:
        codex_provider.complete_platform_login(
            request=_request(guest_token),
            response=Response(),
            login_id="login_pending",
            user=guest,
        )
    assert unfinished.value.status_code == 400

    monkeypatch.setattr(
        codex_provider,
        "claim_completed_codex_platform_login",
        lambda login_id, user_id: CodexAccountView(type="chatgpt", email=None),
    )
    with pytest.raises(HTTPException) as unidentified:
        codex_provider.complete_platform_login(
            request=_request(guest_token),
            response=Response(),
            login_id="login_no_email",
            user=guest,
        )
    assert unidentified.value.status_code == 422


def test_connected_guest_can_resume_platform_login_without_login_id(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    guest_token, guest = auth.start_guest_session()
    monkeypatch.setattr(codex_provider, "auth_service", auth)
    monkeypatch.setattr(
        codex_provider,
        "codex_provider_status",
        lambda user_id, refresh: codex_provider.CodexProviderStatus(
            enabled=True,
            available=True,
            configured=True,
            account=CodexAccountView(type="chatgpt", email="student@example.com"),
        ),
    )

    route_response = Response()
    response = codex_provider.complete_platform_login(
        request=_request(guest_token), response=route_response, user=guest
    )

    assert response.token is None
    assert _formal_cookie_token(route_response) != guest_token
    assert response.user.id == guest.id
    assert response.user.email == "student@example.com"


def test_platform_login_completion_is_idempotent_for_promoted_bearer(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    guest_token, guest = auth.start_guest_session()
    provider_calls: list[str] = []
    monkeypatch.setattr(codex_provider, "auth_service", auth)

    def provider_status(user_id: str, refresh: bool):
        provider_calls.append(user_id)
        return codex_provider.CodexProviderStatus(
            enabled=True,
            available=True,
            configured=True,
            account=CodexAccountView(type="chatgpt", email="student@example.com"),
        )

    monkeypatch.setattr(codex_provider, "codex_provider_status", provider_status)
    first_response = Response()
    first = codex_provider.complete_platform_login(
        request=_request(guest_token), response=first_response, user=guest
    )
    formal_token = _formal_cookie_token(first_response)
    second_response = Response()
    second = codex_provider.complete_platform_login(
        request=_request(formal_token),
        response=second_response,
        login_id="response_was_lost",
        user=first.user,
    )

    assert second.token is None
    assert _formal_cookie_token(second_response) == formal_token
    assert second.user.id == first.user.id
    assert provider_calls == [guest.id]


def test_platform_login_flag_is_a_server_side_gate(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    guest_token, guest = auth.start_guest_session()
    monkeypatch.setattr(codex_provider, "auth_service", auth)
    monkeypatch.setattr(codex_provider, "chatgpt_platform_login_enabled", lambda: False)

    with pytest.raises(HTTPException) as disabled:
        codex_provider.complete_platform_login(
            request=_request(guest_token), response=Response(), user=guest
        )

    assert disabled.value.status_code == 403
    assert auth.get_user_by_token(guest_token).role == "guest"


def test_platform_login_device_start_is_separate_from_provider_connection(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    _, guest = auth.start_guest_session()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(codex_provider, "auth_service", auth)
    monkeypatch.setattr(
        codex_provider,
        "start_codex_device_login",
        lambda user_id, purpose="provider": (
            calls.append((user_id, purpose))
            or CodexLoginStartResponse(
                login_id="login_1",
                verification_url="https://auth.openai.com/device",
                user_code="ABCD-EFGH",
            )
        ),
    )

    response = codex_provider.platform_login_device(user=guest)

    assert response.login_id == "login_1"
    assert calls == [(guest.id, "platform")]

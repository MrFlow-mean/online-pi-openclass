import io
import json
import sqlite3

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

from app.services import codex_app_server
from app.services.auth_service import AuthService, OAuthProfile, _safe_next_path
from app.services.board_asset_identity import board_asset_content_url, stable_board_asset_id
from app.services.board_asset_store import BoardAssetStore
from app.services.course_store import SqliteCourseStore
from app.services.lesson_factory import create_empty_lesson


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=color).save(output, format="PNG")
    return output.getvalue()


def _append_empty_lesson(workspace, title: str):
    lesson = create_empty_lesson(title)
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return lesson


def test_register_login_and_admin_overview(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    admin_token, admin_user = auth.register("Teacher@Example.com", "correct-password")
    _, student_user = auth.register("student@example.com", "correct-password")

    assert admin_user.email == "teacher@example.com"
    assert admin_user.role == "admin"
    assert student_user.role == "user"
    assert auth.get_user_by_token(admin_token).id == admin_user.id

    login_token, logged_in = auth.login("teacher@example.com", "correct-password")

    assert logged_in.id == admin_user.id
    assert auth.get_user_by_token(login_token).last_login_at is not None

    overview = auth.overview()

    assert overview.stats.users == 2
    assert overview.stats.admins == 1
    assert overview.stats.packages == 0
    assert [user.email for user in overview.users] == ["student@example.com", "teacher@example.com"]
    assert logged_in.auth_identities[0].provider == "email"


def test_register_rejects_duplicate_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.register("student@example.com", "correct-password")

    assert exc_info.value.status_code == 409


def test_register_and_login_with_phone(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    token, user = auth.register("13800138000", "correct-password")

    assert user.phone == "13800138000"
    assert user.email.endswith("@phone.openclass.local")
    assert user.auth_identities[0].provider == "phone"
    assert auth.get_user_by_token(token).id == user.id

    login_token, logged_in = auth.login("+86 138 0013 8000", "correct-password")

    assert logged_in.id == user.id
    assert auth.get_user_by_token(login_token).phone == "13800138000"


def test_guest_session_can_use_workspace_without_creating_user(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "游客临时课程包"
    store.save_for_user(guest_user.id, guest_workspace)

    reloaded_guest = auth.get_user_by_token(guest_token)

    assert reloaded_guest.role == "guest"
    assert store.load_for_user(guest_user.id).packages[0].title == "游客临时课程包"


def test_register_claims_guest_workspace(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "登录前学习记录"
    store.save_for_user(guest_user.id, guest_workspace)

    _, user = auth.register("student@example.com", "correct-password", guest_token=guest_token)

    assert store.load_for_user(user.id).packages[0].title == "登录前学习记录"
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(guest_token)
    assert exc_info.value.status_code == 401


def test_register_rejects_duplicate_phone(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("13800138000", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.register("13800138000", "correct-password")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "该手机号已注册"


def test_login_rejects_wrong_password(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.login("student@example.com", "wrong-password")

    assert exc_info.value.status_code == 401


def test_email_code_login_requires_existing_account_and_consumes_code(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    _, existing_user = auth.register("student@example.com", "correct-password")
    sent: dict[str, str] = {}

    def capture_email(*, email: str, code: str, purpose: str) -> None:
        sent.update(email=email, code=code, purpose=purpose)

    monkeypatch.setattr("app.services.auth_service.send_email_code", capture_email)

    challenge_id, expires_in = auth.request_email_code("Student@Example.com")
    token, user = auth.verify_email_code(challenge_id, sent["code"])

    assert sent["email"] == "student@example.com"
    assert sent["purpose"] == "login"
    assert expires_in == 600
    assert user.id == existing_user.id
    assert auth.get_user_by_token(token).id == user.id
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_email_code(challenge_id, sent["code"])
    assert exc_info.value.status_code == 401


def test_email_code_login_rejects_unregistered_email(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.setattr("app.services.auth_service.send_email_code", lambda **_: None)

    with pytest.raises(HTTPException) as exc_info:
        auth.request_email_code("new-student@example.com")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "该邮箱尚未注册，请先注册"


def test_email_registration_requires_code_and_preserves_username(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    sent: dict[str, str] = {}

    def capture_email(*, email: str, code: str, purpose: str) -> None:
        sent.update(email=email, code=code, purpose=purpose)

    monkeypatch.setattr("app.services.auth_service.send_email_code", capture_email)

    challenge_id, expires_in = auth.request_registration_email_code("New.Student@Example.com")
    token, user = auth.register_email(
        email="new.student@example.com",
        username="新同学",
        password="correct-password",
        challenge_id=challenge_id,
        code=sent["code"],
    )

    assert sent == {
        "email": "new.student@example.com",
        "code": sent["code"],
        "purpose": "register",
    }
    assert expires_in == 600
    assert user.email == "new.student@example.com"
    assert user.display_name == "新同学"
    assert auth.get_user_by_token(token).id == user.id
    login_token, logged_in = auth.login("new.student@example.com", "correct-password")
    assert logged_in.id == user.id
    assert auth.get_user_by_token(login_token).id == user.id

    with pytest.raises(HTTPException) as exc_info:
        auth.register_email(
            email="new.student@example.com",
            username="新同学",
            password="correct-password",
            challenge_id=challenge_id,
            code=sent["code"],
        )
    assert exc_info.value.status_code in {401, 409}


def test_email_registration_rejects_wrong_code_without_creating_account(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        "app.services.auth_service.send_email_code",
        lambda **values: sent.update(code=values["code"]),
    )

    challenge_id, _ = auth.request_registration_email_code("new-student@example.com")
    wrong_code = "000000" if sent["code"] != "000000" else "000001"

    with pytest.raises(HTTPException) as exc_info:
        auth.register_email(
            email="new-student@example.com",
            username="New Student",
            password="correct-password",
            challenge_id=challenge_id,
            code=wrong_code,
        )

    assert exc_info.value.status_code == 401
    with auth.store.connection() as conn:
        assert auth.store.find_user_by_email(conn, "new-student@example.com") is None
        assert auth.store.find_email_challenge(conn, challenge_id)["attempts"] == 1


def test_email_code_login_rejects_bad_code_and_rate_limits_resend(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    sent: dict[str, str] = {}
    auth.register("student@example.com", "correct-password")
    monkeypatch.setattr(
        "app.services.auth_service.send_email_code",
        lambda **values: sent.update(code=values["code"]),
    )

    challenge_id, _ = auth.request_email_code("student@example.com")

    with pytest.raises(HTTPException) as exc_info:
        auth.verify_email_code(challenge_id, "000000" if sent["code"] != "000000" else "000001")
    assert exc_info.value.status_code == 401

    with auth.store.connection() as conn:
        assert auth.store.find_email_challenge(conn, challenge_id)["attempts"] == 1

    with pytest.raises(HTTPException) as exc_info:
        auth.request_email_code("student@example.com")
    assert exc_info.value.status_code == 429


def test_email_challenge_consumption_is_atomic(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    auth.register("student@example.com", "correct-password")
    monkeypatch.setattr("app.services.auth_service.send_email_code", lambda **_: None)

    challenge_id, _ = auth.request_email_code("student@example.com")

    with auth.store.transaction() as conn:
        assert auth.store.consume_email_challenge(conn, challenge_id=challenge_id, now="2026-07-27T00:00:00+00:00")
        assert not auth.store.consume_email_challenge(
            conn,
            challenge_id=challenge_id,
            now="2026-07-27T00:00:01+00:00",
        )


def test_provider_list_includes_supported_social_logins(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.delenv("OPENCLASS_CHATGPT_PLATFORM_LOGIN_ENABLED", raising=False)

    provider_ids = {provider.id for provider in auth.providers()}

    assert {"google", "apple", "github", "microsoft", "x"}.issubset(provider_ids)
    assert "chatgpt" not in provider_ids
    assert "wechat" not in provider_ids


def test_chatgpt_platform_login_provider_requires_explicit_opt_in(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.setenv("OPENCLASS_CHATGPT_PLATFORM_LOGIN_ENABLED", "true")
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)

    providers = {provider.id: provider for provider in auth.providers()}

    assert providers["chatgpt"].kind == "device"
    assert providers["chatgpt"].configured is True


def test_chatgpt_platform_login_provider_reports_unavailable_runtime(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.setenv("OPENCLASS_CHATGPT_PLATFORM_LOGIN_ENABLED", "true")
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: False)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)

    providers = {provider.id: provider for provider in auth.providers()}

    assert providers["chatgpt"].configured is False


def test_provider_configuration_reflects_env(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_SECRET", "google-secret")

    providers = {provider.id: provider for provider in auth.providers()}

    assert providers["google"].configured is True
    assert "wechat" not in providers


def test_x_oauth_authorization_url_uses_pkce(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_ID", "x-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_SECRET", "x-secret")
    monkeypatch.delenv("OPENCLASS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("OPENCLASS_WEB_ORIGIN", raising=False)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/oauth/x/start",
            "headers": [
                (b"host", b"example.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "query_string": b"",
            "server": ("example.com", 443),
            "scheme": "https",
        }
    )

    target = auth.oauth_authorization_url("x", "/studio", request)

    assert target.startswith("https://x.com/i/oauth2/authorize?")
    assert "code_challenge=" in target
    assert "code_challenge_method=S256" in target
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fapi%2Fauth%2Foauth%2Fx%2Fcallback" in target


@pytest.mark.parametrize(
    "value",
    ["//evil.example/path", "/\\evil.example/path", "/safe\npath", "https://evil.example/path"],
)
def test_safe_next_path_rejects_cross_origin_or_ambiguous_paths(value: str) -> None:
    assert _safe_next_path(value) == "/"


def test_safe_next_path_preserves_local_path_and_query() -> None:
    assert _safe_next_path("/home?tab=recent") == "/home?tab=recent"


def test_oauth_login_links_existing_email_and_reuses_unique_account(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    _, email_user = auth.register("student@example.com", "correct-password")
    google_token, google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
        )
    )
    _, second_google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
        )
    )

    assert google_user.id == email_user.id
    assert second_google_user.id == email_user.id
    assert auth.get_user_by_token(google_token).id == email_user.id
    assert {identity.provider for identity in google_user.auth_identities} == {"email", "google"}


def test_oauth_login_without_email_gets_stable_synthetic_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    _, user = auth.login_with_oauth(
        OAuthProfile(
            provider="github",
            subject="12345",
            email=None,
            display_name="octocat",
            avatar_url=None,
        )
    )

    assert user.email == "github-12345@oauth.openclass.local"
    assert user.auth_identities[0].provider == "github"


def test_oauth_upgrade_reuses_guest_identity_and_preserves_workspace_owner(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "登录前学习记录"
    store.save_for_user(guest_user.id, guest_workspace)

    member_token, member = auth.login_with_oauth(
        OAuthProfile(
            provider="chatgpt",
            subject="email:student@example.com",
            email="student@example.com",
            display_name="student",
        ),
        guest_user_id=guest_user.id,
    )

    assert member.id == guest_user.id
    assert member.role in {"user", "admin"}
    assert store.load_for_user(member.id).packages[0].title == "登录前学习记录"
    assert auth.get_user_by_token(member_token).id == member.id
    assert {identity.provider for identity in member.auth_identities} == {"chatgpt"}
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(guest_token)
    assert exc_info.value.status_code == 401


def test_oauth_upgrade_from_a_new_guest_reuses_existing_external_account(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    profile = OAuthProfile(
        provider="chatgpt",
        subject="email:student@example.com",
        email="student@example.com",
        display_name="student",
    )

    _, first_guest = auth.start_guest_session()
    _, existing_member = auth.login_with_oauth(profile, guest_user_id=first_guest.id)
    second_guest_token, second_guest = auth.start_guest_session()
    second_workspace = store.load_for_user(second_guest.id)
    second_workspace.packages[0].title = "第二次登录前记录"
    store.save_for_user(second_guest.id, second_workspace)

    _, reused_member = auth.login_with_oauth(profile, guest_user_id=second_guest.id)

    assert reused_member.id == existing_member.id
    assert store.load_for_user(existing_member.id).packages[0].title == "第二次登录前记录"
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(second_guest_token)
    assert exc_info.value.status_code == 401


def test_existing_account_claim_moves_owner_scopes_and_invalidates_stale_revision(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    profile = OAuthProfile(
        provider="chatgpt",
        subject="email:student@example.com",
        email="student@example.com",
        display_name="student",
    )
    _, first_guest = auth.start_guest_session()
    _, existing_member = auth.login_with_oauth(profile, guest_user_id=first_guest.id)
    existing_workspace = store.load_for_user(existing_member.id)
    store.save_for_user(existing_member.id, existing_workspace)

    _, second_guest = auth.start_guest_session()
    guest_workspace = store.load_for_user(second_guest.id)
    guest_workspace.packages[0].title = "待合并课程"
    store.save_for_user(second_guest.id, guest_workspace)

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE source_notebooks (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL
            );
            CREATE TABLE learning_requirement_runs (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE owner_search USING fts5(
                owner_user_id UNINDEXED,
                body
            );
            """
        )
        conn.execute("INSERT INTO source_notebooks VALUES ('notebook_1', ?)", (second_guest.id,))
        conn.execute("INSERT INTO learning_requirement_runs VALUES ('run_1', ?)", (second_guest.id,))
        conn.execute("INSERT INTO owner_search(owner_user_id, body) VALUES (?, 'evidence')", (second_guest.id,))
        conn.execute(
            "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, '1')",
            (f"workspace_revision:{existing_member.id}",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, '1')",
            (f"workspace_revision:{second_guest.id}",),
        )

    _, reused_member = auth.login_with_oauth(profile, guest_user_id=second_guest.id)

    assert reused_member.id == existing_member.id
    with sqlite3.connect(db_path) as conn:
        for table_name in ("source_notebooks", "learning_requirement_runs", "owner_search"):
            owner = conn.execute(f"SELECT owner_user_id FROM {table_name}").fetchone()[0]
            assert owner == existing_member.id
        revision = conn.execute(
            "SELECT value FROM workspace_settings WHERE key = ?",
            (f"workspace_revision:{existing_member.id}",),
        ).fetchone()[0]
        guest_revision = conn.execute(
            "SELECT value FROM workspace_settings WHERE key = ?",
            (f"workspace_revision:{second_guest.id}",),
        ).fetchone()
    assert revision == "2"
    assert guest_revision is None
    assert store.save_for_user_if_revision(
        existing_member.id,
        store.load_for_user(existing_member.id),
        expected_revision=1,
    ) is False


def test_existing_account_claim_rekeys_and_deduplicates_board_assets(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    _, member = auth.register("member@example.com", "correct-password")
    member_workspace = store.load_for_user(member.id)
    member_lesson_id = _append_empty_lesson(member_workspace, "会员课时").id
    store.save_for_user(member.id, member_workspace)

    guest_token, guest = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest.id)
    guest_lesson = _append_empty_lesson(guest_workspace, "游客课时")
    store.save_for_user(guest.id, guest_workspace)
    guest_lesson_id = guest_lesson.id
    guest_document_id = guest_lesson.board_document.id

    asset_store = BoardAssetStore(db_path, tmp_path / "board-assets")
    duplicate_content = _png_bytes((190, 25, 25))
    unique_content = _png_bytes((25, 70, 190))
    member_asset = asset_store.put_bytes(
        owner_user_id=member.id,
        lesson_id=member_lesson_id,
        content=duplicate_content,
        mime_type="image/png",
    )
    asset_store.add_reference(
        asset_id=member_asset.id,
        owner_user_id=member.id,
        lesson_id=guest_lesson_id,
        document_id=guest_document_id,
        source_visual_id="visual_duplicate",
    )
    guest_duplicate = asset_store.put_bytes(
        owner_user_id=guest.id,
        lesson_id=guest_lesson_id,
        document_id=guest_document_id,
        source_visual_id="visual_duplicate",
        content=duplicate_content,
        mime_type="image/png",
    )
    guest_unique = asset_store.put_bytes(
        owner_user_id=guest.id,
        lesson_id=guest_lesson_id,
        document_id=guest_document_id,
        source_visual_id="visual_unique",
        content=unique_content,
        mime_type="image/png",
    )
    claimed_unique_id = stable_board_asset_id(
        owner_user_id=member.id,
        content_hash=guest_unique.content_hash,
    )
    duplicate_url = board_asset_content_url(guest_duplicate.id)
    unique_url = board_asset_content_url(guest_unique.id)

    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "resourceVisualBlock",
                "attrs": {
                    "assetId": guest_duplicate.id,
                    "originalSrc": duplicate_url,
                },
            },
            {
                "type": "resourceVisualBlock",
                "attrs": {
                    "assetId": guest_unique.id,
                    "originalSrc": unique_url,
                },
            },
        ],
        "userText": guest_duplicate.id,
    }
    content_html = (
        '<section data-type="resource-visual-block" '
        f'data-board-asset-id="{guest_duplicate.id}" data-original-src="{duplicate_url}">'
        f'<p>{guest_duplicate.id}</p></section>'
        '<section data-type="resource-visual-block" '
        f'data-board-asset-id="{guest_unique.id}" data-original-src="{unique_url}"></section>'
    )
    content_text = (
        f"![重复图]({duplicate_url})\n\n![独有图]({unique_url})\n\n"
        f"用户正文保留旧 ID：{guest_duplicate.id}"
    )
    operations_json = json.dumps(
        [
            {
                "op": "insert_after",
                "asset_url": unique_url,
                "note": guest_unique.id,
            }
        ],
        ensure_ascii=False,
    )
    metadata_json = json.dumps(
        {
            "board_asset_ids": [guest_duplicate.id, guest_unique.id],
            "note": guest_duplicate.id,
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE lessons
            SET board_content_json = ?, board_content_html = ?, board_content_text = ?
            WHERE id = ?
            """,
            (json.dumps(content_json), content_html, content_text, guest_lesson_id),
        )
        conn.execute(
            """
            UPDATE lesson_commits
            SET operations_json = ?, snapshot_content_json = ?,
                snapshot_content_html = ?, snapshot_content_text = ?, metadata_json = ?
            WHERE lesson_id = ?
            """,
            (
                operations_json,
                json.dumps(content_json),
                content_html,
                content_text,
                metadata_json,
                guest_lesson_id,
            ),
        )

    _, claimed_member = auth.login(
        "member@example.com",
        "correct-password",
        guest_token=guest_token,
    )

    assert claimed_member.id == member.id
    assert member_asset.id == stable_board_asset_id(
        owner_user_id=member.id,
        content_hash=guest_duplicate.content_hash,
    )
    assert asset_store.get(guest_duplicate.id, guest.id) is None
    assert asset_store.get(guest_unique.id, guest.id) is None
    claimed_unique = asset_store.read_bytes(claimed_unique_id, member.id)
    assert claimed_unique is not None
    assert claimed_unique[1] == unique_content
    repeated_reference = asset_store.add_reference(
        asset_id=claimed_unique_id,
        owner_user_id=member.id,
        lesson_id=guest_lesson_id,
        document_id=guest_document_id,
        source_visual_id="visual_unique",
    )
    assert repeated_reference.asset_id == claimed_unique_id

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        asset_rows = conn.execute(
            "SELECT id, owner_user_id, content_hash FROM board_assets ORDER BY id"
        ).fetchall()
        assert {(row["id"], row["owner_user_id"], row["content_hash"]) for row in asset_rows} == {
            (member_asset.id, member.id, member_asset.content_hash),
            (claimed_unique_id, member.id, guest_unique.content_hash),
        }
        assert conn.execute(
            "SELECT count(*) FROM board_asset_refs WHERE owner_user_id = ?",
            (guest.id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT count(*) FROM board_asset_refs
            WHERE asset_id = ? AND owner_user_id = ? AND lesson_id = ?
              AND document_id = ? AND source_visual_id = 'visual_duplicate'
            """,
            (member_asset.id, member.id, guest_lesson_id, guest_document_id),
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT count(*) FROM board_asset_refs
            WHERE asset_id = ? AND owner_user_id = ? AND lesson_id = ?
              AND document_id = ? AND source_visual_id = 'visual_unique'
            """,
            (claimed_unique_id, member.id, guest_lesson_id, guest_document_id),
        ).fetchone()[0] == 1

        lesson_row = conn.execute(
            """
            SELECT board_content_json, board_content_html, board_content_text
            FROM lessons WHERE id = ?
            """,
            (guest_lesson_id,),
        ).fetchone()
        assert lesson_row is not None
        claimed_json = json.loads(lesson_row["board_content_json"])
        assert claimed_json["content"][0]["attrs"] == {
            "assetId": member_asset.id,
            "originalSrc": board_asset_content_url(member_asset.id),
        }
        assert claimed_json["content"][1]["attrs"] == {
            "assetId": claimed_unique_id,
            "originalSrc": board_asset_content_url(claimed_unique_id),
        }
        assert claimed_json["userText"] == guest_duplicate.id
        assert f'data-board-asset-id="{member_asset.id}"' in lesson_row["board_content_html"]
        assert f'data-board-asset-id="{claimed_unique_id}"' in lesson_row["board_content_html"]
        assert f"<p>{guest_duplicate.id}</p>" in lesson_row["board_content_html"]
        assert f"![重复图]({board_asset_content_url(member_asset.id)})" in lesson_row["board_content_text"]
        assert f"![独有图]({board_asset_content_url(claimed_unique_id)})" in lesson_row["board_content_text"]
        assert f"用户正文保留旧 ID：{guest_duplicate.id}" in lesson_row["board_content_text"]

        commit_row = conn.execute(
            """
            SELECT operations_json, snapshot_content_json, snapshot_content_html,
                   snapshot_content_text, metadata_json
            FROM lesson_commits WHERE lesson_id = ? ORDER BY sort_order DESC LIMIT 1
            """,
            (guest_lesson_id,),
        ).fetchone()
        assert commit_row is not None
        assert json.loads(commit_row["operations_json"])[0] == {
            "op": "insert_after",
            "asset_url": board_asset_content_url(claimed_unique_id),
            "note": guest_unique.id,
        }
        assert json.loads(commit_row["metadata_json"]) == {
            "board_asset_ids": [member_asset.id, claimed_unique_id],
            "note": guest_duplicate.id,
        }
        assert json.loads(commit_row["snapshot_content_json"])["userText"] == guest_duplicate.id
        assert f"<p>{guest_duplicate.id}</p>" in commit_row["snapshot_content_html"]
        assert f"用户正文保留旧 ID：{guest_duplicate.id}" in commit_row["snapshot_content_text"]

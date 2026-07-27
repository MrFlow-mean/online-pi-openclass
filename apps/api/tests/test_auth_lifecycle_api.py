from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import auth as auth_router
from app.services.auth_service import AUTH_COOKIE_NAME, AuthService
from app.services.course_store import SqliteCourseStore


def test_login_sets_http_only_cookie_and_logout_revokes_it(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    service = AuthService(db_path)
    service.register("student@example.com", "correct-password")
    monkeypatch.setattr(auth_router, "auth_service", service)
    monkeypatch.setenv("OPENCLASS_PUBLIC_ORIGIN", "https://example.test")
    monkeypatch.setenv("OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED", "false")
    app = FastAPI()
    app.include_router(auth_router.router)

    with TestClient(app, base_url="https://example.test") as client:
        login = client.post(
            "/api/auth/login",
            json={"identifier": "student@example.com", "password": "correct-password"},
        )

        assert login.status_code == 200
        assert login.json()["token"] is None
        set_cookie = login.headers["set-cookie"]
        assert f"{AUTH_COOKIE_NAME}=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Secure" in set_cookie
        assert "Max-Age=2592000" in set_cookie
        assert client.get("/api/auth/me").status_code == 200

        logout = client.post("/api/auth/logout")

        assert logout.status_code == 200
        assert f"{AUTH_COOKIE_NAME}=\"\"" in logout.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 401


def test_oauth_redirect_does_not_put_bearer_token_in_url(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    service = AuthService(db_path)
    _, user = service.register("student@example.com", "correct-password")
    request = type("RequestLike", (), {})()

    target = service.oauth_frontend_redirect_url(
        "secret-bearer-token",
        user,
        "/studio?tab=course",
        "https://example.test",
        request,  # type: ignore[arg-type]
    )

    assert target == "https://example.test/auth/callback?next=%2Fstudio%3Ftab%3Dcourse"
    assert "secret-bearer-token" not in target

from __future__ import annotations

from urllib import parse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import UserView
from app.routers import auth
from app.services.community_oauth import CommunityOAuthService


def _user(*, role: str = "user") -> UserView:
    return UserView(
        id="user_answer",
        email="learner@example.com",
        role=role,
        display_name="OpenClass Learner",
        avatar_url="https://example.com/avatar.png",
        created_at="2026-07-25T00:00:00+00:00",
    )


def _client(monkeypatch, *, role: str = "user") -> TestClient:
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", "openclass-answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", "a-long-community-secret")
    monkeypatch.setenv(
        "OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI",
        "https://community.example.com/answer/api/v1/connector/redirect/basic",
    )
    monkeypatch.setattr(auth, "community_oauth_service", CommunityOAuthService())
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[auth.current_user] = lambda: _user(role=role)
    return TestClient(app)


def test_answer_oauth_authorization_code_flow_is_one_time(monkeypatch) -> None:
    client = _client(monkeypatch)
    redirect_uri = "https://community.example.com/answer/api/v1/connector/redirect/basic"
    authorization = client.get(
        "/api/auth/community/authorize",
        params={
            "client_id": "openclass-answer",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": "answer-state",
        },
        follow_redirects=False,
    )

    assert authorization.status_code == 302
    location = parse.urlparse(authorization.headers["location"])
    query = parse.parse_qs(location.query)
    assert query["state"] == ["answer-state"]

    token_response = client.post(
        "/api/auth/community/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": redirect_uri,
            "client_id": "openclass-answer",
            "client_secret": "a-long-community-secret",
        },
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]

    userinfo = client.get(
        "/api/auth/community/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert userinfo.status_code == 200
    assert userinfo.json() == {
        "id": "user_answer",
        "sub": "user_answer",
        "name": "OpenClass Learner",
        "username": "user_answer",
        "email": "learner@example.com",
        "email_verified": False,
        "avatar_url": "https://example.com/avatar.png",
    }

    replay = client.post(
        "/api/auth/community/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": redirect_uri,
            "client_id": "openclass-answer",
            "client_secret": "a-long-community-secret",
        },
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "invalid_grant"


def test_answer_oauth_rejects_unregistered_redirects_and_bad_clients(monkeypatch) -> None:
    client = _client(monkeypatch)

    bad_redirect = client.get(
        "/api/auth/community/authorize",
        params={
            "client_id": "openclass-answer",
            "redirect_uri": "https://evil.example/callback",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    assert bad_redirect.status_code == 400
    assert bad_redirect.json()["detail"] == "invalid_redirect_uri"

    bad_client = client.get(
        "/api/auth/community/authorize",
        params={
            "client_id": "another-client",
            "redirect_uri": "https://community.example.com/answer/api/v1/connector/redirect/basic",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    assert bad_client.status_code == 400
    assert bad_client.json()["detail"] == "invalid_client"


def test_answer_oauth_rejects_guest_accounts(monkeypatch) -> None:
    client = _client(monkeypatch, role="guest")

    response = client.get(
        "/api/auth/community/authorize",
        params={
            "client_id": "openclass-answer",
            "redirect_uri": "https://community.example.com/answer/api/v1/connector/redirect/basic",
            "response_type": "code",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "请先注册或登录 OpenClass"

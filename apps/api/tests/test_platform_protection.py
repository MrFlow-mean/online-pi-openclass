from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.services.http_security import CsrfProtectionMiddleware, SecurityHeadersMiddleware
from app.services.human_verification import TurnstileConfig, TurnstileVerifier
from app.services.rate_limiter import (
    InMemorySlidingWindowRateLimiter,
    RateLimitPolicy,
    anonymized_rate_limit_subject,
    client_ip_from_request,
)


def _request(*, client: str, forwarded_for: str = "") -> Request:
    headers = []
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (client, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_turnstile_verifies_action_hostname_and_remote_ip() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: values[0] for key, values in parse_qs(request.content.decode()).items()})
        return httpx.Response(
            200,
            json={
                "success": True,
                "hostname": "open-classes.com",
                "action": "login",
                "error-codes": [],
            },
        )

    verifier = TurnstileVerifier(
        TurnstileConfig(
            enabled=True,
            secret_key="server-secret",
            expected_hostnames=frozenset({"open-classes.com"}),
        ),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        verifier.verify("browser-token", remote_ip="203.0.113.8", expected_action="login")
    )

    assert result.success is True
    assert result.reason == "verified"
    assert seen["secret"] == "server-secret"
    assert seen["response"] == "browser-token"
    assert seen["remoteip"] == "203.0.113.8"
    assert seen["idempotency_key"]
    assert "server-secret" not in repr(result)


@pytest.mark.parametrize(
    ("hostname", "action", "reason"),
    [
        ("evil.example", "login", "hostname-mismatch"),
        ("open-classes.com", "register", "action-mismatch"),
    ],
)
def test_turnstile_rejects_response_binding_mismatches(
    hostname: str,
    action: str,
    reason: str,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"success": True, "hostname": hostname, "action": action},
        )
    )
    verifier = TurnstileVerifier(
        TurnstileConfig(
            enabled=True,
            secret_key="secret",
            expected_hostnames=frozenset({"open-classes.com"}),
        ),
        transport=transport,
    )

    result = asyncio.run(verifier.verify("token", remote_ip=None, expected_action="login"))

    assert result.success is False
    assert result.reason == reason


def test_turnstile_fails_closed_for_missing_config_and_provider_failure() -> None:
    missing_config = TurnstileVerifier(
        TurnstileConfig(enabled=True, secret_key="", expected_hostnames=frozenset())
    )
    assert asyncio.run(
        missing_config.verify("token", remote_ip=None, expected_action="login")
    ).reason == (
        "configuration-error"
    )

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    unavailable_verifier = TurnstileVerifier(
        TurnstileConfig(
            enabled=True,
            secret_key="secret",
            expected_hostnames=frozenset({"open-classes.com"}),
        ),
        transport=httpx.MockTransport(unavailable),
    )
    result = asyncio.run(
        unavailable_verifier.verify("token", remote_ip=None, expected_action="login")
    )
    assert result.success is False
    assert result.reason == "siteverify-unavailable"


def test_turnstile_defaults_to_fail_closed_in_production(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_ENV", "production")
    monkeypatch.delenv("OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED", raising=False)
    monkeypatch.delenv("OPENCLASS_CLOUDFLARE_TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.delenv("OPENCLASS_TURNSTILE_SECRET_KEY", raising=False)

    config = TurnstileConfig.from_environment()

    assert config.enabled is True
    result = asyncio.run(
        TurnstileVerifier(config).verify("token", remote_ip=None, expected_action="login")
    )
    assert result.success is False
    assert result.reason == "configuration-error"


def test_turnstile_is_disabled_only_for_explicit_local_runtime(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_ENV", "production")
    monkeypatch.setenv("OPENCLASS_LOCAL_RUNTIME", "true")
    monkeypatch.setenv("OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CLOUDFLARE_TURNSTILE_SECRET_KEY", "server-secret")

    config = TurnstileConfig.from_environment()

    assert config.enabled is False
    result = asyncio.run(
        TurnstileVerifier(config).verify(None, remote_ip=None, expected_action="login")
    )
    assert result.success is True
    assert result.reason == "disabled"


def test_sliding_window_rate_limiter_returns_retry_metadata() -> None:
    limiter = InMemorySlidingWindowRateLimiter()
    policy = RateLimitPolicy(limit=2, window_seconds=60)

    assert limiter.check("login", "subject", policy, now=10).allowed is True
    assert limiter.check("login", "subject", policy, now=20).remaining == 0
    blocked = limiter.check("login", "subject", policy, now=30)
    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 40
    assert limiter.check("login", "subject", policy, now=71).allowed is True


def test_rate_limit_subject_does_not_expose_account_identifier() -> None:
    subject = anonymized_rate_limit_subject("203.0.113.8", "Person@Example.com")
    assert len(subject) == 64
    assert "person" not in subject


def test_client_ip_ignores_forwarding_headers_from_untrusted_peer() -> None:
    request = _request(client="198.51.100.9", forwarded_for="203.0.113.7")
    trusted = (ipaddress.ip_network("127.0.0.0/8"),)
    assert client_ip_from_request(request, trusted_proxy_networks=trusted) == "198.51.100.9"


def test_client_ip_walks_trusted_proxy_chain_from_the_nearest_hop() -> None:
    request = _request(
        client="127.0.0.1",
        forwarded_for="203.0.113.7, 173.245.48.10",
    )
    trusted = (
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("173.245.48.0/20"),
    )
    assert client_ip_from_request(request, trusted_proxy_networks=trusted) == "203.0.113.7"


def test_client_ip_rejects_malformed_forwarding_chain() -> None:
    request = _request(client="127.0.0.1", forwarded_for="203.0.113.7, not-an-ip")
    trusted = (ipaddress.ip_network("127.0.0.0/8"),)
    assert client_ip_from_request(request, trusted_proxy_networks=trusted) == "127.0.0.1"


def _security_app(monkeypatch: pytest.MonkeyPatch, *, production: bool = False) -> TestClient:
    if production:
        monkeypatch.setenv("OPENCLASS_ENV", "production")
    else:
        monkeypatch.delenv("OPENCLASS_ENV", raising=False)
    app = FastAPI()
    app.add_middleware(
        CsrfProtectionMiddleware,
        allowed_origins=("https://open-classes.com", "http://localhost:3000"),
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/mutation")
    def mutation() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_csrf_accepts_allowed_origin_and_rejects_cross_site_cookie_request(monkeypatch) -> None:
    client = _security_app(monkeypatch)
    client.cookies.set("openclass.auth.token", "session")

    assert client.post(
        "/mutation",
        headers={"Origin": "https://open-classes.com"},
    ).status_code == 200
    response = client.post(
        "/mutation",
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "请求来源验证失败"}


def test_csrf_fetch_metadata_and_authorization_behavior(monkeypatch) -> None:
    client = _security_app(monkeypatch)
    client.cookies.set("openclass.guest.auth.token", "guest")

    assert client.post(
        "/mutation",
        headers={"Sec-Fetch-Site": "same-origin"},
    ).status_code == 200
    assert client.post(
        "/mutation",
        headers={"Sec-Fetch-Site": "cross-site"},
    ).status_code == 403
    assert client.post(
        "/mutation",
        headers={"Origin": "https://attacker.example", "Authorization": "Bearer explicit"},
    ).status_code == 200
    assert client.post("/mutation").status_code == 200


def test_security_headers_include_hsts_only_in_production(monkeypatch) -> None:
    production = _security_app(monkeypatch, production=True).post("/mutation")
    assert production.headers["strict-transport-security"].startswith("max-age=31536000")
    assert production.headers["x-frame-options"] == "DENY"
    assert production.headers["x-content-type-options"] == "nosniff"
    assert production.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )


def test_api_docs_csp_allows_only_the_swagger_asset_host(monkeypatch) -> None:
    client = _security_app(monkeypatch)
    response = client.get("/docs")
    policy = response.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in policy
    assert "frame-ancestors 'none'" in policy
    assert "*" not in policy

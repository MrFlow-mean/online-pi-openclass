from __future__ import annotations

from app.services import community_adapter as adapter_module
from app.services.community_adapter import CommunityAdapter


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_native_community_remains_the_safe_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_COMMUNITY_PROVIDER", raising=False)
    integration = CommunityAdapter().integration()

    assert integration.provider == "native"
    assert integration.entry_url == "/community"
    assert integration.available is True
    assert integration.setup_required is False


def test_answer_integration_reports_sso_entry_when_fully_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PROVIDER", "answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PUBLIC_URL", "https://community.example.com/")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_INTERNAL_URL", "http://answer:80/")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", "openclass-answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", "a-long-secret")
    monkeypatch.setenv(
        "OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI",
        "https://community.example.com/answer/api/v1/connector/redirect/basic",
    )
    monkeypatch.setattr(adapter_module.urlrequest, "urlopen", lambda request, timeout: _Response())

    integration = CommunityAdapter().integration()

    assert integration.provider == "answer"
    assert integration.public_url == "https://community.example.com"
    assert integration.entry_url == (
        "https://community.example.com/answer/api/v1/connector/login/basic"
    )
    assert integration.available is True
    assert integration.sso_enabled is True
    assert integration.setup_required is False


def test_answer_integration_does_not_claim_readiness_without_service_or_sso(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PROVIDER", "answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PUBLIC_URL", "javascript:alert(1)")
    monkeypatch.delenv("OPENCLASS_COMMUNITY_INTERNAL_URL", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI", raising=False)

    integration = CommunityAdapter().integration()

    assert integration.provider == "answer"
    assert integration.public_url is None
    assert integration.entry_url == "/community"
    assert integration.available is False
    assert integration.sso_enabled is False
    assert integration.setup_required is True

from __future__ import annotations

import io
import json

from app.services import community_adapter as adapter_module
from app.services.community_adapter import CommunityAdapter


class _Response:
    status = 200

    def __init__(self, payload=None) -> None:
        self._stream = io.BytesIO(json.dumps(payload or {}).encode())

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_answer_is_the_only_community_provider(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_COMMUNITY_PUBLIC_URL", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_INTERNAL_URL", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI", raising=False)
    integration = CommunityAdapter().integration()

    assert integration.provider == "answer"
    assert integration.entry_url == "/community"
    assert integration.available is False
    assert integration.setup_required is True


def test_answer_integration_reports_sso_entry_when_fully_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PUBLIC_URL", "https://community.example.com/")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_INTERNAL_URL", "http://answer:80/")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", "openclass-answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", "a-long-secret")
    monkeypatch.setenv(
        "OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI",
        "https://community.example.com/answer/api/v1/connector/redirect/basic",
    )
    def _urlopen(request, timeout):
        if request.full_url.endswith("/connector/info"):
            return _Response(
                {
                    "data": [
                        {
                            "name": "OpenClass",
                            "link": "https://community.example.com/answer/api/v1/connector/login/basic",
                        }
                    ]
                }
            )
        return _Response()

    monkeypatch.setattr(adapter_module.urlrequest, "urlopen", _urlopen)

    integration = CommunityAdapter().integration()

    assert integration.provider == "answer"
    assert integration.public_url == "https://community.example.com"
    assert integration.entry_url == (
        "https://community.example.com/answer/api/v1/connector/login/basic"
    )
    assert integration.available is True
    assert integration.sso_enabled is True
    assert integration.setup_required is False


def test_answer_integration_requires_enabled_basic_connector(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_COMMUNITY_PUBLIC_URL", "https://community.example.com")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID", "openclass-answer")
    monkeypatch.setenv("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET", "a-long-secret")
    monkeypatch.setenv(
        "OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI",
        "https://community.example.com/answer/api/v1/connector/redirect/basic",
    )
    monkeypatch.setattr(
        adapter_module.urlrequest,
        "urlopen",
        lambda request, timeout: _Response({"data": []}),
    )

    integration = CommunityAdapter().integration()

    assert integration.available is True
    assert integration.sso_enabled is False
    assert integration.setup_required is True


def test_answer_integration_does_not_claim_readiness_without_service_or_sso(monkeypatch) -> None:
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

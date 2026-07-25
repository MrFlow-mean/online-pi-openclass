from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from urllib import parse, request as urlrequest

from app.models import CommunityIntegrationView
from app.services.community_oauth import community_oauth_service


HEALTH_CACHE_SECONDS = 15
HEALTH_TIMEOUT_SECONDS = 2


def _optional_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith("your_") or value.lower() in {"changeme", "todo"}:
        return ""
    return value.rstrip("/")


def _http_origin(value: str) -> str:
    parsed = parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


@dataclass(frozen=True)
class CommunityAdapterConfig:
    public_url: str
    internal_url: str


class CommunityAdapter:
    def __init__(self) -> None:
        self._health_lock = threading.RLock()
        self._health_cache: tuple[str, float, bool, bool] | None = None

    def config(self) -> CommunityAdapterConfig:
        public_url = _http_origin(_optional_env("OPENCLASS_COMMUNITY_PUBLIC_URL"))
        internal_url = _http_origin(_optional_env("OPENCLASS_COMMUNITY_INTERNAL_URL"))
        return CommunityAdapterConfig(
            public_url=public_url,
            internal_url=internal_url,
        )

    def integration(self) -> CommunityIntegrationView:
        config = self.config()
        oauth_config = community_oauth_service.config()
        available, connector_available = self._answer_status(
            config.internal_url or config.public_url
        )
        sso_enabled = oauth_config.configured and connector_available
        entry_url = config.public_url
        if entry_url and sso_enabled:
            entry_url = f"{entry_url}/answer/api/v1/connector/login/basic"
        return CommunityIntegrationView(
            provider="answer",
            public_url=config.public_url or None,
            entry_url=entry_url or "/community",
            available=available,
            sso_enabled=sso_enabled,
            setup_required=not (config.public_url and sso_enabled and available),
        )

    def _answer_status(self, base_url: str) -> tuple[bool, bool]:
        if not base_url:
            return False, False
        now = time.monotonic()
        with self._health_lock:
            if self._health_cache and self._health_cache[0] == base_url:
                _, checked_at, available, connector_available = self._health_cache
                if now - checked_at < HEALTH_CACHE_SECONDS:
                    return available, connector_available
        try:
            site_request = urlrequest.Request(
                f"{base_url}/answer/api/v1/siteinfo",
                headers={"Accept": "application/json"},
            )
            with urlrequest.urlopen(site_request, timeout=HEALTH_TIMEOUT_SECONDS) as response:
                available = 200 <= response.status < 500
            connector_request = urlrequest.Request(
                f"{base_url}/answer/api/v1/connector/info",
                headers={"Accept": "application/json"},
            )
            with urlrequest.urlopen(
                connector_request, timeout=HEALTH_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
            connectors = payload.get("data", []) if isinstance(payload, dict) else []
            connector_available = any(
                isinstance(connector, dict)
                and str(connector.get("link", "")).rstrip("/").endswith(
                    "/answer/api/v1/connector/login/basic"
                )
                for connector in connectors
            )
        except OSError:
            available = False
            connector_available = False
        except (TypeError, ValueError):
            connector_available = False
        with self._health_lock:
            self._health_cache = (base_url, now, available, connector_available)
        return available, connector_available


community_adapter = CommunityAdapter()

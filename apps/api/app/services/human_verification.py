from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from app.services.rate_limiter import client_ip_from_request


TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_MAX_TOKEN_LENGTH = 2048


@dataclass(frozen=True)
class TurnstileConfig:
    enabled: bool
    secret_key: str
    expected_hostnames: frozenset[str]
    timeout_seconds: float = 5.0

    @classmethod
    def from_environment(cls) -> "TurnstileConfig":
        secret_key = (
            os.getenv("OPENCLASS_CLOUDFLARE_TURNSTILE_SECRET_KEY", "").strip()
            or os.getenv("OPENCLASS_TURNSTILE_SECRET_KEY", "").strip()
        )
        enabled_value = (
            os.getenv("OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED", "").strip()
            or os.getenv("OPENCLASS_TURNSTILE_ENABLED", "").strip()
        ).casefold()
        public_origin = os.getenv("OPENCLASS_PUBLIC_ORIGIN", "").strip()
        production = (
            os.getenv("OPENCLASS_ENV", "").strip().casefold() == "production"
            or public_origin.startswith("https://")
        )
        local_runtime = os.getenv("OPENCLASS_LOCAL_RUNTIME", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        enabled = False if local_runtime else (
            enabled_value in {"1", "true", "yes", "on"}
            if enabled_value
            else production or bool(secret_key)
        )
        configured_hostnames = {
            hostname.strip().casefold().rstrip(".")
            for hostname in (
                os.getenv("OPENCLASS_CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAMES", "")
                or os.getenv("OPENCLASS_TURNSTILE_EXPECTED_HOSTNAMES", "")
            ).split(",")
            if hostname.strip()
        }
        if not configured_hostnames and public_origin:
            try:
                public_hostname = urlsplit(public_origin).hostname
            except ValueError:
                public_hostname = None
            if public_hostname:
                configured_hostnames.add(public_hostname.casefold().rstrip("."))
        try:
            timeout_seconds = float(
                os.getenv("OPENCLASS_CLOUDFLARE_TURNSTILE_TIMEOUT_SECONDS", "")
                or os.getenv("OPENCLASS_TURNSTILE_TIMEOUT_SECONDS", "5")
            )
        except ValueError:
            timeout_seconds = 5.0
        return cls(
            enabled=enabled,
            secret_key=secret_key,
            expected_hostnames=frozenset(configured_hostnames),
            timeout_seconds=max(0.1, min(timeout_seconds, 30.0)),
        )


@dataclass(frozen=True)
class HumanVerificationResult:
    success: bool
    reason: str
    hostname: str = ""
    action: str = ""
    error_codes: tuple[str, ...] = ()


class TurnstileVerifier:
    """Cloudflare Turnstile adapter with strict server-side response checks."""

    def __init__(
        self,
        config: TurnstileConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    async def verify(
        self,
        token: str | None,
        *,
        remote_ip: str | None,
        expected_action: str,
    ) -> HumanVerificationResult:
        if not self.config.enabled:
            return HumanVerificationResult(success=True, reason="disabled")
        if not self.config.secret_key:
            return HumanVerificationResult(success=False, reason="configuration-error")
        if not self.config.expected_hostnames:
            return HumanVerificationResult(success=False, reason="configuration-error")

        normalized_token = (token or "").strip()
        if not normalized_token:
            return HumanVerificationResult(success=False, reason="missing-token")
        if len(normalized_token) > TURNSTILE_MAX_TOKEN_LENGTH:
            return HumanVerificationResult(success=False, reason="invalid-token")
        if not expected_action.strip():
            return HumanVerificationResult(success=False, reason="configuration-error")

        payload = {
            "secret": self.config.secret_key,
            "response": normalized_token,
            "idempotency_key": str(uuid4()),
        }
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self.config.timeout_seconds,
            ) as client:
                response = await client.post(TURNSTILE_SITEVERIFY_URL, data=payload)
                response.raise_for_status()
                raw: Any = response.json()
        except (httpx.HTTPError, ValueError):
            return HumanVerificationResult(success=False, reason="siteverify-unavailable")

        if not isinstance(raw, dict):
            return HumanVerificationResult(success=False, reason="invalid-response")

        hostname = str(raw.get("hostname") or "").strip().casefold().rstrip(".")
        action = str(raw.get("action") or "").strip()
        raw_error_codes = raw.get("error-codes")
        error_codes = tuple(
            str(code) for code in raw_error_codes if isinstance(code, str)
        ) if isinstance(raw_error_codes, list) else ()

        if raw.get("success") is not True:
            return HumanVerificationResult(
                success=False,
                reason="challenge-rejected",
                hostname=hostname,
                action=action,
                error_codes=error_codes,
            )
        if self.config.expected_hostnames and hostname not in self.config.expected_hostnames:
            return HumanVerificationResult(
                success=False,
                reason="hostname-mismatch",
                hostname=hostname,
                action=action,
            )
        if action != expected_action:
            return HumanVerificationResult(
                success=False,
                reason="action-mismatch",
                hostname=hostname,
                action=action,
            )
        return HumanVerificationResult(
            success=True,
            reason="verified",
            hostname=hostname,
            action=action,
        )


def turnstile_verifier_from_environment(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TurnstileVerifier:
    return TurnstileVerifier(TurnstileConfig.from_environment(), transport=transport)


async def require_turnstile_verification(
    request: Request,
    *,
    token: str | None,
    expected_action: str,
    verifier: TurnstileVerifier | None = None,
) -> HumanVerificationResult:
    active_verifier = verifier or turnstile_verifier_from_environment()
    result = await active_verifier.verify(
        token,
        remote_ip=client_ip_from_request(request),
        expected_action=expected_action,
    )
    if result.success:
        return result
    status_code = 503 if result.reason in {"configuration-error", "siteverify-unavailable"} else 403
    raise HTTPException(
        status_code=status_code,
        detail="人机验证暂时不可用，请稍后重试" if status_code == 503 else "人机验证失败，请重新验证",
    )

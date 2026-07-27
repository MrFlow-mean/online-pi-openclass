from __future__ import annotations

import os
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


AUTH_COOKIE_NAMES = frozenset({"openclass.auth.token", "openclass.guest.auth.token"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def configured_web_origins() -> tuple[str, ...]:
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    for value in (os.getenv("OPENCLASS_PUBLIC_ORIGIN"), os.getenv("OPENCLASS_WEB_ORIGIN")):
        normalized = normalize_origin(value or "")
        if normalized and normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


def normalize_origin(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = 80 if parsed.scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    host = parsed.hostname.casefold().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{port_suffix}"


class CsrfProtectionMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(
            origin for value in allowed_origins if (origin := normalize_origin(value))
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "").upper() not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.headers.get("authorization", "").strip() or not (
            AUTH_COOKIE_NAMES & request.cookies.keys()
        ):
            await self.app(scope, receive, send)
            return

        origin = request.headers.get("origin")
        if origin is not None:
            if normalize_origin(origin) not in self.allowed_origins:
                await self._reject(scope, receive, send)
                return
        else:
            fetch_site = request.headers.get("sec-fetch-site", "").strip().casefold()
            if fetch_site and fetch_site != "same-origin":
                await self._reject(scope, receive, send)
                return
            if not fetch_site and any(
                request.headers.get(name)
                for name in ("sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user")
            ):
                await self._reject(scope, receive, send)
                return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": "请求来源验证失败"},
            status_code=403,
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        public_origin = normalize_origin(os.getenv("OPENCLASS_PUBLIC_ORIGIN", ""))
        self.production = (
            os.getenv("OPENCLASS_ENV", "").strip().casefold() == "production"
            or public_origin.startswith("https://")
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = Headers(raw=message.get("headers", []))
                existing = {key.casefold() for key in headers.keys()}
                additions = {
                    "x-content-type-options": "nosniff",
                    "x-frame-options": "DENY",
                    "referrer-policy": "strict-origin-when-cross-origin",
                    "permissions-policy": "camera=(), geolocation=(), microphone=(self), payment=(self)",
                    "content-security-policy": self._content_security_policy(path),
                }
                if self.production:
                    additions["strict-transport-security"] = "max-age=31536000; includeSubDomains"
                raw_headers = list(message.get("headers", []))
                raw_headers.extend(
                    (key.encode("latin-1"), value.encode("latin-1"))
                    for key, value in additions.items()
                    if key not in existing
                )
                message["headers"] = raw_headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _content_security_policy(path: str) -> str:
        if path in {"/docs", "/redoc"}:
            return (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
            )
        return "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

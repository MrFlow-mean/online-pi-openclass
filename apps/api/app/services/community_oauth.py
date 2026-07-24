from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib import parse

from fastapi import HTTPException

from app.models import UserView


AUTHORIZATION_CODE_TTL_SECONDS = 120
ACCESS_TOKEN_TTL_SECONDS = 3600


def _optional_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith("your_") or value.lower() in {"changeme", "todo"}:
        return ""
    return value


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}")
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid_token") from exc


@dataclass(frozen=True)
class CommunityOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


class CommunityOAuthService:
    def __init__(self) -> None:
        self._used_authorization_codes: dict[str, int] = {}
        self._lock = threading.RLock()

    def config(self) -> CommunityOAuthConfig:
        return CommunityOAuthConfig(
            client_id=_optional_env("OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID"),
            client_secret=_optional_env("OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET"),
            redirect_uri=_optional_env("OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI"),
        )

    def authorization_redirect(
        self,
        *,
        user: UserView,
        client_id: str,
        redirect_uri: str,
        response_type: str,
        state: str,
    ) -> str:
        config = self._configured()
        if user.role == "guest":
            raise HTTPException(status_code=403, detail="请先注册或登录 OpenClass")
        if not hmac.compare_digest(client_id, config.client_id):
            raise HTTPException(status_code=400, detail="invalid_client")
        if not hmac.compare_digest(redirect_uri, config.redirect_uri):
            raise HTTPException(status_code=400, detail="invalid_redirect_uri")
        if response_type != "code":
            raise HTTPException(status_code=400, detail="unsupported_response_type")
        code = self._encode_token(
            {
                "typ": "authorization_code",
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "sub": user.id,
                "email": user.email,
                "name": user.display_name or user.email.partition("@")[0],
                "avatar": user.avatar_url or "",
                "exp": int(time.time()) + AUTHORIZATION_CODE_TTL_SECONDS,
                "jti": secrets.token_urlsafe(18),
            },
            config.client_secret,
        )
        query = parse.urlencode({"code": code, "state": state})
        separator = "&" if parse.urlparse(redirect_uri).query else "?"
        return f"{redirect_uri}{separator}{query}"

    def exchange_code(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        grant_type: str,
    ) -> dict[str, object]:
        config = self._configured()
        if grant_type != "authorization_code":
            raise HTTPException(status_code=400, detail="unsupported_grant_type")
        if not hmac.compare_digest(client_id, config.client_id) or not hmac.compare_digest(
            client_secret,
            config.client_secret,
        ):
            raise HTTPException(status_code=401, detail="invalid_client")
        payload = self._decode_token(code, config.client_secret, expected_type="authorization_code")
        if payload.get("client_id") != config.client_id or payload.get("redirect_uri") != redirect_uri:
            raise HTTPException(status_code=400, detail="invalid_grant")
        code_id = str(payload.get("jti") or "")
        if not code_id:
            raise HTTPException(status_code=400, detail="invalid_grant")
        with self._lock:
            now = int(time.time())
            self._used_authorization_codes = {
                used_code_id: expires_at
                for used_code_id, expires_at in self._used_authorization_codes.items()
                if expires_at >= now
            }
            if code_id in self._used_authorization_codes:
                raise HTTPException(status_code=400, detail="invalid_grant")
            self._used_authorization_codes[code_id] = int(payload["exp"])
        access_token = self._encode_token(
            {
                "typ": "access_token",
                "client_id": config.client_id,
                "sub": payload["sub"],
                "email": payload["email"],
                "name": payload["name"],
                "avatar": payload["avatar"],
                "exp": int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
                "jti": secrets.token_urlsafe(18),
            },
            config.client_secret,
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        }

    def userinfo(self, access_token: str) -> dict[str, object]:
        config = self._configured()
        payload = self._decode_token(
            access_token,
            config.client_secret,
            expected_type="access_token",
        )
        if payload.get("client_id") != config.client_id:
            raise HTTPException(status_code=401, detail="invalid_token")
        subject = str(payload["sub"])
        return {
            "id": subject,
            "sub": subject,
            "name": str(payload.get("name") or "OpenClass learner"),
            "username": subject[:30],
            "email": str(payload.get("email") or ""),
            "email_verified": False,
            "avatar_url": str(payload.get("avatar") or ""),
        }

    def _configured(self) -> CommunityOAuthConfig:
        config = self.config()
        if not config.configured:
            raise HTTPException(status_code=503, detail="社区单点登录尚未配置")
        return config

    def _encode_token(self, payload: dict[str, object], secret: str) -> str:
        encoded_payload = _base64url_encode(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded_payload}.{_base64url_encode(signature)}"

    def _decode_token(
        self,
        token: str,
        secret: str,
        *,
        expected_type: str,
    ) -> dict[str, object]:
        encoded_payload, separator, encoded_signature = token.partition(".")
        if not separator or not encoded_payload or not encoded_signature:
            raise HTTPException(status_code=401, detail="invalid_token")
        try:
            expected_signature = hmac.new(
                secret.encode("utf-8"),
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        except UnicodeEncodeError as exc:
            raise HTTPException(status_code=401, detail="invalid_token") from exc
        supplied_signature = _base64url_decode(encoded_signature)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise HTTPException(status_code=401, detail="invalid_token")
        try:
            payload = json.loads(_base64url_decode(encoded_payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=401, detail="invalid_token") from exc
        if not isinstance(payload, dict) or payload.get("typ") != expected_type:
            raise HTTPException(status_code=401, detail="invalid_token")
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or expires_at < int(time.time()):
            raise HTTPException(status_code=401, detail="invalid_token")
        return payload


community_oauth_service = CommunityOAuthService()

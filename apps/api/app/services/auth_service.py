from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import re
import secrets
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import parse, request as urlrequest

import certifi
from fastapi import HTTPException, Request, WebSocket

from app.models import AdminOverview, AdminStats, AuthIdentityView, AuthProviderView, UserView, new_id
from app.services.auth_store import AuthStore
from app.services.email_sender import send_email_code


PBKDF2_ITERATIONS = 210_000
SESSION_TOKEN_BYTES = 32
OAUTH_STATE_BYTES = 24
OAUTH_STATE_TTL = timedelta(minutes=15)
EMAIL_CODE_TTL = timedelta(minutes=10)
EMAIL_CODE_RESEND_INTERVAL = timedelta(seconds=60)
EMAIL_CODE_MAX_ATTEMPTS = 5
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
AUTH_COOKIE_NAME = "openclass.auth.token"
PHONE_EMAIL_DOMAIN = "phone.openclass.local"
GUEST_EMAIL_DOMAIN = "guest.openclass.local"


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    phone: str | None
    role: str
    created_at: str
    display_name: str | None = None
    avatar_url: str | None = None
    last_login_at: str | None = None


@dataclass(frozen=True)
class AccountIdentifier:
    kind: str
    subject: str
    email: str | None = None
    phone: str | None = None

    @property
    def storage_email(self) -> str:
        if self.email:
            return self.email
        if self.phone:
            return _synthetic_email_for_phone(self.phone)
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号")

    @property
    def display_name(self) -> str:
        if self.email:
            return self.email.split("@", 1)[0]
        if self.phone:
            return _mask_phone(self.phone)
        return "开放课堂用户"


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str | None
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class OAuthProviderConfig:
    id: str
    label: str
    description: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_env: str
    client_secret_env: str
    userinfo_url: str | None = None
    email_url: str | None = None
    response_mode: str | None = None
    pkce: bool = False
    token_auth_method: str = "body"

    @property
    def client_id(self) -> str | None:
        return _normalize_optional_secret(os.getenv(self.client_id_env))

    @property
    def client_secret(self) -> str | None:
        return _normalize_optional_secret(os.getenv(self.client_secret_env))

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower().startswith("your_") or normalized.lower() in {"changeme", "todo"}:
        return None
    return normalized


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise HTTPException(status_code=422, detail="请输入有效邮箱")
    return normalized


def _normalize_phone(phone: str) -> str:
    compact = re.sub(r"[\s().-]", "", phone.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("0086"):
        compact = compact[4:]
    if not re.fullmatch(r"1[3-9]\d{9}", compact):
        raise HTTPException(status_code=422, detail="请输入有效手机号")
    return compact


def _normalize_account_identifier(identifier: str) -> AccountIdentifier:
    raw = identifier.strip()
    if not raw:
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号")
    if "@" in raw:
        email = _normalize_email(raw)
        return AccountIdentifier(kind="email", subject=email, email=email)
    try:
        phone = _normalize_phone(raw)
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号") from exc
    return AccountIdentifier(kind="phone", subject=phone, phone=phone)


def _synthetic_email_for_phone(phone: str) -> str:
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]
    return f"phone-{digest}@{PHONE_EMAIL_DOMAIN}"


def _synthetic_email_for_guest(guest_user_id: str) -> str:
    return f"{guest_user_id}@{GUEST_EMAIL_DOMAIN}"


def _mask_phone(phone: str) -> str:
    if len(phone) == 11:
        return f"{phone[:3]}****{phone[-4:]}"
    if len(phone) <= 4:
        return phone
    return f"{phone[:2]}****{phone[-2:]}"


def _admin_emails() -> set[str]:
    raw = os.getenv("OPENCLASS_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _public_origin(request: Request | None = None) -> str:
    configured = _normalize_optional_secret(os.getenv("OPENCLASS_PUBLIC_ORIGIN") or os.getenv("OPENCLASS_WEB_ORIGIN"))
    if configured:
        return configured.rstrip("/")
    if request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if forwarded_proto and forwarded_host:
            return f"{forwarded_proto.split(',')[0].strip()}://{forwarded_host.split(',')[0].strip()}".rstrip("/")
        return str(request.base_url).rstrip("/")
    return "http://localhost:3000"


def _frontend_origin(request: Request | None = None) -> str:
    configured = _normalize_optional_secret(os.getenv("OPENCLASS_WEB_ORIGIN") or os.getenv("OPENCLASS_PUBLIC_ORIGIN"))
    if configured:
        return configured.rstrip("/")
    if request is not None:
        origin = request.headers.get("origin")
        if origin:
            return origin.rstrip("/")
        referer = request.headers.get("referer")
        if referer:
            parsed = parse.urlparse(referer)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        base = _public_origin(request)
        parsed_base = parse.urlparse(base)
        if parsed_base.hostname in {"localhost", "127.0.0.1"} and parsed_base.port == 8000:
            return f"{parsed_base.scheme}://{parsed_base.hostname}:3000"
        return base
    return "http://localhost:3000"


def _safe_next_path(value: str | None) -> str:
    if not value:
        return "/"
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "/"
    parsed = parse.urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        return "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
    password_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt, PBKDF2_ITERATIONS)
    return password_salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    _, candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, hash_hex)


def _provider_label(provider: str) -> str:
    return {
        "email": "邮箱",
        "phone": "手机号",
        "chatgpt": "ChatGPT",
        "google": "Google",
        "apple": "Apple",
        "github": "GitHub",
        "microsoft": "Microsoft",
        "x": "X",
    }.get(provider, provider)


def _oauth_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _oauth_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _identity_view(row: sqlite3.Row) -> AuthIdentityView:
    return AuthIdentityView(
        provider=row["provider"],
        provider_label=_provider_label(row["provider"]),
        email=row["email"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _user_view(row: sqlite3.Row | AuthUser, identities: list[AuthIdentityView] | None = None) -> UserView:
    return UserView(
        id=row["id"] if isinstance(row, sqlite3.Row) else row.id,
        email=row["email"] if isinstance(row, sqlite3.Row) else row.email,
        phone=(
            row["phone"]
            if isinstance(row, sqlite3.Row) and "phone" in row.keys()
            else (None if isinstance(row, sqlite3.Row) else row.phone)
        ),
        role=row["role"] if isinstance(row, sqlite3.Row) else row.role,  # type: ignore[arg-type]
        display_name=row["display_name"] if isinstance(row, sqlite3.Row) and "display_name" in row.keys() else (None if isinstance(row, sqlite3.Row) else row.display_name),
        avatar_url=row["avatar_url"] if isinstance(row, sqlite3.Row) and "avatar_url" in row.keys() else (None if isinstance(row, sqlite3.Row) else row.avatar_url),
        created_at=row["created_at"] if isinstance(row, sqlite3.Row) else row.created_at,
        last_login_at=row["last_login_at"] if isinstance(row, sqlite3.Row) else row.last_login_at,
        auth_identities=identities or [],
    )


def _oauth_providers() -> dict[str, OAuthProviderConfig]:
    return {
        "google": OAuthProviderConfig(
            id="google",
            label="Google",
            description="使用 Google 账号登录开放课堂。",
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            scopes=("openid", "email", "profile"),
            client_id_env="OPENCLASS_OAUTH_GOOGLE_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_GOOGLE_CLIENT_SECRET",
        ),
        "apple": OAuthProviderConfig(
            id="apple",
            label="Apple",
            description="使用 Apple ID 登录开放课堂。",
            auth_url="https://appleid.apple.com/auth/authorize",
            token_url="https://appleid.apple.com/auth/token",
            scopes=("name", "email"),
            client_id_env="OPENCLASS_OAUTH_APPLE_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_APPLE_CLIENT_SECRET",
            response_mode="form_post",
        ),
        "github": OAuthProviderConfig(
            id="github",
            label="GitHub",
            description="使用 GitHub 账号登录开放课堂。",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            userinfo_url="https://api.github.com/user",
            email_url="https://api.github.com/user/emails",
            scopes=("read:user", "user:email"),
            client_id_env="OPENCLASS_OAUTH_GITHUB_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_GITHUB_CLIENT_SECRET",
        ),
        "microsoft": OAuthProviderConfig(
            id="microsoft",
            label="Microsoft",
            description="使用 Microsoft 账号登录开放课堂。",
            auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            userinfo_url="https://graph.microsoft.com/oidc/userinfo",
            scopes=("openid", "email", "profile"),
            client_id_env="OPENCLASS_OAUTH_MICROSOFT_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_MICROSOFT_CLIENT_SECRET",
        ),
        "x": OAuthProviderConfig(
            id="x",
            label="X",
            description="使用 X 账号登录开放课堂。",
            auth_url="https://x.com/i/oauth2/authorize",
            token_url="https://api.x.com/2/oauth2/token",
            userinfo_url="https://api.x.com/2/users/me?user.fields=profile_image_url",
            scopes=("users.read",),
            client_id_env="OPENCLASS_OAUTH_X_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_X_CLIENT_SECRET",
            pkce=True,
            token_auth_method="basic",
        ),
    }


def _post_form(url: str, data: dict[str, str], *, basic_auth: tuple[str, str] | None = None) -> dict[str, Any]:
    encoded = parse.urlencode(data).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "OpenClass OAuth",
    }
    if basic_auth is not None:
        username, password = basic_auth
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    req = urlrequest.Request(
        url,
        data=encoded,
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=12, context=_URLLIB_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="第三方登录令牌交换失败") from exc


def _get_json(url: str, access_token: str) -> dict[str, Any] | list[Any]:
    req = urlrequest.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "OpenClass OAuth",
        },
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=12, context=_URLLIB_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="第三方账号资料读取失败") from exc


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        _, payload, _ = token.split(".", 2)
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="第三方身份令牌解析失败") from exc


class AuthService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.store = AuthStore(path)

    def register(self, identifier: str, password: str, *, guest_token: str | None = None) -> tuple[str, UserView]:
        account = _normalize_account_identifier(identifier)
        salt, password_hash = _hash_password(password)
        created_at = _now_iso()

        with self.store.transaction() as conn:
            user_count = self.store.user_count(conn)
            role = "admin" if user_count == 0 or (account.email and account.email in _admin_emails()) else "user"
            try:
                user_id = new_id("user")
                self.store.create_password_user(
                    conn,
                    user_id=user_id,
                    email=account.storage_email,
                    phone=account.phone,
                    password_salt=salt,
                    password_hash=password_hash,
                    role=role,
                    display_name=account.display_name,
                    created_at=created_at,
                )
                self.store.create_identity(
                    conn,
                    provider=account.kind,
                    provider_subject=account.subject,
                    user_id=user_id,
                    email=account.email,
                    display_name=account.display_name,
                    created_at=created_at,
                    last_login_at=created_at,
                )
            except sqlite3.IntegrityError as exc:
                message = "该手机号已注册" if account.kind == "phone" else "该邮箱已注册"
                raise HTTPException(status_code=409, detail=message) from exc
            self._claim_guest_workspace(conn, guest_token=guest_token, user_id=user_id)
            return self._issue_session_for_user_id(conn, user_id)

    def register_email(
        self,
        *,
        email: str,
        username: str,
        password: str,
        challenge_id: str,
        code: str,
        guest_token: str | None = None,
    ) -> tuple[str, UserView]:
        account = _normalize_account_identifier(email)
        if account.kind != "email" or not account.email:
            raise HTTPException(status_code=422, detail="请输入有效邮箱")
        display_name = username.strip()
        if len(display_name) < 2 or len(display_name) > 50:
            raise HTTPException(status_code=422, detail="用户名需要 2 到 50 个字符")
        password_salt, password_hash = _hash_password(password)
        now = datetime.now(timezone.utc)

        with self.store.transaction() as conn:
            challenge = self.store.find_email_challenge(conn, challenge_id)
            expires_at = _parse_iso(challenge["expires_at"]) if challenge is not None else None
            is_valid_challenge = (
                challenge is not None
                and challenge["purpose"] == "register"
                and challenge["email"] == account.email
                and challenge["consumed_at"] is None
                and expires_at is not None
                and expires_at > now
                and int(challenge["attempts"]) < EMAIL_CODE_MAX_ATTEMPTS
            )
            if is_valid_challenge:
                candidate_hash = hashlib.sha256(
                    f"{challenge['code_salt']}:{code}".encode("utf-8")
                ).hexdigest()
                if not hmac.compare_digest(candidate_hash, challenge["code_hash"]):
                    self.store.increment_email_challenge_attempts(conn, challenge_id)
                else:
                    if self.store.find_user_by_email(conn, account.email) is not None:
                        raise HTTPException(status_code=409, detail="该邮箱已注册")
                    consumed = self.store.consume_email_challenge(
                        conn,
                        challenge_id=challenge_id,
                        now=now.isoformat(),
                    )
                    if consumed:
                        try:
                            user_id = new_id("user")
                            role = (
                                "admin"
                                if self.store.user_count(conn) == 0 or account.email in _admin_emails()
                                else "user"
                            )
                            self.store.create_password_user(
                                conn,
                                user_id=user_id,
                                email=account.email,
                                phone=None,
                                password_salt=password_salt,
                                password_hash=password_hash,
                                role=role,
                                display_name=display_name,
                                created_at=now.isoformat(),
                            )
                            self.store.create_identity(
                                conn,
                                provider="email",
                                provider_subject=account.email,
                                user_id=user_id,
                                email=account.email,
                                display_name=display_name,
                                created_at=now.isoformat(),
                                last_login_at=now.isoformat(),
                            )
                        except sqlite3.IntegrityError as exc:
                            raise HTTPException(status_code=409, detail="该邮箱已注册") from exc
                        self._claim_guest_workspace(conn, guest_token=guest_token, user_id=user_id)
                        return self._issue_session_for_user_id(conn, user_id)

        raise HTTPException(status_code=401, detail="验证码无效或已过期")

    def login(self, identifier: str, password: str, *, guest_token: str | None = None) -> tuple[str, UserView]:
        account = _normalize_account_identifier(identifier)
        with self.store.transaction() as conn:
            row = (
                self.store.find_user_by_phone(conn, account.phone or "")
                if account.kind == "phone"
                else self.store.find_user_by_email(conn, account.email or "")
            )
            if row is None or not _verify_password(password, row["password_salt"], row["password_hash"]):
                raise HTTPException(status_code=401, detail="账号或密码不正确")
            now = _now_iso()
            self.store.touch_password_login(conn, user_id=row["id"], provider=account.kind, now=now)
            self._claim_guest_workspace(conn, guest_token=guest_token, user_id=row["id"])
            return self._issue_session_for_user_id(conn, row["id"])

    def request_email_code(self, email: str) -> tuple[str, int]:
        account = _normalize_account_identifier(email)
        if account.kind != "email" or not account.email:
            raise HTTPException(status_code=422, detail="请输入有效邮箱")
        with self.store.connection() as conn:
            if self.store.find_user_by_email(conn, account.email) is None:
                raise HTTPException(status_code=404, detail="该邮箱尚未注册，请先注册")
        return self._request_email_code(account.email, purpose="login")

    def request_registration_email_code(self, email: str) -> tuple[str, int]:
        account = _normalize_account_identifier(email)
        if account.kind != "email" or not account.email:
            raise HTTPException(status_code=422, detail="请输入有效邮箱")
        with self.store.connection() as conn:
            if self.store.find_user_by_email(conn, account.email) is not None:
                raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录")
        return self._request_email_code(account.email, purpose="register")

    def _request_email_code(self, email: str, *, purpose: str) -> tuple[str, int]:
        now = datetime.now(timezone.utc)
        challenge_id = new_id("email_challenge")
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_salt = secrets.token_hex(16)
        code_hash = hashlib.sha256(f"{code_salt}:{code}".encode("utf-8")).hexdigest()

        with self.store.transaction() as conn:
            latest = self.store.latest_email_challenge(conn, email=email, purpose=purpose)
            latest_created = _parse_iso(latest["created_at"]) if latest is not None else None
            if latest_created and now - latest_created < EMAIL_CODE_RESEND_INTERVAL:
                raise HTTPException(status_code=429, detail="验证码发送过于频繁，请稍后重试")
            self.store.create_email_challenge(
                conn,
                challenge_id=challenge_id,
                email=email,
                purpose=purpose,
                code_salt=code_salt,
                code_hash=code_hash,
                created_at=now.isoformat(),
                expires_at=(now + EMAIL_CODE_TTL).isoformat(),
            )

        try:
            send_email_code(email=email, code=code, purpose=purpose)
        except Exception:
            with self.store.transaction() as conn:
                self.store.delete_email_challenge(conn, challenge_id)
            raise
        return challenge_id, int(EMAIL_CODE_TTL.total_seconds())

    def verify_email_code(
        self,
        challenge_id: str,
        code: str,
        *,
        guest_token: str | None = None,
    ) -> tuple[str, UserView]:
        now = datetime.now(timezone.utc)
        with self.store.transaction() as conn:
            challenge = self.store.find_email_challenge(conn, challenge_id)
            expires_at = _parse_iso(challenge["expires_at"]) if challenge is not None else None
            if (
                challenge is None
                or challenge["consumed_at"] is not None
                or expires_at is None
                or expires_at <= now
                or int(challenge["attempts"]) >= EMAIL_CODE_MAX_ATTEMPTS
                or challenge["purpose"] != "login"
            ):
                raise HTTPException(status_code=401, detail="验证码无效或已过期")

            candidate_hash = hashlib.sha256(
                f"{challenge['code_salt']}:{code}".encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(candidate_hash, challenge["code_hash"]):
                self.store.increment_email_challenge_attempts(conn, challenge_id)
            else:
                consumed = self.store.consume_email_challenge(
                    conn,
                    challenge_id=challenge_id,
                    now=now.isoformat(),
                )
                if not consumed:
                    raise HTTPException(status_code=401, detail="验证码无效或已过期")

                email = str(challenge["email"])
                row = self.store.find_user_by_email(conn, email)
                if row is None:
                    raise HTTPException(status_code=404, detail="该邮箱尚未注册，请先注册")
                user_id = str(row["id"])
                self.store.touch_password_login(
                    conn,
                    user_id=user_id,
                    provider="email",
                    now=now.isoformat(),
                )
                self._claim_guest_workspace(conn, guest_token=guest_token, user_id=user_id)
                return self._issue_session_for_user_id(conn, user_id)

        raise HTTPException(status_code=401, detail="验证码无效或已过期")

    def login_with_oauth(
        self,
        profile: OAuthProfile,
        *,
        guest_user_id: str | None = None,
        guest_session_token: str | None = None,
        before_claim: Callable[[str], None] | None = None,
    ) -> tuple[str, UserView]:
        if not profile.provider or not profile.subject:
            raise HTTPException(status_code=422, detail="第三方账号资料不完整")
        provider = profile.provider.strip().lower()
        subject = profile.subject.strip()
        normalized_email = self._email_for_oauth_profile(profile)
        now = _now_iso()

        with self.store.transaction() as conn:
            session_token_hash = self._token_hash(guest_session_token) if guest_session_token else None
            guest_session = (
                self.store.find_guest_session_by_token(conn, session_token_hash)
                if session_token_hash
                else None
            )
            formal_session = (
                self.store.find_user_by_session_token(conn, session_token_hash)
                if session_token_hash
                else None
            )
            if guest_session_token and guest_session is None and formal_session is None:
                raise HTTPException(status_code=401, detail="游客会话已失效，请重新登录")
            if guest_session is not None:
                token_guest_user_id = str(guest_session["guest_user_id"])
                if guest_user_id and token_guest_user_id != guest_user_id:
                    raise HTTPException(status_code=401, detail="游客会话与当前账户不匹配")
                guest_user_id = token_guest_user_id

            identity = self.store.find_user_by_oauth_identity(conn, provider=provider, provider_subject=subject)
            if identity is not None:
                user_id = identity["id"]
            else:
                existing = self.store.find_user_by_email(conn, normalized_email)
                if existing is not None:
                    user_id = existing["id"]
                else:
                    # Keeping a first-time guest upgrade on the same principal preserves
                    # workspace ownership and provider-scoped runtime state keyed by user id.
                    user_id = guest_user_id or new_id("user")
                    password_salt, password_hash = _hash_password(secrets.token_urlsafe(32))
                    user_count = self.store.user_count(conn)
                    role = "admin" if user_count == 0 or normalized_email in _admin_emails() else "user"
                    self.store.create_oauth_user(
                        conn,
                        user_id=user_id,
                        email=normalized_email,
                        password_salt=password_salt,
                        password_hash=password_hash,
                        role=role,
                        display_name=profile.display_name or normalized_email.split("@", 1)[0],
                        avatar_url=profile.avatar_url,
                        now=now,
                    )
                self.store.create_identity(
                    conn,
                    provider=provider,
                    provider_subject=subject,
                    user_id=user_id,
                    email=profile.email,
                    display_name=profile.display_name,
                    avatar_url=profile.avatar_url,
                    created_at=now,
                    last_login_at=now,
                )

            if formal_session is not None and str(formal_session["id"]) != str(user_id):
                raise HTTPException(status_code=409, detail="当前会话已经属于另一个账户")
            if before_claim is not None:
                before_claim(str(user_id))

            self.store.touch_oauth_identity(
                conn,
                provider=provider,
                provider_subject=subject,
                email=profile.email,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
                now=now,
            )
            self.store.touch_oauth_user_profile(
                conn,
                user_id=user_id,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
                now=now,
            )
            if guest_session_token and session_token_hash:
                if formal_session is None:
                    self.store.create_session(
                        conn,
                        token_hash=session_token_hash,
                        user_id=user_id,
                        now=now,
                    )
                else:
                    self.store.touch_session(conn, session_token_hash, now)
                self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)
                refreshed = self.store.find_user_by_id(conn, user_id)
                if refreshed is None:
                    raise HTTPException(status_code=404, detail="用户不存在")
                return guest_session_token, _user_view(
                    refreshed,
                    self._identities_for_user(conn, user_id),
                )
            self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)
            return self._issue_session_for_user_id(conn, user_id)

    def normalize_oauth_email(self, email: str) -> str:
        return _normalize_email(email)

    def get_user_by_token(self, token: str) -> UserView:
        token_hash = self._token_hash(token)
        with self.store.transaction() as conn:
            row = self.store.find_user_by_session_token(conn, token_hash)
            if row is None:
                guest = self.store.find_guest_session_by_token(conn, token_hash)
                if guest is None:
                    raise HTTPException(status_code=401, detail="请先登录")
                self.store.touch_guest_session(conn, token_hash, _now_iso())
                return self._guest_user_view(guest["guest_user_id"], guest["created_at"], guest["last_seen_at"])
            self.store.touch_session(conn, token_hash, _now_iso())
            return _user_view(row, self._identities_for_user(conn, row["id"]))

    def start_guest_session(self) -> tuple[str, UserView]:
        now = _now_iso()
        with self.store.transaction() as conn:
            for _attempt in range(10):
                guest_user_id = new_id("guest")
                token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
                token_hash = self._token_hash(token)
                if self.store.principal_id_exists(conn, guest_user_id):
                    continue
                if self.store.find_guest_session_by_token(conn, token_hash) is not None:
                    continue
                if self.store.find_user_by_session_token(conn, token_hash) is not None:
                    continue
                self.store.create_guest_session(
                    conn,
                    token_hash=token_hash,
                    guest_user_id=guest_user_id,
                    now=now,
                )
                break
            else:
                raise HTTPException(status_code=503, detail="暂时无法创建游客会话，请稍后重试")
        return token, self._guest_user_view(guest_user_id, now, now)

    def overview(self) -> AdminOverview:
        with self.store.connection() as conn:
            users = [
                _user_view(row, self._identities_for_user(conn, row["id"]))
                for row in self.store.list_users(conn)
            ]
            stats_row = self.store.admin_stats(conn)
        return AdminOverview(
            stats=AdminStats(
                users=stats_row["users"],
                admins=stats_row["admins"],
                packages=stats_row["packages"],
                lessons=stats_row["lessons"],
                resources=stats_row["resources"],
            ),
            users=users,
        )

    def providers(self) -> list[AuthProviderView]:
        options = [
            AuthProviderView(
                id="email",
                label="邮箱/手机号",
                description="使用已验证邮箱注册，或使用邮箱/手机号登录。",
                configured=True,
                kind="password",
            )
        ]
        for provider in _oauth_providers().values():
            options.append(
                AuthProviderView(
                    id=provider.id,
                    label=provider.label,
                    description=provider.description,
                    configured=provider.configured,
                    kind="oauth",
                )
            )
        from app.services.codex_app_server import (
            chatgpt_platform_login_enabled,
            codex_app_server_available,
            codex_app_server_runtime_enabled,
        )

        if chatgpt_platform_login_enabled():
            options.append(
                AuthProviderView(
                    id="chatgpt",
                    label="ChatGPT",
                    description="使用 ChatGPT 账号登录并保存 OpenClass 学习记录。",
                    configured=codex_app_server_runtime_enabled() and codex_app_server_available(),
                    kind="device",
                )
            )
        return options

    def oauth_authorization_url(
        self,
        provider_id: str,
        next_path: str,
        request: Request,
        *,
        guest_token: str | None = None,
    ) -> str:
        provider = self._configured_provider(provider_id)
        state = secrets.token_urlsafe(OAUTH_STATE_BYTES)
        redirect_uri = self._oauth_redirect_uri(provider.id, request)
        guest_user_id = self._guest_user_id_for_token(guest_token)
        code_verifier = _oauth_code_verifier() if provider.pkce else None
        params = {
            "client_id": provider.client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(provider.scopes),
            "state": state,
        }
        if provider.id == "google":
            params["prompt"] = "select_account"
        if provider.pkce and code_verifier:
            params["code_challenge"] = _oauth_code_challenge(code_verifier)
            params["code_challenge_method"] = "S256"
        if provider.response_mode:
            params["response_mode"] = provider.response_mode

        with self.store.transaction() as conn:
            self.store.create_oauth_state(
                conn,
                state=state,
                provider=provider.id,
                next_path=_safe_next_path(next_path),
                frontend_origin=_frontend_origin(request),
                guest_user_id=guest_user_id,
                code_verifier=code_verifier,
                created_at=_now_iso(),
            )
        return f"{provider.auth_url}?{parse.urlencode(params)}"

    def complete_oauth_callback(self, provider_id: str, payload: dict[str, str], request: Request) -> tuple[str, UserView, str, str]:
        provider = self._configured_provider(provider_id)
        state = payload.get("state", "")
        code = payload.get("code", "")
        if not state or not code:
            raise HTTPException(status_code=400, detail="第三方登录回调缺少授权码")
        next_path, frontend_origin, guest_user_id, code_verifier = self._consume_oauth_state(provider.id, state)
        token_payload = self._exchange_oauth_code(provider, code, request, code_verifier=code_verifier)
        profile = self._profile_from_oauth(provider, token_payload)
        token, user = self.login_with_oauth(profile, guest_user_id=guest_user_id)
        return token, user, next_path, frontend_origin

    def _exchange_oauth_code(
        self,
        provider: OAuthProviderConfig,
        code: str,
        request: Request,
        *,
        code_verifier: str | None = None,
    ) -> dict[str, Any]:
        token_data = {
            "client_id": provider.client_id or "",
            "client_secret": provider.client_secret or "",
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._oauth_redirect_uri(provider.id, request),
        }
        basic_auth = None
        if provider.pkce:
            if not code_verifier:
                raise HTTPException(status_code=400, detail="第三方登录状态已失效，请重新发起登录")
            token_data["code_verifier"] = code_verifier
        if provider.token_auth_method == "basic":
            basic_auth = (provider.client_id or "", provider.client_secret or "")
            token_data.pop("client_secret", None)
        return _post_form(
            provider.token_url,
            token_data,
            basic_auth=basic_auth,
        )

    def oauth_frontend_redirect_url(self, token: str, user: UserView, next_path: str, frontend_origin: str, request: Request) -> str:
        target = parse.urljoin((frontend_origin or _frontend_origin(request)).rstrip("/"), "/auth/callback")
        query = parse.urlencode(
            {
                "token": token,
                "user_id": user.id,
                "next": _safe_next_path(next_path),
            }
        )
        return f"{target}?{query}"

    def _issue_session_for_user_id(self, conn: sqlite3.Connection, user_id: str) -> tuple[str, UserView]:
        row = self.store.find_user_by_id(conn, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        self.store.create_session(conn, token_hash=self._token_hash(token), user_id=row["id"], now=now)
        refreshed = self.store.find_user_by_id(conn, row["id"])
        return token, _user_view(refreshed, self._identities_for_user(conn, row["id"]))

    def _guest_user_view(self, guest_user_id: str, created_at: str, last_seen_at: str | None = None) -> UserView:
        return UserView(
            id=guest_user_id,
            email=_synthetic_email_for_guest(guest_user_id),
            role="guest",
            display_name="游客",
            created_at=created_at,
            last_login_at=last_seen_at,
            auth_identities=[],
        )

    def _guest_user_id_for_token(self, guest_token: str | None) -> str | None:
        if not guest_token:
            return None
        token_hash = self._token_hash(guest_token)
        with self.store.connection() as conn:
            return self.store.guest_user_id_for_token_hash(conn, token_hash)

    def _claim_guest_workspace(
        self,
        conn: sqlite3.Connection,
        *,
        guest_token: str | None,
        user_id: str,
    ) -> None:
        if not guest_token:
            return
        token_hash = self._token_hash(guest_token)
        guest_user_id = self.store.guest_user_id_for_token_hash(conn, token_hash)
        if guest_user_id is None:
            return
        self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)

    def _claim_guest_workspace_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        guest_user_id: str | None,
        user_id: str,
    ) -> None:
        if not guest_user_id:
            return
        if guest_user_id == user_id:
            self.store.delete_guest_sessions(conn, guest_user_id=guest_user_id)
            return
        self.store.claim_guest_workspace(conn, guest_user_id=guest_user_id, user_id=user_id)

    def _identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[AuthIdentityView]:
        rows = self.store.identities_for_user(conn, user_id)
        return [_identity_view(row) for row in rows]

    def _email_for_oauth_profile(self, profile: OAuthProfile) -> str:
        if profile.email:
            try:
                return _normalize_email(profile.email)
            except HTTPException:
                pass
        safe_subject = "".join(char if char.isalnum() else "-" for char in profile.subject.lower()).strip("-")
        return f"{profile.provider}-{safe_subject or secrets.token_hex(6)}@oauth.openclass.local"

    def _configured_provider(self, provider_id: str) -> OAuthProviderConfig:
        provider = _oauth_providers().get(provider_id.strip().lower())
        if provider is None:
            raise HTTPException(status_code=404, detail="暂不支持该第三方登录方式")
        if not provider.configured:
            raise HTTPException(status_code=503, detail=f"{provider.label} 登录尚未配置 OAuth Client")
        return provider

    def _oauth_redirect_uri(self, provider_id: str, request: Request) -> str:
        return parse.urljoin(_public_origin(request), f"/api/auth/oauth/{provider_id}/callback")

    def _consume_oauth_state(self, provider_id: str, state: str) -> tuple[str, str, str | None, str | None]:
        with self.store.transaction() as conn:
            row = self.store.consume_oauth_state(conn, provider=provider_id, state=state)
            if row is None:
                raise HTTPException(status_code=400, detail="第三方登录状态已失效，请重新发起登录")
            created_at = _parse_iso(row["created_at"])
            if created_at is None or datetime.now(timezone.utc) - created_at > OAUTH_STATE_TTL:
                raise HTTPException(status_code=400, detail="第三方登录状态已过期，请重新发起登录")
            return (
                _safe_next_path(row["next_path"]),
                row["frontend_origin"] or _frontend_origin(None),
                row["guest_user_id"],
                row["code_verifier"],
            )

    def _profile_from_oauth(self, provider: OAuthProviderConfig, token_payload: dict[str, Any]) -> OAuthProfile:
        access_token = str(token_payload.get("access_token") or "")
        id_token = str(token_payload.get("id_token") or "")
        if provider.id == "apple":
            claims = _decode_jwt_payload(id_token)
            return OAuthProfile(
                provider=provider.id,
                subject=str(claims.get("sub") or ""),
                email=str(claims.get("email") or "") or None,
                display_name=None,
                avatar_url=None,
            )
        if not access_token:
            raise HTTPException(status_code=502, detail="第三方登录没有返回访问令牌")
        if provider.id == "github":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                raise HTTPException(status_code=502, detail="GitHub 账号资料格式异常")
            email = raw_profile.get("email")
            if not email and provider.email_url:
                raw_emails = _get_json(provider.email_url, access_token)
                if isinstance(raw_emails, list):
                    for item in raw_emails:
                        if isinstance(item, dict) and item.get("primary") and item.get("verified") and item.get("email"):
                            email = item["email"]
                            break
            return OAuthProfile(
                provider=provider.id,
                subject=str(raw_profile.get("id") or ""),
                email=str(email or "") or None,
                display_name=str(raw_profile.get("name") or raw_profile.get("login") or "") or None,
                avatar_url=str(raw_profile.get("avatar_url") or "") or None,
            )
        if provider.id == "x":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                raise HTTPException(status_code=502, detail="X 账号资料格式异常")
            data = raw_profile.get("data")
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="X 账号资料格式异常")
            return OAuthProfile(
                provider=provider.id,
                subject=str(data.get("id") or ""),
                email=str(data.get("email") or "") or None,
                display_name=str(data.get("name") or data.get("username") or "") or None,
                avatar_url=str(data.get("profile_image_url") or "") or None,
            )
        raw_profile = _get_json(provider.userinfo_url or "", access_token)
        if not isinstance(raw_profile, dict):
            raise HTTPException(status_code=502, detail="第三方账号资料格式异常")
        return OAuthProfile(
            provider=provider.id,
            subject=str(raw_profile.get("sub") or raw_profile.get("id") or ""),
            email=str(raw_profile.get("email") or raw_profile.get("userPrincipalName") or "") or None,
            display_name=str(raw_profile.get("name") or raw_profile.get("preferred_username") or "") or None,
            avatar_url=str(raw_profile.get("picture") or "") or None,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer_token_from_parts(
    authorization: str,
    *,
    cookie_token: str | None = None,
    query_token: str | None = None,
) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    if query_token:
        return query_token.strip()
    if cookie_token:
        return cookie_token.strip()
    raise HTTPException(status_code=401, detail="请先登录")


def bearer_token_from_request(request: Request) -> str:
    return _bearer_token_from_parts(
        request.headers.get("Authorization", ""),
        cookie_token=request.cookies.get(AUTH_COOKIE_NAME),
        query_token=request.query_params.get("access_token"),
    )


def bearer_token_from_websocket(websocket: WebSocket) -> str:
    return _bearer_token_from_parts(
        websocket.headers.get("Authorization", ""),
        cookie_token=websocket.cookies.get(AUTH_COOKIE_NAME),
        query_token=websocket.query_params.get("access_token"),
    )

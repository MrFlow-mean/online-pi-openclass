from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.models import AuthSessionResponse, CodexAccountView, CodexLoginStartResponse, CodexLoginStatusResponse, CodexProviderStatus, UserView
from app.routers.auth import auth_service, current_user
from app.services.auth_service import (
    AUTH_COOKIE_NAME,
    GUEST_AUTH_COOKIE_NAME,
    OAuthProfile,
    auth_cookie_max_age,
    auth_cookie_secure,
    bearer_token_from_request,
)
from app.services.codex_app_server import (
    CodexAppServerError,
    CodexLoginRateLimitError,
    cancel_codex_login,
    chatgpt_platform_login_enabled,
    claim_completed_codex_platform_login,
    complete_codex_platform_login_claim,
    copy_codex_auth,
    codex_login_status,
    codex_provider_status,
    logout_codex,
    remove_codex_auth,
    release_codex_platform_login_claim,
    start_codex_device_login,
)

router = APIRouter(prefix="/api/codex")


def _rotate_platform_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=auth_cookie_max_age(),
        httponly=True,
        secure=auth_cookie_secure(request),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        GUEST_AUTH_COOKIE_NAME,
        secure=auth_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _codex_error(exc: Exception) -> HTTPException:
    status_code = 429 if isinstance(exc, CodexLoginRateLimitError) else 400
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/status", response_model=CodexProviderStatus)
def status(user: UserView = Depends(current_user), include_rate_limits: bool = False) -> CodexProviderStatus:
    return codex_provider_status(
        user.id,
        refresh=False,
        include_rate_limits=include_rate_limits,
    )


@router.post("/login/device", response_model=CodexLoginStartResponse)
def login_device(user: UserView = Depends(current_user)) -> CodexLoginStartResponse:
    try:
        return start_codex_device_login(user.id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.post("/platform-login/device", response_model=CodexLoginStartResponse)
def platform_login_device(user: UserView = Depends(current_user)) -> CodexLoginStartResponse:
    if not chatgpt_platform_login_enabled():
        raise HTTPException(status_code=403, detail="ChatGPT 平台登录未启用")
    if user.role != "guest":
        raise HTTPException(status_code=409, detail="当前已经是 OpenClass 正式账户")
    try:
        return start_codex_device_login(user.id, purpose="platform")
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


def _completed_chatgpt_account(login_id: str | None, user_id: str) -> CodexAccountView:
    if login_id:
        account = claim_completed_codex_platform_login(login_id, user_id)
    else:
        provider = codex_provider_status(user_id, refresh=True)
        account = provider.account if provider.configured else None
    if account is None or account.type != "chatgpt":
        raise HTTPException(status_code=409, detail="ChatGPT 登录尚未完成")
    if not (account.email or "").strip():
        raise HTTPException(status_code=422, detail="ChatGPT 账号没有返回可用于平台登录的邮箱")
    return account


@router.post("/login/complete", response_model=AuthSessionResponse)
def complete_platform_login(
    request: Request,
    response: Response,
    login_id: str | None = None,
    user: UserView = Depends(current_user),
) -> AuthSessionResponse:
    if not chatgpt_platform_login_enabled():
        raise HTTPException(status_code=403, detail="ChatGPT 平台登录未启用")
    session_token = bearer_token_from_request(request)
    if user.role != "guest":
        if any(identity.provider == "chatgpt" for identity in user.auth_identities):
            _rotate_platform_cookie(response, request, session_token)
            return AuthSessionResponse(user=user)
        raise HTTPException(status_code=409, detail="当前正式账户尚未关联 ChatGPT 身份")
    claimed_login = bool(login_id)
    try:
        account = _completed_chatgpt_account(login_id, user.id)
        email = auth_service.normalize_oauth_email(account.email or "")
        profile = OAuthProfile(
            provider="chatgpt",
            # The official account/read surface currently exposes email but no immutable id.
            # This local/trusted adapter is therefore guarded by an explicit opt-in flag.
            subject=f"email:{email}",
            email=email,
            display_name=email.split("@", 1)[0],
        )

        def copy_credential_for_target(target_user_id: str) -> None:
            if target_user_id != user.id:
                copy_codex_auth(user.id, target_user_id)

        token, platform_user = auth_service.login_with_oauth(
            profile,
            guest_user_id=user.id,
            guest_session_token=session_token,
            before_claim=copy_credential_for_target,
        )
        if login_id:
            complete_codex_platform_login_claim(login_id, user.id)
        if platform_user.id != user.id:
            remove_codex_auth(user.id)
        _rotate_platform_cookie(response, request, token)
        return AuthSessionResponse(user=platform_user)
    except CodexAppServerError as exc:
        if claimed_login and login_id:
            release_codex_platform_login_claim(login_id, user.id)
        raise _codex_error(exc) from exc
    except Exception:
        if claimed_login and login_id:
            release_codex_platform_login_claim(login_id, user.id)
        raise


@router.get("/login/{login_id}", response_model=CodexLoginStatusResponse)
def login_status(login_id: str, user: UserView = Depends(current_user)) -> CodexLoginStatusResponse:
    try:
        return codex_login_status(login_id, user.id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.post("/login/{login_id}/cancel", response_model=CodexLoginStatusResponse)
def login_cancel(login_id: str, user: UserView = Depends(current_user)) -> CodexLoginStatusResponse:
    try:
        return cancel_codex_login(login_id, user.id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.post("/logout")
def logout(user: UserView = Depends(current_user)) -> dict[str, bool]:
    try:
        logout_codex(user.id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc
    return {"ok": True}

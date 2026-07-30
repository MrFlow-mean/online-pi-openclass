from __future__ import annotations

import base64
from urllib import parse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import RedirectResponse

from app.models import (
    AdminOverview,
    AccountDataExport,
    AccountDeleteRequest,
    AuthMessageResponse,
    AuthProviderView,
    AuthRequest,
    AuthSessionResponse,
    EmailCodeRequest,
    EmailCodeRequestResponse,
    EmailRegistrationRequest,
    EmailCodeVerifyRequest,
    HumanVerificationRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    UserView,
)
from app.services.auth_service import (
    AUTH_COOKIE_NAME,
    GUEST_AUTH_COOKIE_NAME,
    AuthService,
    auth_cookie_max_age,
    auth_cookie_secure,
    bearer_token_from_request,
    bearer_token_from_websocket,
)
from app.services.community_oauth import community_oauth_service
from app.services.human_verification import require_turnstile_verification
from app.services.rate_limiter import enforce_auth_rate_limit
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api")
auth_service = AuthService(DATABASE_PATH)


def _set_auth_cookie(response: Response, request: Request, token: str) -> None:
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
        httponly=False,
        secure=auth_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _clear_auth_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        httponly=True,
        secure=auth_cookie_secure(request),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        GUEST_AUTH_COOKIE_NAME,
        httponly=False,
        secure=auth_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def current_user(request: Request) -> UserView:
    token = bearer_token_from_request(request)
    return auth_service.get_user_by_token(token)


def optional_current_user(request: Request) -> UserView | None:
    try:
        token = bearer_token_from_request(request)
        return auth_service.get_user_by_token(token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


def current_websocket_user(websocket: WebSocket) -> UserView:
    token = bearer_token_from_websocket(websocket)
    return auth_service.get_user_by_token(token)


def current_admin(user: UserView = Depends(current_user)) -> UserView:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.post("/auth/register", response_model=AuthSessionResponse)
async def register(payload: EmailRegistrationRequest, request: Request, response: Response) -> AuthSessionResponse:
    enforce_auth_rate_limit("register", request, account_identifier=payload.email)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="register",
    )
    token, user = auth_service.register_email(
        email=payload.email,
        username=payload.username,
        password=payload.password,
        challenge_id=payload.challenge_id,
        code=payload.code,
        guest_token=payload.guest_token,
    )
    _set_auth_cookie(response, request, token)
    return AuthSessionResponse(user=user)


@router.post("/auth/login", response_model=AuthSessionResponse)
async def login(payload: AuthRequest, request: Request, response: Response) -> AuthSessionResponse:
    enforce_auth_rate_limit("login", request, account_identifier=payload.account_identifier())
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="login",
    )
    token, user = auth_service.login(payload.account_identifier(), payload.password, guest_token=payload.guest_token)
    _set_auth_cookie(response, request, token)
    return AuthSessionResponse(user=user)


@router.post("/auth/email/code", response_model=EmailCodeRequestResponse)
async def request_email_code(payload: EmailCodeRequest, request: Request) -> EmailCodeRequestResponse:
    enforce_auth_rate_limit("login", request, account_identifier=payload.email)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="email_code_login",
    )
    challenge_id, expires_in_seconds = auth_service.request_email_code(payload.email)
    return EmailCodeRequestResponse(
        challenge_id=challenge_id,
        expires_in_seconds=expires_in_seconds,
        message="验证码已发送，请检查邮箱",
    )


@router.post("/auth/register/email/code", response_model=EmailCodeRequestResponse)
async def request_registration_email_code(payload: EmailCodeRequest, request: Request) -> EmailCodeRequestResponse:
    enforce_auth_rate_limit("register", request, account_identifier=payload.email)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="register",
    )
    challenge_id, expires_in_seconds = auth_service.request_registration_email_code(payload.email)
    return EmailCodeRequestResponse(
        challenge_id=challenge_id,
        expires_in_seconds=expires_in_seconds,
        message="验证码已发送，请检查邮箱",
    )


@router.post("/auth/email/verify", response_model=AuthSessionResponse)
async def verify_email_code(payload: EmailCodeVerifyRequest, request: Request, response: Response) -> AuthSessionResponse:
    enforce_auth_rate_limit("login", request, account_identifier=payload.challenge_id)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="email_code_login",
    )
    token, user = auth_service.verify_email_code(
        payload.challenge_id,
        payload.code,
        guest_token=payload.guest_token,
    )
    _set_auth_cookie(response, request, token)
    return AuthSessionResponse(user=user)


@router.post("/auth/guest", response_model=AuthSessionResponse)
def guest(request: Request) -> AuthSessionResponse:
    enforce_auth_rate_limit("login", request)
    token, user = auth_service.start_guest_session()
    return AuthSessionResponse(token=token, user=user)


@router.post("/auth/password/forgot", response_model=EmailCodeRequestResponse)
async def forgot_password(payload: EmailCodeRequest, request: Request) -> EmailCodeRequestResponse:
    enforce_auth_rate_limit("password_recovery", request, account_identifier=payload.email)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="password_forgot",
    )
    challenge_id, expires_in_seconds = auth_service.request_password_reset(payload.email)
    return EmailCodeRequestResponse(
        challenge_id=challenge_id,
        expires_in_seconds=expires_in_seconds,
        message="如果该邮箱已注册，重置验证码已发送",
    )


@router.post("/auth/password/reset", response_model=AuthMessageResponse)
async def reset_password(payload: PasswordResetRequest, request: Request) -> AuthMessageResponse:
    enforce_auth_rate_limit("password_recovery", request, account_identifier=payload.challenge_id)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="password_reset",
    )
    auth_service.reset_password(payload.challenge_id, payload.code, payload.password)
    return AuthMessageResponse(message="密码已重置，请重新登录")


@router.post("/auth/email/verification/request", response_model=EmailCodeRequestResponse)
async def request_email_verification(
    payload: HumanVerificationRequest,
    request: Request,
    user: UserView = Depends(current_user),
) -> EmailCodeRequestResponse:
    enforce_auth_rate_limit("password_recovery", request, account_identifier=user.id)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="email_verification_request",
    )
    challenge_id, expires_in_seconds = auth_service.request_email_verification(user.id)
    return EmailCodeRequestResponse(
        challenge_id=challenge_id,
        expires_in_seconds=expires_in_seconds,
        message="邮箱验证码已发送",
    )


@router.post("/auth/email/verification/confirm", response_model=UserView)
async def confirm_email_verification(
    payload: EmailCodeVerifyRequest,
    request: Request,
    user: UserView = Depends(current_user),
) -> UserView:
    enforce_auth_rate_limit("password_recovery", request, account_identifier=user.id)
    await require_turnstile_verification(
        request,
        token=payload.turnstile_token,
        expected_action="email_verification_confirm",
    )
    return auth_service.confirm_email_verification(user.id, payload.challenge_id, payload.code)


@router.post("/auth/password/change", response_model=AuthSessionResponse)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: UserView = Depends(current_user),
) -> AuthSessionResponse:
    current_token = bearer_token_from_request(request)
    token, refreshed_user = auth_service.change_password(
        user.id,
        current_token,
        payload.current_password,
        payload.new_password,
    )
    _set_auth_cookie(response, request, token)
    return AuthSessionResponse(user=refreshed_user)


@router.post("/auth/logout", response_model=AuthMessageResponse)
def logout(request: Request, response: Response) -> AuthMessageResponse:
    auth_service.logout(bearer_token_from_request(request))
    _clear_auth_cookie(response, request)
    return AuthMessageResponse(message="已退出登录")


@router.post("/auth/sessions/revoke-all", response_model=AuthMessageResponse)
def revoke_all_sessions(
    request: Request,
    response: Response,
    user: UserView = Depends(current_user),
) -> AuthMessageResponse:
    auth_service.revoke_all_sessions(user.id)
    _clear_auth_cookie(response, request)
    return AuthMessageResponse(message="所有会话已撤销，请重新登录")


@router.get("/auth/export", response_model=AccountDataExport)
@router.get("/auth/data-export", response_model=AccountDataExport)
def export_account_data(user: UserView = Depends(current_user)) -> AccountDataExport:
    return AccountDataExport.model_validate(auth_service.export_account_data(user.id))


@router.delete("/auth/account", response_model=AuthMessageResponse)
def delete_account(
    payload: AccountDeleteRequest,
    request: Request,
    response: Response,
    user: UserView = Depends(current_user),
) -> AuthMessageResponse:
    auth_service.delete_account(user.id, payload.password, payload.confirmation)
    _clear_auth_cookie(response, request)
    return AuthMessageResponse(message="账户已注销")


@router.get("/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)) -> UserView:
    return user


@router.get("/auth/community/avatar/{user_id}")
def community_avatar(user_id: str) -> RedirectResponse:
    return RedirectResponse(
        auth_service.community_avatar_url(user_id),
        status_code=302,
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/auth/community/authorize")
def authorize_community_login(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    state: str = "",
    scope: str = "",
    user: UserView = Depends(current_user),
) -> RedirectResponse:
    del scope
    target = community_oauth_service.authorization_redirect(
        user=user,
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        state=state,
    )
    return RedirectResponse(target, status_code=302)


@router.post("/auth/community/token")
async def exchange_community_authorization_code(request: Request) -> dict[str, object]:
    form = parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    client_id = _form_value(form, "client_id")
    client_secret = _form_value(form, "client_secret")
    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.casefold() == "basic" and credentials:
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            basic_client_id, separator, basic_client_secret = decoded.partition(":")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=401, detail="invalid_client") from exc
        if not separator:
            raise HTTPException(status_code=401, detail="invalid_client")
        client_id = parse.unquote(basic_client_id)
        client_secret = parse.unquote(basic_client_secret)
    return community_oauth_service.exchange_code(
        code=_form_value(form, "code"),
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=_form_value(form, "redirect_uri"),
        grant_type=_form_value(form, "grant_type"),
    )


@router.get("/auth/community/userinfo")
def community_userinfo(request: Request) -> dict[str, object]:
    scheme, _, access_token = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not access_token.strip():
        raise HTTPException(status_code=401, detail="invalid_token")
    return community_oauth_service.userinfo(access_token.strip())


@router.get("/auth/providers", response_model=list[AuthProviderView])
def auth_providers() -> list[AuthProviderView]:
    return auth_service.providers()


def _form_value(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name, [])
    return values[0] if values else ""


@router.get("/auth/oauth/{provider}/start")
def oauth_start(
    provider: str,
    request: Request,
    next: str = "/",  # noqa: A002
) -> RedirectResponse:
    guest_token = request.cookies.get(GUEST_AUTH_COOKIE_NAME)
    return RedirectResponse(
        auth_service.oauth_authorization_url(provider, next, request, guest_token=guest_token),
        status_code=303,
    )


@router.api_route("/auth/oauth/{provider}/callback", methods=["GET", "POST"])
async def oauth_callback(provider: str, request: Request) -> RedirectResponse:
    payload = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        payload.update({key: str(value) for key, value in form.items()})
    if payload.get("error"):
        target = f"{str(request.base_url).rstrip('/')}/auth/callback?{parse.urlencode({'error': payload.get('error_description') or payload['error']})}"
        return RedirectResponse(target, status_code=303)
    token, user, next_path, frontend_origin = auth_service.complete_oauth_callback(provider, payload, request)
    response = RedirectResponse(
        auth_service.oauth_frontend_redirect_url(token, user, next_path, frontend_origin, request),
        status_code=303,
    )
    _set_auth_cookie(response, request, token)
    return response


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: UserView = Depends(current_admin)) -> AdminOverview:
    return auth_service.overview()

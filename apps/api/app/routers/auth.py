from __future__ import annotations

import base64
from urllib import parse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse

from app.models import AdminOverview, AuthProviderView, AuthRequest, AuthSessionResponse, UserView
from app.services.auth_service import AuthService, bearer_token_from_request, bearer_token_from_websocket
from app.services.community_oauth import community_oauth_service
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api")
auth_service = AuthService(DATABASE_PATH)


def current_user(request: Request) -> UserView:
    token = bearer_token_from_request(request)
    return auth_service.get_user_by_token(token)


def current_websocket_user(websocket: WebSocket) -> UserView:
    token = bearer_token_from_websocket(websocket)
    return auth_service.get_user_by_token(token)


def current_admin(user: UserView = Depends(current_user)) -> UserView:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.post("/auth/register", response_model=AuthSessionResponse)
def register(payload: AuthRequest) -> AuthSessionResponse:
    token, user = auth_service.register(payload.account_identifier(), payload.password, guest_token=payload.guest_token)
    return AuthSessionResponse(token=token, user=user)


@router.post("/auth/login", response_model=AuthSessionResponse)
def login(payload: AuthRequest) -> AuthSessionResponse:
    token, user = auth_service.login(payload.account_identifier(), payload.password, guest_token=payload.guest_token)
    return AuthSessionResponse(token=token, user=user)


@router.post("/auth/guest", response_model=AuthSessionResponse)
def guest() -> AuthSessionResponse:
    token, user = auth_service.start_guest_session()
    return AuthSessionResponse(token=token, user=user)


@router.get("/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)) -> UserView:
    return user


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
    guest_token: str | None = None,
) -> RedirectResponse:
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
    return RedirectResponse(
        auth_service.oauth_frontend_redirect_url(token, user, next_path, frontend_origin, request),
        status_code=303,
    )


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: UserView = Depends(current_admin)) -> AdminOverview:
    return auth_service.overview()

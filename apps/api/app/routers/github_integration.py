from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.models import GitHubConnectionView, GitHubRepositoryView, UserView
from app.routers.auth import current_user
from app.services.github_app import GitHubAppError, github_app_service


router = APIRouter()


class GitHubInstallStartRequest(BaseModel):
    next_path: str = "/studio"


class GitHubInstallStartResponse(BaseModel):
    install_url: str


@router.get("/api/integrations/github/status", response_model=GitHubConnectionView)
def github_connection_status(user: UserView = Depends(current_user)) -> GitHubConnectionView:
    return github_app_service.status(user.id)


@router.post("/api/integrations/github/install/start", response_model=GitHubInstallStartResponse)
def start_github_install(
    request: GitHubInstallStartRequest,
    user: UserView = Depends(current_user),
) -> GitHubInstallStartResponse:
    try:
        return GitHubInstallStartResponse(
            install_url=github_app_service.start_install(owner_user_id=user.id, next_path=request.next_path)
        )
    except GitHubAppError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/integrations/github/install/callback")
def complete_github_install(
    state: str = Query(min_length=16),
    installation_id: int = Query(gt=0),
) -> RedirectResponse:
    try:
        next_path, installation = github_app_service.complete_install(
            state=state,
            installation_id=installation_id,
        )
    except GitHubAppError as exc:
        return _web_redirect("/studio", status="error", message=str(exc))
    return _web_redirect(
        next_path,
        status="connected",
        message=installation.account_login or "GitHub",
    )


@router.get("/api/integrations/github/repositories", response_model=list[GitHubRepositoryView])
def list_github_repositories(user: UserView = Depends(current_user)) -> list[GitHubRepositoryView]:
    try:
        return github_app_service.repositories(user.id)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/api/integrations/github/connection", response_model=GitHubConnectionView)
def disconnect_github(user: UserView = Depends(current_user)) -> GitHubConnectionView:
    github_app_service.disconnect(user.id)
    return github_app_service.status(user.id)


@router.post("/api/integrations/github/webhook")
async def github_webhook(request: Request) -> dict[str, bool]:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event = request.headers.get("X-GitHub-Event", "")
    try:
        github_app_service.verify_webhook(body, signature)
        payload = json.loads(body) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("Webhook payload must be an object.")
        github_app_service.handle_webhook(event=event, payload=payload)
    except (GitHubAppError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


def _web_redirect(next_path: str, *, status: str, message: str) -> RedirectResponse:
    base = (os.getenv("OPENCLASS_WEB_ORIGIN") or os.getenv("OPENCLASS_PUBLIC_ORIGIN") or "http://localhost:3000").rstrip("/")
    safe_path = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/studio"
    separator = "&" if "?" in safe_path else "?"
    query = urlencode({"github_connection": status, "github_message": message})
    return RedirectResponse(f"{base}{safe_path}{separator}{query}", status_code=303)

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import AIModelCatalog, UserView
from app.routers.auth import current_user
from app.routers import (
    auth,
    chat,
    codex_provider,
    communities,
    documents,
    geometry,
    lesson_merges,
    realtime,
    sources,
    speech,
    workspace,
)
from app.services.ai_model_catalog import build_model_catalog, realtime_runtime_enabled
from app.services.codex_app_server import codex_app_server_available, codex_app_server_runtime_enabled
from app.services.deepseek_api import deepseek_provider_configured
from app.services.workspace_state import ensure_data_dirs

ensure_data_dirs()

app = FastAPI(title="AI Board Course System API", version="0.2.0")

cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
for origin in (os.getenv("OPENCLASS_PUBLIC_ORIGIN"), os.getenv("OPENCLASS_WEB_ORIGIN")):
    if origin and origin.rstrip("/") not in cors_origins:
        cors_origins.append(origin.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspace.router)
app.include_router(auth.router)
app.include_router(communities.router)
app.include_router(documents.router)
app.include_router(lesson_merges.router)
app.include_router(chat.router)
app.include_router(codex_provider.router)
app.include_router(sources.router)
app.include_router(speech.router)
app.include_router(geometry.router)
app.include_router(realtime.router)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "codex": {
            "enabled": codex_app_server_runtime_enabled(),
            "available": codex_app_server_available(),
        },
        "deepseek": {
            "configured": deepseek_provider_configured(),
            "access": "shared_unmetered",
        },
        "workflow": {"status": "provider_neutral_board"},
        "realtime": {
            "status": "enabled" if realtime_runtime_enabled() else "disabled",
            "provider": "openai",
        },
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
def get_ai_models(user: UserView = Depends(current_user)) -> AIModelCatalog:
    return build_model_catalog(user.id)

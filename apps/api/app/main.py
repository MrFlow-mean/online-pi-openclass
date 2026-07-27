from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import AIModelCatalog, UserView
from app.routers.auth import current_user
from app.routers import (
    auth,
    billing,
    chat,
    codex_provider,
    communities,
    contributions,
    documents,
    geometry,
    lesson_merges,
    model_credentials,
    realtime,
    sources,
    speech,
    workspace,
)
from app.services.ai_model_catalog import (
    build_model_catalog_with_pricing,
    realtime_runtime_enabled,
)
from app.services.codex_app_server import codex_app_server_available, codex_app_server_runtime_enabled
from app.services.deepseek_api import deepseek_provider_configured
from app.services.openrouter_provisioning import (
    OpenRouterProvisioningService,
    OpenRouterProvisioningWorker,
)
from app.services.workspace_state import ensure_data_dirs
from app.services.source_ingestion_jobs import source_ingestion_task_manager

ensure_data_dirs()
openrouter_provisioning_service = OpenRouterProvisioningService(billing.billing_service)
openrouter_provisioning_worker = OpenRouterProvisioningWorker(
    openrouter_provisioning_service
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    source_ingestion_task_manager.recover_active()
    openrouter_provisioning_worker.start()
    try:
        yield
    finally:
        await openrouter_provisioning_worker.stop()


app = FastAPI(title="AI Board Course System API", version="0.2.0", lifespan=lifespan)

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
app.include_router(billing.router)
app.include_router(communities.router)
app.include_router(contributions.router)
app.include_router(documents.router)
app.include_router(lesson_merges.router)
app.include_router(model_credentials.router)
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
        "openrouter": openrouter_provisioning_service.health(
            worker_healthy=openrouter_provisioning_worker.healthy
        ),
        "workflow": {"status": "provider_neutral_board"},
        "realtime": {
            "status": "enabled" if realtime_runtime_enabled() else "disabled",
            "provider": "openai",
        },
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
async def get_ai_models(user: UserView = Depends(current_user)) -> AIModelCatalog:
    return await build_model_catalog_with_pricing(user.id)

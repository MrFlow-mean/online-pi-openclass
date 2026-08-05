from __future__ import annotations

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
    contact,
    contributions,
    documents,
    geometry,
    github_integration,
    lesson_merges,
    model_credentials,
    project_collaboration,
    realtime,
    sources,
    speech,
    workspace,
)
from app.services import source_document_toolchain
from app.services.ai_model_catalog import (
    build_model_catalog_with_pricing,
    realtime_runtime_enabled,
)
from app.services.codex_app_server import codex_app_server_available, codex_app_server_runtime_enabled
from app.services.deepseek_api import deepseek_provider_configured
from app.services.http_security import (
    CsrfProtectionMiddleware,
    SecurityHeadersMiddleware,
    configured_web_origins,
)
from app.services.openrouter_provisioning import (
    OpenRouterProvisioningService,
    OpenRouterProvisioningWorker,
)
from app.services.workspace_state import ensure_data_dirs
from app.services.source_ingestion_jobs import source_ingestion_task_manager
from app.services.pi_source_runtime import cleanup_orphan_source_workspaces

ensure_data_dirs()
openrouter_provisioning_service = OpenRouterProvisioningService(billing.billing_service)
openrouter_provisioning_worker = OpenRouterProvisioningWorker(
    openrouter_provisioning_service
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    cleanup_orphan_source_workspaces()
    source_ingestion_task_manager.recover_active()
    openrouter_provisioning_worker.start()
    try:
        yield
    finally:
        await openrouter_provisioning_worker.stop()


app = FastAPI(title="AI Board Course System API", version="0.2.0", lifespan=lifespan)

cors_origins = list(configured_web_origins())

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CsrfProtectionMiddleware, allowed_origins=cors_origins)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(workspace.router)
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(communities.router)
app.include_router(contact.router)
app.include_router(contributions.router)
app.include_router(project_collaboration.router)
app.include_router(documents.router)
app.include_router(lesson_merges.router)
app.include_router(model_credentials.router)
app.include_router(chat.router)
app.include_router(codex_provider.router)
app.include_router(sources.router)
app.include_router(speech.router)
app.include_router(geometry.router)
app.include_router(github_integration.router)
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
        "documents": {"pdf": source_document_toolchain.pdf_toolchain_health()},
        "workflow": {"status": "provider_neutral_board"},
        "realtime": {
            "status": "enabled" if realtime_runtime_enabled() else "disabled",
            "provider": "openai",
        },
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
async def get_ai_models(user: UserView = Depends(current_user)) -> AIModelCatalog:
    return await build_model_catalog_with_pricing(user.id)

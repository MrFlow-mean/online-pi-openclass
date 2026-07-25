from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.models import (
    AIModelSelection,
    SourceCatalogBatchView,
    SourceCatalogView,
    SourceIngestionJob,
    SourceIngestionRecord,
    SourceStructureView,
    UserView,
)
from app.routers.auth import current_user
from app.services import workspace_state
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import SourceIngestionError, source_download_path, source_ingestion_service
from app.services.source_ingestion_jobs import source_ingestion_task_manager
from app.services.source_structure_indexer import source_structure_indexer
from app.services.source_structure_store import source_structure_store

router = APIRouter()


class SourceUpdateRequest(BaseModel):
    title: str


class SourceContentView(BaseModel):
    source: SourceIngestionRecord
    content: str


class SourceContentUpdateRequest(BaseModel):
    content: str


def _parse_catalog_model(raw: str | None) -> AIModelSelection | None:
    if raw is None or not raw.strip():
        return None
    try:
        selection = AIModelSelection.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="catalog_model must be a valid model selection.") from exc
    if not selection.model.strip():
        raise HTTPException(status_code=400, detail="Catalog extraction requires a configured text model.")
    return selection


@router.get("/api/packages/{package_id}/sources", response_model=list[SourceIngestionRecord])
def list_package_sources(package_id: str, user: UserView = Depends(current_user)) -> list[SourceIngestionRecord]:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    return source_ingestion_service.list_sources(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/sources", response_model=SourceIngestionRecord)
async def import_package_source(
    package_id: str,
    source_uri: str | None = Form(default=None),
    title: str = Form(default=""),
    text: str | None = Form(default=None),
    catalog_model: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    package = workspace_state.get_package(workspace, package_id)
    try:
        if file is not None:
            selected_catalog_model = _parse_catalog_model(catalog_model)
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            queued = await run_in_threadpool(
                source_ingestion_service.queue_file_source,
                owner_user_id=user.id,
                package=package,
                file_name=file.filename or "source",
                content=content,
                mime_type=file.content_type or "application/octet-stream",
                title=title,
                catalog_model=selected_catalog_model,
            )
            source_ingestion_task_manager.submit(
                owner_user_id=user.id,
                package_id=package.id,
                source_id=queued.id,
            )
            return queued
        if text and text.strip():
            return source_ingestion_service.add_text_source(
                owner_user_id=user.id,
                package=package,
                text=text,
                title=title,
            )
        if source_uri and source_uri.strip():
            return source_ingestion_service.add_url_source(
                owner_user_id=user.id,
                package=package,
                source_uri=source_uri,
                title=title,
            )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Provide a file, source_uri, or pasted text.")


@router.get("/api/packages/{package_id}/sources/jobs", response_model=list[SourceIngestionJob])
def list_package_source_jobs(
    package_id: str,
    user: UserView = Depends(current_user),
) -> list[SourceIngestionJob]:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    return source_ingestion_service.list_jobs(owner_user_id=user.id, package_id=package_id)


@router.patch("/api/packages/{package_id}/sources/{source_id}", response_model=SourceIngestionRecord)
def update_package_source(
    package_id: str,
    source_id: str,
    request: SourceUpdateRequest,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    updated = source_ingestion_service.rename_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
        title=request.title,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found or title is empty.")
    return updated


@router.post("/api/packages/{package_id}/sources/{source_id}/retry", response_model=SourceIngestionRecord)
def retry_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    try:
        source_ingestion_task_manager.submit(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
            retry=True,
        )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = next(
        (
            source
            for source in source_ingestion_service.list_sources(
                owner_user_id=user.id,
                package_id=package_id,
            )
            if source.id == source_id
        ),
        None,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return updated


@router.get("/api/packages/{package_id}/sources/{source_id}/content", response_model=SourceContentView)
def get_package_source_content(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceContentView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    result = source_ingestion_service.source_content(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    source, content = result
    return SourceContentView(source=source, content=content)


@router.put("/api/packages/{package_id}/sources/{source_id}/content", response_model=SourceContentView)
def update_package_source_content(
    package_id: str,
    source_id: str,
    request: SourceContentUpdateRequest,
    user: UserView = Depends(current_user),
) -> SourceContentView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    try:
        updated = source_ingestion_service.update_source_content(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
            content=request.content,
        )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    content_result = source_ingestion_service.source_content(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    return SourceContentView(source=updated, content=content_result[1] if content_result else "")


@router.get("/api/packages/{package_id}/sources/{source_id}/download")
def download_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> FileResponse:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    path = source_download_path(source)
    if path is None:
        raise HTTPException(status_code=404, detail="Source file is unavailable.")
    return FileResponse(
        Path(path),
        media_type=str(source.metadata.get("original_mime_type") or source.mime_type or "application/octet-stream"),
        filename=str(source.metadata.get("original_file_name") or source.file_name or path.name),
    )


@router.delete("/api/packages/{package_id}/sources/{source_id}", response_model=SourceIngestionRecord)
def delete_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    removed = source_ingestion_service.remove_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return removed


@router.get("/api/packages/{package_id}/sources/{source_id}/structure", response_model=SourceStructureView)
def get_package_source_structure(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceStructureView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source_structure_store.get_structure_view(source=source)


@router.get("/api/packages/{package_id}/sources/catalogs", response_model=SourceCatalogBatchView)
def get_package_source_catalogs(
    package_id: str,
    user: UserView = Depends(current_user),
) -> SourceCatalogBatchView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    sources = source_evidence_store.list_sources(owner_user_id=user.id, package_id=package_id)
    return source_structure_store.get_catalog_views(package_id=package_id, sources=sources)


@router.get("/api/packages/{package_id}/sources/{source_id}/catalog", response_model=SourceCatalogView)
def get_package_source_catalog(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceCatalogView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source_structure_store.get_catalog_view(source=source)


@router.post(
    "/api/packages/{package_id}/sources/{source_id}/catalog/rebuild",
    response_model=SourceCatalogView,
)
def rebuild_package_source_catalog(
    package_id: str,
    source_id: str,
    catalog_model: str | None = Form(default=None),
    user: UserView = Depends(current_user),
) -> SourceCatalogView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if (
        source.metadata.get("source_processing_owner") == "open_notebook"
        and source.metadata.get("catalog_pipeline") != "codex_directory_v1"
    ):
        raise HTTPException(
            status_code=409,
            detail="OpenNotebook-managed sources do not use the local catalog rebuild pipeline.",
        )
    try:
        rebuilt = source_ingestion_service.rebuild_catalog(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
            catalog_model=_parse_catalog_model(catalog_model),
        )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if rebuilt is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source_structure_store.get_catalog_view(source=rebuilt)


@router.post("/api/packages/{package_id}/sources/{source_id}/structure/rebuild", response_model=SourceStructureView)
def rebuild_package_source_structure(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceStructureView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    current = source_structure_store.get_structure(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if current is not None and current.strategy == "codex_directory_v1":
        rebuilt = source_ingestion_service.rebuild_catalog(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
        )
        if rebuilt is None:
            raise HTTPException(status_code=404, detail="Source not found.")
        return source_structure_store.get_structure_view(source=rebuilt)
    if source.metadata.get("source_processing_owner") == "open_notebook":
        raise HTTPException(
            status_code=409,
            detail="OpenNotebook-managed sources do not use the local structure rebuild pipeline.",
        )
    source_structure_indexer.rebuild_structure(source)
    return source_structure_store.get_structure_view(source=source)

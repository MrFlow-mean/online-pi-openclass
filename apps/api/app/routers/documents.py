from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.models import (
    BoardDocument,
    BoardSegmentKind,
    ChatResponse,
    CoursePackageView,
    CreateBranchRequest,
    DocumentAIEditRequest,
    DocumentSaveRequest,
    DocumentSegmentSearchResponse,
    ManualCommitRequest,
    RestoreCommitRequest,
    SwitchBranchRequest,
    UserView,
)
from app.routers.auth import current_user
from app.services.chat_service import document_ai_edit_request
from app.services.board_asset_store import get_board_asset_store
from app.services.history import create_branch, current_head_commit, restore_commit, switch_branch
from app.services.html_document_export import HtmlExportBudgetError, export_html
from app.services.lesson_package_format import RIDOC_MEDIA_TYPE, RidocFormatError
from app.services.lesson_package_export import export_lesson_ridoc
from app.services.rich_document import (
    document_changed,
    export_docx,
    import_docx,
    is_document_empty,
    rich_structure_counts,
    rich_structure_score,
    would_flatten_rich_document,
)
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import (
    EXPORT_DIR,
    UPLOAD_DIR,
    commit_document_snapshot,
    find_lesson_package,
    load_workspace_for_user,
    load_workspace_for_user_with_revision,
    package_view_for_lesson,
    save_workspace_for_user_if_revision,
    search_document_segments_for_user,
)

router = APIRouter()

_DOCX_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_BOARD_ASSET_CACHE_CONTROL = "private, max-age=86400, immutable"


def _clear_transient_ai_state(lesson) -> None:
    lesson.board_teaching_guide = None
    lesson.board_teaching_progress = None
    lesson.learning_requirements = None
    lesson.board_task_requirements = None


@router.get("/api/documents/search", response_model=DocumentSegmentSearchResponse)
def search_documents(
    q: str = "",
    kind: BoardSegmentKind | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: UserView = Depends(current_user),
) -> DocumentSegmentSearchResponse:
    return DocumentSegmentSearchResponse(
        query=q,
        kind=kind,
        results=search_document_segments_for_user(user.id, q, kind=kind, limit=limit),
    )


@router.get("/api/board-assets/{asset_id}/content")
def get_board_asset_content(
    asset_id: str,
    request: Request,
    user: UserView = Depends(current_user),
) -> Response:
    stored = get_board_asset_store().read_bytes(asset_id, user.id)
    if stored is None:
        # Owner scoping is deliberately indistinguishable from a missing asset.
        raise HTTPException(status_code=404, detail="板书图片不存在")
    record, content = stored
    etag = f'"{record.content_hash}"'
    headers = {
        "Cache-Control": _BOARD_ASSET_CACHE_CONTROL,
        "Content-Disposition": f'inline; filename="{record.id}{_board_asset_extension(record.mime_type)}"',
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if _etag_matches(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type=record.mime_type, headers=headers)


def _etag_matches(raw_header: str, etag: str) -> bool:
    if not raw_header:
        return False
    expected = etag.removeprefix("W/")
    for candidate in raw_header.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == expected:
            return True
    return False


def _board_asset_extension(mime_type: str) -> str:
    return {
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime_type, ".bin")


def _save_document_request(lesson_id: str, request: DocumentSaveRequest, user_id: str) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user_id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    current_head = current_head_commit(lesson)
    is_autosave = request.metadata.get("autosave") is True or request.metadata.get("kind") == "auto_document_save"
    allow_structure_removal = request.metadata.get("structure_removal_intent") is True
    guard_document: BoardDocument | None = None
    if request.base_commit_id and request.base_commit_id != current_head.id:
        if is_autosave:
            return package_view_for_lesson(workspace, package, lesson.id)
        raise HTTPException(status_code=409, detail="文档已在本次保存前更新，请刷新后再保存")
    if not document_changed(lesson.board_document, request.document):
        return package_view_for_lesson(workspace, package, lesson.id)
    if is_autosave:
        guard_document = _recent_structured_snapshot_for_autosave(lesson, current_head, request.document) or current_head.snapshot
        if would_flatten_rich_document(
            current_document=guard_document,
            new_document=request.document,
            allow_structure_removal=allow_structure_removal,
        ):
            return package_view_for_lesson(workspace, package, lesson.id)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/save",
        lesson=lesson,
        trace_prefix="document_save",
    ):
        previous_updated_at = lesson.updated_at
        lesson.board_document = request.document
        _clear_transient_ai_state(lesson)
        commit_metadata: dict[str, object] = {
            "kind": "manual_document_save",
            **request.metadata,
            **_document_save_structure_metadata(
                base_commit_id=request.base_commit_id or current_head.id,
                current_head_commit_id=current_head.id,
                before_document=current_head.snapshot,
                after_document=request.document,
                flatten_guard_evaluated=is_autosave,
            ),
        }
        commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata=commit_metadata,
        )
        committed_head = current_head_commit(lesson)
        if is_autosave and guard_document and would_flatten_rich_document(
            current_document=guard_document,
            new_document=committed_head.snapshot,
            allow_structure_removal=allow_structure_removal,
        ):
            _rollback_unpersisted_commit(
                lesson,
                commit_id=committed_head.id,
                restore_head=current_head,
                updated_at=previous_updated_at,
            )
            return package_view_for_lesson(workspace, package, lesson.id)
        committed_head.metadata.update(
            _document_save_structure_metadata(
                base_commit_id=request.base_commit_id or current_head.id,
                current_head_commit_id=current_head.id,
                before_document=current_head.snapshot,
                after_document=committed_head.snapshot,
                flatten_guard_evaluated=is_autosave,
            )
        )
        save_workspace_for_user_if_revision(user_id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)

def _visible_document_text(document: BoardDocument) -> str:
    return " ".join((document.content_text or "").split())


def _structured_document_score(document: BoardDocument) -> int:
    return rich_structure_score(rich_structure_counts(document))


def _recent_structured_snapshot_for_autosave(
    lesson,
    current_head,
    new_document: BoardDocument,
) -> BoardDocument | None:
    target_text = _visible_document_text(new_document)
    if not target_text:
        return None
    commits_by_id = {commit.id: commit for commit in lesson.history_graph.commits}
    cursor = current_head
    seen: set[str] = set()
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        if _structured_document_score(cursor.snapshot) >= 8 and _visible_document_text(cursor.snapshot) == target_text:
            return cursor.snapshot
        parent_id = cursor.parent_ids[0] if cursor.parent_ids else None
        cursor = commits_by_id.get(parent_id) if parent_id else None
    return None


def _rollback_unpersisted_commit(lesson, *, commit_id: str, restore_head, updated_at: str) -> None:
    branch = lesson.history_graph.branches[lesson.history_graph.current_branch]
    branch.head_commit_id = restore_head.id
    lesson.board_document = restore_head.snapshot
    lesson.updated_at = updated_at
    lesson.history_graph.commits = [
        commit for commit in lesson.history_graph.commits if commit.id != commit_id
    ]


def _document_save_structure_metadata(
    *,
    base_commit_id: str,
    current_head_commit_id: str,
    before_document: BoardDocument,
    after_document: BoardDocument,
    flatten_guard_evaluated: bool,
) -> dict[str, object]:
    before_counts = rich_structure_counts(before_document)
    after_counts = rich_structure_counts(after_document)
    return {
        "base_commit_id": base_commit_id,
        "current_head_commit_id": current_head_commit_id,
        "structure_before": before_counts,
        "structure_after": after_counts,
        "structure_score_before": rich_structure_score(before_counts),
        "structure_score_after": rich_structure_score(after_counts),
        "flatten_guard_evaluated": flatten_guard_evaluated,
        "flatten_guard_triggered": False,
    }


@router.post("/api/lessons/{lesson_id}/manual-commit", response_model=CoursePackageView)
def manual_commit(
    lesson_id: str,
    request: ManualCommitRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/manual-commit",
        lesson=lesson,
        trace_prefix="manual_commit",
        commit_label=request.label,
    ):
        if request.document is not None:
            lesson.board_document = request.document
        _clear_transient_ai_state(lesson)
        commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata={"kind": "manual_document_edit"},
        )
        save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/document/save", response_model=CoursePackageView)
def save_document(
    lesson_id: str,
    request: DocumentSaveRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    return _save_document_request(lesson_id, request, user.id)


@router.post("/api/lessons/{lesson_id}/document/save-beacon", response_model=CoursePackageView)
async def save_document_beacon(
    lesson_id: str,
    request: Request,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
        save_request = DocumentSaveRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid document save payload") from exc
    return _save_document_request(lesson_id, save_request, user.id)


@router.post("/api/lessons/{lesson_id}/document/ai-edit", response_model=ChatResponse)
def ai_edit_document(
    lesson_id: str,
    request: DocumentAIEditRequest,
    user: UserView = Depends(current_user),
) -> ChatResponse:
    return document_ai_edit_request(
        lesson_id,
        request.instruction,
        request.selection_text,
        request.conversation,
        user_id=user.id,
    )


@router.post("/api/lessons/{lesson_id}/document/import-docx", response_model=CoursePackageView)
def import_document_docx(
    lesson_id: str,
    file: UploadFile = File(...),
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    safe_name = Path(file.filename or "document.docx").name
    destination = UPLOAD_DIR / f"{lesson_id}_{safe_name}"
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/import-docx",
        lesson=lesson,
        trace_prefix="import_docx",
        filename=safe_name,
    ):
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        lesson.board_document = import_docx(destination, title=lesson.board_document.title or lesson.title)
        _clear_transient_ai_state(lesson)
        commit_document_snapshot(
            lesson,
            label="Import DOCX",
            message=f"Imported {safe_name} into the rich document editor",
            metadata={"kind": "import_docx", "filename": safe_name},
        )
        save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.get("/api/lessons/{lesson_id}/document/export-docx")
def export_document_docx(lesson_id: str, user: UserView = Depends(current_user)) -> FileResponse:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    document = _exportable_document_for_lesson(lesson)
    if is_document_empty(document):
        raise HTTPException(status_code=409, detail="当前板书文档为空，不能导出 DOCX")
    target_path = _unique_export_path("docx")
    export_docx(document, target_path, owner_user_id=user.id)
    return FileResponse(
        target_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{lesson.slug or lesson.id}.docx",
        headers=_DOCX_NO_STORE_HEADERS,
        background=BackgroundTask(target_path.unlink, missing_ok=True),
    )


@router.get("/api/lessons/{lesson_id}/document/export-html")
def export_document_html(lesson_id: str, user: UserView = Depends(current_user)) -> FileResponse:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    document = _exportable_document_for_lesson(lesson)
    if is_document_empty(document):
        raise HTTPException(status_code=409, detail="当前板书文档为空，不能导出 HTML")
    target_path = _unique_export_path("html")
    try:
        export_html(document, target_path, owner_user_id=user.id)
    except HtmlExportBudgetError as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return FileResponse(
        target_path,
        media_type="text/html; charset=utf-8",
        filename=f"{lesson.slug or lesson.id}.html",
        headers=_DOCX_NO_STORE_HEADERS,
        background=BackgroundTask(target_path.unlink, missing_ok=True),
    )


@router.get("/api/lessons/{lesson_id}/document/export-ridoc")
def export_lesson_package(
    lesson_id: str,
    source_mode: str = Query("evidence", pattern="^(evidence|references)$"),
    user: UserView = Depends(current_user),
) -> FileResponse:
    workspace = load_workspace_for_user(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    target_path = _unique_export_path("ridoc")
    try:
        export_lesson_ridoc(
            owner_user_id=user.id,
            package_id=package.id,
            lesson=lesson,
            target_path=target_path,
            source_mode=source_mode,
        )
    except RidocFormatError as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        target_path,
        media_type=RIDOC_MEDIA_TYPE,
        filename=f"{lesson.slug or lesson.id}.ridoc",
        headers=_DOCX_NO_STORE_HEADERS,
        background=BackgroundTask(target_path.unlink, missing_ok=True),
    )


def _unique_export_path(extension: str) -> Path:
    safe_extension = extension.lower().lstrip(".")
    if safe_extension not in {"docx", "html", "ridoc"}:
        raise ValueError("Unsupported document export extension.")
    return EXPORT_DIR / "requests" / f"{uuid.uuid4().hex}.{safe_extension}"


def _exportable_document_for_lesson(lesson) -> BoardDocument:
    if not is_document_empty(lesson.board_document):
        return lesson.board_document
    try:
        head_snapshot = current_head_commit(lesson).snapshot
    except Exception:
        return lesson.board_document
    if not is_document_empty(head_snapshot):
        return head_snapshot
    return lesson.board_document


@router.post("/api/lessons/{lesson_id}/branches", response_model=CoursePackageView)
def create_lesson_branch(
    lesson_id: str,
    request: CreateBranchRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches",
        lesson=lesson,
        trace_prefix="create_branch",
        branch_name=request.name,
        from_commit_id=request.from_commit_id,
    ):
        create_branch(lesson, request.name, request.from_commit_id)
        save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/branches/checkout", response_model=CoursePackageView)
def checkout_lesson_branch(
    lesson_id: str,
    request: SwitchBranchRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches/checkout",
        lesson=lesson,
        trace_prefix="switch_branch",
        branch_name=request.name,
    ):
        switch_branch(lesson, request.name)
        save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/restore", response_model=CoursePackageView)
def restore_lesson_commit(
    lesson_id: str,
    request: RestoreCommitRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/restore",
        lesson=lesson,
        trace_prefix="restore_commit",
        commit_id=request.commit_id,
        restore_label=request.label,
    ):
        restore_commit(lesson, request.commit_id, request.label)
        save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson.id)

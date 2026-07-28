from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.models import (
    BatchLessonActionRequest,
    CourseGraphEdge,
    CoursePackage,
    CoursePackageView,
    CreatePackageRequest,
    GenerateLessonRequest,
    MoveLessonRequest,
    PublicCoursePackageView,
    PublicCourseSearchResult,
    PublicLessonView,
    ReorderTabsRequest,
    UpdateLessonRequest,
    UpdatePackageRequest,
    UpdateVisibilityRequest,
    UserView,
    WorkspaceStateView,
    now_iso,
)
from app.routers.auth import current_user
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson, create_untitled_lesson
from app.services.lesson_package_format import (
    RIDOC_MAX_ARCHIVE_BYTES,
    RidocFormatError,
    RidocSizeError,
    RidocVersionError,
    read_ridoc,
)
from app.services.lesson_package_import import import_ridoc_archive, rollback_imported_assets
from app.services.lesson_summary import lesson_content_summary
from app.services.personal_lesson_copy import retain_public_lesson_as_personal_copy
from app.services.publication_review import (
    PublicationReviewProgressUpdate,
    review_project_publication,
)
from app.services.workspace_batch_actions import apply_lesson_batch_action
from app.services.workspace_state import (
    find_lesson_package,
    get_package,
    get_standalone_package,
    get_course_store,
    load_workspace_for_user,
    load_workspace_for_user_with_revision,
    load_workspace_package_for_user,
    load_workspace_package_for_user_with_revision,
    normalize_package_state,
    package_view_for_lesson,
    UPLOAD_DIR,
    save_workspace_for_user_if_revision,
    workspace_view,
)

router = APIRouter()


def _public_lesson_view(lesson) -> PublicLessonView:
    return PublicLessonView(
        id=lesson.id,
        title=lesson.title,
        summary=lesson_content_summary(lesson),
        tags=lesson.tags,
        board_document=lesson.board_document,
        updated_at=lesson.updated_at,
    )


@router.post("/api/workspace/import-ridoc", response_model=CoursePackageView)
def import_lesson_package(
    file: UploadFile = File(...),
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    safe_name = Path(file.filename or "lesson.ridoc").name
    if Path(safe_name).suffix.lower() != ".ridoc":
        raise HTTPException(status_code=400, detail="请选择 .ridoc 课程文件")
    import_dir = UPLOAD_DIR / "ridoc-imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    destination = import_dir / f"{uuid.uuid4().hex}.ridoc"
    imported_lesson_id: str | None = None
    try:
        written = 0
        with destination.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > RIDOC_MAX_ARCHIVE_BYTES:
                    raise RidocSizeError("RIDOC archive exceeds the 256 MiB limit.")
                output.write(chunk)
        archive = read_ridoc(destination)
        workspace, revision = load_workspace_for_user_with_revision(user.id)
        package = import_ridoc_archive(
            owner_user_id=user.id,
            workspace=workspace,
            archive=archive,
        )
        imported_lesson_id = package.lessons[0].id
        try:
            save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
        except Exception:
            rollback_imported_assets(owner_user_id=user.id, lesson_id=imported_lesson_id)
            raise
        return package_view_for_lesson(workspace, package, imported_lesson_id)
    except RidocSizeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except RidocVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RidocFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        destination.unlink(missing_ok=True)


@router.get("/api/workspace", response_model=WorkspaceStateView)
def get_workspace(user: UserView = Depends(current_user)) -> WorkspaceStateView:
    return workspace_view(load_workspace_for_user(user.id))


@router.post("/api/packages", response_model=WorkspaceStateView)
def create_package(request: CreatePackageRequest, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Package title is required")

    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package = CoursePackage(
        title=title,
        summary=request.summary.strip(),
        lessons=[],
    )
    workspace.packages.append(package)
    workspace.active_package_id = package.id
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/packages/{package_id}/open", response_model=WorkspaceStateView)
def open_package(package_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    get_package(workspace, package_id)
    workspace.active_package_id = package_id
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/packages/{package_id}", response_model=WorkspaceStateView)
def update_package(
    package_id: str,
    request: UpdatePackageRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package = get_package(workspace, package_id)

    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Package title is required")
        package.title = title

    if request.summary is not None:
        package.summary = request.summary.strip()

    if request.visibility is not None:
        if workspace.packages and package.id == workspace.packages[0].id:
            raise HTTPException(status_code=400, detail="Standalone lessons manage visibility individually")
        if request.visibility == "private":
            package.visibility = "private"
        else:
            package.publication_review = review_project_publication(
                owner_user_id=user.id,
                package=package,
            )
            package.visibility = (
                "public" if package.publication_review.status == "approved" else "private"
            )

    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/visibility", response_model=WorkspaceStateView)
def update_lesson_visibility(
    lesson_id: str,
    request: UpdateVisibilityRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    if not workspace.packages or package.id != workspace.packages[0].id:
        raise HTTPException(status_code=400, detail="Packaged lessons inherit their package visibility")
    if request.visibility == "private":
        lesson.visibility = "private"
    else:
        lesson.publication_review = review_project_publication(
            owner_user_id=user.id,
            package=package,
            lesson_id=lesson.id,
        )
        lesson.visibility = (
            "public" if lesson.publication_review.status == "approved" else "private"
        )
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/visibility/stream")
def stream_lesson_visibility_update(
    lesson_id: str,
    request: UpdateVisibilityRequest,
    user: UserView = Depends(current_user),
) -> StreamingResponse:
    if request.visibility != "public":
        raise HTTPException(status_code=400, detail="Streaming review is only used when publishing")

    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    if not workspace.packages or package.id != workspace.packages[0].id:
        raise HTTPException(status_code=400, detail="Packaged lessons inherit their package visibility")

    event_queue: Queue[dict[str, object] | None] = Queue()

    def report_progress(progress: PublicationReviewProgressUpdate) -> None:
        event_queue.put(
            {
                "type": "progress",
                "progress": progress.model_dump(mode="json"),
            }
        )

    def run_review() -> None:
        try:
            lesson.publication_review = review_project_publication(
                owner_user_id=user.id,
                package=package,
                lesson_id=lesson.id,
                progress_callback=report_progress,
            )
            lesson.visibility = (
                "public" if lesson.publication_review.status == "approved" else "private"
            )
            save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
            event_queue.put(
                {
                    "type": "result",
                    "workspace": workspace_view(workspace).model_dump(mode="json"),
                }
            )
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "更新课程可见权限失败"
            event_queue.put({"type": "error", "detail": str(detail)})
        finally:
            event_queue.put(None)

    def event_stream() -> Iterator[str]:
        Thread(target=run_review, name=f"publication-review-{lesson.id}", daemon=True).start()
        while True:
            event = event_queue.get()
            if event is None:
                return
            yield f"{json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/lessons/{lesson_id}/rename", response_model=WorkspaceStateView)
def update_lesson(
    lesson_id: str,
    request: UpdateLessonRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Lesson title is required")

    workspace, revision = load_workspace_for_user_with_revision(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    lesson.title = title
    lesson.updated_at = now_iso()
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.get("/api/public/lessons/{lesson_id}", response_model=PublicLessonView)
def get_public_lesson(
    lesson_id: str,
    history_node: str | None = Query(default=None),
) -> PublicLessonView:
    lesson = get_course_store().load_public_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Public lesson not found")
    if history_node:
        commit = next(
            (item for item in lesson.history_graph.commits if item.id == history_node),
            None,
        )
        if commit is None:
            raise HTTPException(status_code=404, detail="Public history node not found")
        lesson = lesson.model_copy(
            update={
                "board_document": commit.snapshot,
                "updated_at": commit.created_at,
            }
        )
    return _public_lesson_view(lesson)


@router.get("/api/public/courses/search", response_model=list[PublicCourseSearchResult])
def search_public_courses(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=50),
    user: UserView = Depends(current_user),
) -> list[PublicCourseSearchResult]:
    return get_course_store().search_public_courses(
        q,
        exclude_owner_user_id=user.id,
        limit=limit,
    )


@router.post("/api/public/lessons/{lesson_id}/fork", response_model=CoursePackageView)
def fork_public_lesson(
    lesson_id: str,
    history_node: str | None = Query(default=None),
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    source_lesson = get_course_store().load_public_lesson(lesson_id)
    if source_lesson is None:
        raise HTTPException(status_code=404, detail="Public lesson not found")

    source_commit = current_head_commit(source_lesson)
    if history_node:
        source_commit = next(
            (item for item in source_lesson.history_graph.commits if item.id == history_node),
            None,
        )
        if source_commit is None:
            raise HTTPException(status_code=404, detail="Public history node not found")
        source_lesson = source_lesson.model_copy(
            update={
                "board_document": source_commit.snapshot,
                "updated_at": source_commit.created_at,
            }
        )

    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, personal_lesson = retain_public_lesson_as_personal_copy(
        workspace,
        source_lesson,
        source_commit_id=source_commit.id,
    )
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, personal_lesson.id)


@router.get("/api/public/packages/{package_id}", response_model=PublicCoursePackageView)
def get_public_package(package_id: str) -> PublicCoursePackageView:
    package = get_course_store().load_public_package(package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Public package not found")
    return PublicCoursePackageView(
        id=package.id,
        title=package.title,
        summary=package.summary,
        lessons=[_public_lesson_view(lesson) for lesson in package.lessons],
    )


@router.post("/api/packages/{package_id}/delete", response_model=WorkspaceStateView)
def delete_package(package_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    get_package(workspace, package_id)

    if len(workspace.packages) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only course package")

    workspace.packages = [package for package in workspace.packages if package.id != package_id]
    if workspace.active_package_id == package_id:
        workspace.active_package_id = workspace.packages[0].id if workspace.packages else None

    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/move", response_model=WorkspaceStateView)
def move_lesson(
    lesson_id: str,
    request: MoveLessonRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    source_package, lesson = find_lesson_package(workspace, lesson_id)
    target_package = get_package(workspace, request.target_package_id)

    if source_package.id == target_package.id:
        raise HTTPException(status_code=400, detail="Lesson is already in the selected package")

    source_package.lessons = [current for current in source_package.lessons if current.id != lesson_id]
    source_package.open_lesson_ids = [current for current in source_package.open_lesson_ids if current != lesson_id]
    source_package.workspace_tab_order = [current for current in source_package.workspace_tab_order if current != lesson_id]
    if source_package.active_lesson_id == lesson_id:
        source_package.active_lesson_id = None

    target_package.lessons.append(lesson)
    if lesson.id not in target_package.open_lesson_ids:
        target_package.open_lesson_ids.append(lesson.id)
    if lesson.id not in target_package.workspace_tab_order:
        target_package.workspace_tab_order.append(lesson.id)
    if target_package.active_lesson_id is None:
        target_package.active_lesson_id = lesson.id

    normalize_package_state(source_package)
    normalize_package_state(target_package)
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/delete", response_model=WorkspaceStateView)
def delete_lesson(lesson_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)

    package.lessons = [current for current in package.lessons if current.id != lesson_id]
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = None

    normalize_package_state(package)
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.post("/api/lessons/batch", response_model=WorkspaceStateView)
def batch_lessons(
    request: BatchLessonActionRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    apply_lesson_batch_action(workspace, request)
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return workspace_view(workspace)


@router.get("/api/course-package", response_model=CoursePackageView)
def get_course_package(user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace, package = load_workspace_package_for_user(user.id)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/lessons/generate", response_model=CoursePackageView)
def generate_lesson(
    request: GenerateLessonRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    source_package = None
    if request.branch_from_lesson_id:
        source_package, _ = find_lesson_package(workspace, request.branch_from_lesson_id)
    if request.target_package_id:
        package = get_package(workspace, request.target_package_id)
    elif source_package is not None:
        package = source_package
    else:
        package = get_standalone_package(workspace)
    if source_package is not None and source_package.id != package.id:
        raise HTTPException(status_code=400, detail="Branch source lesson must be in the target package")

    requested_title = (request.topic or "").strip()
    lesson = (
        create_empty_lesson(requested_title)
        if requested_title
        else create_untitled_lesson(timezone_name=request.timezone)
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    workspace.active_package_id = package.id
    if request.branch_from_lesson_id:
        package.course_graph.append(
            CourseGraphEdge(
                source_lesson_id=request.branch_from_lesson_id,
                target_lesson_id=lesson.id,
                relationship="deep_dive",
            )
        )
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/workspace/reorder", response_model=CoursePackageView)
def reorder_workspace_tabs(
    request: ReorderTabsRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, package, revision = load_workspace_package_for_user_with_revision(user.id)
    package.workspace_tab_order = request.ordered_lesson_ids
    package.open_lesson_ids = request.ordered_lesson_ids
    package.active_lesson_id = request.active_lesson_id or (
        request.ordered_lesson_ids[0] if request.ordered_lesson_ids else None
    )
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/lessons/{lesson_id}/open", response_model=CoursePackageView)
def open_lesson_tab(lesson_id: str, user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)
    if lesson_id not in package.open_lesson_ids:
        package.open_lesson_ids.append(lesson_id)
    if lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(lesson_id)
    package.active_lesson_id = lesson_id
    workspace.active_package_id = package.id
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, lesson_id)


@router.post("/api/lessons/{lesson_id}/close", response_model=CoursePackageView)
def close_lesson_tab(lesson_id: str, user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = package.workspace_tab_order[0] if package.workspace_tab_order else None
    save_workspace_for_user_if_revision(user.id, workspace, expected_revision=revision)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.get("/api/lessons/{lesson_id}/head")
def get_lesson_head(lesson_id: str, user: UserView = Depends(current_user)) -> dict[str, str]:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    head = current_head_commit(lesson)
    return {"lesson_id": lesson_id, "head_commit_id": head.id}

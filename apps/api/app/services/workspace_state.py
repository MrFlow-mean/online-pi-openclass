from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

from app.models import (
    BoardSegmentKind,
    CommitRecord,
    CoursePackage,
    CoursePackageView,
    DocumentSegmentSearchResult,
    Lesson,
    LessonMergeSession,
    LessonView,
    ResourceLibraryItem,
    WorkspaceState,
    WorkspaceStateView,
)
from app.services.blank_board_intake import active_requirement_from_history
from app.services.config import API_BASE_DIR as BASE_DIR, DATA_DIR, ROOT_DIR, load_root_dotenv
from app.services.course_store import SqliteCourseStore
from app.services.history import commit_operations
from app.services.lesson_summary import lesson_content_summary


def _load_root_dotenv() -> None:
    load_root_dotenv()


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


_load_root_dotenv()

DATABASE_PATH = _path_from_env("OPENCLASS_DATABASE_PATH", DATA_DIR / "openclass.sqlite3")
LEGACY_STORE_PATH = _path_from_env("OPENCLASS_LEGACY_STORE_PATH", DATA_DIR / "store.json")
STORE = SqliteCourseStore(DATABASE_PATH, legacy_json_path=LEGACY_STORE_PATH)
UPLOAD_DIR = _path_from_env("OPENCLASS_UPLOAD_DIR", DATA_DIR / "uploads")
EXPORT_DIR = _path_from_env("OPENCLASS_EXPORT_DIR", DATA_DIR / "exports")


def ensure_data_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_store() -> SqliteCourseStore:
    return STORE


def get_course_store() -> SqliteCourseStore:
    return get_store()


def load_workspace() -> WorkspaceState:
    return get_store().load()


def save_workspace(workspace: WorkspaceState) -> None:
    get_store().save(workspace)


def load_workspace_for_user(user_id: str) -> WorkspaceState:
    return get_store().load_for_user(user_id)


def load_workspace_for_user_with_revision(user_id: str) -> tuple[WorkspaceState, int]:
    return get_store().load_for_user_with_revision(user_id)


def save_workspace_for_user_if_revision(
    user_id: str,
    workspace: WorkspaceState,
    *,
    expected_revision: int,
) -> None:
    saved = get_store().save_for_user_if_revision(
        user_id,
        workspace,
        expected_revision=expected_revision,
    )
    if not saved:
        raise HTTPException(
            status_code=409,
            detail="工作区已在本次操作期间更新，请刷新后重试",
        )


def save_lesson_for_user_if_head(
    user_id: str,
    lesson: Lesson,
    *,
    expected_branch_name: str,
    expected_head_commit_id: str,
) -> bool:
    return get_store().save_lesson_for_user_if_head(
        user_id,
        lesson,
        expected_branch_name=expected_branch_name,
        expected_head_commit_id=expected_head_commit_id,
    )


def append_non_document_commit_for_user_if_head(
    user_id: str,
    lesson_id: str,
    commit: CommitRecord,
    *,
    expected_branch_name: str,
    expected_head_commit_id: str,
    lesson_updated_at: str,
) -> bool:
    return get_store().append_non_document_commit_for_user_if_head(
        user_id,
        lesson_id,
        commit,
        expected_branch_name=expected_branch_name,
        expected_head_commit_id=expected_head_commit_id,
        lesson_updated_at=lesson_updated_at,
    )


def load_merge_session_for_user(user_id: str, session_id: str) -> LessonMergeSession | None:
    return get_store().load_merge_session_for_user(user_id, session_id)


def load_active_merge_session_for_user(
    user_id: str,
    lesson_id: str,
) -> LessonMergeSession | None:
    return get_store().load_active_merge_session_for_user(user_id, lesson_id)


def save_merge_session_for_user(session: LessonMergeSession) -> None:
    get_store().save_merge_session_for_user(session)


def save_merge_session_for_user_if_version(
    session: LessonMergeSession,
    *,
    expected_version: int,
) -> None:
    saved = get_store().save_merge_session_for_user_if_version(
        session,
        expected_version=expected_version,
    )
    if not saved:
        raise HTTPException(
            status_code=409,
            detail="合并草案已在其他窗口中更新，请刷新后继续",
        )


def save_workspace_and_merge_session_for_user_if_revision(
    user_id: str,
    workspace: WorkspaceState,
    session: LessonMergeSession,
    *,
    expected_revision: int,
) -> None:
    saved = get_store().save_workspace_and_merge_session_for_user_if_revision(
        user_id,
        workspace,
        session,
        expected_revision=expected_revision,
    )
    if not saved:
        raise HTTPException(
            status_code=409,
            detail="工作区或合并草案已更新，请刷新后重试",
        )


def search_document_segments_for_user(
    user_id: str,
    query: str = "",
    *,
    kind: BoardSegmentKind | None = None,
    limit: int = 20,
) -> list[DocumentSegmentSearchResult]:
    return get_store().search_document_segments(query, owner_user_id=user_id, kind=kind, limit=limit)


def get_package(workspace: WorkspaceState, package_id: str) -> CoursePackage:
    for package in workspace.packages:
        if package.id == package_id:
            return package
    raise HTTPException(status_code=404, detail=f"Unknown course package {package_id}")


def get_active_package(workspace: WorkspaceState) -> CoursePackage:
    if not workspace.packages:
        raise HTTPException(status_code=404, detail="No course package available")
    if workspace.active_package_id:
        return get_package(workspace, workspace.active_package_id)
    workspace.active_package_id = workspace.packages[0].id
    return workspace.packages[0]


def get_standalone_package(workspace: WorkspaceState) -> CoursePackage:
    if not workspace.packages:
        raise HTTPException(status_code=404, detail="No standalone course pool available")
    return workspace.packages[0]


def load_workspace_package() -> tuple[WorkspaceState, CoursePackage]:
    workspace = load_workspace()
    package = get_active_package(workspace)
    return workspace, package


def load_workspace_package_for_user(user_id: str) -> tuple[WorkspaceState, CoursePackage]:
    workspace = load_workspace_for_user(user_id)
    package = get_active_package(workspace)
    return workspace, package


def load_workspace_package_for_user_with_revision(
    user_id: str,
) -> tuple[WorkspaceState, CoursePackage, int]:
    workspace, revision = load_workspace_for_user_with_revision(user_id)
    package = get_active_package(workspace)
    return workspace, package, revision


def get_lesson(package: CoursePackage, lesson_id: str) -> Lesson:
    for lesson in package.lessons:
        if lesson.id == lesson_id:
            return lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def find_lesson_package(workspace: WorkspaceState, lesson_id: str) -> tuple[CoursePackage, Lesson]:
    for package in workspace.packages:
        for lesson in package.lessons:
            if lesson.id == lesson_id:
                return package, lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def is_standalone_package(workspace: WorkspaceState, package: CoursePackage) -> bool:
    return bool(workspace.packages and workspace.packages[0].id == package.id)


def resources_visible_to_lesson(
    package: CoursePackage,
    *,
    lesson_id: str | None,
    isolate_lesson_resources: bool,
) -> list[ResourceLibraryItem]:
    if not isolate_lesson_resources:
        return list(package.resources)
    if not lesson_id:
        return []
    return [
        resource
        for resource in package.resources
        if resource.scope_lesson_id == lesson_id
    ]


def package_context_for_lesson(
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson_id: str | None,
) -> CoursePackage:
    resources = resources_visible_to_lesson(
        package,
        lesson_id=lesson_id,
        isolate_lesson_resources=is_standalone_package(workspace, package),
    )
    return package.model_copy(update={"resources": resources})


def normalize_package_state(package: CoursePackage) -> None:
    lesson_ids = [lesson.id for lesson in package.lessons]
    valid_ids = set(lesson_ids)
    package.open_lesson_ids = [lesson_id for lesson_id in package.open_lesson_ids if lesson_id in valid_ids]
    package.workspace_tab_order = [lesson_id for lesson_id in package.workspace_tab_order if lesson_id in valid_ids]
    package.course_graph = [
        edge
        for edge in package.course_graph
        if edge.source_lesson_id in valid_ids and edge.target_lesson_id in valid_ids
    ]

    if not package.lessons:
        package.active_lesson_id = None
        package.open_lesson_ids = []
        package.workspace_tab_order = []
        return

    if not package.workspace_tab_order:
        package.workspace_tab_order = [package.lessons[0].id]

    if not package.open_lesson_ids:
        package.open_lesson_ids = list(package.workspace_tab_order)

    for lesson_id in package.workspace_tab_order:
        if lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(lesson_id)

    if package.active_lesson_id not in valid_ids:
        package.active_lesson_id = package.workspace_tab_order[0]
    elif package.active_lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(package.active_lesson_id)
        if package.active_lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(package.active_lesson_id)


def lesson_view(lesson: Lesson) -> LessonView:
    lesson = _codex_only_lesson(lesson)
    return LessonView.model_validate(
        lesson.model_dump(mode="json", exclude={"teaching_guide", "board_teaching_guide"})
    )


def _codex_only_lesson(lesson: Lesson) -> Lesson:
    active_requirement = (
        active_requirement_from_history(lesson)
        if not lesson.board_document.content_text.strip()
        else None
    )
    return lesson.model_copy(
        update={
            "summary": lesson_content_summary(lesson),
            "board_teaching_guide": None,
            "board_teaching_progress": None,
            "learning_requirements": active_requirement,
            "board_task_requirements": None,
        }
    )


def package_view(
    package: CoursePackage,
    *,
    is_standalone: bool = False,
    resource_lesson_id: str | None = None,
    isolate_lesson_resources: bool = False,
) -> CoursePackageView:
    lessons_for_view = [_codex_only_lesson(lesson) for lesson in package.lessons]
    visible_package = package.model_copy(
        update={
            "lessons": lessons_for_view,
            "resources": resources_visible_to_lesson(
                package,
                lesson_id=resource_lesson_id,
                isolate_lesson_resources=isolate_lesson_resources,
            )
        }
    )
    package_data = visible_package.model_dump(
        mode="json",
        exclude={"lessons": {"__all__": {"teaching_guide", "board_teaching_guide"}}},
    )
    package_data["is_standalone"] = is_standalone
    return CoursePackageView.model_validate(package_data)


def package_view_for_lesson(
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson_id: str | None,
) -> CoursePackageView:
    standalone = is_standalone_package(workspace, package)
    return package_view(
        package,
        is_standalone=standalone,
        resource_lesson_id=lesson_id,
        isolate_lesson_resources=standalone,
    )


def workspace_view(workspace: WorkspaceState) -> WorkspaceStateView:
    return WorkspaceStateView(
        packages=[
            package_view_for_lesson(workspace, package, package.active_lesson_id)
            for package in workspace.packages
        ],
        active_package_id=workspace.active_package_id,
    )


def commit_document_snapshot(
    lesson: Lesson,
    *,
    label: str,
    message: str,
    metadata: dict[str, object] | None = None,
) -> None:
    commit_operations(
        lesson,
        [],
        label=label,
        message=message,
        new_document=lesson.board_document,
        metadata=metadata,
    )

from __future__ import annotations

from app.models import BoardDocument, CourseGraphEdge, CoursePackage, Lesson, WorkspaceState, new_id, now_iso
from app.services.lesson_factory import build_teaching_guide, create_empty_lesson
from app.services.workspace_state import get_standalone_package, normalize_package_state


PUBLIC_SOURCE_LESSON_ID_KEY = "forked_from_public_lesson_id"
PUBLIC_SOURCE_COMMIT_ID_KEY = "forked_from_public_commit_id"


def _activate_lesson(
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
) -> None:
    if lesson.id not in package.open_lesson_ids:
        package.open_lesson_ids.append(lesson.id)
    if lesson.id not in package.workspace_tab_order:
        package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    workspace.active_package_id = package.id
    normalize_package_state(package)


def _existing_personal_copy(
    workspace: WorkspaceState,
    source_lesson_id: str,
) -> tuple[CoursePackage, Lesson] | None:
    for package in workspace.packages:
        for lesson in package.lessons:
            if lesson.id == source_lesson_id:
                return package, lesson
            if any(
                commit.metadata.get(PUBLIC_SOURCE_LESSON_ID_KEY) == source_lesson_id
                for commit in lesson.history_graph.commits
            ):
                return package, lesson
    return None


def retain_public_lesson_as_personal_copy(
    workspace: WorkspaceState,
    source_lesson: Lesson,
    *,
    source_commit_id: str,
) -> tuple[CoursePackage, Lesson]:
    existing = _existing_personal_copy(workspace, source_lesson.id)
    if existing is not None:
        package, lesson = existing
        _activate_lesson(workspace, package, lesson)
        return package, lesson

    package = get_standalone_package(workspace)
    personal_lesson = _copy_public_lesson(
        source_lesson,
        source_commit_id=source_commit_id,
    )
    package.lessons.append(personal_lesson)
    _activate_lesson(workspace, package, personal_lesson)
    return package, personal_lesson


def _copy_public_lesson(
    source_lesson: Lesson,
    *,
    source_commit_id: str,
) -> Lesson:
    personal_lesson = create_empty_lesson(source_lesson.title)
    personal_document = BoardDocument.model_validate(
        source_lesson.board_document.model_dump(mode="json")
    ).model_copy(update={"id": new_id("document")})
    personal_lesson.board_document = personal_document
    personal_lesson.summary = source_lesson.summary
    personal_lesson.tags = list(source_lesson.tags)
    personal_lesson.teaching_guide = build_teaching_guide(
        personal_lesson.id,
        personal_lesson.title,
        personal_document,
    )

    initial_commit = personal_lesson.history_graph.commits[0]
    initial_commit.label = "Personal copy baseline"
    initial_commit.message = "Saved a private, editable copy of a public lesson"
    initial_commit.snapshot = BoardDocument.model_validate(
        personal_document.model_dump(mode="json")
    )
    published_conversation = (
        source_lesson.published_version.conversation
        if source_lesson.published_version is not None
        else []
    )
    initial_commit.metadata.update(
        {
            "kind": "initial_document",
            "history_node_kind": "system",
            "history_node_title": "Personal copy baseline",
            "history_node_summary": "Private starting point saved from a public lesson",
            PUBLIC_SOURCE_LESSON_ID_KEY: source_lesson.id,
            PUBLIC_SOURCE_COMMIT_ID_KEY: source_commit_id,
            "forked_from_public_at": now_iso(),
            "published_conversation": [
                turn.model_dump(mode="json")
                for turn in published_conversation
            ],
        }
    )

    return personal_lesson


def retain_public_package_as_personal_copy(
    workspace: WorkspaceState,
    source_package: CoursePackage,
    source_lessons: list[tuple[Lesson, str]],
) -> tuple[CoursePackage, Lesson]:
    if not source_lessons:
        raise ValueError("At least one public lesson is required")

    source_lesson_ids = [lesson.id for lesson, _commit_id in source_lessons]
    for package in workspace.packages[1:]:
        copies_by_source_id = {
            str(commit.metadata[PUBLIC_SOURCE_LESSON_ID_KEY]): lesson
            for lesson in package.lessons
            for commit in lesson.history_graph.commits
            if commit.metadata.get(PUBLIC_SOURCE_LESSON_ID_KEY) in source_lesson_ids
        }
        if all(source_id in copies_by_source_id for source_id in source_lesson_ids):
            lesson = copies_by_source_id[source_lesson_ids[0]]
            _activate_lesson(workspace, package, lesson)
            return package, lesson

    personal_package = CoursePackage(
        title=source_package.title,
        summary=source_package.summary,
        visibility="private",
        lessons=[],
    )
    source_to_personal_id: dict[str, str] = {}
    for source_lesson, source_commit_id in source_lessons:
        personal_lesson = _copy_public_lesson(
            source_lesson,
            source_commit_id=source_commit_id,
        )
        personal_package.lessons.append(personal_lesson)
        source_to_personal_id[source_lesson.id] = personal_lesson.id

    personal_package.course_graph = [
        CourseGraphEdge(
            source_lesson_id=source_to_personal_id[edge.source_lesson_id],
            target_lesson_id=source_to_personal_id[edge.target_lesson_id],
            relationship=edge.relationship,
        )
        for edge in source_package.course_graph
        if edge.source_lesson_id in source_to_personal_id
        and edge.target_lesson_id in source_to_personal_id
    ]

    workspace.packages.append(personal_package)
    first_lesson = personal_package.lessons[0]
    _activate_lesson(workspace, personal_package, first_lesson)
    return personal_package, first_lesson

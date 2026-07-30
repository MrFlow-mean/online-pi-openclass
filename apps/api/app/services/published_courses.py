from __future__ import annotations

from app.models import (
    BranchRef,
    CoursePackage,
    Lesson,
    LessonHistoryGraph,
    PublishedConversationTurn,
    PublishedCoursePackageVersion,
    PublishedLessonVersion,
)
from app.services.history import current_head_commit


_LEGACY_DISPLAYABLE_CHAT_COMMIT_KINDS = {
    "chat_flow",
    "board_section_teaching",
    "board_document_generation",
    "board_document_edit",
    "basic_chat",
    "learning_requirement_refinement",
    "board_task_requirement_refinement",
}


def _metadata_text(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) else ""


def _published_conversation_target_id(lesson: Lesson) -> str:
    head = current_head_commit(lesson)
    restored_commit_id = _metadata_text(head.metadata, "restored_commit_id")
    if head.metadata.get("kind") == "restore_snapshot" and restored_commit_id:
        return restored_commit_id
    return head.id


def _published_lineage_ids(lesson: Lesson) -> set[str]:
    commits_by_id = {commit.id: commit for commit in lesson.history_graph.commits}
    lineage: set[str] = set()
    commit_id = _published_conversation_target_id(lesson)
    while commit_id and commit_id not in lineage:
        lineage.add(commit_id)
        commit = commits_by_id.get(commit_id)
        commit_id = commit.parent_ids[0] if commit and commit.parent_ids else ""
    return lineage


def _commit_contains_public_conversation(metadata: dict[str, object]) -> bool:
    if _metadata_text(metadata, "chat_visibility") == "hidden":
        return False
    if _metadata_text(metadata, "requirement_phase") in {"ready", "frozen"}:
        return False
    history_node_kind = _metadata_text(metadata, "history_node_kind")
    if history_node_kind == "chat":
        return True
    if str(metadata.get("kind") or "") in _LEGACY_DISPLAYABLE_CHAT_COMMIT_KINDS:
        return True
    return not history_node_kind and bool(
        _metadata_text(metadata, "user_message")
        or _metadata_text(metadata, "assistant_message")
    )


def capture_published_conversation(lesson: Lesson) -> list[PublishedConversationTurn]:
    lineage_ids = _published_lineage_ids(lesson)
    conversation: list[PublishedConversationTurn] = []
    for commit in lesson.history_graph.commits:
        if commit.id not in lineage_ids:
            continue
        inherited_conversation = commit.metadata.get("published_conversation")
        if isinstance(inherited_conversation, list):
            for item in inherited_conversation:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if (
                    role in {"user", "assistant"}
                    and isinstance(content, str)
                    and content.strip()
                ):
                    conversation.append(
                        PublishedConversationTurn(role=role, content=content.strip())
                    )
        if not _commit_contains_public_conversation(commit.metadata):
            continue
        user_message = _metadata_text(commit.metadata, "user_message")
        assistant_message = _metadata_text(commit.metadata, "assistant_message")
        if user_message:
            conversation.append(PublishedConversationTurn(role="user", content=user_message))
        if assistant_message:
            conversation.append(
                PublishedConversationTurn(role="assistant", content=assistant_message)
            )
    return conversation


def capture_lesson_version(lesson: Lesson) -> PublishedLessonVersion:
    return PublishedLessonVersion(
        lesson_id=lesson.id,
        source_commit_id=current_head_commit(lesson).id,
        title=lesson.title,
        slug=lesson.slug,
        summary=lesson.summary,
        tags=list(lesson.tags),
        board_document=lesson.board_document.model_copy(deep=True),
        conversation=capture_published_conversation(lesson),
    )


def upload_lesson_version(lesson: Lesson) -> None:
    lesson.published_version = capture_lesson_version(lesson)
    lesson.visibility = "public"


def upload_package_version(package: CoursePackage) -> None:
    lesson_versions = [capture_lesson_version(lesson) for lesson in package.lessons]
    package.published_version = PublishedCoursePackageVersion(
        title=package.title,
        summary=package.summary,
        lessons=lesson_versions,
        course_graph=[edge.model_copy(deep=True) for edge in package.course_graph],
    )
    package.visibility = "public"


def published_lesson_copy(
    lesson: Lesson,
    version: PublishedLessonVersion | None = None,
) -> Lesson | None:
    snapshot = version or lesson.published_version
    if snapshot is None:
        return None
    source_commit = next(
        (commit for commit in lesson.history_graph.commits if commit.id == snapshot.source_commit_id),
        None,
    )
    if source_commit is None:
        return None
    public_commit = source_commit.model_copy(
        deep=True,
        update={"snapshot": snapshot.board_document.model_copy(deep=True)},
    )
    public_branch = BranchRef(
        name="main",
        head_commit_id=public_commit.id,
        base_commit_id=public_commit.id,
        created_at=public_commit.created_at,
    )
    return lesson.model_copy(
        deep=True,
        update={
            "title": snapshot.title,
            "slug": snapshot.slug,
            "summary": snapshot.summary,
            "tags": list(snapshot.tags),
            "board_document": snapshot.board_document.model_copy(deep=True),
            "published_version": snapshot.model_copy(deep=True),
            "history_graph": LessonHistoryGraph(
                branches={"main": public_branch},
                commits=[public_commit],
                current_branch="main",
            ),
            "updated_at": snapshot.published_at,
        },
    )


def published_package_copy(package: CoursePackage) -> CoursePackage | None:
    snapshot = package.published_version
    if snapshot is None:
        return None
    lessons_by_id = {lesson.id: lesson for lesson in package.lessons}
    public_lessons: list[Lesson] = []
    for lesson_version in snapshot.lessons:
        lesson = lessons_by_id.get(lesson_version.lesson_id)
        if lesson is None:
            return None
        public_lesson = published_lesson_copy(lesson, lesson_version)
        if public_lesson is None:
            return None
        public_lessons.append(public_lesson)
    return package.model_copy(
        deep=True,
        update={
            "title": snapshot.title,
            "summary": snapshot.summary,
            "lessons": public_lessons,
            "course_graph": [edge.model_copy(deep=True) for edge in snapshot.course_graph],
            "resources": [],
            "open_lesson_ids": [],
            "active_lesson_id": public_lessons[0].id if public_lessons else None,
            "workspace_tab_order": [lesson.id for lesson in public_lessons],
        },
    )

from __future__ import annotations

from app.models import (
    BranchRef,
    CoursePackage,
    Lesson,
    LessonHistoryGraph,
    PublishedCoursePackageVersion,
    PublishedLessonVersion,
)
from app.services.history import current_head_commit


def capture_lesson_version(lesson: Lesson) -> PublishedLessonVersion:
    return PublishedLessonVersion(
        lesson_id=lesson.id,
        source_commit_id=current_head_commit(lesson).id,
        title=lesson.title,
        slug=lesson.slug,
        summary=lesson.summary,
        tags=list(lesson.tags),
        board_document=lesson.board_document.model_copy(deep=True),
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

import pytest
from fastapi import HTTPException

from app.models import (
    BatchLessonActionRequest,
    CoursePackage,
    CreatePackageRequest,
    GenerateLessonRequest,
    UserView,
    WorkspaceState,
)
from app.routers import workspace as workspace_router
from app.services.lesson_factory import create_empty_lesson


def _user() -> UserView:
    return UserView(
        id="user_test",
        email="test@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_create_package_without_summary_keeps_summary_empty(monkeypatch) -> None:
    workspace = WorkspaceState(packages=[], active_package_id=None)
    saved_workspaces = []

    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda user_id: (workspace, 0),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda user_id, next_workspace, *, expected_revision: saved_workspaces.append(next_workspace),
    )

    workspace_router.create_package(CreatePackageRequest(title="空课程包", summary=""), user=_user())

    assert workspace.packages[0].summary == ""
    assert workspace.active_package_id == workspace.packages[0].id
    assert saved_workspaces == [workspace]


def test_generate_lesson_without_target_uses_standalone_pool(monkeypatch) -> None:
    standalone_package = CoursePackage(id="course_standalone", title="单独课程", summary="", lessons=[])
    package_lesson = create_empty_lesson("包内旧课")
    course_package = CoursePackage(
        id="course_package",
        title="课程包",
        summary="",
        lessons=[package_lesson],
        open_lesson_ids=[package_lesson.id],
        active_lesson_id=package_lesson.id,
        workspace_tab_order=[package_lesson.id],
    )
    workspace = WorkspaceState(packages=[standalone_package, course_package], active_package_id=course_package.id)
    saved_workspaces = []

    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda user_id: (workspace, 0),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda user_id, next_workspace, *, expected_revision: saved_workspaces.append(next_workspace),
    )

    response = workspace_router.generate_lesson(
        GenerateLessonRequest(topic="独立新课", start_blank=True),
        user=_user(),
    )

    assert [lesson.title for lesson in standalone_package.lessons] == ["独立新课"]
    assert [lesson.title for lesson in course_package.lessons] == ["包内旧课"]
    assert workspace.active_package_id == standalone_package.id
    assert response.is_standalone is True
    assert saved_workspaces == [workspace]


def test_generate_lesson_without_topic_creates_pending_untitled_page(monkeypatch) -> None:
    standalone_package = CoursePackage(id="course_standalone", title="单独课程", summary="", lessons=[])
    workspace = WorkspaceState(packages=[standalone_package], active_package_id=standalone_package.id)
    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda _user_id: (workspace, 0),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda *_args, **_kwargs: None,
    )

    response = workspace_router.generate_lesson(
        GenerateLessonRequest(timezone="Asia/Shanghai"),
        user=_user(),
    )

    lesson = standalone_package.lessons[0]
    assert lesson.title == "无标题"
    assert lesson.history_graph.commits[0].metadata["auto_title_pending"] is True
    assert lesson.history_graph.commits[0].metadata["auto_title_timezone"] == "Asia/Shanghai"
    assert response.active_lesson_id == lesson.id


def test_generate_lesson_with_target_keeps_course_package_content_isolated(monkeypatch) -> None:
    standalone_package = CoursePackage(id="course_standalone", title="单独课程", summary="", lessons=[])
    course_package = CoursePackage(id="course_package", title="课程包", summary="", lessons=[])
    workspace = WorkspaceState(packages=[standalone_package, course_package], active_package_id=standalone_package.id)
    saved_workspaces = []

    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda user_id: (workspace, 0),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda user_id, next_workspace, *, expected_revision: saved_workspaces.append(next_workspace),
    )

    response = workspace_router.generate_lesson(
        GenerateLessonRequest(topic="包内新课", target_package_id=course_package.id, start_blank=True),
        user=_user(),
    )

    assert standalone_package.lessons == []
    assert [lesson.title for lesson in course_package.lessons] == ["包内新课"]
    assert workspace.active_package_id == course_package.id
    assert response.is_standalone is False
    assert saved_workspaces == [workspace]


def test_generate_lesson_without_blank_flag_still_creates_codex_only_document(monkeypatch) -> None:
    package = CoursePackage(id="course", title="Course", summary="", lessons=[])
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda _user_id: (workspace, 0),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda *_args, **_kwargs: None,
    )

    workspace_router.generate_lesson(
        GenerateLessonRequest(topic="Codex document", start_blank=False),
        user=_user(),
    )

    lesson = package.lessons[0]
    assert lesson.board_document.content_text == ""
    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None


def test_batch_move_lessons_validates_then_saves_once(monkeypatch) -> None:
    first = create_empty_lesson("First")
    second = create_empty_lesson("Second")
    standalone = CoursePackage(
        id="standalone",
        title="Standalone",
        summary="",
        is_standalone=True,
        lessons=[first, second],
        open_lesson_ids=[first.id, second.id],
        active_lesson_id=first.id,
        workspace_tab_order=[first.id, second.id],
    )
    target = CoursePackage(id="target", title="Target", summary="", lessons=[])
    workspace = WorkspaceState(packages=[standalone, target], active_package_id=standalone.id)
    saved_workspaces = []
    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda _user_id: (workspace, 7),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda _user_id, next_workspace, *, expected_revision: saved_workspaces.append(
            (next_workspace, expected_revision)
        ),
    )

    workspace_router.batch_lessons(
        BatchLessonActionRequest(
            action="move",
            lesson_ids=[first.id, second.id, first.id],
            target_package_id=target.id,
        ),
        user=_user(),
    )

    assert standalone.lessons == []
    assert [lesson.id for lesson in target.lessons] == [first.id, second.id]
    assert target.workspace_tab_order == [first.id, second.id]
    assert saved_workspaces == [(workspace, 7)]


def test_batch_delete_rejects_missing_lesson_without_partial_mutation(monkeypatch) -> None:
    lesson = create_empty_lesson("Keep me")
    package = CoursePackage(id="package", title="Package", summary="", lessons=[lesson])
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    saved_workspaces = []
    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda _user_id: (workspace, 3),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda *_args, **_kwargs: saved_workspaces.append(workspace),
    )

    with pytest.raises(HTTPException) as error:
        workspace_router.batch_lessons(
            BatchLessonActionRequest(
                action="delete",
                lesson_ids=[lesson.id, "missing_lesson"],
            ),
            user=_user(),
        )

    assert error.value.status_code == 404
    assert [current.id for current in package.lessons] == [lesson.id]
    assert saved_workspaces == []


def test_batch_delete_removes_selected_lessons_and_normalizes_package(monkeypatch) -> None:
    first = create_empty_lesson("First")
    second = create_empty_lesson("Second")
    remaining = create_empty_lesson("Remaining")
    package = CoursePackage(
        id="package",
        title="Package",
        summary="",
        lessons=[first, second, remaining],
        open_lesson_ids=[first.id, second.id, remaining.id],
        active_lesson_id=second.id,
        workspace_tab_order=[first.id, second.id, remaining.id],
    )
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    saved_revisions = []
    monkeypatch.setattr(
        workspace_router,
        "load_workspace_for_user_with_revision",
        lambda _user_id: (workspace, 5),
    )
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user_if_revision",
        lambda _user_id, _workspace, *, expected_revision: saved_revisions.append(expected_revision),
    )

    workspace_router.batch_lessons(
        BatchLessonActionRequest(action="delete", lesson_ids=[first.id, second.id]),
        user=_user(),
    )

    assert [lesson.id for lesson in package.lessons] == [remaining.id]
    assert package.open_lesson_ids == [remaining.id]
    assert package.workspace_tab_order == [remaining.id]
    assert package.active_lesson_id == remaining.id
    assert saved_revisions == [5]

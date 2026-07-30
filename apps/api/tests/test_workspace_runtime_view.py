from __future__ import annotations

from app.models import BoardTaskRequirementSheet
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


def test_nonempty_lesson_view_restores_active_board_task_from_head_runtime() -> None:
    lesson = create_empty_lesson("Runtime view")
    lesson.board_document = build_document(
        title="Runtime view",
        content_text="# Existing board\n\nBounded content.",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    task = BoardTaskRequirementSheet(
        requested_action="interact",
        target_hint="bounded content",
        question_or_topic="continue the active interaction",
        special_interaction_requirements="follow the saved learner-defined rule",
        content_extent="paragraph",
        topic_relation="current_document",
        document_destination="current_lesson",
        interaction_session={"session_id": "interaction_restore"},
    )
    lesson.board_task_requirements = task
    commit_operations(
        lesson,
        operations=[],
        label="Active interaction",
        message="Persist the active board task runtime.",
        metadata={"kind": "board_task_requirement_refinement"},
    )
    lesson.board_task_requirements = None

    view = workspace_state.lesson_view(lesson)

    assert view.board_task_requirements is not None
    assert view.board_task_requirements.task_id == task.task_id
    assert view.board_task_requirements.interaction_session == {
        "session_id": "interaction_restore"
    }


def test_empty_lesson_view_does_not_expose_a_stale_board_task() -> None:
    lesson = create_empty_lesson("Empty runtime view")
    lesson.board_task_requirements = BoardTaskRequirementSheet(
        requested_action="write",
        question_or_topic="stale task",
    )
    commit_operations(
        lesson,
        operations=[],
        label="Stale task",
        message="Capture a task on an empty lesson for isolation testing.",
        metadata={"kind": "board_task_requirement_refinement"},
    )
    lesson.board_task_requirements = None

    view = workspace_state.lesson_view(lesson)

    assert view.board_task_requirements is None

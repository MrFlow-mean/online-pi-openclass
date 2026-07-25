from __future__ import annotations

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    WorkspaceState,
)
from app.services.ai_execution_adapter import StructuredExecutionResult
from app.services.course_store import SqliteCourseStore
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import (
    UNTITLED_LESSON_TITLE,
    build_requirements,
    create_empty_lesson,
    create_untitled_lesson,
)
from app.services import lesson_title, workspace_state


class _TitleAdapter:
    def __init__(self, title: str) -> None:
        self.title = title

    def parse_structured(self, **_kwargs) -> StructuredExecutionResult:
        return StructuredExecutionResult(output_parsed={"title": self.title})


def _response(workspace: WorkspaceState, package: CoursePackage, lesson_id: str) -> ChatResponse:
    lesson = next(item for item in package.lessons if item.id == lesson_id)
    return ChatResponse(
        chatbot_message="我们先从日常问候开始。",
        learning_requirement_sheet=build_requirements(lesson.title),
        learning_clarification=LearningClarificationStatus(
            progress=40,
            label="已了解方向",
            reason="已经开始对话。",
        ),
        board_decision=BoardDecision(action="no_change", reason="No board change."),
        course_package=workspace_state.package_view_for_lesson(
            workspace,
            package,
            lesson_id,
        ),
    )


def _workspace_with_untitled_lesson() -> tuple[WorkspaceState, CoursePackage, object]:
    lesson = create_untitled_lesson(timezone_name="Asia/Shanghai")
    commit_operations(
        lesson,
        [],
        label="Pi conversation",
        message="Pi completed the user turn.",
        metadata={
            "kind": "basic_chat",
            "user_message": "我想练习法语日常对话",
            "assistant_message": "我们先从日常问候开始。",
        },
    )
    package = CoursePackage(
        title="课程工作台",
        summary="",
        lessons=[lesson],
        open_lesson_ids=[lesson.id],
        active_lesson_id=lesson.id,
        workspace_tab_order=[lesson.id],
    )
    return WorkspaceState(packages=[package], active_package_id=package.id), package, lesson


def test_only_explicit_untitled_lessons_are_pending_for_auto_title() -> None:
    pending = create_untitled_lesson()
    named = create_empty_lesson(UNTITLED_LESSON_TITLE)

    assert lesson_title.lesson_has_pending_auto_title(pending)
    assert not lesson_title.lesson_has_pending_auto_title(named)


def test_duplicate_titles_receive_created_time_and_ordinal() -> None:
    title = lesson_title.disambiguated_lesson_title(
        "法语日常对话",
        [
            "法语日常对话",
            "法语日常对话 2026-07-24 09:10（第2个）",
            "其他学习主题",
        ],
        created_at="2026-07-24T14:30:00+00:00",
        timezone_name="Asia/Shanghai",
    )

    assert title == "法语日常对话 2026-07-24 22:30（第3个）"


def test_successful_chat_generates_and_persists_title(monkeypatch, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        lesson_title,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: _TitleAdapter("《法语日常对话》"),
    )
    monkeypatch.setattr(lesson_title.ai_usage_logger, "log_event", lambda *_args, **_kwargs: {})
    workspace, package, lesson = _workspace_with_untitled_lesson()
    store.save_for_user("user_title", workspace)

    response = lesson_title.maybe_generate_lesson_title(
        lesson.id,
        ChatRequest(message="我想练习法语日常对话"),
        _response(workspace, package, lesson.id),
        user_id="user_title",
    )

    returned_lesson = next(item for item in response.course_package.lessons if item.id == lesson.id)
    persisted_workspace = store.load_for_user("user_title")
    _persisted_package, persisted_lesson = workspace_state.find_lesson_package(
        persisted_workspace,
        lesson.id,
    )
    assert returned_lesson.title == "法语日常对话"
    assert persisted_lesson.title == "法语日常对话"
    assert persisted_lesson.board_document.title == "法语日常对话"
    assert current_head_commit(persisted_lesson).snapshot.title == "法语日常对话"
    assert current_head_commit(persisted_lesson).metadata["auto_title_generated"] is True


def test_title_generation_failure_keeps_successful_chat_response(monkeypatch, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        lesson_title,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("title model unavailable")),
    )
    monkeypatch.setattr(lesson_title.ai_usage_logger, "log_event", lambda *_args, **_kwargs: {})
    workspace, package, lesson = _workspace_with_untitled_lesson()
    store.save_for_user("user_title", workspace)
    original_response = _response(workspace, package, lesson.id)

    response = lesson_title.maybe_generate_lesson_title(
        lesson.id,
        ChatRequest(message="继续学习"),
        original_response,
        user_id="user_title",
    )

    assert response is original_response
    assert response.chatbot_message == "我们先从日常问候开始。"
    assert response.course_package.lessons[0].title == UNTITLED_LESSON_TITLE

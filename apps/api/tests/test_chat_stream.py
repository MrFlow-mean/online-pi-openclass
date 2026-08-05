import json
import threading
import time

from app.models import (
    AgentActivityEvent,
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    WorkspaceState,
)
from app.routers import chat as chat_router
from app.services.ai_logging import ai_log_context
from app.services.codex_app_server import CodexTurnCancelledError
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import package_view_for_lesson


def _chat_response(
    lesson_id: str = "lesson_stream_test",
    *,
    chatbot_message: str = "已经完成。",
    document_text: str = "",
    board_document_operation_status: str = "none",
) -> ChatResponse:
    lesson = create_empty_lesson("流式回合")
    lesson.id = lesson_id
    lesson.board_document.content_text = document_text
    requirements = build_requirements(lesson.title)
    package = CoursePackage(
        id="course_stream_test",
        title="课程包",
        summary="",
        lessons=[lesson],
        open_lesson_ids=[lesson.id],
        active_lesson_id=lesson.id,
        workspace_tab_order=[lesson.id],
    )
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        learning_clarification=LearningClarificationStatus(
            progress=100,
            label="已完成",
            reason="测试回合已完成。",
        ),
        board_decision=BoardDecision(action="no_change", reason="测试不修改板书。"),
        board_document_operation_status=board_document_operation_status,
        course_package=package_view_for_lesson(workspace, package, lesson.id),
    )


def _parse_sse(block: str) -> tuple[str, dict]:
    event = "message"
    data_lines: list[str] = []
    for line in block.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    return event, json.loads("\n".join(data_lines))


def _collect_events(stream) -> list[tuple[str, dict]]:
    return [_parse_sse(block) for block in stream]


def _joined_delta(events: list[tuple[str, dict]], event_name: str) -> str:
    return "".join(str(payload.get("delta") or "") for event, payload in events if event == event_name)


def test_chat_stream_emits_heartbeat_before_final(monkeypatch) -> None:
    logged_events: list[dict] = []

    def slow_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        time.sleep(0.03)
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "CHAT_STREAM_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(chat_router, "process_chat_on_lesson", slow_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "heartbeat" in event_names
    assert event_names[-1] == "final"
    assert [event["stream_event"] for event in logged_events] == [
        "stream_started",
        "process_chat_returned",
        "first_chat_delta_sent",
        "stream_final_sent",
    ]
    lifecycle_elapsed = [
        event["elapsed_ms"]
        for event in logged_events
        if event["stream_event"] in {"stream_started", "process_chat_returned", "stream_final_sent"}
    ]
    assert lifecycle_elapsed == sorted(lifecycle_elapsed)
    first_chat_delta = next(event for event in logged_events if event["stream_event"] == "first_chat_delta_sent")
    assert first_chat_delta["role"] == "codex"
    assert first_chat_delta["field"] == "agent_message"
    assert logged_events[-1]["produced_commit_id"] is not None


def test_chat_stream_emits_live_codex_delta_without_replaying_final_message(monkeypatch) -> None:
    logged_events: list[dict] = []

    def process_with_codex_stream(*args, **kwargs) -> ChatResponse:
        kwargs["on_delta"]("这是 Codex 的实时回复。")
        return _chat_response("lesson_stream_test", chatbot_message="这是 Codex 的实时回复。")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_codex_stream)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="我想学一个宽泛主题"),
            user_id="user_stream_test",
        )
    )

    assert _joined_delta(events, "chat_delta") == "这是 Codex 的实时回复。"
    assert [event for event, _payload in events].count("final") == 1
    first_chat_delta_events = [
        event for event in logged_events if event["stream_event"] == "first_chat_delta_sent"
    ]
    assert len(first_chat_delta_events) == 1
    assert first_chat_delta_events[0]["role"] == "codex"
    assert first_chat_delta_events[0]["field"] == "agent_message"


def test_chat_stream_synthesizes_final_chatbot_message_as_delta(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response("lesson_stream_test", chatbot_message="最终回复也要流式出现。"),
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="随便聊聊"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert _joined_delta(events, "chat_delta") == "最终回复也要流式出现。"
    assert event_names.index("chat_delta") < event_names.index("final")


def test_chat_stream_emits_agent_activity_before_final(monkeypatch) -> None:
    def process_with_agent_activity(*args, **kwargs) -> ChatResponse:
        response = _chat_response("lesson_stream_test", chatbot_message="最终回复。")
        response.agent_activity = [
            AgentActivityEvent(
                turn_id="agentturn_test",
                stage="turn_decision",
                label="判断任务类型",
                role="codex",
            )
        ]
        return response

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_agent_activity)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="随便聊聊"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    activity_payload = next(payload for event, payload in events if event == "agent_activity")
    assert activity_payload["stage"] == "turn_decision"
    assert activity_payload["label"] == "判断任务类型"
    assert event_names.index("agent_activity") < event_names.index("final")


def test_chat_stream_emits_live_activity_updates_and_avoids_duplicate_final_event(monkeypatch) -> None:
    running = AgentActivityEvent(
        id="activity_live",
        turn_id="agentturn_live",
        stage="execute_role",
        label="运行命令",
        status="running",
        role="OpenClass tool",
        metadata={"kind": "commandExecution", "detail": "partial"},
    )
    completed = running.model_copy(
        update={
            "label": "命令执行完成",
            "status": "completed",
            "metadata": {"kind": "commandExecution", "detail": "complete"},
        }
    )

    def process_with_live_activity(*args, **kwargs) -> ChatResponse:
        kwargs["on_agent_activity"](running)
        kwargs["on_agent_activity"](completed)
        response = _chat_response("lesson_stream_test", chatbot_message="最终回复。")
        response.agent_activity = [completed]
        return response

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_live_activity)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="执行并说明过程"),
            user_id="user_stream_test",
        )
    )

    activity_payloads = [payload for event, payload in events if event == "agent_activity"]
    assert [payload["status"] for payload in activity_payloads] == ["running", "completed"]
    assert activity_payloads[-1]["metadata"]["detail"] == "complete"
    assert [event for event, _payload in events].index("agent_activity") < [
        event for event, _payload in events
    ].index("final")


def test_chat_stream_routes_board_task_updates_to_their_own_event(monkeypatch) -> None:
    def process_with_structured_updates(*args, **kwargs) -> ChatResponse:
        kwargs["on_requirement_update"]({"requirement_phase": "collecting"})
        kwargs["on_requirement_update"](
            {
                "board_task_sheet": {"requested_action": "explain"},
                "board_task_phase": "ready",
            }
        )
        return _chat_response("lesson_stream_test", chatbot_message="请确认目标。")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_structured_updates)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="讲解这一段"),
            user_id="user_stream_test",
        )
    )

    requirement_payload = next(
        payload for event, payload in events if event == "requirement_update"
    )
    board_task_payload = next(
        payload for event, payload in events if event == "board_task_update"
    )
    assert requirement_payload["requirement_phase"] == "collecting"
    assert board_task_payload["board_task_phase"] == "ready"


def test_chat_stream_preserves_live_model_delta_chunks(monkeypatch) -> None:
    sleep_calls: list[float] = []

    def process_with_model_chunks(*args, **kwargs) -> ChatResponse:
        kwargs["on_delta"]("真正的")
        kwargs["on_delta"]("流式输出")
        return _chat_response("lesson_stream_test", chatbot_message="真正的流式输出")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_model_chunks)
    monkeypatch.setattr(chat_router.time, "sleep", sleep_calls.append)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="随便聊聊"),
            user_id="user_stream_test",
        )
    )

    chat_deltas = [payload["delta"] for event, payload in events if event == "chat_delta"]
    assert chat_deltas == ["真正的", "流式输出"]
    assert _joined_delta(events, "chat_delta") == "真正的流式输出"
    assert sleep_calls == []


def test_chat_stream_synthesizes_document_delta_for_succeeded_board_operation(monkeypatch) -> None:
    logged_events: list[dict] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response(
            "lesson_stream_test",
            chatbot_message="板书已生成。",
            document_text="# 新板书\n\n这里是生成后的板书内容。",
            board_document_operation_status="succeeded",
        ),
    )
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )
    monkeypatch.setattr(chat_router, "CHAT_STREAM_DOCUMENT_DELTA_CHARS", 4)
    monkeypatch.setattr(chat_router, "CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(chat_router.time, "sleep", sleep_calls.append)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="生成板书"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    document_deltas = [payload["delta"] for event, payload in events if event == "document_delta"]
    assert _joined_delta(events, "document_delta") == "# 新板书\n\n这里是生成后的板书内容。"
    assert document_deltas == [
        "# 新板",
        "书\n\n这",
        "里是生成",
        "后的板书",
        "内容。",
    ]
    assert sleep_calls == [0.02] * len(document_deltas)
    assert event_names.index("document_delta") < event_names.index("final")
    first_document_delta_events = [
        event for event in logged_events if event["stream_event"] == "first_document_delta_sent"
    ]
    assert len(first_document_delta_events) == 1
    assert first_document_delta_events[0]["role"] == "codex"
    assert first_document_delta_events[0]["field"] == "board.md"


def test_chat_stream_emits_only_final_validated_board_document(monkeypatch) -> None:
    def process_with_final_board(*args, **kwargs) -> ChatResponse:
        return _chat_response(
            "lesson_stream_test",
            chatbot_message="",
            document_text="# 最终保存版\n\n这里只显示聊天处理完成后的板书。",
            board_document_operation_status="succeeded",
        )

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_final_board)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="开始生成板书", board_generation_action="start"),
            user_id="user_stream_test",
        )
    )

    document_text = _joined_delta(events, "document_delta")
    assert document_text == "# 最终保存版\n\n这里只显示聊天处理完成后的板书。"
    assert "中间草稿" not in document_text


def test_chat_stream_emits_persisted_board_before_chat_processing_returns(monkeypatch) -> None:
    allow_process_return = threading.Event()
    document_text = "# 已提交板书\n\n自动讲解仍在运行。"

    def process_after_board_commit(*args, **kwargs) -> ChatResponse:
        kwargs["on_document_commit"](document_text, "commit_board_saved")
        assert allow_process_return.wait(timeout=1)
        return _chat_response(
            "lesson_stream_test",
            chatbot_message="第一节讲解完成。",
            document_text=document_text,
            board_document_operation_status="succeeded",
        )

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_after_board_commit)
    monkeypatch.setattr(chat_router, "CHAT_STREAM_DOCUMENT_DELTA_CHARS", len(document_text))
    monkeypatch.setattr(chat_router, "CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS", 0)

    stream = chat_router._chat_stream_events(
        "lesson_stream_test",
        ChatRequest(message="生成后立即讲解", board_generation_action="start"),
        user_id="user_stream_test",
    )
    first_event = _parse_sse(next(stream))
    committed_event = _parse_sse(next(stream))

    assert first_event[0] == "phase"
    assert committed_event == ("document_delta", {"delta": document_text})

    allow_process_return.set()
    remaining_events = [_parse_sse(block) for block in stream]
    all_events = [first_event, committed_event, *remaining_events]
    assert _joined_delta(all_events, "document_delta") == document_text
    assert [event for event, _payload in all_events].count("document_delta") == 1
    assert [event for event, _payload in all_events].index("document_delta") < [
        event for event, _payload in all_events
    ].index("final")


def test_chat_stream_does_not_synthesize_chat_delta_for_silent_board_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response(
            "lesson_stream_test",
            chatbot_message="",
            document_text="# 新板书\n\n## 1. 可定位小节\n\n这里是生成后的板书内容。",
            board_document_operation_status="succeeded",
        ),
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="开始生成板书", board_generation_action="start"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "chat_delta" not in event_names
    assert _joined_delta(events, "document_delta") == "# 新板书\n\n## 1. 可定位小节\n\n这里是生成后的板书内容。"
    assert event_names.index("document_delta") < event_names.index("final")


def test_chat_stream_worker_error_emits_error_and_lifecycle_log(monkeypatch) -> None:
    logged_events: list[dict] = []

    def failing_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", failing_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "final" not in event_names
    error_payload = next(payload for event, payload in events if event == "error")
    assert error_payload["message"] == "model unavailable"
    assert error_payload["trace_id"].startswith("chat_")
    assert [event["stream_event"] for event in logged_events] == ["stream_started", "stream_error"]
    assert logged_events[-1]["error_message"] == "model unavailable"


def test_chat_stream_logs_disconnect_before_final(monkeypatch) -> None:
    logged_events: list[dict] = []

    def slow_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        time.sleep(0.05)
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", slow_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    stream = chat_router._chat_stream_events(
        "lesson_stream_test",
        ChatRequest(message="帮我继续"),
        user_id="user_stream_test",
    )
    next(stream)
    stream.close()

    assert "stream_disconnected_background_continues" in [
        event["stream_event"] for event in logged_events
    ]


def test_chat_stream_continues_worker_after_disconnect(monkeypatch) -> None:
    logged_events: list[dict] = []
    allow_model_delta = threading.Event()
    worker_finished = threading.Event()

    def cancellable_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        allow_model_delta.wait(timeout=1)
        assert not kwargs["is_cancelled"]()
        kwargs["on_delta"]("不会继续输出")
        worker_finished.set()
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", cancellable_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    stream = chat_router._chat_stream_events(
        "lesson_stream_test",
        ChatRequest(message="帮我继续"),
        user_id="user_stream_test",
    )
    next(stream)
    stream.close()
    allow_model_delta.set()

    assert worker_finished.wait(timeout=1)
    deadline = time.monotonic() + 1
    while "background_process_completed" not in [
        event.get("stream_event") for event in logged_events
    ]:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    stream_events = [event["stream_event"] for event in logged_events]
    assert "stream_disconnected_background_continues" in stream_events
    assert "process_chat_returned" in stream_events
    assert "background_process_completed" in stream_events
    assert "stream_final_sent" not in stream_events
    assert "stream_cancelled" not in stream_events


def test_chat_stream_explicit_cancel_stops_worker(monkeypatch) -> None:
    logged_events: list[dict] = []
    worker_started = threading.Event()
    cancellation_seen = threading.Event()

    def cancellable_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        worker_started.set()
        while not kwargs["is_cancelled"]():
            time.sleep(0.001)
        cancellation_seen.set()
        raise CodexTurnCancelledError("cancelled")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", cancellable_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    request = ChatRequest(
        message="停止这个回合",
        session_id="session_explicit_cancel",
        input_event_id="input_explicit_cancel",
    )
    stream = chat_router._chat_stream_events(
        "lesson_stream_test",
        request,
        user_id="user_stream_test",
    )
    next(stream)
    assert worker_started.wait(timeout=1)
    assert chat_router.chat_stream_cancellation_registry.cancel(
        user_id="user_stream_test",
        lesson_id="lesson_stream_test",
        session_id=request.session_id or "",
        input_event_id=request.input_event_id or "",
    )
    stream.close()

    assert cancellation_seen.wait(timeout=1)
    deadline = time.monotonic() + 1
    while "stream_cancelled" not in [event.get("stream_event") for event in logged_events]:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    stream_events = [event["stream_event"] for event in logged_events]
    assert "stream_cancelled" in stream_events
    assert "stream_final_sent" not in stream_events


def test_route_context_reuses_outer_stream_trace() -> None:
    with ai_log_context(trace_id="chat_outer_trace"):
        with bind_ai_request_context("/api/example", trace_prefix="chat") as context:
            assert context["trace_id"] == "chat_outer_trace"

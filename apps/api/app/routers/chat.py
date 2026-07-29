from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from time import perf_counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.models import AgentActivityEvent, ChatRequest, ChatResponse, UserView, new_id, now_iso
from app.routers.auth import current_user
from app.services.ai_logging import ai_log_context, ai_usage_logger
from app.services.chat_service import process_chat_on_lesson
from app.services.codex_app_server import CodexTurnCancelledError

router = APIRouter()
CHAT_STREAM_HEARTBEAT_SECONDS = 10.0
CHAT_STREAM_DOCUMENT_DELTA_CHARS = 8
CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS = 0.012


@dataclass
class ChatStreamState:
    trace_id: str
    lesson_id: str
    user_id: str
    user_message_excerpt: str
    started_at: float = field(default_factory=perf_counter)
    last_phase: str = "request"
    first_chat_delta_ms: int | None = None
    first_document_delta_ms: int | None = None
    process_returned_ms: int | None = None
    final_enqueued: bool = False
    final_yielded: bool = False
    error_enqueued: bool = False
    produced_commit_id: str | None = None


def _message_excerpt(message: str, limit: int = 180) -> str:
    compact = " ".join(message.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _head_commit_id(response: ChatResponse, lesson_id: str) -> str | None:
    lesson = next((item for item in response.course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return None
    branch = lesson.history_graph.branches.get(lesson.history_graph.current_branch)
    if branch is not None:
        return branch.head_commit_id
    if lesson.history_graph.commits:
        return lesson.history_graph.commits[-1].id
    return None


def _lesson_document_text(response: ChatResponse, lesson_id: str) -> str:
    lesson = next((item for item in response.course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return ""
    return lesson.board_document.content_text or ""


def _log_stream_lifecycle(state: ChatStreamState, event: str, **payload: object) -> None:
    ai_usage_logger.log_event(
        "chat_stream_lifecycle",
        stream_event=event,
        trace_id=state.trace_id,
        lesson_id=state.lesson_id,
        user_id=state.user_id,
        user_message_excerpt=state.user_message_excerpt,
        last_phase=state.last_phase,
        final_enqueued=state.final_enqueued,
        final_yielded=state.final_yielded,
        error_enqueued=state.error_enqueued,
        produced_commit_id=state.produced_commit_id,
        **payload,
    )


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _elapsed_ms_since(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _document_delta_chunks(document_text: str) -> Iterator[str]:
    chunk_size = max(1, CHAT_STREAM_DOCUMENT_DELTA_CHARS)
    for start in range(0, len(document_text), chunk_size):
        yield document_text[start : start + chunk_size]


def _chat_stream_events(lesson_id: str, request: ChatRequest, *, user_id: str) -> Iterator[str]:
    events: queue.Queue[tuple[str, object] | None] = queue.Queue()
    cancel_event = threading.Event()
    state = ChatStreamState(
        trace_id=new_id("chat"),
        lesson_id=lesson_id,
        user_id=user_id,
        user_message_excerpt=_message_excerpt(request.message),
    )
    chat_delta_emitted = False
    document_delta_emitted = False
    emitted_activity_payloads: dict[str, str] = {}

    def emit(event: str, data: object) -> None:
        events.put((event, data))

    def log_first_delta_once(
        *,
        metric: str,
        role: str,
        field: str,
    ) -> None:
        if metric == "chat" and state.first_chat_delta_ms is None:
            state.first_chat_delta_ms = _elapsed_ms_since(state.started_at)
            _log_stream_lifecycle(
                state,
                "first_chat_delta_sent",
                elapsed_ms=state.first_chat_delta_ms,
                role=role,
                field=field,
            )
        elif metric == "document" and state.first_document_delta_ms is None:
            state.first_document_delta_ms = _elapsed_ms_since(state.started_at)
            _log_stream_lifecycle(
                state,
                "first_document_delta_sent",
                elapsed_ms=state.first_document_delta_ms,
                role=role,
                field=field,
            )

    def emit_codex_delta(delta: str) -> None:
        nonlocal chat_delta_emitted
        if not delta:
            return
        state.last_phase = "codex"
        log_first_delta_once(metric="chat", role="codex", field="agent_message")
        emit("chat_delta", {"delta": delta})
        chat_delta_emitted = True

    def emit_requirement_update(payload: dict[str, object]) -> None:
        state.last_phase = "learning_requirement"
        emit("requirement_update", payload)

    def emit_agent_activity_event(event: AgentActivityEvent) -> None:
        state.last_phase = "codex_activity"
        payload = event.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if emitted_activity_payloads.get(event.id) == serialized:
            return
        emitted_activity_payloads[event.id] = serialized
        emit("agent_activity", payload)

    def emit_missing_visible_deltas(response: ChatResponse) -> None:
        nonlocal chat_delta_emitted, document_delta_emitted
        if not chat_delta_emitted and response.chatbot_message:
            log_first_delta_once(metric="chat", role="codex", field="agent_message")
            emit("chat_delta", {"delta": response.chatbot_message})
            chat_delta_emitted = True
        if (
            not document_delta_emitted
            and response.board_document_operation_status == "succeeded"
        ):
            document_text = _lesson_document_text(response, lesson_id)
            if document_text:
                log_first_delta_once(metric="document", role="codex", field="board.md")
                for delta in _document_delta_chunks(document_text):
                    emit("document_delta", {"delta": delta})
                document_delta_emitted = True

    def emit_agent_activity(response: ChatResponse) -> None:
        for event in response.agent_activity:
            emit_agent_activity_event(event)

    def run() -> None:
        with ai_log_context(
            trace_id=state.trace_id,
            route="/api/lessons/{lesson_id}/chat/stream",
            lesson_id=lesson_id,
            user_id=user_id,
        ):
            _log_stream_lifecycle(state, "stream_started", elapsed_ms=0)
            try:
                emit("phase", {"label": "正在准备回复", "role": "request"})
                response = process_chat_on_lesson(
                    lesson_id,
                    request,
                    user_id=user_id,
                    on_delta=emit_codex_delta,
                    on_requirement_update=emit_requirement_update,
                    on_agent_activity=emit_agent_activity_event,
                    is_cancelled=cancel_event.is_set,
                )
                state.process_returned_ms = _elapsed_ms_since(state.started_at)
                _log_stream_lifecycle(
                    state,
                    "process_chat_returned",
                    elapsed_ms=state.process_returned_ms,
                )
                state.produced_commit_id = _head_commit_id(response, lesson_id)
                emit_missing_visible_deltas(response)
                emit_agent_activity(response)
                state.final_enqueued = True
                emit("final", response.model_dump(mode="json"))
                _log_stream_lifecycle(
                    state,
                    "stream_final_sent",
                    elapsed_ms=_elapsed_ms_since(state.started_at),
                )
            except CodexTurnCancelledError:
                _log_stream_lifecycle(
                    state,
                    "stream_cancelled",
                    elapsed_ms=_elapsed_ms_since(state.started_at),
                )
            except Exception as exc:  # pragma: no cover - route safety net
                state.error_enqueued = True
                emit("error", {"message": str(exc), "trace_id": state.trace_id})
                _log_stream_lifecycle(
                    state,
                    "stream_error",
                    elapsed_ms=_elapsed_ms_since(state.started_at),
                    error_message=str(exc),
                )
            finally:
                events.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        while True:
            try:
                item = events.get(timeout=CHAT_STREAM_HEARTBEAT_SECONDS)
            except queue.Empty:
                yield _sse_event("heartbeat", {"trace_id": state.trace_id, "ts": now_iso()})
                continue
            if item is None:
                break
            event, data = item
            if event == "final":
                state.final_yielded = True
            yield _sse_event(event, data)
            if event == "document_delta" and CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS > 0:
                time.sleep(CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS)
    finally:
        if not state.final_yielded and not state.error_enqueued:
            cancel_event.set()
            _log_stream_lifecycle(
                state,
                "stream_disconnected_or_no_final",
                elapsed_ms=_elapsed_ms_since(state.started_at),
            )


@router.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    user: UserView = Depends(current_user),
) -> ChatResponse:
    return process_chat_on_lesson(lesson_id, request, user_id=user.id)


@router.post("/api/lessons/{lesson_id}/chat/stream")
def stream_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    user: UserView = Depends(current_user),
) -> StreamingResponse:
    return StreamingResponse(
        _chat_stream_events(lesson_id, request, user_id=user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock

from app.models import (
    AgentActivityEvent,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    DecisionTrace,
    SelectionRef,
)
from app.services import workspace_state
from app.services.chat_turn_gate import complete_non_learning_turn, evaluate_turn_gate
from app.services.codex_chat import process_codex_chat_on_lesson
from app.services.existing_board.interaction_workflow import (
    ExistingBoardInteractionReroute,
)
from app.services.existing_board.workflow import process_existing_board_workflow
from app.services.history import bind_commit_metadata
from app.services.lesson_title import maybe_generate_lesson_title

_IDEMPOTENCY_CACHE_LIMIT = 256
_idempotency_lock = Lock()
_idempotency_cache: OrderedDict[
    tuple[str, str, str],
    tuple[str, ChatResponse],
] = OrderedDict()
_idempotency_inflight: dict[
    tuple[str, str, str],
    tuple[str, Future[ChatResponse]],
] = {}


def process_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    on_delta: Callable[[str], None] | None = None,
    on_requirement_update: Callable[[dict[str, object]], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    commit_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    idempotency_key = _chat_idempotency_key(request, user_id=user_id)
    if idempotency_key is None:
        return _process_chat_on_lesson_once(
            lesson_id,
            request,
            user_id=user_id,
            on_delta=on_delta,
            on_requirement_update=on_requirement_update,
            on_agent_activity=on_agent_activity,
            is_cancelled=is_cancelled,
            commit_metadata=commit_metadata,
        )
    fingerprint = _chat_request_fingerprint(lesson_id, request)
    with _idempotency_lock:
        cached = _idempotency_cache.get(idempotency_key)
        if cached is not None:
            cached_fingerprint, cached_response = cached
            _require_matching_fingerprint(cached_fingerprint, fingerprint)
            _idempotency_cache.move_to_end(idempotency_key)
            return _copy_response(cached_response)
        existing = _idempotency_inflight.get(idempotency_key)
        if existing is None:
            future: Future[ChatResponse] = Future()
            _idempotency_inflight[idempotency_key] = (fingerprint, future)
            owns_execution = True
        else:
            inflight_fingerprint, future = existing
            _require_matching_fingerprint(inflight_fingerprint, fingerprint)
            owns_execution = False
    if not owns_execution:
        return _copy_response(future.result())
    try:
        response = _process_chat_on_lesson_once(
            lesson_id,
            request,
            user_id=user_id,
            on_delta=on_delta,
            on_requirement_update=on_requirement_update,
            on_agent_activity=on_agent_activity,
            is_cancelled=is_cancelled,
            commit_metadata={
                **(commit_metadata or {}),
                **_chat_idempotency_metadata(request),
            },
        )
    except BaseException as exc:
        with _idempotency_lock:
            _idempotency_inflight.pop(idempotency_key, None)
            future.set_exception(exc)
        raise
    frozen_response = _copy_response(response)
    with _idempotency_lock:
        _idempotency_inflight.pop(idempotency_key, None)
        _idempotency_cache[idempotency_key] = (fingerprint, frozen_response)
        _idempotency_cache.move_to_end(idempotency_key)
        while len(_idempotency_cache) > _IDEMPOTENCY_CACHE_LIMIT:
            _idempotency_cache.popitem(last=False)
        future.set_result(frozen_response)
    return _copy_response(frozen_response)


def _process_chat_on_lesson_once(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    on_delta: Callable[[str], None] | None = None,
    on_requirement_update: Callable[[dict[str, object]], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    commit_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    gate = evaluate_turn_gate(
        request,
        lesson_id=lesson_id,
        user_id=user_id,
        on_agent_activity=on_agent_activity,
    )
    with bind_commit_metadata({**(commit_metadata or {}), **_chat_edit_metadata(request)}):
        if gate.decision.intent in {"ordinary_chat", "unclear"}:
            return complete_non_learning_turn(
                lesson_id,
                request,
                gate,
                user_id=user_id,
                on_delta=on_delta,
                on_agent_activity=on_agent_activity,
                is_cancelled=is_cancelled,
            )
        if _should_use_bounded_existing_board_workflow(
            lesson_id,
            request,
            user_id=user_id,
        ):
            try:
                response = process_existing_board_workflow(
                    lesson_id,
                    request,
                    user_id=user_id,
                    adapter=gate.adapter,
                    selected_model=gate.envelope.selected_model,
                    on_delta=on_delta,
                    on_board_task_update=on_requirement_update,
                    on_agent_activity=on_agent_activity,
                    is_cancelled=is_cancelled,
                )
            except ExistingBoardInteractionReroute as reroute:
                return _process_chat_on_lesson_once(
                    lesson_id,
                    reroute.request,
                    user_id=user_id,
                    on_delta=on_delta,
                    on_requirement_update=on_requirement_update,
                    on_agent_activity=on_agent_activity,
                    is_cancelled=is_cancelled,
                    commit_metadata={
                        **(commit_metadata or {}),
                        "interaction_reroute_dispatch_key": reroute.dispatch_key,
                    },
                )
        else:
            response = process_codex_chat_on_lesson(
                lesson_id,
                request,
                user_id=user_id,
                on_delta=on_delta,
                on_requirement_update=on_requirement_update,
                on_agent_activity=on_agent_activity,
                is_cancelled=is_cancelled,
            )
    response.turn_decision = gate.decision
    response.decision_trace = _merge_decision_trace(gate.trace, response.decision_trace)
    response.agent_activity = _merge_activity(gate.activity, response.agent_activity)
    return maybe_generate_lesson_title(
        lesson_id,
        request,
        response,
        user_id=user_id,
    )


def _should_use_bounded_existing_board_workflow(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
) -> bool:
    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if not lesson.board_document.content_text.strip():
        return False
    if request.formula_ink is not None or request.attachments:
        return False
    if request.source_query_scope is not None:
        return False
    if request.teaching_action is not None:
        return False
    references = [
        *([request.selection] if request.selection is not None else []),
        *request.selections,
    ]
    return all(reference.kind != "source" for reference in references)


def _merge_decision_trace(
    gate_trace: DecisionTrace,
    workflow_trace: DecisionTrace | None,
) -> DecisionTrace:
    if workflow_trace is None:
        return gate_trace.model_copy(update={"role_executed": "workflow"})
    return workflow_trace.model_copy(
        update={
            "intent_signals": list(
                dict.fromkeys([*gate_trace.intent_signals, *workflow_trace.intent_signals])
            ),
            "matched_rules": list(
                dict.fromkeys([*gate_trace.matched_rules, *workflow_trace.matched_rules])
            ),
        }
    )


def _chat_idempotency_key(
    request: ChatRequest,
    *,
    user_id: str,
) -> tuple[str, str, str] | None:
    if request.session_id is None or request.input_event_id is None:
        return None
    return (user_id, request.session_id, request.input_event_id)


def _chat_request_fingerprint(lesson_id: str, request: ChatRequest) -> str:
    payload = {
        "lesson_id": lesson_id,
        "request": request.model_dump(mode="json"),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _chat_idempotency_metadata(request: ChatRequest) -> dict[str, object]:
    return {
        "chat_idempotency_key": f"{request.session_id}:{request.input_event_id}",
        "chat_session_id": request.session_id or "",
        "chat_input_event_id": request.input_event_id or "",
        "chat_turn_id": request.turn_id or "",
    }


def _require_matching_fingerprint(expected: str, actual: str) -> None:
    if expected != actual:
        raise ValueError("The chat input event id was reused for a different request.")


def _copy_response(response: ChatResponse) -> ChatResponse:
    return ChatResponse.model_validate(response.model_dump(mode="json"))


def _clear_idempotency_state_for_tests() -> None:
    with _idempotency_lock:
        _idempotency_cache.clear()
        _idempotency_inflight.clear()


def _chat_edit_metadata(request: ChatRequest) -> dict[str, object]:
    if not request.chat_edit_source_commit_id:
        return {}
    return {
        "chat_edit_source_commit_id": request.chat_edit_source_commit_id,
        "chat_edit_base_commit_id": request.chat_edit_base_commit_id,
        "chat_edit_original_message": request.chat_edit_original_message or "",
    }


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    selection = (
        SelectionRef(
            kind="board",
            excerpt=selection_text,
            location_kind="target_range",
        )
        if selection_text
        else None
    )
    return process_chat_on_lesson(
        lesson_id,
        ChatRequest(
            message=instruction,
            interaction_mode="direct_edit",
            selection=selection,
            conversation=conversation,
        ),
        user_id=user_id,
    )


def _merge_activity(
    first: list[AgentActivityEvent],
    second: list[AgentActivityEvent],
) -> list[AgentActivityEvent]:
    merged: dict[str, AgentActivityEvent] = {}
    order: list[str] = []
    for event in [*first, *second]:
        if event.id not in merged:
            order.append(event.id)
        merged[event.id] = event
    return [merged[event_id] for event_id in order]

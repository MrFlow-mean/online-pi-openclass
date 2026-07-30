from __future__ import annotations

from typing import Callable

from app.models import (
    AgentActivityEvent,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    SelectionRef,
)
from app.services.chat_turn_gate import complete_non_learning_turn, evaluate_turn_gate
from app.services.codex_chat import process_codex_chat_on_lesson
from app.services.history import bind_commit_metadata
from app.services.lesson_title import maybe_generate_lesson_title


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
    gate = evaluate_turn_gate(
        request,
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
    response.decision_trace = gate.trace.model_copy(update={"role_executed": "workflow"})
    response.agent_activity = _merge_activity(gate.activity, response.agent_activity)
    return maybe_generate_lesson_title(
        lesson_id,
        request,
        response,
        user_id=user_id,
    )


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

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    ChatInputKind,
    ChatRequest,
    RealtimeToolCallRequest,
    RealtimeToolCallResponse,
    SelectionRef,
    new_id,
)
from app.services.ai_logging import ai_usage_logger
from app.services.chat_turn_gate import TurnGateResult


def realtime_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "run_chatbot_workflow",
            "description": (
                "Mandatory first-turn handoff for every learner utterance. Preserve message as the learner's exact "
                "wording. The backend makes the authoritative ordinary_chat, learning_need, or unclear decision and "
                "runs the permitted Chatbot path. Legacy intent and reason fields are optional provider hints only; "
                "they never control routing or board access."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The learner's exact current utterance."},
                    "intent": {
                        "type": "string",
                        "enum": ["ordinary_chat", "learning_need", "unclear"],
                        "description": "Deprecated provider hint. The backend independently decides the route.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Deprecated explanation for the provider hint; not authoritative.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    ]


def execute_realtime_tool(
    *,
    lesson_id: str,
    user_id: str,
    request: RealtimeToolCallRequest,
) -> RealtimeToolCallResponse:
    try:
        if request.name == "read_board_context":
            raise ValueError(
                "Board context is available only inside the authorized OpenClass workflow"
            )
        elif request.name == "run_chatbot_workflow":
            message = str(request.arguments.get("message") or "").strip()
            if not message:
                raise ValueError("message is required")
            provider_hint = _legacy_provider_turn_hint(request.arguments)
            from app.services.chat_service import process_chat_on_lesson

            if request.turn_id is None or request.input_event_id is None:
                raise ValueError(
                    "Realtime workflow input is missing turn_id or input_event_id"
                )
            turn_id = request.turn_id
            input_event_id = request.input_event_id
            provider_reference = request.provider_reference or request.call_id
            frozen_references, frozen_text_model = _realtime_input_snapshot(request)
            workflow_run_id = new_id("workflow_run")
            commit_metadata: dict[str, object] = {
                "chat_visibility": "hidden",
                "interaction_channel": "realtime_tool",
                "realtime_client_session_id": request.client_session_id,
                "realtime_turn_id": turn_id,
                "realtime_input_event_id": input_event_id,
                "realtime_provider_reference": provider_reference,
                "workflow_run_id": workflow_run_id,
            }
            if provider_hint:
                commit_metadata["realtime_provider_turn_hint"] = provider_hint
            chat_response = process_chat_on_lesson(
                lesson_id,
                ChatRequest(
                    message=message,
                    session_id=request.client_session_id,
                    turn_id=turn_id,
                    input_event_id=input_event_id,
                    channel="realtime",
                    input_kind=request.input_kind,
                    provider_reference=provider_reference,
                    text_model=frozen_text_model,
                    selection=frozen_references[0] if frozen_references else None,
                    selections=frozen_references,
                ),
                user_id=user_id,
                commit_metadata=commit_metadata,
            )
            response = _chat_response_as_realtime_result(
                chat_response,
                lesson_id=lesson_id,
            )
            if provider_hint:
                ai_usage_logger.log_event(
                    "realtime_provider_turn_hint",
                    lesson_id=lesson_id,
                    client_session_id=request.client_session_id,
                    turn_id=turn_id,
                    tool_call_id=request.call_id,
                    provider_hint=provider_hint,
                    authoritative_route=response.model_output["route"],
                )
        else:  # pragma: no cover - guarded by the request schema
            raise ValueError(f"Unsupported realtime tool: {request.name}")
        ai_usage_logger.log_event(
            "realtime_tool_call",
            tool_name=request.name,
            tool_call_id=request.call_id,
            lesson_id=lesson_id,
            client_session_id=request.client_session_id,
            status=response.status,
        )
        return response
    except Exception as exc:
        ai_usage_logger.log_event(
            "realtime_tool_call_error",
            tool_name=request.name,
            tool_call_id=request.call_id,
            lesson_id=lesson_id,
            client_session_id=request.client_session_id,
            error=str(exc),
        )
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": str(exc)},
        )


def execute_realtime_delegation(
    *,
    lesson_id: str,
    user_id: str,
    message: str,
    client_session_id: str,
    delegation_id: str,
    turn_id: str | None = None,
    workflow_run_id: str | None = None,
    input_event_id: str | None = None,
    input_kind: ChatInputKind = "voice",
    provider_reference: str | None = None,
    selections: list[SelectionRef] | None = None,
    text_model: AIModelSelection | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    prepared_gate: TurnGateResult | None = None,
) -> RealtimeToolCallResponse:
    """Run a Codex Live client delegation through the normal Chatbot workflow."""
    normalized = message.strip()
    if not normalized:
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": "委托内容为空"},
        )
    required_identifiers = {
        "turn_id": (turn_id or "").strip(),
        "workflow_run_id": (workflow_run_id or "").strip(),
        "input_event_id": (input_event_id or "").strip(),
    }
    missing_identifiers = [
        name for name, value in required_identifiers.items() if not value
    ]
    if missing_identifiers:
        return RealtimeToolCallResponse(
            status="error",
            model_output={
                "status": "error",
                "message": (
                    "Codex Live delegation is missing frozen identifiers: "
                    + ", ".join(missing_identifiers)
                ),
            },
        )
    if selections is None or text_model is None:
        return RealtimeToolCallResponse(
            status="error",
            model_output={
                "status": "error",
                "message": (
                    "Codex Live delegation is missing its frozen references "
                    "or selected text model."
                ),
            },
        )
    effective_turn_id = required_identifiers["turn_id"]
    effective_workflow_run_id = required_identifiers["workflow_run_id"]
    effective_input_event_id = required_identifiers["input_event_id"]
    effective_provider_reference = (
        (provider_reference or "").strip() or delegation_id.strip() or None
    )
    frozen_references = [
        reference.model_copy(deep=True)
        for reference in selections
    ]
    if len(frozen_references) > 8:
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": "A realtime turn accepts at most 8 references."},
        )
    frozen_text_model = text_model.model_copy(deep=True) if text_model is not None else None
    try:
        from app.services.chat_service import process_chat_on_lesson

        commit_metadata: dict[str, object] = {
            "chat_visibility": "visible",
            "interaction_channel": "realtime_delegation",
            "realtime_client_session_id": client_session_id,
            "realtime_turn_id": effective_turn_id,
            "realtime_input_event_id": effective_input_event_id,
            "realtime_provider_reference": effective_provider_reference or "",
            "workflow_run_id": effective_workflow_run_id,
        }
        if delegation_id.strip():
            commit_metadata["delegation_id"] = delegation_id.strip()
            commit_metadata["realtime_delegation_id"] = delegation_id.strip()
        request = ChatRequest(
            message=normalized,
            session_id=client_session_id,
            turn_id=effective_turn_id,
            input_event_id=effective_input_event_id,
            channel="realtime",
            input_kind=input_kind,
            provider_reference=effective_provider_reference,
            text_model=frozen_text_model,
            selection=frozen_references[0] if frozen_references else None,
            selections=frozen_references,
        )
        process_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "on_delta": on_delta,
            "on_agent_activity": on_agent_activity,
            "is_cancelled": is_cancelled,
            "commit_metadata": commit_metadata,
        }
        if prepared_gate is not None:
            process_kwargs["prepared_gate"] = prepared_gate
        chat_response = process_chat_on_lesson(
            lesson_id,
            request,
            **process_kwargs,
        )
        response = _chat_response_as_realtime_result(
            chat_response,
            lesson_id=lesson_id,
        )
        ai_usage_logger.log_event(
            "realtime_delegation_completed",
            lesson_id=lesson_id,
            client_session_id=client_session_id,
            delegation_id=delegation_id,
            workflow_run_id=effective_workflow_run_id,
            turn_id=effective_turn_id,
            input_event_id=effective_input_event_id,
            status=response.status,
        )
        return response
    except Exception as exc:
        ai_usage_logger.log_event(
            "realtime_delegation_error",
            lesson_id=lesson_id,
            client_session_id=client_session_id,
            delegation_id=delegation_id,
            workflow_run_id=effective_workflow_run_id,
            turn_id=effective_turn_id,
            input_event_id=effective_input_event_id,
            error=str(exc),
        )
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": str(exc)},
        )


def _legacy_provider_turn_hint(arguments: dict[str, Any]) -> dict[str, str]:
    hint: dict[str, str] = {}
    for key in ("intent", "reason"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            hint[key] = value.strip()
    return hint


def _realtime_input_snapshot(
    request: RealtimeToolCallRequest,
) -> tuple[list[SelectionRef], AIModelSelection | None]:
    """Validate the client-owned input snapshot carried inside legacy tool arguments."""

    raw_snapshot = request.arguments.get("__openclass_turn_snapshot")
    if raw_snapshot is None:
        raise ValueError("Realtime workflow input is missing its frozen input snapshot")
    if not isinstance(raw_snapshot, dict):
        raise TypeError("Realtime input snapshot must be an object")
    raw_references = raw_snapshot.get("references", [])
    if not isinstance(raw_references, list):
        raise TypeError("Realtime input snapshot references must be a list")
    if len(raw_references) > 8:
        raise ValueError("A realtime turn accepts at most 8 references")
    references = [
        SelectionRef.model_validate(reference).model_copy(deep=True)
        for reference in raw_references
    ]
    raw_text_model = raw_snapshot.get("text_model")
    if raw_text_model is None:
        raise ValueError("Realtime input snapshot is missing the selected text model")
    text_model = AIModelSelection.model_validate(raw_text_model).model_copy(deep=True)
    return references, text_model


def _chat_response_as_realtime_result(chat_response, *, lesson_id: str) -> RealtimeToolCallResponse:
    decision = getattr(chat_response, "turn_decision", None)
    if decision is None:
        raise ValueError("Chatbot workflow response is missing its authoritative TurnDecision")
    trace = getattr(chat_response, "decision_trace", None)
    model_output: dict[str, Any] = {
        "status": "ok",
        "route": decision.intent,
        "turn_decision": decision.model_dump(mode="json"),
        "chatbot_message": chat_response.chatbot_message,
        "needs_clarification": chat_response.needs_clarification,
        "clarification_questions": chat_response.clarification_questions,
        "instruction": (
            "Present chatbot_message faithfully and naturally. Do not claim an action beyond this authoritative "
            "Chatbot result."
        ),
    }
    if trace is not None:
        model_output["decision_trace"] = trace.model_dump(mode="json")
    return RealtimeToolCallResponse(
        status="ok",
        model_output=model_output,
        resolved_focus=_latest_resolved_focus(chat_response.course_package, lesson_id),
        course_package=chat_response.course_package,
    )


def _latest_resolved_focus(course_package, lesson_id: str):
    lesson = next((item for item in course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return None
    branch = lesson.history_graph.branches.get(lesson.history_graph.current_branch)
    commit_id = branch.head_commit_id if branch else None
    commit = next((item for item in lesson.history_graph.commits if item.id == commit_id), None)
    raw_focus = commit.metadata.get("resolved_focus") if commit and isinstance(commit.metadata, dict) else None
    if not isinstance(raw_focus, dict):
        return None
    from app.models import BoardFocusRef

    try:
        return BoardFocusRef.model_validate(raw_focus)
    except ValueError:
        return None

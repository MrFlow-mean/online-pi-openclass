from __future__ import annotations

from typing import Any, Callable

from app.models import AgentActivityEvent, ChatRequest, RealtimeToolCallRequest, RealtimeToolCallResponse, SelectionRef
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.chat.turn_context import board_state
from app.services.realtime_board_context import read_realtime_board_context


def realtime_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "read_board_context",
            "description": (
                "Read a bounded, authorized range from the current OpenClass board. "
                "Call this before discussing, explaining, quoting, or role-playing from board content. "
                "Use mode=current_selection for the learner's active board references; the client may return an "
                "ordered references array when the learner accumulated more than one selection. Use every item "
                "in that array without replacing an earlier reference with a later one. Use mode=outline to inspect headings, "
                "or mode=target with a location, heading, example, phrase, or section description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["target", "current_selection", "outline"]},
                    "target": {"type": "string", "description": "Requested board location or content description."},
                    "max_chars": {"type": "integer", "minimum": 800, "maximum": 12000},
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "run_chatbot_workflow",
            "description": (
                "Mandatory first-turn gate for every learner utterance. Classify the current utterance, using "
                "conversation history when it continues an active task. ordinary_chat is social conversation with "
                "no learning or work goal. learning_need is a concrete request to learn, practise, explain, write, "
                "edit, or work on content, including a rule-based learning interaction. unclear is a possible "
                "learning goal that is ambiguous or too broad to act on. Preserve message as the learner's exact "
                "wording. The backend inspects board state only for learning_need."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The learner's exact current utterance."},
                    "intent": {
                        "type": "string",
                        "enum": ["ordinary_chat", "learning_need", "unclear"],
                    },
                    "reason": {
                        "type": "string",
                        "description": "A concise content-agnostic reason for this TurnDecision.",
                    },
                },
                "required": ["message", "intent", "reason"],
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
    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    try:
        if request.name == "read_board_context":
            result = read_realtime_board_context(
                lesson_id=lesson_id,
                user_id=user_id,
                arguments=request.arguments,
                selection=request.selection,
            )
            response = RealtimeToolCallResponse(
                status="ok",
                model_output=result.model_output,
                resolved_focus=result.focus,
            )
        elif request.name == "run_chatbot_workflow":
            message = str(request.arguments.get("message") or "").strip()
            if not message:
                raise ValueError("message is required")
            intent = str(request.arguments.get("intent") or "").strip()
            if intent not in {"ordinary_chat", "learning_need", "unclear"}:
                raise ValueError("intent must be ordinary_chat, learning_need, or unclear")
            reason = str(request.arguments.get("reason") or "").strip()
            if not reason:
                raise ValueError("reason is required")
            decision_trace = {
                "intent_signals": [f"realtime_intent:{intent}"],
                "matched_rules": ["explicit_learning_need_board_gate"],
                "selected_action": intent,
                "target_resolver": "board_state" if intent == "learning_need" else "none",
                "sequence_mode": "single_turn",
                "role_executed": "turn_decision",
                "document_changed": False,
                "reason": reason,
            }
            if intent == "ordinary_chat":
                response = RealtimeToolCallResponse(
                    status="ok",
                    model_output={
                        "status": "ok",
                        "route": intent,
                        "board_state": "not_checked",
                        "decision_trace": decision_trace,
                        "instruction": (
                            "Reply naturally to this ordinary conversation. Do not mention, read, summarize, "
                            "or modify the board or any learning requirement sheet."
                        ),
                    },
                )
            elif intent == "unclear":
                response = RealtimeToolCallResponse(
                    status="ok",
                    model_output={
                        "status": "ok",
                        "route": intent,
                        "board_state": "not_checked",
                        "decision_trace": decision_trace,
                        "instruction": (
                            "Help the learner narrow the possible learning goal with one focused question and, "
                            "when useful, a few relevant directions. Do not read or modify the board, generate "
                            "learning content, or update any requirement sheet yet."
                        ),
                    },
                )
            else:
                computed_board_state = board_state(lesson.board_document.content_text)
                from app.services.chat_service import process_chat_on_lesson

                chat_response = process_chat_on_lesson(
                    lesson_id,
                    ChatRequest(message=message, selection=request.selection),
                    user_id=user_id,
                    commit_metadata={
                        "chat_visibility": "hidden",
                        "interaction_channel": "realtime_tool",
                        "realtime_client_session_id": request.client_session_id,
                        "realtime_turn_id": request.turn_id or "",
                    },
                )
                focus = _latest_resolved_focus(chat_response.course_package, lesson_id)
                response = RealtimeToolCallResponse(
                    status="ok",
                    model_output={
                        "status": "ok",
                        "route": intent,
                        "board_state": (
                            "board_blank" if computed_board_state == "empty" else "board_nonempty"
                        ),
                        "decision_trace": decision_trace,
                        "chatbot_message": chat_response.chatbot_message,
                        "needs_clarification": chat_response.needs_clarification,
                        "clarification_questions": chat_response.clarification_questions,
                        "instruction": (
                            "Present chatbot_message faithfully and naturally. Do not claim an action beyond "
                            "this result. The Chatbot workflow already handled the required board path."
                        ),
                    },
                    resolved_focus=focus,
                    course_package=chat_response.course_package,
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
    selection: SelectionRef | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RealtimeToolCallResponse:
    """Run a Codex Live client delegation through the normal Chatbot workflow."""
    normalized = message.strip()
    if not normalized:
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": "委托内容为空"},
        )
    try:
        from app.services.chat_service import process_chat_on_lesson

        chat_response = process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=normalized, selection=selection),
            user_id=user_id,
            on_delta=on_delta,
            on_agent_activity=on_agent_activity,
            is_cancelled=is_cancelled,
            commit_metadata={
                "chat_visibility": "visible",
                "interaction_channel": "realtime_delegation",
                "realtime_client_session_id": client_session_id,
                "realtime_delegation_id": delegation_id,
            },
        )
        response = RealtimeToolCallResponse(
            status="ok",
            model_output={
                "status": "ok",
                "route": "client_delegation",
                "chatbot_message": chat_response.chatbot_message,
                "needs_clarification": chat_response.needs_clarification,
                "clarification_questions": chat_response.clarification_questions,
            },
            resolved_focus=_latest_resolved_focus(chat_response.course_package, lesson_id),
            course_package=chat_response.course_package,
        )
        ai_usage_logger.log_event(
            "realtime_delegation_completed",
            lesson_id=lesson_id,
            client_session_id=client_session_id,
            delegation_id=delegation_id,
            status=response.status,
        )
        return response
    except Exception as exc:
        ai_usage_logger.log_event(
            "realtime_delegation_error",
            lesson_id=lesson_id,
            client_session_id=client_session_id,
            delegation_id=delegation_id,
            error=str(exc),
        )
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": str(exc)},
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

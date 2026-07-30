from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (
    AgentActivityEvent,
    BoardExplanationDirective,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ConversationTurn,
)
from app.services.ai_execution_adapter import AIExecutionAdapter
from app.services.existing_board.focus_resolver import (
    MAX_APPROVED_BOARD_TARGET_CHARS,
)

BOARD_MANAGER_DIRECTIVE_INSTRUCTIONS = """
You are the Board Manager. Produce a BoardExplanationDirective from only the
complete board-task sheet, its already-resolved bounded target, and the supplied
teaching requirements. Approve explanation only when the target and boundary
are sufficient. Never expand the target excerpt, invent adjacent board content,
or answer the learner directly.
""".strip()

CHATBOT_APPROVED_DIRECTIVE_INSTRUCTIONS = """
You are the learner-facing Chatbot. Generate a substantive explanation only
from the approved BoardExplanationDirective in the payload. Follow its teaching
instruction and constraints, stay inside its target excerpt, and do not infer
or claim access to any other board content. Generate the response directly;
do not use a fixed opening or closing template.
""".strip()

CHATBOT_DIRECTIVE_STATUS_INSTRUCTIONS = """
You are the learner-facing Chatbot handling a non-approved board explanation
directive. Generate only the clarification or blocked-status response authorized
by the directive. Do not explain lesson content, invent target text, or use a
fixed response template.
""".strip()


class ExplanationWorkflowError(ValueError):
    pass


class BoardFreeRecentConversation(BaseModel):
    """Conversation context explicitly prepared without board-derived content."""

    board_content_included: Literal[False]
    turns: list[ConversationTurn] = Field(default_factory=list, max_length=8)


class ExistingBoardExplanationResult(BaseModel):
    directive: BoardExplanationDirective
    chatbot_message: str
    substantive_explanation_allowed: bool
    document_changed: Literal[False] = False
    activity: list[AgentActivityEvent] = Field(default_factory=list)


def run_existing_board_explanation(
    *,
    adapter: AIExecutionAdapter,
    board_task: BoardTaskRequirementSheet,
    resolved_focus: BoardFocusRef,
    teaching_requirements: Sequence[str],
    current_user_message: str = "",
    recent_conversation: BoardFreeRecentConversation | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
    on_text_delta: Callable[[str], None] | None = None,
) -> ExistingBoardExplanationResult:
    safe_focus, safe_task = _validate_and_bound_inputs(board_task, resolved_focus)
    safe_requirements = _validate_teaching_requirements(teaching_requirements)
    safe_recent_conversation = recent_conversation or BoardFreeRecentConversation(
        board_content_included=False
    )
    _validate_board_free_conversation(safe_recent_conversation, safe_focus)

    board_manager_payload = {
        "board_task_requirement_sheet": safe_task.model_dump(mode="json"),
        "resolved_target": safe_focus.model_dump(mode="json"),
        "teaching_requirements": safe_requirements,
        "response_contract": BoardExplanationDirective.model_json_schema(),
    }
    directive_response = adapter.parse_structured(
        system_prompt=BOARD_MANAGER_DIRECTIVE_INSTRUCTIONS,
        user_prompt=json.dumps(
            board_manager_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        schema=BoardExplanationDirective,
        on_activity=on_activity,
    )
    directive = BoardExplanationDirective.model_validate(directive_response.output_parsed)
    directive = _bound_directive(directive, safe_focus)

    approved = directive.status == "approved"
    chatbot_payload: dict[str, object] = {
        "response_mode": (
            "approved_bounded_explanation" if approved else "directive_status_only"
        ),
        "board_explanation_directive": directive.model_dump(mode="json"),
    }
    if approved:
        message = current_user_message.strip()
        if message:
            chatbot_payload["current_user_message"] = message
        chatbot_payload["recent_conversation"] = [
            turn.model_dump(mode="json") for turn in safe_recent_conversation.turns
        ]

    chatbot_response = adapter.complete_text(
        system_prompt=(
            CHATBOT_APPROVED_DIRECTIVE_INSTRUCTIONS
            if approved
            else CHATBOT_DIRECTIVE_STATUS_INSTRUCTIONS
        ),
        user_prompt=json.dumps(
            chatbot_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_cancelled=is_cancelled,
        on_activity=on_activity,
        on_text_delta=on_text_delta,
    )
    chatbot_message = chatbot_response.output_text.strip()
    if not chatbot_message:
        raise ExplanationWorkflowError("Chatbot returned an empty directive response")

    return ExistingBoardExplanationResult(
        directive=directive,
        chatbot_message=chatbot_message,
        substantive_explanation_allowed=approved,
        activity=[
            *list(getattr(directive_response, "activity", [])),
            *list(getattr(chatbot_response, "activity", [])),
        ],
    )


def _validate_and_bound_inputs(
    board_task: BoardTaskRequirementSheet,
    resolved_focus: BoardFocusRef,
) -> tuple[BoardFocusRef, BoardTaskRequirementSheet]:
    if board_task.location_status != "resolved":
        raise ExplanationWorkflowError("Board Manager requires a resolved board task")
    if board_task.requested_action != "explain":
        raise ExplanationWorkflowError("Board Manager requires an explain board task")
    if board_task.missing_items:
        raise ExplanationWorkflowError("Board Manager requires a complete board task")
    if not board_task.question_or_topic.strip():
        raise ExplanationWorkflowError("Board Manager requires the explanation topic")
    if board_task.target_location is None:
        raise ExplanationWorkflowError("Board Manager requires a resolved target location")
    if resolved_focus.source != "board":
        raise ExplanationWorkflowError("Explanation focus must come from the board")
    if not all(
        (
            resolved_focus.lesson_id,
            resolved_focus.document_id,
            resolved_focus.segment_id,
            resolved_focus.text_hash,
            resolved_focus.excerpt.strip(),
        )
    ):
        raise ExplanationWorkflowError("Resolved focus lacks stable target identity")
    if resolved_focus.confidence <= 0:
        raise ExplanationWorkflowError("Resolved focus lacks reliable confidence")
    if len(resolved_focus.excerpt) > MAX_APPROVED_BOARD_TARGET_CHARS:
        raise ExplanationWorkflowError("Resolved target excerpt exceeds the bounded scope")

    expected_identity = _focus_identity(board_task.target_location)
    actual_identity = _focus_identity(resolved_focus)
    if expected_identity != actual_identity:
        raise ExplanationWorkflowError("Resolved focus does not match the board task target")

    safe_focus = resolved_focus.model_copy(
        deep=True,
        update={
            "excerpt": resolved_focus.excerpt.strip(),
            "before_text": "",
            "after_text": "",
        },
    )
    safe_task = board_task.model_copy(
        deep=True,
        update={
            "target_location": safe_focus.model_copy(deep=True),
            "target_candidates": [],
            "mutation_plan": None,
            "interaction_session": None,
        },
    )
    return safe_focus, safe_task


def _focus_identity(focus: BoardFocusRef) -> tuple[str | None, ...]:
    return (
        focus.lesson_id,
        focus.document_id,
        focus.segment_id,
        focus.text_hash,
    )


def _validate_teaching_requirements(requirements: Sequence[str]) -> list[str]:
    normalized = [item.strip() for item in requirements if item.strip()]
    if not normalized:
        raise ExplanationWorkflowError("Board Manager requires teaching requirements")
    if len(normalized) > 12 or any(len(item) > 1_000 for item in normalized):
        raise ExplanationWorkflowError("Teaching requirements exceed the bounded scope")
    return normalized


def _validate_board_free_conversation(
    conversation: BoardFreeRecentConversation,
    focus: BoardFocusRef,
) -> None:
    excerpt = focus.excerpt.strip()
    if excerpt and any(excerpt in turn.content for turn in conversation.turns):
        raise ExplanationWorkflowError(
            "Recent conversation must not repeat board-derived target content"
        )


def _bound_directive(
    directive: BoardExplanationDirective,
    focus: BoardFocusRef,
) -> BoardExplanationDirective:
    if directive.status == "approved":
        if directive.target_excerpt.strip() != focus.excerpt:
            raise ExplanationWorkflowError(
                "Approved directive must preserve the resolved target excerpt exactly"
            )
        if not directive.teaching_instruction.strip():
            raise ExplanationWorkflowError(
                "Approved directive requires a teaching instruction"
            )
        return directive.model_copy(
            deep=True,
            update={"target_excerpt": focus.excerpt},
        )

    return directive.model_copy(
        deep=True,
        update={
            "target_summary": "",
            "target_excerpt": "",
            "teaching_instruction": "",
            "constraints": [],
        },
    )

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.models import (
    BoardExplanationDirective,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ConversationTurn,
)
from app.services.existing_board.explanation_workflow import (
    BoardFreeRecentConversation,
    ExplanationWorkflowError,
    run_existing_board_explanation,
)


TARGET_EXCERPT = "bounded approved excerpt"
BOARD_SENTINEL = "FULL_BOARD_CONTENT_MUST_NOT_REACH_CHATBOT"


def _focus(*, before_text: str = "", after_text: str = "") -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id="lesson_1",
        document_id="document_1",
        segment_id="segment_1",
        kind="paragraph",
        excerpt=TARGET_EXCERPT,
        before_text=before_text,
        after_text=after_text,
        text_hash="target_hash",
        confidence=1.0,
    )


def _task(*, location_status: str = "resolved") -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        location_kind="target_range",
        target_hint="the selected target",
        target_location=_focus(before_text=BOARD_SENTINEL),
        location_status=location_status,
        requested_action="explain",
        question_or_topic="explain the approved target under the requested boundary",
        target_candidates=[
            BoardFocusRef(
                source="board",
                lesson_id="lesson_1",
                document_id="document_1",
                segment_id="other_segment",
                excerpt=BOARD_SENTINEL,
                text_hash="other_hash",
                confidence=0.8,
            )
        ],
        mutation_plan={"unapproved_board_content": BOARD_SENTINEL},
        interaction_session={"unapproved_board_content": BOARD_SENTINEL},
        missing_items=[],
        progress=100,
    )


class RecordingAdapter:
    def __init__(self, directive: BoardExplanationDirective) -> None:
        self.directive = directive
        self.parse_calls: list[dict[str, object]] = []
        self.text_calls: list[dict[str, object]] = []
        self.call_instance_ids: list[int] = []

    def parse_structured(self, **kwargs):
        self.call_instance_ids.append(id(self))
        self.parse_calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.directive, activity=[])

    def complete_text(self, **kwargs):
        self.call_instance_ids.append(id(self))
        self.text_calls.append(kwargs)
        return SimpleNamespace(output_text="model generated learner-facing response", activity=[])


def test_unresolved_board_task_is_rejected_before_board_manager_runs() -> None:
    adapter = RecordingAdapter(
        BoardExplanationDirective(
            status="approved",
            target_excerpt=TARGET_EXCERPT,
            teaching_instruction="explain within the directive boundary",
        )
    )

    with pytest.raises(ExplanationWorkflowError, match="resolved"):
        run_existing_board_explanation(
            adapter=adapter,
            board_task=_task(location_status="ambiguous"),
            resolved_focus=_focus(),
            teaching_requirements=["stay within the approved target"],
            current_user_message="current request",
        )

    assert adapter.parse_calls == []
    assert adapter.text_calls == []


def test_approved_directive_is_the_only_board_content_visible_to_chatbot() -> None:
    adapter = RecordingAdapter(
        BoardExplanationDirective(
            status="approved",
            target_summary="bounded target",
            target_excerpt=TARGET_EXCERPT,
            teaching_instruction="explain only this target",
            constraints=["do not expand beyond the target"],
        )
    )

    result = run_existing_board_explanation(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        teaching_requirements=["use the learner's requested depth"],
        current_user_message="current request",
        recent_conversation=BoardFreeRecentConversation(
            board_content_included=False,
            turns=[ConversationTurn(role="assistant", content="board-free prior context")],
        ),
    )

    assert len(adapter.parse_calls) == 1
    assert len(adapter.text_calls) == 1
    assert adapter.parse_calls[0]["schema"] is BoardExplanationDirective
    board_manager_payload = json.loads(str(adapter.parse_calls[0]["user_prompt"]))
    chatbot_payload = json.loads(str(adapter.text_calls[0]["user_prompt"]))
    assert board_manager_payload["resolved_target"]["excerpt"] == TARGET_EXCERPT
    assert board_manager_payload["board_task_requirement_sheet"]["target_location"]["before_text"] == ""
    assert board_manager_payload["board_task_requirement_sheet"]["target_candidates"] == []
    assert board_manager_payload["board_task_requirement_sheet"]["mutation_plan"] is None
    assert board_manager_payload["board_task_requirement_sheet"]["interaction_session"] is None
    assert "current_user_message" not in board_manager_payload
    assert "recent_conversation" not in board_manager_payload
    assert BOARD_SENTINEL not in str(adapter.parse_calls[0]["user_prompt"])
    assert chatbot_payload["board_explanation_directive"]["target_excerpt"] == TARGET_EXCERPT
    assert chatbot_payload["current_user_message"] == "current request"
    assert chatbot_payload["recent_conversation"] == [
        {"role": "assistant", "content": "board-free prior context"}
    ]
    assert "board_task_requirement_sheet" not in chatbot_payload
    assert BOARD_SENTINEL not in str(adapter.text_calls[0]["user_prompt"])
    assert result.substantive_explanation_allowed is True
    assert result.document_changed is False


@pytest.mark.parametrize("status", ["needs_clarification", "blocked"])
def test_unapproved_directive_uses_status_response_mode_without_substantive_explanation(
    status: str,
) -> None:
    adapter = RecordingAdapter(
        BoardExplanationDirective(
            status=status,
            target_summary="unapproved summary",
            target_excerpt=TARGET_EXCERPT,
            teaching_instruction="this must not be executed",
            clarification_question="model supplied clarification request",
            reason="model supplied boundary decision",
        )
    )

    result = run_existing_board_explanation(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        teaching_requirements=["stay within the approved target"],
        current_user_message="current request",
    )

    assert len(adapter.parse_calls) == 1
    assert len(adapter.text_calls) == 1
    chatbot_payload = json.loads(str(adapter.text_calls[0]["user_prompt"]))
    assert chatbot_payload["response_mode"] == "directive_status_only"
    assert chatbot_payload["board_explanation_directive"]["status"] == status
    assert chatbot_payload["board_explanation_directive"]["target_excerpt"] == ""
    assert chatbot_payload["board_explanation_directive"]["teaching_instruction"] == ""
    assert result.substantive_explanation_allowed is False
    assert result.document_changed is False


def test_directive_and_chatbot_calls_share_the_exact_adapter_instance() -> None:
    adapter = RecordingAdapter(
        BoardExplanationDirective(
            status="approved",
            target_excerpt=TARGET_EXCERPT,
            teaching_instruction="explain only the approved target",
        )
    )

    run_existing_board_explanation(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        teaching_requirements=["stay bounded"],
        current_user_message="current request",
    )

    assert len(adapter.parse_calls) == 1
    assert len(adapter.text_calls) == 1
    assert adapter.call_instance_ids == [id(adapter), id(adapter)]

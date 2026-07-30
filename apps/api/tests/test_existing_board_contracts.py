from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import BoardFocusRef, BoardTaskRequirementSheet, TurnDecision


def _candidate(index: int) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id="lesson_contract",
        document_id="document_contract",
        segment_id=f"segment_{index}",
        excerpt=f"candidate {index}",
        confidence=0.8,
    )


def test_board_task_contract_carries_location_action_interaction_extent_and_destination() -> None:
    task = BoardTaskRequirementSheet(
        requested_action="interact",
        target_hint="a bounded target",
        question_or_topic="practise the approved content",
        special_interaction_requirements="alternate according to the learner-defined rule",
        content_extent="section",
        topic_relation="current_document",
        document_destination="current_lesson",
        target_candidates=[_candidate(1), _candidate(2)],
        target_resolution_reason="ambiguous_candidates",
        base_commit_id="commit_base",
        base_document_hash="hash_base",
        interaction_session={"session_id": "interaction_1"},
    )

    payload = task.model_dump(mode="json")

    assert payload["task_id"].startswith("boardtask_")
    assert payload["requested_action"] == "interact"
    assert payload["special_interaction_requirements"].startswith("alternate")
    assert payload["content_extent"] == "section"
    assert payload["topic_relation"] == "current_document"
    assert payload["document_destination"] == "current_lesson"
    assert len(payload["target_candidates"]) == 2
    assert payload["interaction_session"]["session_id"] == "interaction_1"


@pytest.mark.parametrize("action", ["write", "edit", "explain", "delete", "interact", "chat"])
def test_board_task_contract_accepts_all_current_and_legacy_actions(action: str) -> None:
    task = BoardTaskRequirementSheet(requested_action=action)

    assert task.requested_action == action


def test_board_task_candidates_are_bounded() -> None:
    with pytest.raises(ValidationError):
        BoardTaskRequirementSheet(target_candidates=[_candidate(index) for index in range(6)])


def test_turn_decision_exposes_active_relation_and_board_access() -> None:
    decision = TurnDecision(
        intent="learning_need",
        continuation="board_task",
        relation_to_active="supplement",
        board_access="state_check_only",
        reason="The user supplements an active board task.",
    )

    assert decision.relation_to_active == "supplement"
    assert decision.board_access == "state_check_only"


def test_turn_decision_rejects_direct_bounded_board_access() -> None:
    with pytest.raises(ValidationError):
        TurnDecision(
            intent="learning_need",
            relation_to_active="continue",
            board_access="bounded_board_role",
            reason="The Turn Router must not receive board content.",
        )

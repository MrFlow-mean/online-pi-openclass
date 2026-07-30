from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import BoardFocusRef
from app.services.existing_board.interaction_session import (
    InteractionRouteDecision,
    InteractionRule,
    InteractionSession,
    transition_interaction,
)


def _session() -> InteractionSession:
    return InteractionSession(
        session_id="interaction_session_1",
        source_board_task_id="board_task_1",
        source_board_task_run_id="board_task_run_1",
        source_board_task_version_id="board_task_version_1",
        target=BoardFocusRef(
            source="board",
            lesson_id="lesson_1",
            document_id="document_1",
            segment_id="segment_1",
            excerpt="bounded target excerpt",
            text_hash="target_hash",
            confidence=1.0,
        ),
        interaction_rule=InteractionRule(
            rule_text="alternate turns within the approved target",
            interaction_goal="complete the bounded interaction",
            compliant_input_description="input follows the current turn contract",
            assistant_behavior_instruction="perform only the next rule-defined step",
        ),
    )


def test_continue_rule_advances_progress_without_mutating_the_board_or_input_session() -> None:
    original = _session()

    result = transition_interaction(
        original,
        InteractionRouteDecision(
            input_event_id="event_1",
            route="continue_rule",
            progress_note="first rule step completed",
        ),
    )

    assert result.transition_applied is True
    assert result.should_continue_rule is True
    assert result.should_reroute_original is False
    assert result.document_changed is False
    assert result.session.current_state == "active"
    assert result.session.turn_count == 1
    assert result.session.progress.completed_rule_turns == 1
    assert result.session.progress.last_route == "continue_rule"
    assert original.turn_count == 0
    assert original.progress.completed_rule_turns == 0


def test_rule_violation_records_correction_and_keeps_session_active() -> None:
    result = transition_interaction(
        _session(),
        InteractionRouteDecision(
            input_event_id="event_violation",
            route="rule_violation",
            reason="the structured turn contract was not met",
            correction_note="return to the current rule-defined step",
        ),
    )

    assert result.transition_applied is True
    assert result.should_continue_rule is True
    assert result.should_reroute_original is False
    assert result.document_changed is False
    assert result.session.current_state == "active"
    assert result.session.progress.rule_violation_count == 1
    assert result.session.progress.records[-1].correction_note == "return to the current rule-defined step"


def test_rule_violation_without_a_correction_record_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InteractionRouteDecision(
            input_event_id="event_invalid_violation",
            route="rule_violation",
        )


def test_unknown_route_is_rejected_instead_of_falling_back() -> None:
    with pytest.raises(ValidationError):
        InteractionRouteDecision(
            input_event_id="event_invalid_route",
            route="unsupported_route",
        )


def test_explicit_exit_ends_the_session_and_later_input_cannot_reactivate_it() -> None:
    exited = transition_interaction(
        _session(),
        InteractionRouteDecision(input_event_id="event_exit", route="exit_rule"),
    )
    after_exit = transition_interaction(
        exited.session,
        InteractionRouteDecision(input_event_id="event_after_exit", route="continue_rule"),
    )

    assert exited.session.current_state == "exited"
    assert exited.should_continue_rule is False
    assert exited.document_changed is False
    assert after_exit.transition_applied is False
    assert after_exit.session.current_state == "exited"
    assert after_exit.session.turn_count == 1


def test_serialized_session_restores_progress_before_the_next_transition() -> None:
    first = transition_interaction(
        _session(),
        InteractionRouteDecision(
            input_event_id="event_before_refresh",
            route="continue_rule",
            progress_note="progress before refresh",
        ),
    )
    restored = InteractionSession.model_validate_json(first.session.model_dump_json())

    second = transition_interaction(
        restored,
        InteractionRouteDecision(
            input_event_id="event_after_refresh",
            route="continue_rule",
            progress_note="progress after refresh",
        ),
    )

    assert second.session.current_state == "active"
    assert second.session.turn_count == 2
    assert second.session.progress.completed_rule_turns == 2
    assert [record.input_event_id for record in second.session.progress.records] == [
        "event_before_refresh",
        "event_after_refresh",
    ]


def test_new_task_replaces_session_and_original_input_is_rerouted_at_most_once() -> None:
    first = transition_interaction(
        _session(),
        InteractionRouteDecision(
            input_event_id="event_new_task",
            route="new_task",
            reason="a separate task was identified upstream",
        ),
    )
    duplicate = transition_interaction(
        first.session,
        InteractionRouteDecision(
            input_event_id="event_new_task",
            route="new_task",
            reason="duplicate delivery",
        ),
    )

    assert first.session.current_state == "replaced"
    assert first.session.reroute_count == 1
    assert first.should_reroute_original is True
    assert first.reroute_dispatch_key == "interaction_session_1:event_new_task"
    assert first.document_changed is False
    assert duplicate.transition_applied is False
    assert duplicate.should_reroute_original is False
    assert duplicate.reroute_dispatch_key is None
    assert duplicate.session.reroute_count == 1
    assert duplicate.session.turn_count == 1

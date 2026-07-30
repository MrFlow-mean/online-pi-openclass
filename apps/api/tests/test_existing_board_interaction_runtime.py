from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.models import BoardFocusRef, BoardTaskRequirementSheet
from app.services.existing_board.interaction_runtime import (
    InteractionRouteModelDraft,
    run_existing_board_interaction,
)
from app.services.existing_board.interaction_session import (
    InteractionRule,
    InteractionSession,
)


FULL_BOARD_SENTINEL = "FULL_BOARD_MUST_NOT_REACH_INTERACTION_MODELS"


def _focus() -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id="lesson_1",
        document_id="document_1",
        segment_id="segment_1",
        kind="paragraph",
        heading_path=["Approved section"],
        excerpt="bounded approved interaction excerpt",
        before_text=FULL_BOARD_SENTINEL,
        after_text=FULL_BOARD_SENTINEL,
        text_hash="target_hash",
        confidence=1.0,
    )


def _task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        task_id="board_task_1",
        location_kind="target_range",
        target_hint="the approved target",
        target_location=_focus(),
        location_status="resolved",
        requested_action="interact",
        question_or_topic="continue the learner-defined interaction",
        special_interaction_requirements="take turns under the learner-defined rule",
        content_extent="paragraph",
        topic_relation="current_document",
        document_destination="current_lesson",
        target_candidates=[
            BoardFocusRef(
                source="board",
                lesson_id="lesson_1",
                document_id="document_1",
                segment_id="unapproved_segment",
                excerpt=FULL_BOARD_SENTINEL,
                text_hash="unapproved_hash",
                confidence=0.8,
            )
        ],
        interaction_session={"unapproved_content": FULL_BOARD_SENTINEL},
        missing_items=[],
        progress=100,
    )


def _rule() -> InteractionRule:
    return InteractionRule(
        rule_text="take turns under the learner-defined rule",
        interaction_goal="complete the bounded interaction",
        compliant_input_description="the input performs the learner's current turn",
        assistant_behavior_instruction="perform only the next assistant turn",
    )


def _session() -> InteractionSession:
    focus = _focus().model_copy(update={"before_text": "", "after_text": ""})
    return InteractionSession(
        session_id="interaction_session_1",
        source_board_task_id="board_task_1",
        source_board_task_run_id="board_task_run_1",
        source_board_task_version_id="board_task_version_1",
        target=focus,
        interaction_rule=_rule(),
    )


class RecordingAdapter:
    def __init__(
        self,
        structured_outputs: list[dict[str, object]],
        text_outputs: list[str] | None = None,
    ) -> None:
        self.structured_outputs = list(structured_outputs)
        self.text_outputs = list(text_outputs or [])
        self.parse_calls: list[dict[str, object]] = []
        self.text_calls: list[dict[str, object]] = []
        self.call_instance_ids: list[int] = []

    def parse_structured(self, **kwargs):
        self.call_instance_ids.append(id(self))
        self.parse_calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.structured_outputs.pop(0), activity=[])

    def complete_text(self, **kwargs):
        self.call_instance_ids.append(id(self))
        self.text_calls.append(kwargs)
        return SimpleNamespace(output_text=self.text_outputs.pop(0), activity=[])


def _route_draft(route: str) -> dict[str, object]:
    return {
        "route": route,
        "reason": f"model reason for {route}",
        "progress_note": f"model progress for {route}",
        "correction_note": (
            "return to the current rule-defined step" if route == "rule_violation" else ""
        ),
    }


@pytest.mark.parametrize(
    ("route", "expected_state", "expects_reply", "expects_reroute"),
    [
        ("continue_rule", "active", True, False),
        ("rule_violation", "active", True, False),
        ("exit_rule", "exited", True, False),
        ("new_task", "replaced", False, True),
    ],
)
def test_runtime_applies_all_four_routes_without_board_mutation(
    route: str,
    expected_state: str,
    expects_reply: bool,
    expects_reroute: bool,
) -> None:
    adapter = RecordingAdapter(
        [_route_draft(route)],
        ["model generated interaction reply"] if expects_reply else [],
    )

    result = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=_session(),
        current_message="current learner input",
        input_event_id=f"event_{route}",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )

    assert result.transition.route == route
    assert result.transition.transition_applied is True
    assert result.transition.session.current_state == expected_state
    assert result.transition.document_changed is False
    assert result.document_changed is False
    assert result.should_reroute_original is expects_reroute
    assert bool(result.chatbot_message) is expects_reply
    assert len(adapter.text_calls) == int(expects_reply)
    if expects_reroute:
        assert result.original_input_for_reroute == "current learner input"
        assert result.reroute_dispatch_key == (
            f"interaction_session_1:event_{route}"
        )
    else:
        assert result.original_input_for_reroute is None


def test_serialized_session_restores_progress_and_duplicate_event_is_not_reexecuted() -> None:
    adapter = RecordingAdapter(
        [_route_draft("continue_rule"), _route_draft("rule_violation")],
        ["first reply", "second reply"],
    )
    first = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=_session(),
        current_message="first input",
        input_event_id="event_before_refresh",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )
    restored = InteractionSession.model_validate_json(
        first.transition.session.model_dump_json()
    )
    second = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=restored,
        current_message="second input",
        input_event_id="event_after_refresh",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )
    call_count_after_second = len(adapter.call_instance_ids)
    duplicate = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=second.transition.session,
        current_message="duplicate delivery",
        input_event_id="event_after_refresh",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )

    assert second.transition.session.turn_count == 2
    assert second.transition.session.progress.completed_rule_turns == 1
    assert second.transition.session.progress.rule_violation_count == 1
    assert duplicate.duplicate_input is True
    assert duplicate.transition.transition_applied is False
    assert duplicate.chatbot_message == ""
    assert duplicate.should_reroute_original is False
    assert len(adapter.call_instance_ids) == call_count_after_second


def test_duplicate_new_task_input_is_rerouted_only_once() -> None:
    adapter = RecordingAdapter([_route_draft("new_task")])
    first = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=_session(),
        current_message="a separate learner task",
        input_event_id="event_new_task",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )
    duplicate = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=first.transition.session,
        current_message="duplicate separate learner task",
        input_event_id="event_new_task",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )

    assert first.should_reroute_original is True
    assert first.original_input_for_reroute == "a separate learner task"
    assert duplicate.duplicate_input is True
    assert duplicate.should_reroute_original is False
    assert duplicate.original_input_for_reroute is None
    assert len(adapter.parse_calls) == 1
    assert adapter.text_calls == []


def test_supplied_first_turn_rule_skips_rule_builder() -> None:
    adapter = RecordingAdapter(
        [_route_draft("exit_rule")],
        ["model generated exit response"],
    )

    result = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=None,
        initial_rule=_rule(),
        interaction_session_id="interaction_session_supplied_rule",
        current_message="end this interaction",
        input_event_id="event_supplied_rule",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )

    assert result.rule_built is False
    assert result.transition.session.interaction_rule == _rule()
    assert len(adapter.parse_calls) == 1
    assert adapter.parse_calls[0]["schema"] is InteractionRouteModelDraft


def test_first_session_rule_is_model_structured_and_all_model_context_is_bounded() -> None:
    adapter = RecordingAdapter(
        [
            _rule().model_dump(mode="json"),
            _route_draft("continue_rule"),
        ],
        ["model generated first interaction reply"],
    )

    result = run_existing_board_interaction(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        session=None,
        initial_rule=None,
        interaction_session_id="interaction_session_new",
        current_message="first learner input",
        input_event_id="event_first",
        board_task_run_id="board_task_run_1",
        board_task_version_id="board_task_version_1",
    )

    assert result.rule_built is True
    assert result.transition.session.session_id == "interaction_session_new"
    assert result.transition.session.interaction_rule == _rule()
    assert adapter.parse_calls[0]["schema"] is InteractionRule
    assert adapter.parse_calls[1]["schema"] is InteractionRouteModelDraft
    assert adapter.call_instance_ids == [id(adapter), id(adapter), id(adapter)]

    all_prompts = [
        *(str(call["user_prompt"]) for call in adapter.parse_calls),
        *(str(call["user_prompt"]) for call in adapter.text_calls),
    ]
    assert all(FULL_BOARD_SENTINEL not in prompt for prompt in all_prompts)
    for prompt in all_prompts:
        payload = json.loads(prompt)
        if "approved_target" in payload:
            assert payload["approved_target"]["excerpt"] == (
                "bounded approved interaction excerpt"
            )
            assert payload["approved_target"]["before_text"] == ""
            assert payload["approved_target"]["after_text"] == ""

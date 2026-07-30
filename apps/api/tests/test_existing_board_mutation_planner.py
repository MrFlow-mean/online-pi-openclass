from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.models import BoardFocusRef, BoardTaskRequirementSheet
from app.services.existing_board.mutation_planner import (
    ConfirmedContentAbsentInsertion,
    MutationPlannerError,
    MutationPlannerModelDraft,
    plan_existing_board_mutation,
)


FULL_BOARD_SENTINEL = "FULL_BOARD_MUST_NEVER_REACH_MUTATION_PLANNER"
CONVERSATION_SENTINEL = "CONVERSATION_MUST_NEVER_REACH_MUTATION_PLANNER"
SOURCE_SENTINEL = "SOURCE_BODY_MUST_NEVER_REACH_MUTATION_PLANNER"


def _focus() -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id="lesson_1",
        document_id="document_1",
        segment_id="segment_1",
        kind="paragraph",
        heading_path=["Parent section"],
        excerpt="bounded authorized excerpt",
        before_text=FULL_BOARD_SENTINEL,
        after_text=SOURCE_SENTINEL,
        text_hash="target_text_hash",
        excerpt_hash="target_excerpt_hash",
        confidence=1.0,
    )


def _task(
    *,
    action: str = "edit",
    extent: str = "section",
    destination: str = "current_lesson",
    location_status: str = "resolved",
    location_kind: str = "target_range",
    confirmation_status: str = "none",
) -> BoardTaskRequirementSheet:
    focus = _focus()
    return BoardTaskRequirementSheet(
        location_kind=location_kind,
        target_hint="the already resolved bounded target",
        target_location=focus if location_status == "resolved" else None,
        location_status=location_status,
        requested_action=action,
        question_or_topic="revise the target and add supporting content",
        special_interaction_requirements="none",
        content_extent=extent,
        topic_relation="current_document",
        document_destination=destination,
        base_commit_id="commit_current",
        base_document_hash="document_hash_current",
        mutation_plan={"untrusted_previous_payload": FULL_BOARD_SENTINEL},
        interaction_session={"untrusted_conversation": CONVERSATION_SENTINEL},
        missing_items=[],
        progress=100,
        confirmation_status=confirmation_status,
    )


class RecordingAdapter:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []
        self.call_instance_ids: list[int] = []

    def parse_structured(self, **kwargs):
        self.calls.append(kwargs)
        self.call_instance_ids.append(id(self))
        return SimpleNamespace(output_parsed=self.output, activity=[])


def _draft(
    operations: list[dict[str, object]],
    *,
    extent: str = "section",
    destination: str = "current_lesson",
    topic_relation: str = "current_document",
    requires_confirmation: bool = False,
) -> dict[str, object]:
    return {
        "operations": operations,
        "extent": extent,
        "destination": destination,
        "topic_relation": topic_relation,
        "requires_confirmation": requires_confirmation,
        "reason": "model supplied planning rationale",
    }


def test_edit_and_write_order_is_preserved_without_full_board_context() -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "edit_target",
                    "action": "edit",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "Rewritten bounded content.",
                },
                {
                    "operation_id": "write_support",
                    "action": "write",
                    "binding": {"kind": "insertion_anchor", "position": "after"},
                    "content_markdown": "Supporting content.",
                },
            ]
        )
    )

    result = plan_existing_board_mutation(
        adapter=adapter,
        board_task=_task(),
        resolved_focus=_focus(),
        current_commit_id="commit_current",
        current_document_hash="document_hash_current",
        parent_heading_path=["Parent section"],
    )

    assert [operation.action for operation in result.plan.operations] == ["edit", "write"]
    assert [operation.operation_id for operation in result.plan.operations] == [
        "edit_target",
        "write_support",
    ]
    assert result.plan.operations[0].binding.kind == "target_range"
    assert result.plan.operations[0].binding.segment_id == "segment_1"
    assert result.plan.operations[1].binding.kind == "insertion_anchor"
    assert result.plan.operations[1].binding.position == "after"
    assert result.plan.base_commit_id == "commit_current"
    assert result.plan.base_document_hash == "document_hash_current"

    assert len(adapter.calls) == 1
    assert adapter.call_instance_ids == [id(adapter)]
    assert adapter.calls[0]["schema"] is MutationPlannerModelDraft
    assert adapter.calls[0]["allow_live_web_search"] is False
    prompt = str(adapter.calls[0]["user_prompt"])
    payload = json.loads(prompt)
    assert payload["resolved_target"]["excerpt"] == "bounded authorized excerpt"
    assert payload["resolved_target"]["before_text"] == ""
    assert payload["resolved_target"]["after_text"] == ""
    assert FULL_BOARD_SENTINEL not in prompt
    assert CONVERSATION_SENTINEL not in prompt
    assert SOURCE_SENTINEL not in prompt
    assert "board_document" not in prompt
    assert "conversation" not in prompt
    assert "source_body" not in prompt


@pytest.mark.parametrize(
    ("task_action", "extent", "operation_action"),
    [
        ("delete", "paragraph", "delete"),
        ("edit", "whole_board", "edit"),
    ],
)
def test_delete_and_whole_board_force_confirmation(
    task_action: str,
    extent: str,
    operation_action: str,
) -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "dangerous_operation",
                    "action": operation_action,
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "" if operation_action == "delete" else "Replacement.",
                }
            ],
            extent=extent,
            requires_confirmation=False,
        )
    )

    result = plan_existing_board_mutation(
        adapter=adapter,
        board_task=_task(action=task_action, extent=extent),
        resolved_focus=_focus(),
        current_commit_id="commit_current",
        current_document_hash="document_hash_current",
        parent_heading_path=["Parent section"],
    )

    assert result.plan.requires_confirmation is True
    assert result.plan.confirmation_status == "pending"
    assert result.plan.execution_allowed is False


@pytest.mark.parametrize("location_status", ["missing", "ambiguous", "selected"])
def test_unresolved_target_fails_before_model_call(location_status: str) -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "edit_target",
                    "action": "edit",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "Changed.",
                }
            ]
        )
    )

    with pytest.raises(MutationPlannerError, match="resolved target"):
        plan_existing_board_mutation(
            adapter=adapter,
            board_task=_task(location_status=location_status),
            resolved_focus=None,
            current_commit_id="commit_current",
            current_document_hash="document_hash_current",
            parent_heading_path=["Parent section"],
        )

    assert adapter.calls == []


def test_unresolved_destination_fails_before_model_call() -> None:
    adapter = RecordingAdapter(_draft([]))

    with pytest.raises(MutationPlannerError, match="destination"):
        plan_existing_board_mutation(
            adapter=adapter,
            board_task=_task(destination="unresolved"),
            resolved_focus=_focus(),
            current_commit_id="commit_current",
            current_document_hash="document_hash_current",
            parent_heading_path=["Parent section"],
        )

    assert adapter.calls == []


def test_confirmed_content_absent_location_can_only_create_an_insertion_plan() -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "write_missing_content",
                    "action": "write",
                    "binding": {"kind": "insertion_anchor", "position": "after"},
                    "content_markdown": "New bounded content.",
                }
            ]
        )
    )
    insertion = ConfirmedContentAbsentInsertion(
        confirmed=True,
        lesson_id="lesson_1",
        document_id="document_1",
        anchor_segment_id="segment_parent",
        anchor_text_hash="parent_hash",
        position="after",
        parent_heading_path=["Parent section"],
    )

    result = plan_existing_board_mutation(
        adapter=adapter,
        board_task=_task(
            action="write",
            location_status="content_absent",
            location_kind="insertion_anchor",
        ),
        content_absent_insertion=insertion,
        current_commit_id="commit_current",
        current_document_hash="document_hash_current",
        parent_heading_path=["Parent section"],
    )

    binding = result.plan.operations[0].binding
    assert binding.kind == "insertion_anchor"
    assert binding.segment_id == "segment_parent"
    assert binding.text_hash == "parent_hash"
    assert binding.position == "after"


def test_model_cannot_change_the_authorized_binding_kind() -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "invalid_edit",
                    "action": "edit",
                    "binding": {"kind": "insertion_anchor", "position": "after"},
                    "content_markdown": "Changed.",
                }
            ]
        )
    )

    with pytest.raises(MutationPlannerError, match="target_range"):
        plan_existing_board_mutation(
            adapter=adapter,
            board_task=_task(),
            resolved_focus=_focus(),
            current_commit_id="commit_current",
            current_document_hash="document_hash_current",
            parent_heading_path=["Parent section"],
        )

    assert len(adapter.calls) == 1


def test_resolved_target_cannot_be_rebound_to_another_parent_path() -> None:
    adapter = RecordingAdapter(_draft([]))

    with pytest.raises(MutationPlannerError, match="parent path"):
        plan_existing_board_mutation(
            adapter=adapter,
            board_task=_task(),
            resolved_focus=_focus(),
            current_commit_id="commit_current",
            current_document_hash="document_hash_current",
            parent_heading_path=["Different parent"],
        )

    assert adapter.calls == []


def test_model_cannot_add_delete_without_delete_authorization() -> None:
    adapter = RecordingAdapter(
        _draft(
            [
                {
                    "operation_id": "authorized_edit",
                    "action": "edit",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "Changed.",
                },
                {
                    "operation_id": "unauthorized_delete",
                    "action": "delete",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "",
                },
            ]
        )
    )

    with pytest.raises(MutationPlannerError, match="delete authorization"):
        plan_existing_board_mutation(
            adapter=adapter,
            board_task=_task(action="edit"),
            resolved_focus=_focus(),
            current_commit_id="commit_current",
            current_document_hash="document_hash_current",
            parent_heading_path=["Parent section"],
        )

    assert len(adapter.calls) == 1

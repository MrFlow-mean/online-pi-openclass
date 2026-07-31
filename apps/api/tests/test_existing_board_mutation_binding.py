from __future__ import annotations

import pytest
from app.models import BoardDocument, BoardFocusRef
from app.services.board_segment_index import (
    build_board_segment_index,
    segment_text_hash,
)
from app.services.existing_board import mutation_binding as binding_module
from app.services.existing_board.focus_resolver import resolve_board_focus
from app.services.existing_board.mutation_binding import (
    ConfirmedWriteAnchor,
    bind_and_execute_board_mutation,
)
from app.services.existing_board.mutation_plan import board_document_hash
from app.services.existing_board.mutation_planner import (
    BoardMutationPlanDraft,
    MutationPlannerBinding,
    MutationPlannerOperationDraft,
)
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document, document_to_markdown


def _document(markdown: str) -> BoardDocument:
    return build_document(
        title="Binding fixture",
        content_text=markdown,
        document_id="document_binding_fixture",
    )


def _focus(document: BoardDocument, text: str, *, occurrence: int = 0) -> BoardFocusRef:
    matches = [
        segment
        for segment in build_board_segment_index(document).segments
        if segment.text == text
    ]
    segment = matches[occurrence]
    return BoardFocusRef(
        source="board",
        lesson_id="lesson_binding_fixture",
        document_id=document.id,
        segment_id=segment.segment_id,
        kind=segment.kind,
        heading_path=list(segment.heading_path),
        excerpt=segment.text,
        text_hash=segment.text_hash,
        excerpt_hash=segment_text_hash(segment.text),
        confidence=1.0,
    )


def _section_focus(document: BoardDocument, heading: str) -> BoardFocusRef:
    lesson = create_empty_lesson("Binding fixture")
    lesson.id = "lesson_binding_fixture"
    lesson.board_document = document.model_copy(deep=True)
    result = resolve_board_focus(
        lesson,
        target_text=heading,
        content_extent="section",
    )
    assert result.status == "resolved"
    assert result.focus is not None
    return result.focus


def _operation(
    operation_id: str,
    action: str,
    focus: BoardFocusRef,
    *,
    position: str,
    content: str,
) -> MutationPlannerOperationDraft:
    return MutationPlannerOperationDraft(
        operation_id=operation_id,
        action=action,
        binding=MutationPlannerBinding(
            kind="insertion_anchor" if action == "write" else "target_range",
            position=position,
            lesson_id=focus.lesson_id or "",
            document_id=focus.document_id or "",
            segment_id=focus.segment_id or "",
            text_hash=focus.text_hash or "",
            excerpt_hash=focus.excerpt_hash or "",
            parent_heading_path=list(focus.heading_path),
        ),
        content_markdown=content,
    )


def _draft(
    document: BoardDocument,
    operations: list[MutationPlannerOperationDraft],
    *,
    extent: str = "section",
    requires_confirmation: bool = False,
    confirmation_status: str = "not_required",
    execution_allowed: bool = True,
    base_commit_id: str = "commit_current",
) -> BoardMutationPlanDraft:
    return BoardMutationPlanDraft(
        plan_id="mutationplan_binding_fixture",
        board_task_id="boardtask_binding_fixture",
        base_commit_id=base_commit_id,
        base_document_hash=board_document_hash(document),
        extent=extent,
        destination="current_lesson",
        topic_relation="current_document",
        requires_confirmation=requires_confirmation,
        confirmation_status=confirmation_status,
        execution_allowed=execution_allowed,
        operations=operations,
        reason="bounded test plan",
    )


def _confirmed_anchor(
    operation_id: str,
    focus: BoardFocusRef,
    *,
    position: str,
) -> ConfirmedWriteAnchor:
    return ConfirmedWriteAnchor(
        confirmed=True,
        operation_id=operation_id,
        lesson_id=focus.lesson_id or "",
        document_id=focus.document_id or "",
        segment_id=focus.segment_id or "",
        text_hash=focus.text_hash or "",
        position=position,
        parent_heading_path=list(focus.heading_path),
    )


def test_edit_and_confirmed_write_bind_once_then_execute_atomically(monkeypatch) -> None:
    document = _document("# Section\n\nOld paragraph.\n\nClosing.")
    focus = _focus(document, "Old paragraph.")
    draft = _draft(
        document,
        [
            _operation(
                "edit_target",
                "edit",
                focus,
                position="replace",
                content="Rewritten paragraph.",
            ),
            _operation(
                "write_example",
                "write",
                focus,
                position="after",
                content="Example paragraph.",
            ),
        ],
    )
    calls: list[object] = []
    real_executor = binding_module.execute_board_mutation_plan

    def recording_executor(plan, current_document, *, current_commit_id):
        calls.append(plan)
        return real_executor(
            plan,
            current_document,
            current_commit_id=current_commit_id,
        )

    monkeypatch.setattr(
        binding_module,
        "execute_board_mutation_plan",
        recording_executor,
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
        confirmed_write_anchors=[
            _confirmed_anchor("write_example", focus, position="after")
        ],
    )

    assert result.status == "applied"
    assert result.reason == "applied"
    assert result.document_changed is True
    assert result.atomic_operation_count == 2
    assert len(calls) == 1
    assert [operation.action for operation in calls[0].operations] == ["edit", "write"]
    assert document_to_markdown(result.document) == (
        "# Section\n\nRewritten paragraph.\n\nExample paragraph.\n\nClosing."
    )
    assert document_to_markdown(document) == "# Section\n\nOld paragraph.\n\nClosing."


def test_confirmed_section_end_write_stays_inside_the_resolved_section() -> None:
    document = _document(
        "# Root\n\n## Target\n\nPrompt.\n\n## Outside\n\nKeep outside."
    )
    focus = _section_focus(document, "Target")
    draft = _draft(
        document,
        [
            _operation(
                "write_section_answer",
                "write",
                focus,
                position="parent_end",
                content="Agent-generated answer.",
            )
        ],
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
        confirmed_write_anchors=[
            _confirmed_anchor(
                "write_section_answer",
                focus,
                position="parent_end",
            )
        ],
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == (
        "# Root\n\n## Target\n\nPrompt.\n\nAgent-generated answer."
        "\n\n## Outside\n\nKeep outside."
    )


def test_duplicate_text_binding_changes_only_the_explicit_segment() -> None:
    document = _document("# Section\n\nRepeated.\n\nRepeated.")
    second_focus = _focus(document, "Repeated.", occurrence=1)
    draft = _draft(
        document,
        [
            _operation(
                "edit_second",
                "edit",
                second_focus,
                position="replace",
                content="Changed second.",
            )
        ],
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=second_focus,
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == (
        "# Section\n\nRepeated.\n\nChanged second."
    )


def test_write_without_an_operation_specific_confirmed_anchor_is_zero_change(
    monkeypatch,
) -> None:
    document = _document("# Section\n\nTarget.")
    focus = _focus(document, "Target.")
    draft = _draft(
        document,
        [
            _operation(
                "write_unconfirmed",
                "write",
                focus,
                position="after",
                content="New content.",
            )
        ],
    )
    calls: list[object] = []
    monkeypatch.setattr(
        binding_module,
        "execute_board_mutation_plan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "rejected"
    assert result.reason == "write_anchor_not_confirmed"
    assert result.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")
    assert calls == []


@pytest.mark.parametrize(
    ("current_commit_id", "override_hash", "reason"),
    [
        ("commit_newer", None, "base_commit_mismatch"),
        ("commit_current", "stale_document_hash", "base_document_hash_mismatch"),
    ],
)
def test_stale_draft_is_rejected_before_binding_or_execution(
    current_commit_id: str,
    override_hash: str | None,
    reason: str,
) -> None:
    document = _document("# Section\n\nTarget.")
    focus = _focus(document, "Target.")
    draft = _draft(
        document,
        [
            _operation(
                "edit_target",
                "edit",
                focus,
                position="replace",
                content="Changed.",
            )
        ],
    )
    if override_hash is not None:
        draft = draft.model_copy(update={"base_document_hash": override_hash})

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id=current_commit_id,
        resolved_focus=focus,
    )

    assert result.status == "rejected"
    assert result.reason == reason
    assert result.bound_plan is None
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


def test_stale_or_mismatched_focus_cannot_expand_the_target() -> None:
    document = _document("# Section\n\nAuthorized target.\n\nOutside content.")
    focus = _focus(document, "Authorized target.")
    stale_focus = focus.model_copy(update={"excerpt": "Outside content."})
    draft = _draft(
        document,
        [
            _operation(
                "edit_target",
                "edit",
                focus,
                position="replace",
                content="Changed.",
            )
        ],
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=stale_focus,
    )

    assert result.status == "rejected"
    assert result.reason == "focus_excerpt_hash_mismatch"
    assert result.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


@pytest.mark.parametrize(
    ("action", "extent"),
    [("delete", "paragraph"), ("edit", "whole_board")],
)
def test_delete_and_whole_board_pending_confirmation_are_zero_change(
    action: str,
    extent: str,
) -> None:
    document = _document("Only authorized content.")
    focus = _focus(document, "Only authorized content.")
    draft = _draft(
        document,
        [
            _operation(
                "dangerous_operation",
                action,
                focus,
                position="replace",
                content="" if action == "delete" else "Replacement board.",
            )
        ],
        extent=extent,
        requires_confirmation=True,
        confirmation_status="pending",
        execution_allowed=False,
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "rejected"
    assert result.reason == "confirmation_required"
    assert result.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


def test_confirmed_whole_board_requires_the_focus_to_cover_the_exact_document() -> None:
    document = _document("# Section\n\nOnly paragraph.")
    focus = _focus(document, "Only paragraph.")
    draft = _draft(
        document,
        [
            _operation(
                "replace_board",
                "edit",
                focus,
                position="replace",
                content="Replacement board.",
            )
        ],
        extent="whole_board",
        requires_confirmation=True,
        confirmation_status="confirmed",
        execution_allowed=True,
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "rejected"
    assert result.reason == "whole_board_scope_not_authorized"
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


def test_confirmed_delete_includes_only_the_adjacent_markdown_separator() -> None:
    document = _document("# Section\n\nAuthorized target.\n\nOutside content.")
    focus = _focus(document, "Authorized target.")
    draft = _draft(
        document,
        [
            _operation(
                "delete_target",
                "delete",
                focus,
                position="replace",
                content="",
            )
        ],
        extent="paragraph",
        requires_confirmation=True,
        confirmation_status="confirmed",
        execution_allowed=True,
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == "# Section\n\nOutside content."
    assert result.execution_audit is not None
    assert result.execution_audit.authorized_scopes[0].start == len("# Section\n\n")


def test_cross_segment_section_edit_replaces_only_the_frozen_range() -> None:
    document = _document(
        "# Root\n\n## Target\n\nOld one.\n\n### Detail\n\nOld two."
        "\n\n## Outside\n\nKeep outside."
    )
    focus = _section_focus(document, "Target")
    draft = _draft(
        document,
        [
            _operation(
                "edit_section",
                "edit",
                focus,
                position="replace",
                content="## Replacement\n\nNew bounded section.",
            )
        ],
        extent="section",
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == (
        "# Root\n\n## Replacement\n\nNew bounded section."
        "\n\n## Outside\n\nKeep outside."
    )


def test_cross_segment_section_delete_does_not_remove_the_next_section() -> None:
    document = _document(
        "# Root\n\n## Target\n\nDelete one.\n\nDelete two."
        "\n\n## Outside\n\nKeep outside."
    )
    focus = _section_focus(document, "Target")
    draft = _draft(
        document,
        [
            _operation(
                "delete_section",
                "delete",
                focus,
                position="replace",
                content="",
            )
        ],
        extent="section",
        requires_confirmation=True,
        confirmation_status="confirmed",
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=focus,
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == (
        "# Root\n\n## Outside\n\nKeep outside."
    )


def test_tampered_frozen_segment_order_is_rejected_before_execution() -> None:
    document = _document("# Root\n\n## Target\n\nFirst.\n\nSecond.\n\n## Outside")
    focus = _section_focus(document, "Target")
    tampered = focus.model_copy(
        update={"source_segment_ids": list(reversed(focus.source_segment_ids))}
    )
    draft = _draft(
        document,
        [
            _operation(
                "edit_section",
                "edit",
                tampered,
                position="replace",
                content="## Replacement",
            )
        ],
        extent="section",
    )

    result = bind_and_execute_board_mutation(
        draft=draft,
        document=document,
        current_commit_id="commit_current",
        resolved_focus=tampered,
    )

    assert result.status == "rejected"
    assert result.reason == "focus_source_segment_order_mismatch"
    assert result.document_changed is False
    assert document_to_markdown(result.document) == document_to_markdown(document)

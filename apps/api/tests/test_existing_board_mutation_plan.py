from __future__ import annotations

import pytest

from app.models import BoardDocument
from app.services.existing_board.mutation_plan import (
    BoardInsertionAnchor,
    BoardMutationOperation,
    BoardMutationPlan,
    BoardMutationTargetRange,
    board_document_hash,
    execute_board_mutation_plan,
    mutation_text_hash,
)
from app.services.rich_document import build_document, document_to_markdown


def _document(markdown: str) -> BoardDocument:
    return build_document(
        title="Mutation fixture",
        content_text=markdown,
        document_id="doc_mutation_fixture",
    )


def _target(markdown: str, excerpt: str) -> BoardMutationTargetRange:
    start = markdown.index(excerpt)
    return BoardMutationTargetRange(
        start=start,
        end=start + len(excerpt),
        expected_excerpt=excerpt,
        expected_excerpt_hash=mutation_text_hash(excerpt),
    )


def _anchor(markdown: str, offset: int) -> BoardInsertionAnchor:
    left_context = markdown[max(0, offset - 24) : offset]
    right_context = markdown[offset : offset + 24]
    return BoardInsertionAnchor(
        offset=offset,
        left_context=left_context,
        right_context=right_context,
        left_context_hash=mutation_text_hash(left_context),
        right_context_hash=mutation_text_hash(right_context),
    )


def _plan(
    document: BoardDocument,
    operations: list[BoardMutationOperation],
    *,
    base_commit_id: str = "commit_base",
    extent: str = "section",
    requires_confirmation: bool = False,
    confirmation_status: str = "not_required",
) -> BoardMutationPlan:
    return BoardMutationPlan(
        plan_id="plan_fixture",
        base_commit_id=base_commit_id,
        base_document_hash=board_document_hash(document),
        extent=extent,
        destination="current_lesson",
        requires_confirmation=requires_confirmation,
        confirmation_status=confirmation_status,
        operations=operations,
    )


def test_edit_and_write_are_applied_atomically_in_plan_order() -> None:
    document = _document("# Guide\n\nOld paragraph.\n\nClosing.")
    markdown = document_to_markdown(document)
    target = _target(markdown, "Old paragraph.")
    insert_at = markdown.index("\n\nClosing.")
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="edit_body",
                action="edit",
                target_range=target,
                content_markdown="Clear paragraph.",
            ),
            BoardMutationOperation(
                operation_id="write_example",
                action="write",
                insertion_anchor=_anchor(markdown, insert_at),
                content_markdown="\n\nExample.",
            ),
        ],
    )

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id="commit_base",
    )

    assert result.status == "applied"
    assert document_to_markdown(result.document) == (
        "# Guide\n\nClear paragraph.\n\nExample.\n\nClosing."
    )
    assert result.audit.applied_operation_ids == ["edit_body", "write_example"]
    assert result.audit.document_changed is True
    assert document_to_markdown(document) == markdown


@pytest.mark.parametrize(
    ("action", "extent", "target_excerpt"),
    [
        ("delete", "paragraph", "Old paragraph."),
        ("edit", "whole_board", None),
    ],
)
def test_dangerous_mutations_are_zero_change_until_confirmed(
    action: str,
    extent: str,
    target_excerpt: str | None,
) -> None:
    document = _document("# Guide\n\nOld paragraph.")
    markdown = document_to_markdown(document)
    excerpt = target_excerpt or markdown
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="dangerous_change",
                action=action,
                target_range=_target(markdown, excerpt),
                content_markdown="Replacement board." if action == "edit" else "",
            )
        ],
        extent=extent,
        requires_confirmation=True,
        confirmation_status="pending",
    )

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id="commit_base",
    )

    assert result.status == "rejected"
    assert result.audit.reason == "confirmation_required"
    assert result.audit.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


@pytest.mark.parametrize(
    ("current_commit_id", "base_document_hash", "reason"),
    [
        ("commit_newer", None, "base_commit_mismatch"),
        ("commit_base", "stale_hash", "base_document_hash_mismatch"),
    ],
)
def test_stale_plan_is_rejected_before_any_mutation(
    current_commit_id: str,
    base_document_hash: str | None,
    reason: str,
) -> None:
    document = _document("# Guide\n\nOld paragraph.")
    markdown = document_to_markdown(document)
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="edit_body",
                action="edit",
                target_range=_target(markdown, "Old paragraph."),
                content_markdown="New paragraph.",
            )
        ],
    )
    if base_document_hash is not None:
        plan = plan.model_copy(update={"base_document_hash": base_document_hash})

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id=current_commit_id,
    )

    assert result.status == "rejected"
    assert result.audit.reason == reason
    assert result.audit.applied_operation_ids == []
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


def test_second_invalid_operation_rolls_back_the_first_operation() -> None:
    document = _document("# Guide\n\nOld paragraph.\n\nClosing.")
    markdown = document_to_markdown(document)
    invalid_anchor = _anchor(markdown, markdown.index("Closing."))
    invalid_anchor = invalid_anchor.model_copy(
        update={"right_context": "not the current document"}
    )
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="valid_edit",
                action="edit",
                target_range=_target(markdown, "Old paragraph."),
                content_markdown="Changed paragraph.",
            ),
            BoardMutationOperation(
                operation_id="invalid_write",
                action="write",
                insertion_anchor=invalid_anchor,
                content_markdown="Example.",
            ),
        ],
    )

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id="commit_base",
    )

    assert result.status == "rejected"
    assert result.audit.reason == "anchor_context_hash_mismatch"
    assert result.audit.applied_operation_ids == []
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


def test_new_lesson_destination_never_mutates_the_current_document() -> None:
    document = _document("# Guide\n\nOld paragraph.")
    markdown = document_to_markdown(document)
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="new_lesson_content",
                action="edit",
                target_range=_target(markdown, "Old paragraph."),
                content_markdown="Independent content.",
            )
        ],
    ).model_copy(update={"destination": "new_lesson"})

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id="commit_base",
    )

    assert result.status == "rejected"
    assert result.audit.reason == "destination_requires_new_lesson_handler"
    assert result.audit.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")


@pytest.mark.parametrize(
    "unauthorized_target",
    [
        BoardMutationTargetRange(
            start=0,
            end=7,
            expected_excerpt="# Other",
            expected_excerpt_hash=mutation_text_hash("# Other"),
        ),
        BoardMutationTargetRange(
            start=999,
            end=1004,
            expected_excerpt="Other",
            expected_excerpt_hash=mutation_text_hash("Other"),
        ),
    ],
)
def test_target_outside_the_authorized_excerpt_is_rejected(
    unauthorized_target: BoardMutationTargetRange,
) -> None:
    document = _document("# Guide\n\nAuthorized paragraph.")
    plan = _plan(
        document,
        [
            BoardMutationOperation(
                operation_id="out_of_scope_edit",
                action="edit",
                target_range=unauthorized_target,
                content_markdown="Changed.",
            )
        ],
    )

    result = execute_board_mutation_plan(
        plan,
        document,
        current_commit_id="commit_base",
    )

    assert result.status == "rejected"
    assert result.audit.reason in {"target_out_of_bounds", "target_excerpt_mismatch"}
    assert result.audit.document_changed is False
    assert result.document.model_dump(mode="json") == document.model_dump(mode="json")

from __future__ import annotations

from app.models import SelectionRef
from app.services.board_segment_index import (
    build_board_segment_index,
    segment_text_hash,
)
from app.services.existing_board.focus_resolver import (
    FocusResolver,
    resolve_board_focus,
)
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


def _lesson(content: str):
    lesson = create_empty_lesson("Resolver test")
    lesson.board_document = build_document(
        title="Resolver test",
        content_text=content,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    return lesson


def test_frozen_selection_resolves_only_after_identity_and_hash_validation() -> None:
    lesson = _lesson("# Root\n\n## Target\n\nSelected paragraph")
    segment = build_board_segment_index(lesson.board_document).segments[-1]

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="Selected paragraph",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=segment.segment_id,
            text_hash=segment.text_hash,
        ),
    )

    assert result.status == "resolved"
    assert result.machine_reason == "resolved_by_selection"
    assert result.focus is not None
    assert result.focus.segment_id == segment.segment_id
    assert result.focus.confidence == 1.0


def test_selection_from_another_lesson_fails_closed_without_text_fallback() -> None:
    lesson = _lesson("# Root\n\nShared text")

    result = resolve_board_focus(
        lesson,
        target_text="Shared text",
        selection=SelectionRef(
            kind="board",
            excerpt="Shared text",
            lesson_id="lesson_other",
            document_id=lesson.board_document.id,
        ),
    )

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "selection_lesson_mismatch"
    assert result.focus is None


def test_selection_from_another_document_fails_closed() -> None:
    lesson = _lesson("# Root\n\nShared text")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="Shared text",
            lesson_id=lesson.id,
            document_id="doc_other",
        ),
    )

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "selection_document_mismatch"


def test_stale_selection_hash_fails_closed_and_returns_bounded_current_candidate() -> None:
    lesson = _lesson("# Root\n\nCurrent paragraph")
    segment = build_board_segment_index(lesson.board_document).segments[-1]

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="Current paragraph",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=segment.segment_id,
            text_hash=segment_text_hash("Old paragraph"),
        ),
    )

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "selection_stale_hash"
    assert len(result.candidates) == 1
    assert result.candidates[0].segment_id == segment.segment_id


def test_selection_from_a_stale_board_commit_fails_closed() -> None:
    lesson = _lesson("# Root\n\nCurrent paragraph")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="Current paragraph",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id="commit_before_selection",
        ),
    )

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "selection_stale_version"


def test_selection_from_the_current_board_commit_can_resolve() -> None:
    lesson = _lesson("# Root\n\nCurrent paragraph")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="Current paragraph",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
        ),
    )

    assert result.status == "resolved"
    assert result.machine_reason == "resolved_by_selection"


def test_target_range_preserves_the_exact_frozen_selection_excerpt() -> None:
    lesson = _lesson("# Root\n\nPrefix exact selected phrase suffix.")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            location_kind="target_range",
            excerpt="exact selected phrase",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
        ),
    )

    assert result.status == "resolved"
    assert result.focus is not None
    assert result.focus.excerpt == "exact selected phrase"
    assert result.focus.excerpt_hash == segment_text_hash("exact selected phrase")


def test_cursor_anchor_resolves_from_frozen_before_and_after_context() -> None:
    lesson = _lesson("# Root\n\nAlpha before cursor after omega.\n\nUnrelated paragraph.")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            location_kind="insertion_anchor",
            excerpt="before cursor | after omega",
            before_text="Alpha before cursor",
            after_text="after omega.",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
        ),
    )

    assert result.status == "resolved"
    assert result.focus is not None
    assert result.focus.before_text == "Alpha before cursor"
    assert result.focus.after_text == "after omega."


def test_cursor_anchor_with_duplicate_context_returns_candidates() -> None:
    lesson = _lesson("# Root\n\nSame anchor text.\n\nSame anchor text.")

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            location_kind="insertion_anchor",
            excerpt="Same anchor",
            before_text="Same anchor",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
        ),
    )

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "ambiguous_candidates"
    assert len(result.candidates) == 2


def test_unique_heading_and_structural_ordinal_resolve_deterministically() -> None:
    lesson = _lesson(
        "# Root\n\n## Preparation\n\nFirst body\n\n## Execution\n\nSecond body"
    )

    heading = resolve_board_focus(lesson, target_text="Execution")
    ordinal = resolve_board_focus(lesson, target_text="第2节")

    assert heading.status == "resolved"
    assert heading.machine_reason == "resolved_by_heading"
    assert heading.focus is not None and heading.focus.excerpt == "Execution"
    assert ordinal.status == "resolved"
    assert ordinal.machine_reason == "resolved_by_ordinal"
    assert ordinal.focus is not None and ordinal.focus.excerpt == "Execution"


def test_paragraph_ordinal_counts_paragraph_segments_only() -> None:
    lesson = _lesson("# Root\n\nFirst paragraph\n\n## Detail\n\nSecond paragraph")

    result = resolve_board_focus(lesson, target_text="请定位第二段")

    assert result.status == "resolved"
    assert result.machine_reason == "resolved_by_ordinal"
    assert result.focus is not None and result.focus.excerpt == "Second paragraph"


def test_non_ordinal_phrases_do_not_trigger_structural_resolution() -> None:
    lesson = _lesson("# Root\n\n## First\n\nAlpha\n\n## Second\n\nBeta")

    for target_text in ("共2节内容", "第零节"):
        result = resolve_board_focus(lesson, target_text=target_text)

        assert result.machine_reason != "resolved_by_ordinal"


def test_unique_text_clue_resolves_only_above_threshold_and_margin() -> None:
    lesson = _lesson(
        "# Root\n\nThe preparation material is listed here.\n\n"
        "The feedback mechanism adjusts the next action.\n\nA separate closing note."
    )

    result = FocusResolver().resolve(lesson, target_text="feedback mechanism")

    assert result.status == "resolved"
    assert result.machine_reason == "resolved_by_text_clue"
    assert result.focus is not None
    assert "feedback mechanism" in result.focus.excerpt


def test_duplicate_text_is_ambiguous_and_candidates_are_limited_to_five() -> None:
    lesson = _lesson("# Root\n\n" + "\n\n".join(["Repeated target"] * 7))

    result = resolve_board_focus(lesson, target_text="Repeated target")

    assert result.status == "target_not_resolved"
    assert result.machine_reason == "ambiguous_candidates"
    assert result.focus is None
    assert len(result.candidates) == 5


def test_low_similarity_clue_does_not_manufacture_a_focus() -> None:
    lesson = _lesson("# Root\n\nAlpha material\n\nBeta material")

    result = resolve_board_focus(lesson, target_text="unrelated quantum orchard")

    assert result.status == "target_not_resolved"
    assert result.machine_reason in {"target_not_found", "below_confidence_threshold"}
    assert result.focus is None


def test_resolution_output_never_contains_the_full_board_or_adjacent_secret() -> None:
    secret = "SECRET_OUTSIDE_TARGET"
    long_target = "target-start " + ("x" * 500) + " target-end"
    lesson = _lesson(f"# Root\n\n{long_target}\n\n{secret}")
    segment = build_board_segment_index(lesson.board_document).segments[1]

    result = resolve_board_focus(
        lesson,
        selection=SelectionRef(
            kind="board",
            excerpt="target-start",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=segment.segment_id,
            text_hash=segment.text_hash,
        ),
    )
    serialized = result.model_dump_json()

    assert result.status == "resolved"
    assert result.focus is not None and len(result.focus.excerpt) <= 320
    assert secret not in serialized
    assert result.focus.before_text == ""
    assert result.focus.after_text == ""


def test_section_extent_freezes_the_complete_heading_subtree_only() -> None:
    lesson = _lesson(
        "# Root\n\n## Target\n\nFirst body.\n\n### Detail\n\nNested body."
        "\n\n## Outside\n\nSECRET_OUTSIDE_SECTION"
    )
    index = build_board_segment_index(lesson.board_document)

    result = resolve_board_focus(
        lesson,
        target_text="Target",
        content_extent="section",
    )

    assert result.status == "resolved"
    assert result.focus is not None
    assert result.focus.excerpt == (
        "## Target\n\nFirst body.\n\n### Detail\n\nNested body."
    )
    assert result.focus.source_segment_ids == [
        segment.segment_id for segment in index.segments[1:5]
    ]
    assert result.focus.order_start == 1
    assert result.focus.order_end == 4
    assert "SECRET_OUTSIDE_SECTION" not in result.model_dump_json()


def test_adjacent_frozen_board_selections_merge_into_one_ordered_range() -> None:
    lesson = _lesson("# Root\n\nAlpha selected.\n\nMiddle selected.\n\nOmega outside.")
    segments = build_board_segment_index(lesson.board_document).segments
    selections = [
        SelectionRef(
            kind="board",
            location_kind="target_range",
            excerpt=segment.text,
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
            segment_id=segment.segment_id,
            text_hash=segment.text_hash,
        )
        for segment in segments[1:3]
    ]

    result = FocusResolver().resolve_many(lesson, selections=selections)

    assert result.status == "resolved"
    assert result.focus is not None
    assert result.focus.excerpt == "Alpha selected.\n\nMiddle selected."
    assert result.focus.source_segment_ids == [
        segments[1].segment_id,
        segments[2].segment_id,
    ]
    assert result.focus.order_start == 1
    assert result.focus.order_end == 2


def test_non_adjacent_or_reversed_frozen_selections_fail_closed() -> None:
    lesson = _lesson("# Root\n\nAlpha.\n\nBeta.\n\nGamma.")
    segments = build_board_segment_index(lesson.board_document).segments

    def selection(segment_index: int) -> SelectionRef:
        segment = segments[segment_index]
        return SelectionRef(
            kind="board",
            location_kind="target_range",
            excerpt=segment.text,
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            source_commit_id=current_head_commit(lesson).id,
            segment_id=segment.segment_id,
            text_hash=segment.text_hash,
        )

    for selections in (
        [selection(1), selection(3)],
        [selection(2), selection(1)],
    ):
        result = FocusResolver().resolve_many(lesson, selections=selections)

        assert result.status == "target_not_resolved"
        assert result.machine_reason == "ambiguous_candidates"
        assert result.focus is None

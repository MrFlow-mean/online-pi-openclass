from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from app.models import BoardFocusRef, BoardSegment, Lesson, SelectionRef
from app.services.board_segment_index import (
    build_board_segment_index,
    compact_segment_text,
    segment_text_hash,
)
from app.services.history import current_head_commit


TargetResolutionStatus = Literal["resolved", "target_not_resolved"]
TargetResolutionReason = Literal[
    "resolved_by_selection",
    "resolved_by_heading",
    "resolved_by_ordinal",
    "resolved_by_text_clue",
    "board_empty",
    "target_missing",
    "selection_kind_mismatch",
    "selection_lesson_mismatch",
    "selection_document_mismatch",
    "selection_stale_version",
    "selection_segment_missing",
    "selection_stale_hash",
    "selection_text_mismatch",
    "ambiguous_candidates",
    "below_confidence_threshold",
    "target_not_found",
]


class TargetResolution(BaseModel):
    status: TargetResolutionStatus
    machine_reason: TargetResolutionReason
    focus: BoardFocusRef | None = None
    candidates: list[BoardFocusRef] = Field(default_factory=list, max_length=5)


class FocusResolver:
    """Resolve a board target without model calls or full-document output."""

    def __init__(self, *, confidence_threshold: float = 0.72, candidate_margin: float = 0.12) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if not 0.0 <= candidate_margin <= 1.0:
            raise ValueError("candidate_margin must be between 0 and 1")
        self.confidence_threshold = confidence_threshold
        self.candidate_margin = candidate_margin

    def resolve(
        self,
        lesson: Lesson,
        *,
        target_text: str = "",
        selection: SelectionRef | None = None,
    ) -> TargetResolution:
        index = build_board_segment_index(lesson.board_document)
        segments = [segment for segment in index.segments if compact_segment_text(segment.text)]
        if not segments:
            return self._unresolved("board_empty")

        if selection is not None:
            return self._resolve_selection(lesson, segments, selection)

        target = compact_segment_text(target_text, limit=500)
        if not target:
            return self._unresolved("target_missing")

        heading_matches = [
            segment
            for segment in segments
            if segment.kind == "heading" and _normalize(segment.text) == _normalize(target)
        ]
        if len(heading_matches) == 1:
            return self._resolved(
                lesson,
                heading_matches[0],
                confidence=0.99,
                reason="resolved_by_heading",
            )
        if len(heading_matches) > 1:
            return self._unresolved(
                "ambiguous_candidates",
                candidates=self._candidate_refs(lesson, heading_matches, confidence=0.99),
            )

        ordinal = _parse_ordinal_reference(target)
        if ordinal is not None:
            ordinal_matches = _ordinal_candidates(segments, *ordinal)
            if len(ordinal_matches) == 1:
                return self._resolved(
                    lesson,
                    ordinal_matches[0],
                    confidence=0.96,
                    reason="resolved_by_ordinal",
                )
            if len(ordinal_matches) > 1:
                return self._unresolved(
                    "ambiguous_candidates",
                    candidates=self._candidate_refs(lesson, ordinal_matches, confidence=0.96),
                )

        ranked = sorted(
            ((self._text_score(target, segment), segment) for segment in segments),
            key=lambda item: (-item[0], item[1].order_index, item[1].segment_id),
        )
        best_score, best_segment = ranked[0]
        candidates = [
            self._focus_ref(lesson, segment, confidence=score, reason="candidate")
            for score, segment in ranked[:5]
            if score > 0.0
        ]
        if best_score < 0.25:
            return self._unresolved("target_not_found", candidates=candidates)
        if best_score < self.confidence_threshold:
            return self._unresolved("below_confidence_threshold", candidates=candidates)

        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score - runner_up < self.candidate_margin:
            return self._unresolved("ambiguous_candidates", candidates=candidates)

        return self._resolved(
            lesson,
            best_segment,
            confidence=best_score,
            reason="resolved_by_text_clue",
        )

    def _resolve_selection(
        self,
        lesson: Lesson,
        segments: list[BoardSegment],
        selection: SelectionRef,
    ) -> TargetResolution:
        document = lesson.board_document
        if selection.kind != "board":
            return self._unresolved("selection_kind_mismatch")
        if selection.lesson_id and selection.lesson_id != lesson.id:
            return self._unresolved("selection_lesson_mismatch")
        if selection.document_id and selection.document_id != document.id:
            return self._unresolved("selection_document_mismatch")
        if (
            selection.source_commit_id
            and selection.source_commit_id != current_head_commit(lesson).id
        ):
            return self._unresolved("selection_stale_version")

        if selection.segment_id:
            segment = next(
                (candidate for candidate in segments if candidate.segment_id == selection.segment_id),
                None,
            )
            if segment is None:
                return self._unresolved("selection_segment_missing")
            if selection.text_hash and not _selection_hash_matches(selection, segment):
                return self._unresolved(
                    "selection_stale_hash",
                    candidates=self._candidate_refs(lesson, [segment], confidence=0.0),
                )
            if selection.excerpt and not _selection_excerpt_matches(selection.excerpt, segment.text):
                return self._unresolved(
                    "selection_text_mismatch",
                    candidates=self._candidate_refs(lesson, [segment], confidence=0.0),
                )
            return self._resolved(
                lesson,
                segment,
                confidence=1.0 if selection.text_hash else 0.99,
                reason="resolved_by_selection",
            )

        if selection.text_hash:
            matches = [
                segment
                for segment in segments
                if _selection_hash_matches(selection, segment)
            ]
            if not matches:
                return self._unresolved("selection_stale_hash")
        else:
            matches = [
                segment
                for segment in segments
                if _selection_excerpt_matches(selection.excerpt, segment.text)
            ]
            if not matches:
                return self._unresolved("selection_text_mismatch")

        matches = _narrow_by_heading_path(matches, selection.heading_path)
        if len(matches) != 1:
            return self._unresolved(
                "ambiguous_candidates",
                candidates=self._candidate_refs(
                    lesson,
                    matches,
                    confidence=0.98 if selection.text_hash else 0.94,
                ),
            )
        return self._resolved(
            lesson,
            matches[0],
            confidence=0.98 if selection.text_hash else 0.94,
            reason="resolved_by_selection",
        )

    def _text_score(self, target: str, segment: BoardSegment) -> float:
        query = _normalize(target)
        text = _normalize(segment.text)
        if not query or not text:
            return 0.0
        if query == text:
            return 1.0
        if len(query) >= 2 and query in text:
            return 0.94
        if len(text) >= 4 and text in query:
            return 0.86

        query_grams = _character_grams(query)
        text_grams = _character_grams(text)
        overlap = len(query_grams & text_grams)
        coverage = overlap / len(query_grams) if query_grams else 0.0
        dice = (2 * overlap / (len(query_grams) + len(text_grams))) if query_grams and text_grams else 0.0
        sequence = SequenceMatcher(None, query, text, autojunk=False).ratio()
        score = 0.50 * coverage + 0.30 * dice + 0.20 * sequence

        heading_text = _normalize(" ".join(segment.heading_path))
        if heading_text and len(query) >= 2 and query in heading_text:
            score += 0.04
        return min(round(score, 6), 1.0)

    def _resolved(
        self,
        lesson: Lesson,
        segment: BoardSegment,
        *,
        confidence: float,
        reason: TargetResolutionReason,
    ) -> TargetResolution:
        return TargetResolution(
            status="resolved",
            machine_reason=reason,
            focus=self._focus_ref(lesson, segment, confidence=confidence, reason=reason),
        )

    def _unresolved(
        self,
        reason: TargetResolutionReason,
        *,
        candidates: list[BoardFocusRef] | None = None,
    ) -> TargetResolution:
        return TargetResolution(
            status="target_not_resolved",
            machine_reason=reason,
            candidates=(candidates or [])[:5],
        )

    def _candidate_refs(
        self,
        lesson: Lesson,
        segments: list[BoardSegment],
        *,
        confidence: float,
    ) -> list[BoardFocusRef]:
        return [
            self._focus_ref(lesson, segment, confidence=confidence, reason="candidate")
            for segment in segments[:5]
        ]

    @staticmethod
    def _focus_ref(
        lesson: Lesson,
        segment: BoardSegment,
        *,
        confidence: float,
        reason: str,
    ) -> BoardFocusRef:
        # compact_segment_text appends a three-character ellipsis after its slice.
        excerpt = compact_segment_text(segment.text, limit=318)
        heading_path = [compact_segment_text(item, limit=118) for item in segment.heading_path[-5:]]
        label = heading_path[-1] if heading_path else f"{segment.kind}:{segment.order_index + 1}"
        return BoardFocusRef(
            source="board",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=segment.segment_id,
            kind=segment.kind,
            heading_path=heading_path,
            excerpt=excerpt,
            text_hash=segment.text_hash,
            excerpt_hash=segment_text_hash(excerpt),
            confidence=max(0.0, min(float(confidence), 1.0)),
            reason=reason,
            display_label=compact_segment_text(label, limit=118),
            match_id=f"focus_{segment.text_hash}_{segment.order_index}",
            source_segment_ids=[segment.segment_id],
            order_start=segment.order_index,
            order_end=segment.order_index,
            score_breakdown={"deterministic_score": max(0.0, min(float(confidence), 1.0))},
        )


def resolve_board_focus(
    lesson: Lesson,
    *,
    target_text: str = "",
    selection: SelectionRef | None = None,
    confidence_threshold: float = 0.72,
    candidate_margin: float = 0.12,
) -> TargetResolution:
    return FocusResolver(
        confidence_threshold=confidence_threshold,
        candidate_margin=candidate_margin,
    ).resolve(lesson, target_text=target_text, selection=selection)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())


def _selection_excerpt_matches(excerpt: str, segment_text: str) -> bool:
    selected = _normalize(excerpt)
    current = _normalize(segment_text)
    if not selected or not current:
        return False
    return selected == current or selected in current


def _selection_hash_matches(selection: SelectionRef, segment: BoardSegment) -> bool:
    if not selection.text_hash:
        return True
    if selection.text_hash == segment.text_hash:
        return True
    return (
        selection.text_hash == segment_text_hash(selection.excerpt)
        and _selection_excerpt_matches(selection.excerpt, segment.text)
    )


def _narrow_by_heading_path(
    matches: list[BoardSegment],
    heading_path: list[str],
) -> list[BoardSegment]:
    if len(matches) <= 1 or not heading_path:
        return matches
    expected = [_normalize(item) for item in heading_path if _normalize(item)]
    narrowed = [
        segment
        for segment in matches
        if [_normalize(item) for item in segment.heading_path[-len(expected) :]] == expected
    ]
    return narrowed or matches


_ORDINAL_RE = re.compile(r"第\s*([0-9０-９零一二两三四五六七八九十百]+)\s*(小节|章|节|部分|段|项|条)")


def _parse_ordinal_reference(value: str) -> tuple[int, str] | None:
    match = _ORDINAL_RE.search(unicodedata.normalize("NFKC", value or ""))
    if match is None:
        return None
    number = _ordinal_number(match.group(1))
    if number is None or number < 1:
        return None
    return number, match.group(2)


def _ordinal_number(value: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", value)
    if normalized.isdigit():
        return int(normalized)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = 0
    current = 0
    for character in normalized:
        if character in digits:
            current = digits[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def _ordinal_candidates(
    segments: list[BoardSegment],
    number: int,
    unit: str,
) -> list[BoardSegment]:
    if unit == "段":
        pool = [segment for segment in segments if segment.kind == "paragraph"]
    elif unit in {"项", "条"}:
        pool = [segment for segment in segments if segment.kind == "list"]
    else:
        headings = [segment for segment in segments if segment.kind == "heading"]
        explicit = [segment for segment in headings if _heading_ordinal(segment.text) == number]
        if explicit:
            return explicit
        pool = _structural_headings(headings)
    return [pool[number - 1]] if number <= len(pool) else []


def _heading_ordinal(value: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", value or "")
    chinese = re.match(r"^\s*第\s*([0-9零一二两三四五六七八九十百]+)\s*(?:小节|章|节|部分)", normalized)
    if chinese:
        return _ordinal_number(chinese.group(1))
    numeric = re.match(r"^\s*(\d+)\s*[.、．)]", normalized)
    return int(numeric.group(1)) if numeric else None


def _structural_headings(headings: list[BoardSegment]) -> list[BoardSegment]:
    if len(headings) <= 1:
        return headings
    first_depth = len(headings[0].heading_path)
    deeper = [segment for segment in headings[1:] if len(segment.heading_path) > first_depth]
    if not deeper:
        return headings
    direct_depth = min(len(segment.heading_path) for segment in deeper)
    return [segment for segment in deeper if len(segment.heading_path) == direct_depth]


def _character_grams(value: str) -> set[str]:
    if not value:
        return set()
    if len(value) == 1:
        return {value}
    return {value[index : index + 2] for index in range(len(value) - 1)}

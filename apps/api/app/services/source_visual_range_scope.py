from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from app.services.source_visual_extraction_types import RawSourceVisual, SourceVisualAdapterResult


def scope_visual_adapter_result(
    result: SourceVisualAdapterResult,
    *,
    source_range: dict[str, Any],
    path: Path,
) -> SourceVisualAdapterResult:
    """Keep only visuals that are provably inside one authenticated catalog range."""

    scoped_visuals, visuals_supported = _scoped_raw_visuals(
        result.visuals,
        source_range=source_range,
        path=path,
    )
    scoped_anchors, anchors_supported = _scoped_raw_visuals(
        result.native_chart_anchors,
        source_range=source_range,
        path=path,
    )
    if not visuals_supported or not anchors_supported:
        return SourceVisualAdapterResult(
            status="failed",
            warnings=[
                "The selected catalog range cannot yet prove the position of every source visual."
            ],
        )
    for raw in [*scoped_visuals, *scoped_anchors]:
        raw.metadata = {
            **raw.metadata,
            "source_range_anchor_verified": True,
            "source_range": source_range,
        }
    return SourceVisualAdapterResult(
        visuals=scoped_visuals,
        warnings=list(result.warnings),
        status=result.status,
        native_chart_count=len(scoped_anchors),
        native_chart_anchors=scoped_anchors,
    )


def _scoped_raw_visuals(
    visuals: Sequence[RawSourceVisual],
    *,
    source_range: dict[str, Any],
    path: Path,
) -> tuple[list[RawSourceVisual], bool]:
    kind = str(source_range.get("kind") or "")
    start = _range_int(source_range.get("start"))
    end = _range_int(source_range.get("end"))
    if kind in {"pdf_pages", "ppt_slides"} and start is not None and end is not None:
        return [
            visual
            for visual in visuals
            if (visual.slide_no if kind == "ppt_slides" else visual.page_no) is not None
            and start
            <= int(visual.slide_no if kind == "ppt_slides" else visual.page_no or 0)
            <= end
        ], True
    if kind == "docx_paragraphs" and start is not None and end is not None:
        if any(visual.paragraph_index is None for visual in visuals):
            return [], not visuals
        return [
            visual
            for visual in visuals
            if visual.paragraph_index is not None and start <= visual.paragraph_index <= end
        ], True
    if kind == "epub_spine":
        selected_indices = _selected_epub_spine_indices(visuals, source_range=source_range)
        if selected_indices is None:
            return [], not visuals
        return [
            visual
            for visual in visuals
            if _range_int(visual.metadata.get("epub_spine_index")) in selected_indices
        ], True
    if kind == "sheet_rows" and start is not None and end is not None:
        container = str(source_range.get("container") or "").strip()
        selected: list[RawSourceVisual] = []
        for visual in visuals:
            if container and visual.sheet_name and visual.sheet_name != container:
                continue
            row_range = _spreadsheet_visual_rows(visual)
            if row_range is None:
                return [], not visuals
            first_row, last_row = row_range
            if first_row <= end and last_row >= start:
                selected.append(visual)
        return selected, True
    if kind == "text_lines" and start is not None and end is not None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], False
        selected = []
        for visual in visuals:
            if visual.text_offset is None:
                return [], not visuals
            line_no = text.count("\n", 0, max(0, visual.text_offset)) + 1
            if start <= line_no <= end:
                selected.append(visual)
        return selected, True
    if kind == "dom_anchor":
        metadata = dict(source_range.get("metadata") or {})
        start_offset = _range_int(metadata.get("start_text_offset"))
        end_offset = _range_int(metadata.get("end_text_offset"))
        if start_offset is None or end_offset is None:
            return [], not visuals
        if any(visual.text_offset is None for visual in visuals):
            return [], not visuals
        return [
            visual
            for visual in visuals
            if visual.text_offset is not None and start_offset <= visual.text_offset < end_offset
        ], True
    if kind == "structured_path":
        return [], not visuals
    return [], not visuals


def _selected_epub_spine_indices(
    visuals: Sequence[RawSourceVisual],
    *,
    source_range: dict[str, Any],
) -> set[int] | None:
    start = source_range.get("start")
    end = source_range.get("end")
    start_index = _range_int(start)
    end_index = _range_int(end)
    if start_index is not None and end_index is not None:
        return set(range(start_index, end_index + 1))
    container = str(source_range.get("container") or "").strip()
    item_indices = {
        str(visual.metadata.get("epub_spine_item") or ""): _range_int(
            visual.metadata.get("epub_spine_index")
        )
        for visual in visuals
    }
    if container:
        index = item_indices.get(container)
        return {index} if index is not None else None
    first = item_indices.get(str(start or ""))
    last = item_indices.get(str(end or ""))
    if first is None or last is None or last < first:
        return None
    return set(range(first, last + 1))


def _spreadsheet_visual_rows(visual: RawSourceVisual) -> tuple[int, int] | None:
    reference = str(visual.metadata.get("table_reference") or "")
    row_numbers = [int(value) for value in re.findall(r"[A-Za-z]+(\d+)", reference)]
    if row_numbers:
        return min(row_numbers), max(row_numbers)
    max_row = _range_int(visual.metadata.get("max_row"))
    if max_row is None or len(visual.bbox) < 4:
        return None
    first = max(1, int(visual.bbox[1] * max_row) + 1)
    last = max(first, int(round(visual.bbox[3] * max_row)))
    return first, last


def _range_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    return int(raw) if raw.isdigit() else None

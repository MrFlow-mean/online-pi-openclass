from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from app.models import (
    RetrievalEvidence,
    SourceChapter,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services.source_structure_store import SourceStructureStore, source_structure_store
from app.services.source_visual_extraction import (
    SourceVisualExtractor,
    source_visual_extractor,
)


class SourceRangeVisualError(RuntimeError):
    """Raised when every visual in an authenticated source range cannot be preserved."""


def extract_verified_range_visuals(
    *,
    source: SourceIngestionRecord,
    structure: SourceStructure,
    chapter: SourceChapter,
    source_path: Path,
    source_range: dict[str, Any],
    text_evidence: Sequence[RetrievalEvidence],
    extractor: SourceVisualExtractor = source_visual_extractor,
    store: SourceStructureStore = source_structure_store,
) -> list[SourceVisualEvidence]:
    """Extract and persist every provably scoped visual for one catalog chapter.

    Text remains generative reference material. Visuals use original captures and
    retain their source order, range, hashes, and local text context so the board
    insertion layer can place them deterministically and reject omissions.
    """

    result = extractor.extract(
        record=source,
        path=source_path,
        structure=structure,
        chapters=[chapter],
        chunks=[],
        source_range=source_range,
        source_range_chapter=chapter,
    )
    unverified = [visual for visual in result.visuals if visual.anchor_status != "verified"]
    missing_originals = [
        visual
        for visual in result.visuals
        if not visual.storage_key or not visual.mime_type or not visual.content_hash
    ]
    if result.status != "ready" or result.warnings or unverified or missing_originals:
        raise SourceRangeVisualError(
            "所选章节的全部图表尚不能完整验证并保留原图，本次未生成板书。"
        )

    assets = [
        visual.model_copy(
            update={
                "surrounding_text": _surrounding_text(visual, text_evidence),
                "metadata": {
                    **visual.metadata,
                    "visual_coverage_required": True,
                    "board_render_policy": "original_capture_required",
                },
            }
        )
        for visual in sorted(result.visuals, key=lambda item: item.order_index)
    ]
    store.upsert_scoped_visual_assets(assets)
    return [_as_evidence(asset) for asset in assets]


def _as_evidence(asset: SourceVisualAsset) -> SourceVisualEvidence:
    return SourceVisualEvidence(
        visual_id=asset.id,
        package_id=asset.package_id,
        source_ingestion_id=asset.source_ingestion_id,
        source_chapter_id=asset.chapter_id or "",
        kind=asset.kind,
        source_locator=asset.source_locator,
        page_start=asset.page_start,
        page_end=asset.page_end,
        paragraph_index=asset.paragraph_index,
        slide_no=asset.slide_no,
        sheet_name=asset.sheet_name,
        bbox=asset.bbox,
        before_chunk_id=asset.before_chunk_id,
        after_chunk_id=asset.after_chunk_id,
        caption=asset.caption,
        extracted_text=asset.extracted_text,
        surrounding_text=asset.surrounding_text,
        anchor_status=asset.anchor_status,
        mime_type=asset.mime_type,
        order_index=asset.order_index,
        content_hash=asset.content_hash,
        position_hash=asset.position_hash,
        width=asset.width,
        height=asset.height,
        table_data=asset.table_data,
        confidence=asset.confidence,
        metadata=asset.metadata,
    )


def _surrounding_text(
    visual: SourceVisualAsset,
    evidence_items: Sequence[RetrievalEvidence],
) -> str:
    matching = [
        evidence.expanded_text.strip()
        for evidence in evidence_items
        if evidence.expanded_text.strip() and _evidence_contains_visual(evidence, visual)
    ]
    if not matching:
        matching = [
            evidence.expanded_text.strip()
            for evidence in evidence_items
            if evidence.expanded_text.strip()
        ]
    return "\n\n".join(matching)[:4000]


def _evidence_contains_visual(
    evidence: RetrievalEvidence,
    visual: SourceVisualAsset,
) -> bool:
    locator = str(evidence.metadata.get("source_locator") or "")
    if visual.page_start is not None:
        page_range = _locator_range(locator, prefixes=("pdf:page:", "pdf:pages:"))
        return bool(
            page_range
            and page_range[0] <= visual.page_start <= page_range[1]
        )
    if visual.slide_no is not None:
        slide_range = _locator_range(locator, prefixes=("pptx:slide:", "pptx:slides:"))
        return bool(
            slide_range
            and slide_range[0] <= visual.slide_no <= slide_range[1]
        )
    return bool(locator and visual.source_locator.startswith(locator))


def _locator_range(locator: str, *, prefixes: tuple[str, ...]) -> tuple[int, int] | None:
    for prefix in prefixes:
        if not locator.startswith(prefix):
            continue
        match = re.match(r"(\d+)(?:-(\d+))?", locator[len(prefix) :])
        if match is None:
            return None
        start = int(match.group(1))
        return start, int(match.group(2) or start)
    return None

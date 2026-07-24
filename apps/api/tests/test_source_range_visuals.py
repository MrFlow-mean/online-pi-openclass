from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from app.models import (
    RetrievalEvidence,
    SourceChapter,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisualAsset,
)
from app.services.source_range_visuals import (
    SourceRangeVisualError,
    extract_verified_range_visuals,
)
from app.services.source_structure_store import SourceStructureStore
from app.services.source_visual_extraction import SourceVisualExtractionResult
from app.services.source_visual_storage import persist_source_visual_asset


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8A"
    "AQUBAScY42YAAAAASUVORK5CYII="
)


class _Extractor:
    def __init__(self, result: SourceVisualExtractionResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def extract(self, **kwargs) -> SourceVisualExtractionResult:
        self.calls.append(kwargs)
        return self.result


def _objects(path: Path):
    source = SourceIngestionRecord(
        id="source_range_visuals",
        owner_user_id="owner_range_visuals",
        package_id="package_range_visuals",
        title="Range visuals",
        source_type="local_file",
        file_name=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        status="ready",
    )
    structure = SourceStructure(
        id="structure_range_visuals",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
        strategy="codex_directory_v1",
    )
    chapter = SourceChapter(
        id="chapter_range_visuals",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        title="Selected chapter",
        mapping_status="verified",
    )
    return source, structure, chapter


def _visual(source, structure, chapter, *, visual_id: str, order_index: int, page: int):
    storage_key, content_hash = persist_source_visual_asset(_PNG, mime_type="image/png")
    return SourceVisualAsset(
        id=visual_id,
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        structure_id=structure.id,
        structure_version=5,
        chapter_id=chapter.id,
        kind="chart",
        source_locator=f"pdf:page:{page}:chart:{order_index}",
        page_start=page,
        page_end=page,
        anchor_status="verified",
        mime_type="image/png",
        storage_key=storage_key,
        content_hash=content_hash,
        position_hash=hashlib.sha256(f"position:{page}".encode()).hexdigest(),
        order_index=order_index,
        confidence=0.95,
    )


def test_verified_range_visuals_preserve_every_original_in_source_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source")
    source, structure, chapter = _objects(path)
    later = _visual(source, structure, chapter, visual_id="visual_later", order_index=2, page=3)
    earlier = _visual(source, structure, chapter, visual_id="visual_earlier", order_index=1, page=2)
    extractor = _Extractor(SourceVisualExtractionResult(visuals=[later, earlier], status="ready"))
    store = SourceStructureStore(tmp_path / "source-structure.sqlite3")
    evidence = [
        RetrievalEvidence(
            source_ingestion_id=source.id,
            source_title=source.title,
            expanded_text="Text adjacent to the selected chapter figures.",
            metadata={"source_locator": "pdf:pages:2-3"},
        )
    ]

    visuals = extract_verified_range_visuals(
        source=source,
        structure=structure,
        chapter=chapter,
        source_path=path,
        source_range={"kind": "pdf_pages", "start": 2, "end": 3},
        text_evidence=evidence,
        extractor=extractor,
        store=store,
    )

    assert [visual.visual_id for visual in visuals] == ["visual_earlier", "visual_later"]
    assert all(
        visual.metadata["board_render_policy"] == "original_capture_required"
        for visual in visuals
    )
    assert all("adjacent" in visual.surrounding_text for visual in visuals)
    assert extractor.calls[0]["source_range_chapter"] == chapter
    assert store.read_visual_bytes(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
        visual_id="visual_earlier",
    )[1] == _PNG


def test_verified_range_visuals_fail_closed_when_any_original_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source")
    source, structure, chapter = _objects(path)
    visual = SourceVisualAsset(
        id="visual_without_original",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        structure_id=structure.id,
        chapter_id=chapter.id,
        kind="table",
        source_locator="pdf:page:2:table:0",
        page_start=2,
        page_end=2,
        anchor_status="verified",
        table_data=[["A", "B"]],
        content_hash=hashlib.sha256(b"table").hexdigest(),
        position_hash=hashlib.sha256(b"position").hexdigest(),
        confidence=0.95,
    )
    extractor = _Extractor(SourceVisualExtractionResult(visuals=[visual], status="ready"))

    with pytest.raises(SourceRangeVisualError):
        extract_verified_range_visuals(
            source=source,
            structure=structure,
            chapter=chapter,
            source_path=path,
            source_range={"kind": "pdf_pages", "start": 2, "end": 2},
            text_evidence=[],
            extractor=extractor,
            store=SourceStructureStore(tmp_path / "source-structure.sqlite3"),
        )

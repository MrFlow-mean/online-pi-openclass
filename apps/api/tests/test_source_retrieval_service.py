from __future__ import annotations

from pathlib import Path

import pytest

from app.models import (
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceQueryRef,
    SourceQueryScope,
    SourceStructure,
)
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_retrieval_service import SourceRetrievalError, SourceRetrievalService
from app.services.source_structure_store import SourceStructureStore


def test_source_retrieval_limits_results_to_authenticated_source_scope(tmp_path: Path) -> None:
    service, source_store, structure_store = _service(tmp_path)
    selected = _save_source(
        source_store,
        structure_store,
        source_id="source_selected",
        owner_user_id="user_1",
        package_id="package_1",
        title="Selected",
        content_hash="a" * 64,
        text="The terminal pages explain durable queue recovery semantics.",
        page_start=299,
    )
    _save_source(
        source_store,
        structure_store,
        source_id="source_excluded",
        owner_user_id="user_1",
        package_id="package_1",
        title="Excluded",
        content_hash="b" * 64,
        text="Durable queue recovery semantics appear here too.",
        page_start=1,
    )

    result = service.retrieve(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        query="durable queue recovery",
        scope=SourceQueryScope(
            mode="source",
            refs=[
                SourceQueryRef(
                    source_ingestion_id=selected.id,
                    source_content_hash="a" * 64,
                )
            ],
        ),
    )

    assert result.bundle.evidence_items
    assert {item.source_ingestion_id for item in result.bundle.evidence_items} == {selected.id}
    assert result.citations[0].page_start == 299
    assert result.citations[0].source_content_hash == "a" * 64
    assert result.bundle.confirmed_by_user is True


def test_chapter_scope_cannot_retrieve_a_sibling_chapter(tmp_path: Path) -> None:
    service, source_store, structure_store = _service(tmp_path)
    source = SourceIngestionRecord(
        id="source_chapters",
        owner_user_id="user_1",
        package_id="package_1",
        title="Chapters",
        file_name="chapters.pdf",
        mime_type="application/pdf",
        status="ready",
        metadata={"content_hash": "c" * 64},
    )
    source_store.save_source(source)
    selected_chapter = SourceChapter(
        id="chapter_selected",
        owner_user_id="user_1",
        package_id="package_1",
        source_ingestion_id=source.id,
        title="Selected chapter",
        order_index=0,
        anchor_status="verified",
        body_start_offset=0,
        body_end_offset=100,
    )
    sibling_chapter = SourceChapter(
        id="chapter_sibling",
        owner_user_id="user_1",
        package_id="package_1",
        source_ingestion_id=source.id,
        title="Sibling chapter",
        order_index=1,
        anchor_status="verified",
        body_start_offset=100,
        body_end_offset=200,
    )
    structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="package_1",
            source_ingestion_id=source.id,
            status="ready",
        ),
        chapters=[selected_chapter, sibling_chapter],
        chunks=[
            _chunk(source, "selected evidence alpha", 0, chapter_id=selected_chapter.id),
            _chunk(source, "sibling evidence alpha alpha alpha", 1, chapter_id=sibling_chapter.id),
        ],
    )

    result = service.retrieve(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        query="alpha",
        scope=SourceQueryScope(
            mode="chapter",
            refs=[
                SourceQueryRef(
                    source_ingestion_id=source.id,
                    source_content_hash="c" * 64,
                    source_chapter_id=selected_chapter.id,
                )
            ],
        ),
    )

    assert result.bundle.evidence_items
    assert {item.chapter_id for item in result.bundle.evidence_items} == {selected_chapter.id}
    assert all("sibling" not in item.expanded_text for item in result.bundle.evidence_items)


def test_source_scope_rejects_a_stale_content_hash(tmp_path: Path) -> None:
    service, source_store, structure_store = _service(tmp_path)
    source = _save_source(
        source_store,
        structure_store,
        source_id="source_changed",
        owner_user_id="user_1",
        package_id="package_1",
        title="Changed",
        content_hash="d" * 64,
        text="Current source text",
        page_start=1,
    )

    with pytest.raises(SourceRetrievalError, match="发生变化"):
        service.retrieve(
            owner_user_id="user_1",
            package_id="package_1",
            lesson_id="lesson_1",
            query="source text",
            scope=SourceQueryScope(
                mode="source",
                refs=[
                    SourceQueryRef(
                        source_ingestion_id=source.id,
                        source_content_hash="e" * 64,
                    )
                ],
            ),
        )


def _service(
    tmp_path: Path,
) -> tuple[SourceRetrievalService, SourceEvidenceStore, SourceStructureStore]:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    return (
        SourceRetrievalService(
            evidence_store=source_store,
            structure_store=structure_store,
        ),
        source_store,
        structure_store,
    )


def _save_source(
    source_store: SourceEvidenceStore,
    structure_store: SourceStructureStore,
    *,
    source_id: str,
    owner_user_id: str,
    package_id: str,
    title: str,
    content_hash: str,
    text: str,
    page_start: int,
) -> SourceIngestionRecord:
    source = SourceIngestionRecord(
        id=source_id,
        owner_user_id=owner_user_id,
        package_id=package_id,
        title=title,
        file_name=f"{source_id}.pdf",
        mime_type="application/pdf",
        status="ready",
        metadata={"content_hash": content_hash},
    )
    source_store.save_source(source)
    structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_ingestion_id=source.id,
            status="linear_only",
        ),
        chapters=[],
        chunks=[_chunk(source, text, 0, page_start=page_start)],
    )
    return source


def _chunk(
    source: SourceIngestionRecord,
    text: str,
    order_index: int,
    *,
    chapter_id: str | None = None,
    page_start: int = 1,
) -> SourceChunk:
    return SourceChunk(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        chapter_id=chapter_id,
        order_index=order_index,
        text=text,
        end_offset=len(text),
        page_start=page_start,
        page_end=page_start,
        token_count=max(1, len(text) // 4),
    )

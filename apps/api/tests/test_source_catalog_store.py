from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    SourceCatalogEvidence,
    SourceCatalogRun,
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceRange,
    SourceStructure,
)
from app.services.source_structure_store import SourceStructureStore
from app.services.source_evidence_store import SourceEvidenceStore


def _source_record(source_id: str = "source_catalog") -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=source_id,
        owner_user_id="user_catalog",
        package_id="package_catalog",
        title="Reference",
        file_name="reference.pdf",
        mime_type="application/pdf",
        status="ready",
    )


def _catalog_structure(source: SourceIngestionRecord, *, content_hash: str = "hash-v1") -> SourceStructure:
    return SourceStructure(
        id="structure_catalog",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
        strategy="codex_directory_v1",
        source_content_hash=content_hash,
        catalog_schema_version="codex_directory_v1",
        catalog_model="openai:gpt-test",
    )


def _catalog_chapter(source: SourceIngestionRecord, *, title: str = "Chapter") -> SourceChapter:
    return SourceChapter(
        id="chapter_catalog",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        title=title,
        path=[title],
        source_locator="pdf:4-8",
        anchor_status="verified",
        mapping_status="verified",
        range=SourceRange(kind="pdf_pages", start=4, end=8, display_label="pp. 4-8"),
        catalog_evidence=[
            SourceCatalogEvidence(
                method="document_outline",
                source_locator="outline:0",
                page_start=4,
                page_end=8,
                confidence=1.0,
            )
        ],
    )


def test_pdf_source_range_is_one_based_and_inclusive() -> None:
    source_range = SourceRange(kind="pdf_pages", start=1, end=1)

    assert source_range.end_inclusive is True
    with pytest.raises(ValidationError):
        SourceRange(kind="pdf_pages", start=0, end=1)
    with pytest.raises(ValidationError):
        SourceRange(kind="pdf_pages", start=3, end=2)
    with pytest.raises(ValidationError):
        SourceRange(kind="pdf_pages", start=1, end=2, end_inclusive=False)


def test_legacy_codex_catalog_strategy_remains_readable() -> None:
    source = _source_record()

    structure = SourceStructure(
        id="legacy_codex_structure",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
        strategy="codex_catalog",
    )

    assert structure.strategy == "codex_catalog"


def test_catalog_publication_is_versioned_and_directory_only(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "catalog.sqlite3")
    source = _source_record()
    legacy_structure = SourceStructure(
        id="legacy_structure",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
    )
    store.save_structure_bundle(
        structure=legacy_structure,
        chapters=[],
        chunks=[
            SourceChunk(
                id="legacy_chunk",
                owner_user_id=source.owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                text="Legacy full-text content must not survive the catalog publication.",
            )
        ],
    )
    run = SourceCatalogRun(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="succeeded",
        model="openai:gpt-test",
        turn_count=2,
        page_count=120,
        inspected_page_count=6,
        ocr_page_count=2,
        duration_ms=400,
    )

    first = store.publish_catalog(
        structure=_catalog_structure(source),
        chapters=[_catalog_chapter(source)],
        run=run,
    )
    second = store.publish_catalog(
        structure=first,
        chapters=[_catalog_chapter(source, title="Updated chapter")],
    )

    assert first.catalog_version == 1
    assert second.catalog_version == 2
    structure_view = store.get_structure_view(source=source, chunk_limit=100)
    assert structure_view.chunks == []
    assert structure_view.visuals == []
    catalog = store.get_catalog_view(source=source)
    assert catalog.catalog_version == 2
    assert catalog.source_content_hash == "hash-v1"
    assert catalog.chapters[0].catalog_version == 2
    assert catalog.chapters[0].source_content_hash == "hash-v1"
    assert catalog.chapters[0].range == SourceRange(
        kind="pdf_pages",
        start=4,
        end=8,
        display_label="pp. 4-8",
    )
    assert "chunks" not in catalog.model_dump()
    assert "visuals" not in catalog.model_dump()
    saved_run = store.list_catalog_runs(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
    )[0]
    assert saved_run.catalog_version == 1
    assert saved_run.verification_rate == 1.0
    assert saved_run.inspected_page_count == 6


def test_catalog_publication_rejects_a_stale_run_id(tmp_path: Path) -> None:
    database = tmp_path / "catalog-stale-run.sqlite3"
    source_store = SourceEvidenceStore(database)
    store = SourceStructureStore(database)
    source = _source_record().model_copy(
        update={
            "metadata": {
                "content_hash": "hash-v1",
                "active_catalog_run_id": "run-current",
            }
        }
    )
    source_store.save_source(source)
    stale_run = SourceCatalogRun(
        id="run-stale",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="running",
        metadata={
            "active_catalog_run_id": "run-stale",
            "content_hash": "hash-v1",
        },
    )

    with pytest.raises(RuntimeError, match="newer catalog run"):
        store.publish_catalog(
            structure=_catalog_structure(source),
            chapters=[_catalog_chapter(source)],
            run=stale_run,
        )

    assert store.get_structure(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
    ) is None


def test_failed_catalog_publication_preserves_previous_version(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "catalog-rollback.sqlite3")
    source = _source_record()
    first = store.publish_catalog(
        structure=_catalog_structure(source),
        chapters=[_catalog_chapter(source, title="Published")],
    )
    invalid_chapter = _catalog_chapter(source, title="Must roll back").model_copy(
        update={"metadata": {"not_json_serializable": object()}}
    )

    with pytest.raises(TypeError):
        store.publish_catalog(structure=first, chapters=[invalid_chapter])

    catalog = store.get_catalog_view(source=source)
    assert catalog.catalog_version == 1
    assert [chapter.title for chapter in catalog.chapters] == ["Published"]


def test_legacy_catalog_is_read_without_chunks_and_converts_exclusive_page_end(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "legacy-catalog.sqlite3")
    source = _source_record("legacy_source")
    store.save_structure_bundle(
        structure=SourceStructure(
            id="legacy_structure",
            owner_user_id=source.owner_user_id,
            package_id=source.package_id,
            source_ingestion_id=source.id,
            status="ready",
            strategy="pdf_outline",
        ),
        chapters=[
            SourceChapter(
                id="legacy_chapter",
                owner_user_id=source.owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                title="Legacy chapter",
                page_start=4,
                page_end=7,
                anchor_status="verified",
            )
        ],
        chunks=[
            SourceChunk(
                id="legacy_chunk",
                owner_user_id=source.owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                chapter_id="legacy_chapter",
                text="This body must not be returned by the catalog endpoint.",
            )
        ],
    )

    catalog = store.get_catalog_view(source=source)

    assert catalog.catalog_schema_version == "legacy"
    assert catalog.chapters[0].mapping_status == "verified"
    assert catalog.chapters[0].range == SourceRange(
        kind="pdf_pages",
        start=4,
        end=6,
        display_label="pp. 4-6",
        metadata={"legacy_page_end_exclusive": True},
    )
    payload = catalog.model_dump(mode="json")
    assert "chunks" not in payload
    assert "visuals" not in payload
    assert "This body" not in str(payload)


def test_catalog_batch_includes_saved_and_pending_sources(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "batch-catalog.sqlite3")
    saved = _source_record("saved_source")
    pending = _source_record("pending_source").model_copy(
        update={"status": "parsing", "structure_status": "pending"}
    )
    store.publish_catalog(
        structure=_catalog_structure(saved),
        chapters=[_catalog_chapter(saved)],
    )

    batch = store.get_catalog_views(package_id=saved.package_id, sources=[saved, pending])

    assert batch.package_id == saved.package_id
    assert [catalog.source.id for catalog in batch.catalogs] == [saved.id, pending.id]
    assert batch.catalogs[0].chapter_count == 1
    assert batch.catalogs[1].chapter_count == 0
    assert batch.catalogs[1].status == "pending"


def test_source_summary_keeps_catalog_timestamp_after_ancillary_structure_write(
    tmp_path: Path,
) -> None:
    store = SourceStructureStore(tmp_path / "catalog-summary.sqlite3")
    source = _source_record("source_with_visual_cache")
    published = store.publish_catalog(
        structure=_catalog_structure(source),
        chapters=[_catalog_chapter(source)],
    )

    store.mark_scoped_visual_cache_complete(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        scope_cache_key="chapter-visual-cache",
    )

    current = store.get_structure(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
    )
    summary = store.attach_summary(source)

    assert current is not None
    assert current.catalog_updated_at == published.catalog_updated_at
    assert summary.structure_updated_at == published.catalog_updated_at

from __future__ import annotations

from pathlib import Path

from app.models import SourceIngestionRecord, SourceQueryRef, SourceQueryScope
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_parser_adapters import ParsedDocumentV2, ParsedSourceElement
from app.services.source_qa_enhancement import (
    SourceQAEnhancementService,
    assess_document_pages,
)
from app.services.source_qa_index import SourceQAIndexStore
from app.services.source_retrieval_service import SourceRetrievalService
from app.services.source_structure_store import SourceStructureStore


def test_quality_router_separates_scan_and_complex_layout_pages() -> None:
    document = ParsedDocumentV2(
        source_id="source_1",
        source_content_hash="a" * 64,
        parser="test",
        parser_version="1",
        page_count=3,
        elements=[
            _element(page=1, text=""),
            _element(
                page=2,
                text="table content " * 10,
                element_type="table",
                confidence=0.7,
            ),
            _element(page=3, text="ordinary readable body text " * 10),
        ],
    )

    assessments = assess_document_pages(document)

    assert assessments[0].recommended_parser == "mineru"
    assert assessments[1].recommended_parser == "docling"
    assert assessments[2].status == "good"


def test_page_enhancement_atomically_replaces_only_the_target_page(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    index_store = SourceQAIndexStore(database_path)
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"placeholder")
    record = source_store.save_source(_record("source_enhance"))
    fast = ParsedDocumentV2(
        source_id=record.id,
        source_content_hash="a" * 64,
        parser="fast",
        parser_version="1",
        page_count=2,
        elements=[
            _element(page=1, text="stable first page evidence remains fully readable"),
            _element(page=2, text=""),
        ],
    )
    version, _count = index_store.publish_document(record=record, document=fast)
    record = source_store.save_source(
        record.model_copy(
            update={
                "qa_status": "ready",
                "qa_index_version": version,
                "page_count": 2,
                "indexed_page_count": 2,
            }
        )
    )
    service = SourceQAEnhancementService(
        evidence_store=source_store,
        index_store=index_store,
        parser_router=_EnhancementRouter(),
    )
    record = service.assess_and_queue(record=record, document=fast)

    completed = service.enhance(record=record, path=source_path)
    source_by_id = {record.id: completed}
    ocr_matches = index_store.search(
        owner_user_id="user_1",
        package_id="package_1",
        query="violet scan recovery 55291",
        source_ingestion_ids=[record.id],
        source_by_id=source_by_id,
    )
    retained_matches = index_store.search(
        owner_user_id="user_1",
        package_id="package_1",
        query="stable first page evidence",
        source_ingestion_ids=[record.id],
        source_by_id=source_by_id,
    )

    assert completed.qa_status == "complete"
    assert completed.qa_index_version == 2
    assert completed.enhancement_failed_page_count == 0
    assert ocr_matches and ocr_matches[0].metadata["page_start"] == 2
    assert "55291" in ocr_matches[0].expanded_text
    assert retained_matches
    assert retained_matches[0].metadata["qa_index_version"] == 2


def test_enhancement_failure_preserves_the_fast_index(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    index_store = SourceQAIndexStore(database_path)
    record = source_store.save_source(_record("source_failure"))
    fast = ParsedDocumentV2(
        source_id=record.id,
        source_content_hash="a" * 64,
        parser="fast",
        parser_version="1",
        page_count=2,
        elements=[
            _element(page=1, text="fast index remains available with readable context"),
            _element(page=2, text=""),
        ],
    )
    version, _count = index_store.publish_document(record=record, document=fast)
    record = source_store.save_source(
        record.model_copy(update={"qa_status": "ready", "qa_index_version": version})
    )
    service = SourceQAEnhancementService(
        evidence_store=source_store,
        index_store=index_store,
        parser_router=_FailingRouter(),
    )
    record = service.assess_and_queue(record=record, document=fast)

    completed = service.enhance(record=record, path=tmp_path / "missing.pdf")
    matches = index_store.search(
        owner_user_id="user_1",
        package_id="package_1",
        query="fast index remains",
        source_ingestion_ids=[record.id],
        source_by_id={record.id: completed},
    )

    assert completed.qa_status == "complete"
    assert completed.qa_index_version == 1
    assert completed.enhancement_failed_page_count == 1
    assert matches and "fast index remains available" in matches[0].expanded_text


def test_query_hit_prioritizes_a_pending_low_quality_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    index_store = SourceQAIndexStore(database_path)
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"placeholder")
    record = source_store.save_source(_record("source_query_enhance"))
    fast = ParsedDocumentV2(
        source_id=record.id,
        source_content_hash="a" * 64,
        parser="fast",
        parser_version="1",
        page_count=2,
        elements=[
            _element(page=1, text="ordinary readable page with enough context for quality"),
            _element(page=2, text="scan marker 55291"),
        ],
    )
    version, _count = index_store.publish_document(record=record, document=fast)
    record = source_store.save_source(
        record.model_copy(update={"qa_status": "ready", "qa_index_version": version})
    )
    enhancement_service = SourceQAEnhancementService(
        evidence_store=source_store,
        index_store=index_store,
        parser_router=_EnhancementRouter(),
    )
    record = enhancement_service.assess_and_queue(record=record, document=fast)
    monkeypatch.setenv("OPENCLASS_SOURCE_QA_ENHANCEMENT_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.source_ingestion_service.source_local_path",
        lambda _record: source_path,
    )
    retrieval = SourceRetrievalService(
        evidence_store=source_store,
        structure_store=structure_store,
        qa_index_store=index_store,
        enhancement_service=enhancement_service,
    )

    result = retrieval.retrieve(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        query="scan marker 55291",
        scope=SourceQueryScope(
            mode="source",
            refs=[
                SourceQueryRef(
                    source_ingestion_id=record.id,
                    source_content_hash="a" * 64,
                )
            ],
        ),
    )

    assert result.bundle.evidence_items
    assert "violet scan recovery marker 55291" in result.bundle.evidence_items[0].expanded_text
    assert result.bundle.evidence_items[0].metadata["qa_index_version"] == 2


class _EnhancementRouter:
    def parse_enhancement(
        self,
        *,
        parser: str,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
        page_numbers: list[int],
    ) -> ParsedDocumentV2:
        del path, mime_type
        assert parser == "mineru"
        assert page_numbers == [2]
        return ParsedDocumentV2(
            source_id=source_id,
            source_content_hash=source_content_hash,
            parser="mineru",
            parser_version="test",
            page_count=2,
            elements=[_element(page=2, text="violet scan recovery marker 55291")],
        )


class _FailingRouter:
    def parse_enhancement(self, **_kwargs: object) -> ParsedDocumentV2:
        raise RuntimeError("parser unavailable")


def _record(source_id: str) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=source_id,
        owner_user_id="user_1",
        package_id="package_1",
        title="Source",
        file_name="source.pdf",
        mime_type="application/pdf",
        status="ready",
        metadata={"content_hash": "a" * 64},
    )


def _element(
    *,
    page: int,
    text: str,
    element_type: str = "paragraph",
    confidence: float = 1.0,
) -> ParsedSourceElement:
    return ParsedSourceElement(
        element_id=f"element-{page}-{element_type}",
        page_no=page,
        reading_order=0,
        element_type=element_type,
        raw_text=text,
        normalized_text=text,
        confidence=confidence,
    )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import SourceIngestionRecord, SourceQueryRef, SourceQueryScope
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_parser_adapters import (
    JsonSidecarParserAdapter,
    ParsedDocumentV2,
    ParsedSourceElement,
    SourceParserRouter,
)
from app.services.source_qa_index import SourceQAIndexStore
from app.services.source_qa_indexer import SourceQAIndexer
from app.services.source_retrieval_service import SourceRetrievalService
from app.services.source_structure_store import SourceStructureStore


def test_independent_qa_index_retrieves_a_fact_from_the_last_page(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    evidence_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    qa_store = SourceQAIndexStore(database_path)
    source = evidence_store.save_source(
        SourceIngestionRecord(
            id="source_long",
            owner_user_id="user_1",
            package_id="package_1",
            title="Long source",
            file_name="long.pdf",
            mime_type="application/pdf",
            status="ready",
            qa_status="ready",
            metadata={"content_hash": "a" * 64},
        )
    )
    document = ParsedDocumentV2(
        source_id=source.id,
        source_content_hash="a" * 64,
        parser="test",
        parser_version="1",
        page_count=300,
        elements=[
            ParsedSourceElement(
                element_id=f"page-{page}",
                page_no=page,
                reading_order=0,
                raw_text=(
                    "ordinary background text"
                    if page < 300
                    else "The terminal appendix defines the sapphire recovery marker as 91427."
                ),
                normalized_text=(
                    "ordinary background text"
                    if page < 300
                    else "The terminal appendix defines the sapphire recovery marker as 91427."
                ),
            )
            for page in range(1, 301)
        ],
    )
    version, chunk_count = qa_store.publish_document(record=source, document=document)

    service = SourceRetrievalService(
        evidence_store=evidence_store,
        structure_store=structure_store,
        qa_index_store=qa_store,
    )
    result = service.retrieve(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        query="What is the sapphire recovery marker?",
        scope=SourceQueryScope(
            mode="source",
            refs=[
                SourceQueryRef(
                    source_ingestion_id=source.id,
                    source_content_hash="a" * 64,
                )
            ],
        ),
    )

    assert version == 1
    assert chunk_count == 300
    assert result.bundle.evidence_items
    assert result.citations[0].page_start == 300
    assert "91427" in result.bundle.evidence_items[0].expanded_text
    assert result.citations[0].parser_run_id.startswith("parser_run_")


def test_source_qa_indexer_persists_readiness_without_publishing_shadow_output(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_path = tmp_path / "source.txt"
    source_path.write_text("primary searchable sentence", encoding="utf-8")
    evidence_store = SourceEvidenceStore(database_path)
    qa_store = SourceQAIndexStore(database_path)
    source = evidence_store.save_source(
        SourceIngestionRecord(
            id="source_shadow",
            owner_user_id="user_1",
            package_id="package_1",
            title="Shadow source",
            file_name="source.txt",
            mime_type="text/plain",
            status="ready",
            metadata={"content_hash": "b" * 64},
        )
    )
    router = SourceParserRouter(
        native=_StaticParser("primary", "primary searchable sentence"),
        opendataloader=_StaticSidecarParser("shadow sentence must not be published"),
    )
    router.parse_shadow_opendataloader = lambda **_kwargs: _document(
        source_id=source.id,
        content_hash="b" * 64,
        parser="opendataloader",
        text="shadow sentence must not be published",
    )
    indexer = SourceQAIndexer(
        evidence_store=evidence_store,
        index_store=qa_store,
        parser_router=router,
    )

    ready = indexer.index_fast(record=source, path=source_path)
    matches = qa_store.search(
        owner_user_id="user_1",
        package_id="package_1",
        query="primary searchable",
        source_ingestion_ids=[source.id],
        source_by_id={source.id: ready},
    )

    assert ready.qa_status == "ready"
    assert ready.qa_index_version == 1
    assert ready.indexed_page_count == 1
    assert ready.metadata["source_qa_shadow_status"] == "complete"
    assert matches and "primary searchable sentence" in matches[0].expanded_text
    assert "shadow sentence" not in matches[0].expanded_text


def test_json_sidecar_rejects_mismatched_source_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "parser"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    output = _document(
        source_id="wrong_source",
        content_hash="c" * 64,
        parser="sidecar",
        text="content",
    ).model_dump(mode="json")

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> _Completed:
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text(json.dumps(output), encoding="utf-8")
        return _Completed()

    monkeypatch.setenv("TEST_PARSER_COMMAND", str(executable))
    monkeypatch.setattr("app.services.source_parser_adapters.subprocess.run", fake_run)
    adapter = JsonSidecarParserAdapter(
        name="test_sidecar",
        version="1",
        command_env="TEST_PARSER_COMMAND",
    )

    with pytest.raises(RuntimeError, match="mismatched source identity"):
        adapter.parse(
            source_id="expected_source",
            source_content_hash="c" * 64,
            path=tmp_path / "source.pdf",
            mime_type="application/pdf",
        )


class _StaticParser:
    name = "primary"
    version = "1"

    def __init__(self, parser: str, text: str) -> None:
        self.name = parser
        self.text = text

    def parse(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2:
        del path, mime_type
        return _document(
            source_id=source_id,
            content_hash=source_content_hash,
            parser=self.name,
            text=self.text,
        )


class _StaticSidecarParser(_StaticParser):
    configured = True

    def __init__(self, text: str) -> None:
        super().__init__("opendataloader", text)


def _document(
    *, source_id: str, content_hash: str, parser: str, text: str
) -> ParsedDocumentV2:
    return ParsedDocumentV2(
        source_id=source_id,
        source_content_hash=content_hash,
        parser=parser,
        parser_version="1",
        page_count=1,
        elements=[
            ParsedSourceElement(
                element_id="element-1",
                page_no=1,
                reading_order=0,
                raw_text=text,
                normalized_text=text,
            )
        ],
    )

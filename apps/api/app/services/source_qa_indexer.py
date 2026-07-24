from __future__ import annotations

from pathlib import Path

from app.models import SourceIngestionRecord
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_parser_adapters import SourceParserRouter, source_parser_router
from app.services.source_qa_index import SourceQAIndexStore


class SourceQAIndexingError(RuntimeError):
    pass


class SourceQAIndexer:
    def __init__(
        self,
        *,
        evidence_store: SourceEvidenceStore,
        index_store: SourceQAIndexStore,
        parser_router: SourceParserRouter = source_parser_router,
    ) -> None:
        self.evidence_store = evidence_store
        self.index_store = index_store
        self.parser_router = parser_router

    def index_fast(self, *, record: SourceIngestionRecord, path: Path) -> SourceIngestionRecord:
        content_hash = str(record.metadata.get("content_hash") or "").strip().lower()
        if not content_hash:
            raise SourceQAIndexingError("Source QA indexing requires a content hash.")
        indexing = self.evidence_store.save_source(
            record.model_copy(update={"qa_status": "indexing"})
        )
        try:
            document = self.parser_router.parse_fast(
                source_id=indexing.id,
                source_content_hash=content_hash,
                path=path,
                mime_type=indexing.mime_type,
            )
            index_version, chunk_count = self.index_store.publish_document(
                record=indexing,
                document=document,
            )
            shadow_metadata: dict[str, object] = {}
            try:
                shadow = self.parser_router.parse_shadow_opendataloader(
                    source_id=indexing.id,
                    source_content_hash=content_hash,
                    path=path,
                    mime_type=indexing.mime_type,
                )
            except Exception as exc:
                shadow_metadata = {
                    "source_qa_shadow_status": "failed",
                    "source_qa_shadow_error": str(exc)[:500],
                }
            else:
                if shadow is not None:
                    shadow_metadata = {
                        "source_qa_shadow_status": "complete",
                        "source_qa_shadow_parser": shadow.parser,
                        "source_qa_shadow_parser_version": shadow.parser_version,
                        "source_qa_shadow_page_count": shadow.page_count,
                        "source_qa_shadow_element_count": len(shadow.elements),
                    }
            indexed_pages = len({item.page_no for item in document.elements})
            return self.evidence_store.save_source(
                indexing.model_copy(
                    update={
                        "qa_status": "ready",
                        "indexed_page_count": indexed_pages,
                        "page_count": document.page_count,
                        "qa_index_version": index_version,
                        "enhancement_failed_page_count": 0,
                        "metadata": {
                            **indexing.metadata,
                            "source_qa_parser": document.parser,
                            "source_qa_parser_version": document.parser_version,
                            "source_qa_chunk_count": chunk_count,
                            "source_qa_warnings": document.warnings,
                            **shadow_metadata,
                        },
                    }
                )
            )
        except Exception as exc:
            self.evidence_store.save_source(
                indexing.model_copy(
                    update={
                        "qa_status": "failed",
                        "metadata": {
                            **indexing.metadata,
                            "source_qa_error": str(exc)[:1000],
                        },
                    }
                )
            )
            raise SourceQAIndexingError(str(exc)) from exc

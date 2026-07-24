from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Sequence

from app.models import SourceIngestionRecord, now_iso
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store
from app.services.source_parser_adapters import (
    ParsedDocumentV2,
    SourceParserRouter,
    source_parser_router,
)
from app.services.source_qa_index import SourceQAIndexStore


EnhancementParser = Literal["mineru", "docling"]
_PARSER_LOCKS = {
    "mineru": threading.Lock(),
    "docling": threading.Lock(),
}


@dataclass(frozen=True)
class PageQualityAssessment:
    page_no: int
    status: Literal["good", "pending", "running", "complete", "failed"]
    recommended_parser: EnhancementParser | None
    score: float
    reasons: tuple[str, ...]
    error: str = ""


class SourceQAEnhancementStore:
    def __init__(self, *, index_store: SourceQAIndexStore) -> None:
        self.index_store = index_store
        self.path = index_store.path
        self.coordinator = index_store.coordinator
        self._lock = threading.RLock()
        self._initialized = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            self._initialize(conn)
            yield conn
        finally:
            conn.close()

    def _initialize(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            if self._initialized:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_qa_page_quality (
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    source_content_hash TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    recommended_parser TEXT,
                    score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_ingestion_id, page_no)
                );
                CREATE INDEX IF NOT EXISTS idx_source_qa_page_quality_queue
                    ON source_qa_page_quality(status, recommended_parser, updated_at);
                """
            )
            conn.commit()
            self._initialized = True

    def save_assessments(
        self,
        *,
        record: SourceIngestionRecord,
        assessments: Sequence[PageQualityAssessment],
    ) -> None:
        content_hash = str(record.metadata.get("content_hash") or "")

        def save() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    "DELETE FROM source_qa_page_quality WHERE source_ingestion_id = ?",
                    (record.id,),
                )
                conn.executemany(
                    """
                    INSERT INTO source_qa_page_quality(
                        owner_user_id, package_id, source_ingestion_id, source_content_hash,
                        page_no, status, recommended_parser, score, reasons_json,
                        attempt_count, error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    [
                        (
                            record.owner_user_id, record.package_id, record.id, content_hash,
                            item.page_no, item.status, item.recommended_parser, item.score,
                            json.dumps(item.reasons, ensure_ascii=False), item.error, now_iso(),
                        )
                        for item in assessments
                    ],
                )

        self.coordinator.run_write(self.path, save)

    def pending(
        self,
        *,
        record: SourceIngestionRecord,
        page_numbers: Sequence[int] = (),
    ) -> list[PageQualityAssessment]:
        requested = tuple(sorted({page for page in page_numbers if page >= 1}))
        extra = ""
        params: list[object] = [
            record.owner_user_id,
            record.package_id,
            record.id,
            str(record.metadata.get("content_hash") or ""),
        ]
        if requested:
            extra = f"AND page_no IN ({', '.join('?' for _ in requested)})"
            params.extend(requested)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM source_qa_page_quality
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    AND source_content_hash = ? AND status = 'pending'
                    {extra}
                ORDER BY page_no
                """,
                params,
            ).fetchall()
        return [_assessment_from_row(row) for row in rows]

    def has_pending(self, *, record: SourceIngestionRecord) -> bool:
        return bool(self.pending(record=record))

    def failed_count(self, *, record: SourceIngestionRecord) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM source_qa_page_quality
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    AND source_content_hash = ? AND status = 'failed'
                """,
                (
                    record.owner_user_id,
                    record.package_id,
                    record.id,
                    str(record.metadata.get("content_hash") or ""),
                ),
            ).fetchone()
        return int(row["count"]) if row else 0

    def queued_sources(self) -> list[tuple[str, str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT owner_user_id, package_id, source_ingestion_id
                FROM source_qa_page_quality
                WHERE status = 'pending'
                """
            ).fetchall()
        return [
            (str(row["owner_user_id"]), str(row["package_id"]), str(row["source_ingestion_id"]))
            for row in rows
        ]

    def recover_interrupted(self) -> None:
        def recover() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    UPDATE source_qa_page_quality
                    SET status = 'pending', error = 'Interrupted before completion', updated_at = ?
                    WHERE status = 'running'
                    """,
                    (now_iso(),),
                )

        self.coordinator.run_write(self.path, recover)

    def mark(
        self,
        *,
        record: SourceIngestionRecord,
        page_numbers: Sequence[int],
        status: Literal["pending", "running", "complete", "failed"],
        error: str = "",
    ) -> None:
        pages = tuple(sorted({page for page in page_numbers if page >= 1}))
        if not pages:
            return

        def update() -> None:
            with self._lock, self._connect() as conn, conn:
                placeholders = ", ".join("?" for _ in pages)
                conn.execute(
                    f"""
                    UPDATE source_qa_page_quality
                    SET status = ?, error = ?,
                        attempt_count = attempt_count + ?, updated_at = ?
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        AND page_no IN ({placeholders})
                    """,
                    [
                        status, error[:1000], 1 if status == "running" else 0, now_iso(),
                        record.owner_user_id, record.package_id, record.id, *pages,
                    ],
                )

        self.coordinator.run_write(self.path, update)

    def delete_for_source(self, *, record: SourceIngestionRecord) -> None:
        def delete() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    DELETE FROM source_qa_page_quality
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (record.owner_user_id, record.package_id, record.id),
                )

        self.coordinator.run_write(self.path, delete)


class SourceQAEnhancementService:
    def __init__(
        self,
        *,
        evidence_store: SourceEvidenceStore,
        index_store: SourceQAIndexStore,
        parser_router: SourceParserRouter = source_parser_router,
        enhancement_store: SourceQAEnhancementStore | None = None,
    ) -> None:
        self.evidence_store = evidence_store
        self.index_store = index_store
        self.parser_router = parser_router
        self.store = enhancement_store or SourceQAEnhancementStore(index_store=index_store)
        self._parser_locks = _PARSER_LOCKS

    @property
    def enabled(self) -> bool:
        return os.getenv("OPENCLASS_SOURCE_QA_ENHANCEMENT_ENABLED", "0") == "1"

    def assess_and_queue(
        self,
        *,
        record: SourceIngestionRecord,
        document: ParsedDocumentV2,
    ) -> SourceIngestionRecord:
        assessments = assess_document_pages(document)
        self.store.save_assessments(record=record, assessments=assessments)
        pending_count = sum(item.status == "pending" for item in assessments)
        return self.evidence_store.save_source(
            record.model_copy(
                update={
                    "metadata": {
                        **record.metadata,
                        "source_qa_low_quality_page_count": pending_count,
                    }
                }
            )
        )

    def enhance(
        self,
        *,
        record: SourceIngestionRecord,
        path: Path,
        page_numbers: Sequence[int] = (),
    ) -> SourceIngestionRecord:
        pending = self.store.pending(record=record, page_numbers=page_numbers)
        if not pending:
            return record
        current = self.evidence_store.save_source(
            record.model_copy(update={"qa_status": "enhancing"})
        )
        self.index_store.set_index_status(
            owner_user_id=current.owner_user_id,
            package_id=current.package_id,
            source_ingestion_id=current.id,
            status="enhancing",
        )
        next_version = current.qa_index_version
        for parser in ("mineru", "docling"):
            pages = [item.page_no for item in pending if item.recommended_parser == parser]
            if not pages:
                continue
            self.store.mark(record=current, page_numbers=pages, status="running")
            try:
                with self._parser_locks[parser]:
                    enhanced = self.parser_router.parse_enhancement(
                        parser=parser,
                        source_id=current.id,
                        source_content_hash=str(current.metadata.get("content_hash") or ""),
                        path=path,
                        mime_type=current.mime_type,
                        page_numbers=pages,
                    )
                next_version = self.index_store.publish_enhanced_pages(
                    record=current,
                    document=enhanced,
                    page_numbers=pages,
                )
            except Exception as exc:
                self.store.mark(
                    record=current,
                    page_numbers=pages,
                    status="failed",
                    error=str(exc),
                )
            else:
                self.store.mark(record=current, page_numbers=pages, status="complete")

        remaining = self.store.pending(record=current)
        final_status = "enhancing" if remaining else "complete"
        self.index_store.set_index_status(
            owner_user_id=current.owner_user_id,
            package_id=current.package_id,
            source_ingestion_id=current.id,
            status=final_status,
        )
        return self.evidence_store.save_source(
            current.model_copy(
                update={
                    "qa_status": final_status,
                    "qa_index_version": max(current.qa_index_version, next_version),
                    "enhancement_failed_page_count": self.store.failed_count(record=current),
                    "metadata": {
                        **current.metadata,
                        "source_qa_last_enhancement_at": now_iso(),
                    },
                }
            )
        )


class SourceQAEnhancementTaskManager:
    def __init__(
        self,
        *,
        service: SourceQAEnhancementService,
        max_workers: int = 2,
    ) -> None:
        self.service = service
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="source-qa-enhancement",
        )
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def submit(self, record: SourceIngestionRecord) -> bool:
        if not self.service.enabled or not self.service.store.has_pending(record=record):
            return False
        with self._lock:
            if record.id in self._active:
                return False
            self._active.add(record.id)
        self._executor.submit(self._run, record.owner_user_id, record.package_id, record.id)
        return True

    def recover_active(self) -> None:
        if not self.service.enabled:
            return
        self.service.store.recover_interrupted()
        for owner_user_id, package_id, source_id in self.service.store.queued_sources():
            record = self.service.evidence_store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if record is not None:
                self.submit(record)

    def _run(self, owner_user_id: str, package_id: str, source_id: str) -> None:
        try:
            record = self.service.evidence_store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if record is None:
                return
            from app.services.source_ingestion_service import source_local_path

            path = source_local_path(record)
            if path is not None:
                self.service.enhance(record=record, path=path)
        finally:
            with self._lock:
                self._active.discard(source_id)


def assess_document_pages(document: ParsedDocumentV2) -> list[PageQualityAssessment]:
    result: list[PageQualityAssessment] = []
    for page_no in range(1, document.page_count + 1):
        elements = [item for item in document.elements if item.page_no == page_no]
        text = "\n".join((item.normalized_text or item.raw_text) for item in elements).strip()
        printable = sum(character.isprintable() and not character.isspace() for character in text)
        replacements = text.count("�") + text.count("\ufffd")
        average_confidence = (
            sum(item.confidence for item in elements) / len(elements) if elements else 0.0
        )
        reasons: list[str] = []
        parser: EnhancementParser | None = None
        score = 1.0
        if printable < 32:
            parser = "mineru"
            score = 0.0
            reasons.append("missing_or_sparse_text")
        elif replacements / max(1, len(text)) > 0.03 or average_confidence < 0.35:
            parser = "mineru"
            score = min(0.3, average_confidence)
            reasons.append("garbled_or_low_confidence_text")
        else:
            complex_types = {item.element_type for item in elements} & {"table", "formula"}
            layout_flags = {
                str(item.metadata.get("layout_complexity") or "").lower()
                for item in elements
            }
            bad_reading_order = any(
                str(item.metadata.get("reading_order_quality") or "").lower()
                in {"low", "failed"}
                for item in elements
            )
            if (complex_types and average_confidence < 0.9) or "multi_column" in layout_flags or bad_reading_order:
                parser = "docling"
                score = min(0.7, average_confidence)
                reasons.append("complex_layout_or_structure")
        result.append(
            PageQualityAssessment(
                page_no=page_no,
                status="pending" if parser else "good",
                recommended_parser=parser,
                score=score,
                reasons=tuple(reasons),
            )
        )
    return result


def _assessment_from_row(row: sqlite3.Row) -> PageQualityAssessment:
    try:
        reasons = json.loads(str(row["reasons_json"] or "[]"))
    except json.JSONDecodeError:
        reasons = []
    return PageQualityAssessment(
        page_no=int(row["page_no"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        recommended_parser=(
            str(row["recommended_parser"]) if row["recommended_parser"] else None
        ),  # type: ignore[arg-type]
        score=float(row["score"]),
        reasons=tuple(str(item) for item in reasons),
        error=str(row["error"] or ""),
    )


_default_index_store = SourceQAIndexStore(
    path=source_evidence_store.path,
    coordinator=source_evidence_store.coordinator,
)
source_qa_enhancement_service = SourceQAEnhancementService(
    evidence_store=source_evidence_store,
    index_store=_default_index_store,
)
source_qa_enhancement_task_manager = SourceQAEnhancementTaskManager(
    service=source_qa_enhancement_service,
)

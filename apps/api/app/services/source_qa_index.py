from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from app.models import RetrievalEvidence, SourceIngestionRecord, new_id, now_iso
from app.services.native_source_index import (
    SourceEmbeddingProvider,
)
from app.services.source_ingestion_jobs import (
    SourceIngestionCoordinator,
    source_ingestion_coordinator,
)
from app.services.source_parser_adapters import ParsedDocumentV2, ParsedSourceElement
from app.services.source_qa_model_sidecar import (
    SourceReranker,
    default_embedding_provider,
    default_reranker,
    embed_many,
)


TARGET_CHUNK_TOKENS = 500
MIN_CHUNK_TOKENS = 350
MAX_CHUNK_TOKENS = 700
CHUNK_OVERLAP_RATIO = 0.12
RRF_K = 60


@dataclass(frozen=True)
class SourceQAChunk:
    id: str
    owner_user_id: str
    package_id: str
    source_ingestion_id: str
    source_content_hash: str
    parser_run_id: str
    page_start: int
    page_end: int
    reading_order_start: int
    reading_order_end: int
    text: str
    normalized_text: str
    token_count: int
    element_ids: tuple[str, ...]
    element_types: tuple[str, ...]
    bbox: tuple[float, ...]
    context_path: tuple[str, ...]


class SourceQAIndexStore:
    """Independent full-text QA index; never mutates formal source directory rows."""

    def __init__(
        self,
        path: Path,
        *,
        coordinator: SourceIngestionCoordinator = source_ingestion_coordinator,
        embedding_provider: SourceEmbeddingProvider | None = None,
        reranker: SourceReranker | None = None,
    ) -> None:
        self.path = path
        self.coordinator = coordinator
        self.embedding_provider = embedding_provider or default_embedding_provider()
        self.reranker = reranker or default_reranker()
        self._lock = threading.RLock()
        self._initialized = False
        self.fts_available = False
        self.sqlite_vec_available = False
        self._sqlite_vec_module: object | None = None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._load_sqlite_vec(conn)
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
                CREATE TABLE IF NOT EXISTS source_qa_indexes (
                    source_ingestion_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_content_hash TEXT NOT NULL,
                    parser TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    parser_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    index_version INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    indexed_page_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_qa_indexes_scope
                    ON source_qa_indexes(owner_user_id, package_id, status);

                CREATE TABLE IF NOT EXISTS source_qa_chunks (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    source_content_hash TEXT NOT NULL,
                    parser_run_id TEXT NOT NULL,
                    index_version INTEGER NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    reading_order_start INTEGER NOT NULL,
                    reading_order_end INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    element_ids_json TEXT NOT NULL,
                    element_types_json TEXT NOT NULL,
                    bbox_json TEXT NOT NULL,
                    context_path_json TEXT NOT NULL,
                    previous_chunk_id TEXT,
                    next_chunk_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_qa_chunks_scope
                    ON source_qa_chunks(owner_user_id, package_id, source_ingestion_id, page_start, page_end);

                CREATE TABLE IF NOT EXISTS source_qa_chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_qa_embeddings_scope
                    ON source_qa_chunk_embeddings(owner_user_id, package_id, source_ingestion_id);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS source_qa_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        owner_user_id UNINDEXED,
                        package_id UNINDEXED,
                        source_ingestion_id UNINDEXED,
                        text,
                        tokenize = 'unicode61 remove_diacritics 2'
                    )
                    """
                )
            except sqlite3.OperationalError:
                self.fts_available = False
            else:
                self.fts_available = True
            if self.sqlite_vec_available and self.embedding_provider.dimensions == 1024:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS source_qa_vec_map (
                        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        chunk_id TEXT NOT NULL UNIQUE
                    );
                    CREATE VIRTUAL TABLE IF NOT EXISTS source_qa_vec_chunks USING vec0(
                        embedding float[1024],
                        source_partition integer partition key
                    );
                    """
                )
            conn.commit()
            self._initialized = True

    def _load_sqlite_vec(self, conn: sqlite3.Connection) -> None:
        if (
            self.embedding_provider.dimensions != 1024
            or os.getenv("OPENCLASS_SOURCE_QA_SQLITE_VEC_ENABLED", "1") != "1"
        ):
            return
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except (ImportError, sqlite3.Error):
            self.sqlite_vec_available = False
            self._sqlite_vec_module = None
        else:
            self.sqlite_vec_available = True
            self._sqlite_vec_module = sqlite_vec

    def _delete_vec_chunks(self, conn: sqlite3.Connection, chunk_ids: Sequence[str]) -> None:
        if not self.sqlite_vec_available or not chunk_ids:
            return
        placeholders = ", ".join("?" for _ in chunk_ids)
        rowids = [
            int(row["rowid"])
            for row in conn.execute(
                f"SELECT rowid FROM source_qa_vec_map WHERE chunk_id IN ({placeholders})",
                list(chunk_ids),
            ).fetchall()
        ]
        if rowids:
            row_placeholders = ", ".join("?" for _ in rowids)
            conn.execute(
                f"DELETE FROM source_qa_vec_chunks WHERE rowid IN ({row_placeholders})",
                rowids,
            )
        conn.execute(
            f"DELETE FROM source_qa_vec_map WHERE chunk_id IN ({placeholders})",
            list(chunk_ids),
        )

    def _insert_vec_chunk(
        self,
        conn: sqlite3.Connection,
        *,
        chunk: SourceQAChunk,
        embedding: Sequence[float],
    ) -> None:
        if not self.sqlite_vec_available or self._sqlite_vec_module is None:
            return
        cursor = conn.execute("INSERT INTO source_qa_vec_map(chunk_id) VALUES (?)", (chunk.id,))
        serialize = getattr(self._sqlite_vec_module, "serialize_float32")
        conn.execute(
            "INSERT INTO source_qa_vec_chunks(rowid, embedding, source_partition) VALUES (?, ?, ?)",
            (
                int(cursor.lastrowid),
                serialize(list(embedding)),
                _source_partition(chunk.owner_user_id, chunk.package_id, chunk.source_ingestion_id),
            ),
        )

    def current_index_version(self, *, source_ingestion_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT index_version FROM source_qa_indexes WHERE source_ingestion_id = ?",
                (source_ingestion_id,),
            ).fetchone()
        return int(row["index_version"]) if row else 0

    def ready_source_ids(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_ingestion_ids: Sequence[str],
    ) -> set[str]:
        source_ids = tuple(dict.fromkeys(value for value in source_ingestion_ids if value))
        if not source_ids:
            return set()
        placeholders = ", ".join("?" for _ in source_ids)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_ingestion_id FROM source_qa_indexes
                WHERE owner_user_id = ? AND package_id = ?
                    AND source_ingestion_id IN ({placeholders})
                    AND status IN ('ready', 'enhancing', 'complete')
                """,
                [owner_user_id, package_id, *source_ids],
            ).fetchall()
        return {str(row["source_ingestion_id"]) for row in rows}

    def publish_document(
        self,
        *,
        record: SourceIngestionRecord,
        document: ParsedDocumentV2,
    ) -> tuple[int, int]:
        if document.source_id != record.id:
            raise ValueError("Parsed document source id does not match the ingestion record.")
        content_hash = str(record.metadata.get("content_hash") or "").strip().lower()
        if not content_hash or document.source_content_hash.lower() != content_hash:
            raise ValueError("Parsed document content hash does not match the ingestion record.")
        parser_run_id = new_id("parser_run")
        chunks = build_source_qa_chunks(
            record=record,
            document=document,
            parser_run_id=parser_run_id,
        )
        next_version = self.current_index_version(source_ingestion_id=record.id) + 1
        stamp = now_iso()
        chunk_embeddings = embed_many(
            self.embedding_provider,
            [chunk.normalized_text for chunk in chunks],
        )

        def publish() -> None:
            with self._lock, self._connect() as conn, conn:
                old_ids = [
                    str(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM source_qa_chunks WHERE source_ingestion_id = ?",
                        (record.id,),
                    ).fetchall()
                ]
                if old_ids:
                    placeholders = ", ".join("?" for _ in old_ids)
                    conn.execute(
                        f"DELETE FROM source_qa_chunk_embeddings WHERE chunk_id IN ({placeholders})",
                        old_ids,
                    )
                    self._delete_vec_chunks(conn, old_ids)
                conn.execute("DELETE FROM source_qa_chunks WHERE source_ingestion_id = ?", (record.id,))
                if self.fts_available:
                    conn.execute("DELETE FROM source_qa_chunks_fts WHERE source_ingestion_id = ?", (record.id,))

                for index, (chunk, embedding) in enumerate(zip(chunks, chunk_embeddings)):
                    previous_id = chunks[index - 1].id if index else None
                    next_id = chunks[index + 1].id if index + 1 < len(chunks) else None
                    conn.execute(
                        """
                        INSERT INTO source_qa_chunks(
                            id, owner_user_id, package_id, source_ingestion_id, source_content_hash,
                            parser_run_id, index_version, page_start, page_end, reading_order_start,
                            reading_order_end, text, normalized_text, token_count, element_ids_json,
                            element_types_json, bbox_json, context_path_json, previous_chunk_id,
                            next_chunk_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.id, chunk.owner_user_id, chunk.package_id,
                            chunk.source_ingestion_id, chunk.source_content_hash,
                            chunk.parser_run_id, next_version, chunk.page_start, chunk.page_end,
                            chunk.reading_order_start, chunk.reading_order_end, chunk.text,
                            chunk.normalized_text, chunk.token_count, _dumps(chunk.element_ids),
                            _dumps(chunk.element_types), _dumps(chunk.bbox),
                            _dumps(chunk.context_path), previous_id, next_id, stamp,
                        ),
                    )
                    self._insert_vec_chunk(conn, chunk=chunk, embedding=embedding)
                    conn.execute(
                        """
                        INSERT INTO source_qa_chunk_embeddings(
                            chunk_id, owner_user_id, package_id, source_ingestion_id,
                            provider, model, dimensions, embedding_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.id, chunk.owner_user_id, chunk.package_id,
                            chunk.source_ingestion_id, self.embedding_provider.provider,
                            self.embedding_provider.model, self.embedding_provider.dimensions,
                            _dumps(embedding), stamp,
                        ),
                    )
                    if self.fts_available:
                        conn.execute(
                            "INSERT INTO source_qa_chunks_fts VALUES (?, ?, ?, ?, ?)",
                            (
                                chunk.id, chunk.owner_user_id, chunk.package_id,
                                chunk.source_ingestion_id, chunk.normalized_text,
                            ),
                        )
                conn.execute(
                    """
                    INSERT INTO source_qa_indexes(
                        source_ingestion_id, owner_user_id, package_id, source_content_hash,
                        parser, parser_version, parser_run_id, status, index_version,
                        page_count, indexed_page_count, chunk_count, warnings_json,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_ingestion_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        package_id = excluded.package_id,
                        source_content_hash = excluded.source_content_hash,
                        parser = excluded.parser,
                        parser_version = excluded.parser_version,
                        parser_run_id = excluded.parser_run_id,
                        status = excluded.status,
                        index_version = excluded.index_version,
                        page_count = excluded.page_count,
                        indexed_page_count = excluded.indexed_page_count,
                        chunk_count = excluded.chunk_count,
                        warnings_json = excluded.warnings_json,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        record.id, record.owner_user_id, record.package_id, content_hash,
                        document.parser, document.parser_version, parser_run_id, next_version,
                        document.page_count, len({item.page_no for item in document.elements}),
                        len(chunks), _dumps(document.warnings), _dumps(document.metadata), stamp, stamp,
                    ),
                )

        self.coordinator.run_write(self.path, publish)
        return next_version, len(chunks)

    def delete_for_source(
        self, *, owner_user_id: str, package_id: str, source_ingestion_id: str
    ) -> None:
        def delete() -> None:
            with self._lock, self._connect() as conn, conn:
                chunk_ids = [
                    str(row["id"])
                    for row in conn.execute(
                        """
                        SELECT id FROM source_qa_chunks
                        WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        """,
                        (owner_user_id, package_id, source_ingestion_id),
                    ).fetchall()
                ]
                if chunk_ids:
                    placeholders = ", ".join("?" for _ in chunk_ids)
                    conn.execute(
                        f"DELETE FROM source_qa_chunk_embeddings WHERE chunk_id IN ({placeholders})",
                        chunk_ids,
                    )
                    self._delete_vec_chunks(conn, chunk_ids)
                conn.execute(
                    "DELETE FROM source_qa_chunks WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                    (owner_user_id, package_id, source_ingestion_id),
                )
                if self.fts_available:
                    conn.execute(
                        "DELETE FROM source_qa_chunks_fts WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                        (owner_user_id, package_id, source_ingestion_id),
                    )
                conn.execute(
                    "DELETE FROM source_qa_indexes WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                    (owner_user_id, package_id, source_ingestion_id),
                )

        self.coordinator.run_write(self.path, delete)

    def publish_enhanced_pages(
        self,
        *,
        record: SourceIngestionRecord,
        document: ParsedDocumentV2,
        page_numbers: Sequence[int],
    ) -> int:
        requested_pages = tuple(sorted({page for page in page_numbers if page >= 1}))
        if not requested_pages:
            return self.current_index_version(source_ingestion_id=record.id)
        content_hash = str(record.metadata.get("content_hash") or "").strip().lower()
        if document.source_id != record.id or document.source_content_hash.lower() != content_hash:
            raise ValueError("Enhanced document identity does not match the ingestion record.")
        document_pages = {element.page_no for element in document.elements}
        if not set(requested_pages).issubset(document_pages):
            raise ValueError("Enhanced parser did not return every requested page.")
        parser_run_id = new_id("parser_run")
        enhanced_chunks = [
            chunk
            for chunk in build_source_qa_chunks(
                record=record,
                document=document,
                parser_run_id=parser_run_id,
            )
            if chunk.page_start in requested_pages
        ]
        if not enhanced_chunks:
            raise ValueError("Enhanced parser returned no indexable content.")
        next_version = self.current_index_version(source_ingestion_id=record.id) + 1
        stamp = now_iso()
        chunk_embeddings = embed_many(
            self.embedding_provider,
            [chunk.normalized_text for chunk in enhanced_chunks],
        )

        def publish() -> None:
            with self._lock, self._connect() as conn, conn:
                placeholders = ", ".join("?" for _ in requested_pages)
                old_rows = conn.execute(
                    f"""
                    SELECT id FROM source_qa_chunks
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        AND page_start IN ({placeholders})
                    """,
                    [record.owner_user_id, record.package_id, record.id, *requested_pages],
                ).fetchall()
                old_ids = [str(row["id"]) for row in old_rows]
                if old_ids:
                    old_placeholders = ", ".join("?" for _ in old_ids)
                    conn.execute(
                        f"DELETE FROM source_qa_chunk_embeddings WHERE chunk_id IN ({old_placeholders})",
                        old_ids,
                    )
                    self._delete_vec_chunks(conn, old_ids)
                    conn.execute(
                        f"DELETE FROM source_qa_chunks WHERE id IN ({old_placeholders})",
                        old_ids,
                    )
                if self.fts_available:
                    conn.execute(
                        f"""
                        DELETE FROM source_qa_chunks_fts
                        WHERE source_ingestion_id = ? AND chunk_id IN ({', '.join('?' for _ in old_ids)})
                        """ if old_ids else "SELECT 1",
                        [record.id, *old_ids] if old_ids else [],
                    )
                conn.execute(
                    "UPDATE source_qa_chunks SET index_version = ? WHERE source_ingestion_id = ?",
                    (next_version, record.id),
                )
                for chunk, embedding in zip(enhanced_chunks, chunk_embeddings):
                    conn.execute(
                        """
                        INSERT INTO source_qa_chunks(
                            id, owner_user_id, package_id, source_ingestion_id, source_content_hash,
                            parser_run_id, index_version, page_start, page_end, reading_order_start,
                            reading_order_end, text, normalized_text, token_count, element_ids_json,
                            element_types_json, bbox_json, context_path_json, previous_chunk_id,
                            next_chunk_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            chunk.id, chunk.owner_user_id, chunk.package_id,
                            chunk.source_ingestion_id, chunk.source_content_hash, chunk.parser_run_id,
                            next_version, chunk.page_start, chunk.page_end,
                            chunk.reading_order_start, chunk.reading_order_end, chunk.text,
                            chunk.normalized_text, chunk.token_count, _dumps(chunk.element_ids),
                            _dumps(chunk.element_types), _dumps(chunk.bbox),
                            _dumps(chunk.context_path), stamp,
                        ),
                    )
                    self._insert_vec_chunk(conn, chunk=chunk, embedding=embedding)
                    conn.execute(
                        """
                        INSERT INTO source_qa_chunk_embeddings(
                            chunk_id, owner_user_id, package_id, source_ingestion_id,
                            provider, model, dimensions, embedding_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.id, chunk.owner_user_id, chunk.package_id,
                            chunk.source_ingestion_id, self.embedding_provider.provider,
                            self.embedding_provider.model, self.embedding_provider.dimensions,
                            _dumps(embedding), stamp,
                        ),
                    )
                    if self.fts_available:
                        conn.execute(
                            "INSERT INTO source_qa_chunks_fts VALUES (?, ?, ?, ?, ?)",
                            (
                                chunk.id, chunk.owner_user_id, chunk.package_id,
                                chunk.source_ingestion_id, chunk.normalized_text,
                            ),
                        )
                ordered_ids = [
                    str(row["id"])
                    for row in conn.execute(
                        """
                        SELECT id FROM source_qa_chunks
                        WHERE source_ingestion_id = ?
                        ORDER BY page_start, reading_order_start, id
                        """,
                        (record.id,),
                    ).fetchall()
                ]
                for index, chunk_id in enumerate(ordered_ids):
                    conn.execute(
                        "UPDATE source_qa_chunks SET previous_chunk_id = ?, next_chunk_id = ? WHERE id = ?",
                        (
                            ordered_ids[index - 1] if index else None,
                            ordered_ids[index + 1] if index + 1 < len(ordered_ids) else None,
                            chunk_id,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE source_qa_indexes
                    SET parser_run_id = ?, status = 'enhancing', index_version = ?,
                        chunk_count = ?, updated_at = ?
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (
                        parser_run_id, next_version, len(ordered_ids), stamp,
                        record.owner_user_id, record.package_id, record.id,
                    ),
                )

        self.coordinator.run_write(self.path, publish)
        return next_version

    def set_index_status(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_ingestion_id: str,
        status: str,
    ) -> None:
        if status not in {"ready", "enhancing", "complete", "failed"}:
            raise ValueError("Unsupported Source QA index status.")

        def update() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    UPDATE source_qa_indexes SET status = ?, updated_at = ?
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (status, now_iso(), owner_user_id, package_id, source_ingestion_id),
                )

        self.coordinator.run_write(self.path, update)

    def search(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        source_ingestion_ids: Sequence[str],
        source_by_id: Mapping[str, SourceIngestionRecord],
        page_ranges: Mapping[str, tuple[int, int]] | None = None,
        limit: int = 8,
        token_budget: int = 6_000,
    ) -> list[RetrievalEvidence]:
        source_ids = tuple(dict.fromkeys(value for value in source_ingestion_ids if value))
        if not query.strip() or not source_ids or limit <= 0:
            return []
        placeholders = ", ".join("?" for _ in source_ids)
        scope_sql, scope_params = _page_scope_sql(source_ids, page_ranges or {})
        query_embedding = self.embedding_provider.embed(query)
        vector_ids: list[str] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_qa_chunks.*, source_qa_chunk_embeddings.embedding_json
                FROM source_qa_chunks
                JOIN source_qa_chunk_embeddings
                    ON source_qa_chunk_embeddings.chunk_id = source_qa_chunks.id
                JOIN source_qa_indexes
                    ON source_qa_indexes.source_ingestion_id = source_qa_chunks.source_ingestion_id
                    AND source_qa_indexes.source_content_hash = source_qa_chunks.source_content_hash
                    AND source_qa_indexes.index_version = source_qa_chunks.index_version
                WHERE source_qa_chunks.owner_user_id = ?
                    AND source_qa_chunks.package_id = ?
                    AND source_qa_chunks.source_ingestion_id IN ({placeholders})
                    AND source_qa_indexes.status IN ('ready', 'enhancing', 'complete')
                    {scope_sql}
                """,
                [owner_user_id, package_id, *source_ids, *scope_params],
            ).fetchall()
            if not rows:
                return []
            keyword_ids = self._keyword_candidates(
                conn,
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=query,
                allowed_ids={str(row["id"]) for row in rows},
            )
            vector_ids = self._vector_candidates(
                conn,
                query_embedding=query_embedding,
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_ingestion_ids=source_ids,
                allowed_ids={str(row["id"]) for row in rows},
            )

        if vector_ids:
            semantic_ids = vector_ids
        else:
            semantic_rank = sorted(
                rows,
                key=lambda row: _cosine(query_embedding, _loads(row["embedding_json"], [])),
                reverse=True,
            )[:40]
            semantic_ids = [str(row["id"]) for row in semantic_rank]
        rrf: dict[str, float] = {}
        for rank, chunk_id in enumerate(keyword_ids[:40], start=1):
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for rank, chunk_id in enumerate(semantic_ids, start=1):
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        rows_by_id = {str(row["id"]): row for row in rows}
        fused_ids = sorted(rrf, key=lambda chunk_id: rrf[chunk_id], reverse=True)[:32]
        rerank_scores = self.reranker.rerank(
            query=query,
            documents=[str(rows_by_id[chunk_id]["text"]) for chunk_id in fused_ids],
        )
        score_by_id = {
            chunk_id: rerank_scores[index]
            for index, chunk_id in enumerate(fused_ids)
            if index < len(rerank_scores)
        }
        ranked_ids = sorted(
            fused_ids,
            key=lambda chunk_id: (score_by_id.get(chunk_id, 0.0), rrf[chunk_id]),
            reverse=True,
        )

        evidence: list[RetrievalEvidence] = []
        used_tokens = 0
        covered_chunk_ids: set[str] = set()
        for chunk_id in ranked_ids:
            if chunk_id in covered_chunk_ids:
                continue
            row = rows_by_id[chunk_id]
            expanded_rows = [
                rows_by_id[neighbor_id]
                for neighbor_id in (
                    str(row["previous_chunk_id"] or ""),
                    chunk_id,
                    str(row["next_chunk_id"] or ""),
                )
                if neighbor_id in rows_by_id
                and str(rows_by_id[neighbor_id]["source_ingestion_id"])
                == str(row["source_ingestion_id"])
            ]
            expanded_rows.sort(
                key=lambda item: (int(item["page_start"]), int(item["reading_order_start"]))
            )
            expanded_chunk_ids = [str(item["id"]) for item in expanded_rows]
            token_count = sum(int(item["token_count"]) for item in expanded_rows)
            if used_tokens and used_tokens + token_count > token_budget:
                continue
            source_id = str(row["source_ingestion_id"])
            source = source_by_id.get(source_id)
            if source is None:
                continue
            used_tokens += token_count
            covered_chunk_ids.update(expanded_chunk_ids)
            page_start = int(row["page_start"])
            page_end = int(row["page_end"])
            expanded_page_start = min(int(item["page_start"]) for item in expanded_rows)
            expanded_page_end = max(int(item["page_end"]) for item in expanded_rows)
            expanded_text = "\n\n".join(str(item["text"]) for item in expanded_rows)
            boxes = [
                box
                for item in expanded_rows
                for box in [_loads(item["bbox_json"], [])]
                if isinstance(box, list) and len(box) == 4
            ]
            evidence.append(
                RetrievalEvidence(
                    source_ingestion_id=source_id,
                    open_notebook_source_id=source.open_notebook_source_id,
                    source_title=source.title,
                    source_uri=source.source_uri,
                    section_path=_loads(row["context_path_json"], []),
                    page_range=(
                        f"p. {page_start}" if page_start == page_end else f"pp. {page_start}-{page_end}"
                    ),
                    chunk_ids=expanded_chunk_ids,
                    excerpt=_compact_text(str(row["text"]), 360),
                    expanded_text=expanded_text,
                    relevance_score=score_by_id.get(chunk_id, rrf[chunk_id]),
                    reason="FTS5 and local embeddings fused with RRF, then reranked.",
                    token_count=token_count,
                    metadata={
                        "retrieval_mode": "source_qa_hybrid",
                        "page_start": page_start,
                        "page_end": page_end,
                        "expanded_page_start": expanded_page_start,
                        "expanded_page_end": expanded_page_end,
                        "bbox": _bbox_union(boxes),
                        "parser_run_id": str(row["parser_run_id"]),
                        "qa_index_version": int(row["index_version"]),
                        "source_content_hash": str(row["source_content_hash"]),
                        "embedding_model": self.embedding_provider.model,
                        "reranker_model": self.reranker.model,
                        "element_ids": _loads(row["element_ids_json"], []),
                        "element_types": _loads(row["element_types_json"], []),
                    },
                )
            )
            if len(evidence) >= limit or used_tokens >= token_budget:
                break
        return evidence

    def _keyword_candidates(
        self,
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        allowed_ids: set[str],
    ) -> list[str]:
        if not self.fts_available:
            terms = _query_terms(query)
            if not terms:
                return []
            return [
                str(row["id"])
                for row in conn.execute(
                    "SELECT id, normalized_text FROM source_qa_chunks WHERE owner_user_id = ? AND package_id = ?",
                    (owner_user_id, package_id),
                ).fetchall()
                if str(row["id"]) in allowed_ids
                and any(term in str(row["normalized_text"]).lower() for term in terms)
            ][:40]
        expression = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in _query_terms(query))
        if not expression:
            return []
        try:
            rows = conn.execute(
                """
                SELECT chunk_id, bm25(source_qa_chunks_fts) AS score
                FROM source_qa_chunks_fts
                WHERE source_qa_chunks_fts MATCH ?
                    AND owner_user_id = ? AND package_id = ?
                ORDER BY score LIMIT 160
                """,
                (expression, owner_user_id, package_id),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [str(row["chunk_id"]) for row in rows if str(row["chunk_id"]) in allowed_ids][:40]

    def _vector_candidates(
        self,
        conn: sqlite3.Connection,
        *,
        query_embedding: Sequence[float],
        owner_user_id: str,
        package_id: str,
        source_ingestion_ids: Sequence[str],
        allowed_ids: set[str],
    ) -> list[str]:
        if not self.sqlite_vec_available or self._sqlite_vec_module is None:
            return []
        serialize = getattr(self._sqlite_vec_module, "serialize_float32")
        ranked: list[tuple[float, int]] = []
        try:
            for source_id in source_ingestion_ids:
                rows = conn.execute(
                    """
                    SELECT rowid, distance FROM source_qa_vec_chunks
                    WHERE embedding MATCH ? AND k = 40 AND source_partition = ?
                    ORDER BY distance
                    """,
                    (
                        serialize(list(query_embedding)),
                        _source_partition(owner_user_id, package_id, source_id),
                    ),
                ).fetchall()
                ranked.extend((float(row["distance"]), int(row["rowid"])) for row in rows)
        except sqlite3.OperationalError:
            return []
        ranked.sort()
        rowids = [rowid for _distance, rowid in ranked]
        if not rowids:
            return []
        placeholders = ", ".join("?" for _ in rowids)
        mapped = {
            int(row["rowid"]): str(row["chunk_id"])
            for row in conn.execute(
                f"SELECT rowid, chunk_id FROM source_qa_vec_map WHERE rowid IN ({placeholders})",
                rowids,
            ).fetchall()
        }
        return [mapped[rowid] for rowid in rowids if mapped.get(rowid) in allowed_ids][:40]


def build_source_qa_chunks(
    *, record: SourceIngestionRecord, document: ParsedDocumentV2, parser_run_id: str
) -> list[SourceQAChunk]:
    chunks: list[SourceQAChunk] = []
    headings: list[str] = []
    for page_no in range(1, document.page_count + 1):
        elements = sorted(
            (item for item in document.elements if item.page_no == page_no),
            key=lambda item: item.reading_order,
        )
        page_units: list[tuple[ParsedSourceElement, str, tuple[str, ...]]] = []
        for element in elements:
            text = (element.normalized_text or element.raw_text).strip()
            if not text:
                continue
            if element.element_type == "heading":
                headings = [*headings[-2:], text[:180]]
            page_units.append((element, text, tuple(headings)))
        if not page_units:
            continue
        chunks.extend(
            _chunk_page_units(
                record=record,
                page_no=page_no,
                units=page_units,
                parser_run_id=parser_run_id,
            )
        )
    return chunks


def _chunk_page_units(
    *,
    record: SourceIngestionRecord,
    page_no: int,
    units: list[tuple[ParsedSourceElement, str, tuple[str, ...]]],
    parser_run_id: str,
) -> list[SourceQAChunk]:
    expanded: list[tuple[ParsedSourceElement, str, tuple[str, ...]]] = []
    for element, text, context in units:
        if _estimate_tokens(text) <= MAX_CHUNK_TOKENS or element.element_type in {"table", "formula"}:
            expanded.append((element, text, context))
            continue
        pieces = _split_large_text(text, max_tokens=MAX_CHUNK_TOKENS)
        expanded.extend((element, piece, context) for piece in pieces)

    result: list[SourceQAChunk] = []
    current: list[tuple[ParsedSourceElement, str, tuple[str, ...]]] = []
    current_tokens = 0
    for unit in expanded:
        unit_tokens = _estimate_tokens(unit[1])
        binds_formula_context = (
            unit[0].element_type == "formula"
            or bool(current and current[-1][0].element_type == "formula")
        )
        if (
            current
            and not binds_formula_context
            and current_tokens >= MIN_CHUNK_TOKENS
            and current_tokens + unit_tokens > MAX_CHUNK_TOKENS
        ):
            result.append(_make_chunk(record, page_no, current, parser_run_id))
            overlap_target = max(1, int(TARGET_CHUNK_TOKENS * CHUNK_OVERLAP_RATIO))
            overlap: list[tuple[ParsedSourceElement, str, tuple[str, ...]]] = []
            overlap_tokens = 0
            for previous in reversed(current):
                overlap.insert(0, previous)
                overlap_tokens += _estimate_tokens(previous[1])
                if overlap_tokens >= overlap_target:
                    break
            current = overlap
            current_tokens = overlap_tokens
        current.append(unit)
        current_tokens += unit_tokens
        if current_tokens >= TARGET_CHUNK_TOKENS and unit[0].element_type != "formula":
            result.append(_make_chunk(record, page_no, current, parser_run_id))
            current = []
            current_tokens = 0
    if current:
        result.append(_make_chunk(record, page_no, current, parser_run_id))
    return result


def _make_chunk(
    record: SourceIngestionRecord,
    page_no: int,
    units: list[tuple[ParsedSourceElement, str, tuple[str, ...]]],
    parser_run_id: str,
) -> SourceQAChunk:
    text = "\n\n".join(value for _element, value, _context in units if value).strip()
    elements = [element for element, _value, _context in units]
    context = next((value for _element, _text, value in reversed(units) if value), ())
    bbox = _bbox_union([element.bbox for element in elements if len(element.bbox) == 4])
    identity = "\0".join((record.id, str(page_no), text, parser_run_id))
    return SourceQAChunk(
        id="qa_chunk_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        source_content_hash=str(record.metadata.get("content_hash") or ""),
        parser_run_id=parser_run_id,
        page_start=page_no,
        page_end=page_no,
        reading_order_start=min((item.reading_order for item in elements), default=0),
        reading_order_end=max((item.reading_order for item in elements), default=0),
        text=text,
        normalized_text=_normalize_text(text),
        token_count=_estimate_tokens(text),
        element_ids=tuple(dict.fromkeys(item.element_id for item in elements)),
        element_types=tuple(dict.fromkeys(item.element_type for item in elements)),
        bbox=tuple(bbox),
        context_path=tuple(context),
    )


def _page_scope_sql(
    source_ids: Sequence[str], page_ranges: Mapping[str, tuple[int, int]]
) -> tuple[str, list[object]]:
    normalized = {
        source_id: page_ranges[source_id]
        for source_id in source_ids
        if source_id in page_ranges
    }
    if not normalized:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    for source_id, (page_start, page_end) in normalized.items():
        clauses.append(
            "(source_qa_chunks.source_ingestion_id = ? AND source_qa_chunks.page_start <= ? AND source_qa_chunks.page_end >= ?)"
        )
        params.extend((source_id, page_end, page_start))
    return "AND (" + " OR ".join(clauses) + ")", params


def _split_large_text(text: str, *, max_tokens: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) > 1:
        result: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and _estimate_tokens(candidate) > max_tokens:
                result.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            result.append(current)
        if all(_estimate_tokens(item) <= max_tokens for item in result):
            return result
    words = re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", text)
    return [" ".join(words[index:index + max_tokens]) for index in range(0, len(words), max_tokens)]


def _estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    punctuation = len(re.findall(r"[^\w\s\u3400-\u9fff]", text))
    return max(1, cjk + non_cjk_words + math.ceil(punctuation / 3))


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[\u3400-\u9fff]{1,8}|[A-Za-z0-9_][A-Za-z0-9_.-]*", query.lower())
    return list(dict.fromkeys(term for term in terms if term.strip()))[:24]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _source_partition(owner_user_id: str, package_id: str, source_ingestion_id: str) -> int:
    digest = hashlib.blake2b(
        "\0".join((owner_user_id, package_id, source_ingestion_id)).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") & ((1 << 63) - 1)


def _bbox_union(boxes: list[list[float]]) -> list[float]:
    if not boxes:
        return []
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, fallback: object) -> object:
    try:
        return json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return fallback

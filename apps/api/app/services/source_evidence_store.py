from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.models import EvidenceBundle, SourceIngestionRecord, now_iso
from app.services import workspace_state
from app.services.source_ingestion_jobs import (
    SourceIngestionCoordinator,
    source_ingestion_coordinator,
)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class SourceEvidenceStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        coordinator: SourceIngestionCoordinator = source_ingestion_coordinator,
    ) -> None:
        self._path = path
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        return workspace_state.get_store().path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._initialize_connection(conn, path)
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize_connection(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            path_key = str(path)
            if path_key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_notebooks (
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    open_notebook_notebook_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (owner_user_id, package_id)
                );

                CREATE TABLE IF NOT EXISTS source_ingestions (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_uri TEXT,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    open_notebook_notebook_id TEXT NOT NULL,
                    open_notebook_source_id TEXT NOT NULL,
                    open_notebook_command_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_ingestions_owner_package
                    ON source_ingestions(owner_user_id, package_id, updated_at);

                CREATE TABLE IF NOT EXISTS source_ingestion_tombstones (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    deleted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_bundles (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    lesson_id TEXT,
                    requirement_run_id TEXT,
                    board_task_run_id TEXT,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    query TEXT NOT NULL,
                    evidence_items_json TEXT NOT NULL,
                    visual_items_json TEXT NOT NULL DEFAULT '[]',
                    context_text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    confirmed_by_user INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_bundles_lesson
                    ON evidence_bundles(owner_user_id, lesson_id, status, updated_at);
                """
            )
            evidence_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(evidence_bundles)").fetchall()
            }
            if "visual_items_json" not in evidence_columns:
                conn.execute(
                    "ALTER TABLE evidence_bundles ADD COLUMN visual_items_json TEXT NOT NULL DEFAULT '[]'"
                )
            self._initialized_paths.add(path_key)

    def get_notebook_id(self, *, owner_user_id: str, package_id: str) -> str | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT open_notebook_notebook_id
                    FROM source_notebooks
                    WHERE owner_user_id = ? AND package_id = ?
                    """,
                    (owner_user_id, package_id),
                ).fetchone()
        return str(row["open_notebook_notebook_id"]) if row else None

    def upsert_notebook(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        notebook_id: str,
        title: str,
    ) -> None:
        stamp = now_iso()

        def save_notebook() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    INSERT INTO source_notebooks(
                        owner_user_id, package_id, open_notebook_notebook_id, title, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_user_id, package_id) DO UPDATE SET
                        open_notebook_notebook_id = excluded.open_notebook_notebook_id,
                        title = excluded.title,
                        updated_at = excluded.updated_at
                    """,
                    (owner_user_id, package_id, notebook_id, title, stamp, stamp),
                )

        self.coordinator.run_write(self.path, save_notebook)

    def save_source(self, record: SourceIngestionRecord) -> SourceIngestionRecord:
        record = record.model_copy(update={"updated_at": now_iso()})

        def save_record() -> SourceIngestionRecord:
            with self._lock, self._connect() as conn, conn:
                deleted = conn.execute(
                    """
                    SELECT 1 FROM source_ingestion_tombstones
                    WHERE id = ? AND owner_user_id = ? AND package_id = ?
                    """,
                    (record.id, record.owner_user_id, record.package_id),
                ).fetchone()
                if deleted is not None:
                    return record
                conn.execute(
                    """
                    INSERT INTO source_ingestions(
                        id, owner_user_id, package_id, title, source_type, source_uri, file_name, mime_type,
                        size_bytes, status, error, open_notebook_notebook_id, open_notebook_source_id,
                        open_notebook_command_id, created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        source_type = excluded.source_type,
                        source_uri = excluded.source_uri,
                        file_name = excluded.file_name,
                        mime_type = excluded.mime_type,
                        size_bytes = excluded.size_bytes,
                        status = excluded.status,
                        error = excluded.error,
                        open_notebook_notebook_id = excluded.open_notebook_notebook_id,
                        open_notebook_source_id = excluded.open_notebook_source_id,
                        open_notebook_command_id = excluded.open_notebook_command_id,
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        record.id,
                        record.owner_user_id,
                        record.package_id,
                        record.title,
                        record.source_type,
                        record.source_uri,
                        record.file_name,
                        record.mime_type,
                        record.size_bytes,
                        record.status,
                        record.error,
                        record.open_notebook_notebook_id,
                        record.open_notebook_source_id,
                        record.open_notebook_command_id,
                        record.created_at,
                        record.updated_at,
                        _dumps(record.metadata),
                    ),
                )
            return record

        return self.coordinator.run_write(self.path, save_record)

    def list_sources(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM source_ingestions
                    WHERE owner_user_id = ? AND package_id = ?
                    ORDER BY updated_at DESC, created_at DESC
                    """,
                    (owner_user_id, package_id),
                ).fetchall()
        return [self._source_from_row(row) for row in rows]

    def ready_sources(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionRecord]:
        return [
            source
            for source in self.list_sources(owner_user_id=owner_user_id, package_id=package_id)
            if source.status == "ready"
        ]

    def get_source_by_open_notebook_id(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        open_notebook_source_id: str,
    ) -> SourceIngestionRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM source_ingestions
                    WHERE owner_user_id = ? AND package_id = ? AND open_notebook_source_id = ?
                    """,
                    (owner_user_id, package_id, open_notebook_source_id),
                ).fetchone()
        return self._source_from_row(row) if row else None

    def get_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM source_ingestions
                    WHERE owner_user_id = ? AND package_id = ? AND id = ?
                    """,
                    (owner_user_id, package_id, source_id),
                ).fetchone()
        return self._source_from_row(row) if row else None

    def delete_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionRecord | None:
        record = self.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None

        def delete_record() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    INSERT INTO source_ingestion_tombstones(id, owner_user_id, package_id, deleted_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        package_id = excluded.package_id,
                        deleted_at = excluded.deleted_at
                    """,
                    (source_id, owner_user_id, package_id, now_iso()),
                )
                conn.execute(
                    """
                    DELETE FROM source_ingestions
                    WHERE owner_user_id = ? AND package_id = ? AND id = ?
                    """,
                    (owner_user_id, package_id, source_id),
                )

        self.coordinator.run_write(self.path, delete_record)
        return record

    def save_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        bundle = bundle.model_copy(update={"updated_at": now_iso()})

        def save_evidence_bundle() -> EvidenceBundle:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    INSERT INTO evidence_bundles(
                        id, owner_user_id, package_id, lesson_id, requirement_run_id, board_task_run_id,
                        purpose, status, query, evidence_items_json, visual_items_json, context_text, token_count,
                        confirmed_by_user, created_at, updated_at, confirmed_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        evidence_items_json = excluded.evidence_items_json,
                        visual_items_json = excluded.visual_items_json,
                        context_text = excluded.context_text,
                        token_count = excluded.token_count,
                        confirmed_by_user = excluded.confirmed_by_user,
                        updated_at = excluded.updated_at,
                        confirmed_at = excluded.confirmed_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        bundle.id,
                        bundle.owner_user_id,
                        bundle.package_id,
                        bundle.lesson_id,
                        bundle.requirement_run_id,
                        bundle.board_task_run_id,
                        bundle.purpose,
                        bundle.status,
                        bundle.query,
                        _dumps([item.model_dump(mode="json") for item in bundle.evidence_items]),
                        _dumps([item.model_dump(mode="json") for item in bundle.visual_items]),
                        bundle.context_text,
                        bundle.token_count,
                        int(bundle.confirmed_by_user),
                        bundle.created_at,
                        bundle.updated_at,
                        bundle.confirmed_at,
                        _dumps(bundle.metadata),
                    ),
                )
            return bundle

        return self.coordinator.run_write(self.path, save_evidence_bundle)

    def get_bundle(self, *, owner_user_id: str, bundle_id: str) -> EvidenceBundle | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM evidence_bundles
                    WHERE owner_user_id = ? AND id = ?
                    """,
                    (owner_user_id, bundle_id),
                ).fetchone()
        return self._bundle_from_row(row) if row else None

    def latest_bundle(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        status: str,
        purpose: str | None = None,
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
    ) -> EvidenceBundle | None:
        params: list[object] = [owner_user_id, lesson_id, status]
        filters: list[str] = []
        if purpose:
            filters.append("purpose = ?")
            params.append(purpose)
        if requirement_run_id:
            filters.append("requirement_run_id = ?")
            params.append(requirement_run_id)
        if board_task_run_id:
            filters.append("board_task_run_id = ?")
            params.append(board_task_run_id)
        filter_sql = f"AND {' AND '.join(filters)}" if filters else ""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT *
                    FROM evidence_bundles
                    WHERE owner_user_id = ? AND lesson_id = ? AND status = ?
                    {filter_sql}
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
        return self._bundle_from_row(row) if row else None

    def latest_requirement_bundle(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        requirement_run_id: str,
    ) -> EvidenceBundle | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM evidence_bundles
                    WHERE owner_user_id = ?
                      AND lesson_id = ?
                      AND requirement_run_id = ?
                      AND purpose = 'board_generation'
                      AND status IN ('candidate', 'confirmed')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (owner_user_id, lesson_id, requirement_run_id),
                ).fetchone()
        return self._bundle_from_row(row) if row else None

    def confirm_bundle(self, *, owner_user_id: str, bundle_id: str) -> EvidenceBundle | None:
        bundle = self.get_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
        if bundle is None:
            return None
        confirmed = bundle.model_copy(
            update={
                "status": "confirmed",
                "confirmed_by_user": True,
                "confirmed_at": now_iso(),
            }
        )
        return self.save_bundle(confirmed)

    def archive_bundle(self, *, owner_user_id: str, bundle_id: str) -> EvidenceBundle | None:
        bundle = self.get_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
        if bundle is None:
            return None
        return self.save_bundle(bundle.model_copy(update={"status": "archived"}))

    def consume_bundle(self, *, owner_user_id: str, bundle_id: str) -> EvidenceBundle | None:
        bundle = self.get_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
        if bundle is None:
            return None
        return self.save_bundle(bundle.model_copy(update={"status": "consumed"}))

    def _source_from_row(self, row: sqlite3.Row) -> SourceIngestionRecord:
        return SourceIngestionRecord(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            title=row["title"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            file_name=row["file_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            status=row["status"],
            error=row["error"],
            open_notebook_notebook_id=row["open_notebook_notebook_id"],
            open_notebook_source_id=row["open_notebook_source_id"],
            open_notebook_command_id=row["open_notebook_command_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def _bundle_from_row(self, row: sqlite3.Row) -> EvidenceBundle:
        return EvidenceBundle(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            lesson_id=row["lesson_id"],
            requirement_run_id=row["requirement_run_id"],
            board_task_run_id=row["board_task_run_id"],
            purpose=row["purpose"],
            status=row["status"],
            query=row["query"],
            evidence_items=_loads(row["evidence_items_json"], []),
            visual_items=_loads(row["visual_items_json"], []),
            context_text=row["context_text"],
            token_count=row["token_count"],
            confirmed_by_user=bool(row["confirmed_by_user"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            confirmed_at=row["confirmed_at"],
            metadata=_loads(row["metadata_json"], {}),
        )


source_evidence_store = SourceEvidenceStore()

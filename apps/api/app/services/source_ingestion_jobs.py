from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from app.models import SourceIngestionJob, now_iso
from app.services import workspace_state


WriteResult = TypeVar("WriteResult")
SourceTaskKey = tuple[str, str, str]
logger = logging.getLogger(__name__)


class SourceIngestionCoordinator:
    """Bound source parsing and serialize source writes for one SQLite database."""

    def __init__(
        self,
        *,
        processing_capacity: int = 2,
        large_source_bytes: int = 64 * 1024 * 1024,
        lock_retry_delays: tuple[float, ...] = (0.05, 0.15, 0.5, 1.0, 2.0),
    ) -> None:
        if processing_capacity < 1:
            raise ValueError("processing_capacity must be positive")
        self.processing_capacity = processing_capacity
        self.large_source_bytes = large_source_bytes
        self.lock_retry_delays = lock_retry_delays
        self._processing_available = processing_capacity
        self._processing_waiters: deque[tuple[object, int]] = deque()
        self._processing_condition = threading.Condition()
        self._write_locks: dict[str, threading.RLock] = {}
        self._write_locks_guard = threading.Lock()

    def processing_weight(self, *, size_bytes: int, source_type: str) -> int:
        if size_bytes >= self.large_source_bytes or source_type in {"audio_file", "video_file"}:
            return self.processing_capacity
        return 1

    @contextmanager
    def processing_slot(self, *, weight: int = 1) -> Iterator[None]:
        normalized_weight = max(1, min(self.processing_capacity, weight))
        ticket = object()
        with self._processing_condition:
            self._processing_waiters.append((ticket, normalized_weight))
            while (
                self._processing_waiters[0][0] is not ticket
                or self._processing_available < normalized_weight
            ):
                self._processing_condition.wait()
            self._processing_waiters.popleft()
            self._processing_available -= normalized_weight
        try:
            yield
        finally:
            with self._processing_condition:
                self._processing_available += normalized_weight
                self._processing_condition.notify_all()

    def run_write(self, path: Path, operation: Callable[[], WriteResult]) -> WriteResult:
        write_lock = self._write_lock(path)
        with write_lock:
            for attempt in range(len(self.lock_retry_delays) + 1):
                try:
                    return operation()
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt >= len(self.lock_retry_delays):
                        raise
                    time.sleep(self.lock_retry_delays[attempt])
        raise RuntimeError("unreachable source write retry state")

    def _write_lock(self, path: Path) -> threading.RLock:
        key = str(path.resolve())
        with self._write_locks_guard:
            return self._write_locks.setdefault(key, threading.RLock())


source_ingestion_coordinator = SourceIngestionCoordinator()


class SourceIngestionJobStore:
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
        return self._path or workspace_state.get_store().path

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
            self._initialize_connection(conn, path)
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize_connection(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            key = str(path)
            if key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_uri TEXT,
                    adapter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    phase_history_json TEXT NOT NULL DEFAULT '[]',
                    agent_activity_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_ingestion_jobs_scope
                    ON source_ingestion_jobs(owner_user_id, package_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_source_ingestion_jobs_source
                    ON source_ingestion_jobs(owner_user_id, package_id, source_ingestion_id, updated_at);
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(source_ingestion_jobs)").fetchall()
            }
            if "agent_activity_json" not in columns:
                conn.execute(
                    "ALTER TABLE source_ingestion_jobs "
                    "ADD COLUMN agent_activity_json TEXT NOT NULL DEFAULT '[]'"
                )
            self._initialized_paths.add(key)

    def save(
        self,
        job: SourceIngestionJob,
        *,
        owner_user_id: str,
        package_id: str,
    ) -> SourceIngestionJob:
        job = job.model_copy(update={"updated_at": now_iso()})

        def save_job() -> SourceIngestionJob:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    INSERT INTO source_ingestion_jobs(
                        id, owner_user_id, package_id, source_ingestion_id, source_type, source_uri,
                        adapter, status, progress, error, phase_history_json, agent_activity_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        progress = excluded.progress,
                        error = excluded.error,
                        phase_history_json = excluded.phase_history_json,
                        agent_activity_json = excluded.agent_activity_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        job.id,
                        owner_user_id,
                        package_id,
                        job.resource_id or "",
                        job.source_type,
                        job.source_uri,
                        job.adapter,
                        job.status,
                        job.progress,
                        job.error,
                        json.dumps(job.phase_history, ensure_ascii=False),
                        json.dumps(
                            [event.model_dump(mode="json") for event in job.agent_activity],
                            ensure_ascii=False,
                        ),
                        job.created_at,
                        job.updated_at,
                    ),
                )
            return job

        return self.coordinator.run_write(self.path, save_job)

    def list(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionJob]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM source_ingestion_jobs
                WHERE owner_user_id = ? AND package_id = ?
                ORDER BY updated_at DESC
                """,
                (owner_user_id, package_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_active_scopes(self) -> list[SourceTaskKey]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT owner_user_id, package_id, source_ingestion_id
                FROM source_ingestion_jobs AS candidate
                WHERE candidate.id = (
                    SELECT latest.id
                    FROM source_ingestion_jobs AS latest
                    WHERE latest.owner_user_id = candidate.owner_user_id
                      AND latest.package_id = candidate.package_id
                      AND latest.source_ingestion_id = candidate.source_ingestion_id
                    ORDER BY latest.updated_at DESC, latest.id DESC
                    LIMIT 1
                )
                  AND candidate.status IN ('queued', 'fetching', 'parsing', 'indexing')
                ORDER BY candidate.updated_at ASC
                """
            ).fetchall()
        return [
            (str(row["owner_user_id"]), str(row["package_id"]), str(row["source_ingestion_id"]))
            for row in rows
            if str(row["source_ingestion_id"] or "").strip()
        ]

    def latest_for_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionJob | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM source_ingestion_jobs
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (owner_user_id, package_id, source_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def delete_for_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> None:
        def delete_jobs() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    "DELETE FROM source_ingestion_jobs WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                    (owner_user_id, package_id, source_id),
                )

        self.coordinator.run_write(self.path, delete_jobs)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SourceIngestionJob:
        try:
            phases = json.loads(row["phase_history_json"] or "[]")
        except json.JSONDecodeError:
            phases = []
        try:
            activity = json.loads(row["agent_activity_json"] or "[]")
        except (json.JSONDecodeError, IndexError):
            activity = []
        return SourceIngestionJob(
            id=row["id"],
            resource_id=row["source_ingestion_id"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            adapter=row["adapter"],
            status=row["status"],
            progress=row["progress"],
            error=row["error"],
            phase_history=phases,
            agent_activity=activity,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


source_ingestion_job_store = SourceIngestionJobStore()


class SourceIngestionTaskManager:
    """Run persisted source work outside an individual HTTP request lifecycle."""

    def __init__(self, job_store: SourceIngestionJobStore = source_ingestion_job_store) -> None:
        self.job_store = job_store
        self._active: set[SourceTaskKey] = set()
        self._lock = threading.Lock()

    def submit(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        retry: bool = False,
    ) -> bool:
        key = (owner_user_id, package_id, source_id)
        with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
        threading.Thread(
            target=self._run,
            kwargs={"key": key, "retry": retry},
            daemon=True,
            name=f"source-ingestion-{source_id}",
        ).start()
        return True

    def recover_active(self) -> int:
        return sum(
            self.submit(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            for owner_user_id, package_id, source_id in self.job_store.list_active_scopes()
        )

    def is_active(self, key: SourceTaskKey) -> bool:
        with self._lock:
            return key in self._active

    def _run(self, *, key: SourceTaskKey, retry: bool) -> None:
        owner_user_id, package_id, source_id = key
        try:
            from app.services.source_ingestion_service import source_ingestion_service

            record = source_ingestion_service.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            weight = self.job_store.coordinator.processing_capacity
            if record is not None:
                weight = self.job_store.coordinator.processing_weight(
                    size_bytes=record.size_bytes,
                    source_type=record.source_type,
                )
            operation = (
                source_ingestion_service.retry_source
                if retry
                else source_ingestion_service.process_file_source
            )
            with self.job_store.coordinator.processing_slot(weight=weight):
                operation(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
        except Exception:
            logger.exception("Source ingestion task failed for %s", source_id)
        finally:
            with self._lock:
                self._active.discard(key)


source_ingestion_task_manager = SourceIngestionTaskManager()

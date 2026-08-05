from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from app.models import SourceIngestionJob, now_iso
from app.services import workspace_state


WriteResult = TypeVar("WriteResult")
SourceTaskKey = tuple[str, str, str]
logger = logging.getLogger(__name__)
_CURRENT_SOURCE_TASK = threading.local()


@dataclass
class SourceTaskHandle:
    cancel_event: threading.Event
    key: SourceTaskKey
    run_id: str = ""
    content_hash: str = ""
    thread: threading.Thread | None = None
    process: subprocess.Popen[str] | None = None
    workspace: Path | None = None


def source_task_cancel_requested() -> bool:
    handle = getattr(_CURRENT_SOURCE_TASK, "handle", None)
    return bool(handle is not None and handle.cancel_event.is_set())


def register_current_source_task_process(process: subprocess.Popen[str] | None) -> None:
    handle = getattr(_CURRENT_SOURCE_TASK, "handle", None)
    if handle is None:
        return
    handle.process = process
    _write_task_manifest(handle)


def register_current_source_task_workspace(workspace: Path | None) -> None:
    handle = getattr(_CURRENT_SOURCE_TASK, "handle", None)
    if handle is None:
        return
    handle.workspace = workspace
    _write_task_manifest(handle)


def update_current_source_task_run_id(run_id: str) -> None:
    handle = getattr(_CURRENT_SOURCE_TASK, "handle", None)
    if handle is None:
        return
    handle.run_id = run_id
    _write_task_manifest(handle)


def _write_task_manifest(handle: SourceTaskHandle) -> None:
    if handle.workspace is None or not handle.workspace.is_dir():
        return
    owner_user_id, package_id, source_id = handle.key
    manifest_path = handle.workspace / "source-task-manifest.json"
    payload = {
        "owner_user_id": owner_user_id,
        "package_id": package_id,
        "source_id": source_id,
        "run_id": handle.run_id,
        "content_hash": handle.content_hash,
        "pid": handle.process.pid if handle.process is not None else None,
        "manager_pid": os.getpid(),
        "workspace": str(handle.workspace),
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)


def _terminate_task_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        logger.warning("Pi process group %s did not exit after SIGKILL", process.pid)


class SourceIngestionCoordinator:
    """Coordinate source work and serialize source writes for one SQLite database."""

    def __init__(
        self,
        *,
        processing_capacity: int | None = None,
        large_source_bytes: int = 64 * 1024 * 1024,
        lock_retry_delays: tuple[float, ...] = (0.05, 0.15, 0.5, 1.0, 2.0),
    ) -> None:
        if processing_capacity is not None and processing_capacity < 1:
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
        if self.processing_capacity is None:
            return 1
        if size_bytes >= self.large_source_bytes or source_type in {"audio_file", "video_file"}:
            return self.processing_capacity
        return 1

    @contextmanager
    def processing_slot(self, *, weight: int = 1) -> Iterator[None]:
        if self.processing_capacity is None:
            yield
            return
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
                    run_id TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    heartbeat_at TEXT,
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
            if "run_id" not in columns:
                conn.execute("ALTER TABLE source_ingestion_jobs ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")
            if "cancel_requested" not in columns:
                conn.execute(
                    "ALTER TABLE source_ingestion_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
                )
            if "heartbeat_at" not in columns:
                conn.execute("ALTER TABLE source_ingestion_jobs ADD COLUMN heartbeat_at TEXT")
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
                        run_id, cancel_requested, heartbeat_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        progress = excluded.progress,
                        error = excluded.error,
                        phase_history_json = excluded.phase_history_json,
                        agent_activity_json = excluded.agent_activity_json,
                        run_id = excluded.run_id,
                        cancel_requested = excluded.cancel_requested,
                        heartbeat_at = excluded.heartbeat_at,
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
                        job.run_id or job.id,
                        int(job.cancel_requested),
                        job.heartbeat_at,
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

    def mark_cancel_requested(self, *, owner_user_id: str, package_id: str, source_id: str) -> None:
        def mark_cancelled() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    """
                    UPDATE source_ingestion_jobs
                    SET cancel_requested = 1, updated_at = ?
                    WHERE id = (
                        SELECT id FROM source_ingestion_jobs
                        WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        ORDER BY updated_at DESC, id DESC LIMIT 1
                    )
                    """,
                    (now_iso(), owner_user_id, package_id, source_id),
                )

        self.coordinator.run_write(self.path, mark_cancelled)

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
            run_id=(row["run_id"] or row["id"]),
            resource_id=row["source_ingestion_id"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            adapter=row["adapter"],
            status=row["status"],
            progress=row["progress"],
            error=row["error"],
            phase_history=phases,
            agent_activity=activity,
            cancel_requested=bool(row["cancel_requested"]),
            heartbeat_at=row["heartbeat_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


source_ingestion_job_store = SourceIngestionJobStore()


class SourceIngestionTaskManager:
    """Run persisted source work outside an individual HTTP request lifecycle."""

    def __init__(self, job_store: SourceIngestionJobStore = source_ingestion_job_store) -> None:
        self.job_store = job_store
        self._active: dict[SourceTaskKey, SourceTaskHandle] = {}
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
        latest_job = self.job_store.latest_for_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        latest_run_id = (latest_job.run_id or latest_job.id) if latest_job is not None else ""
        superseded: SourceTaskHandle | None = None
        with self._lock:
            existing = self._active.get(key)
            if existing is not None:
                if existing.thread is not None and not existing.thread.is_alive():
                    self._active.pop(key, None)
                elif existing.run_id == latest_run_id:
                    return False
                else:
                    existing.cancel_event.set()
                    superseded = existing
            handle = SourceTaskHandle(
                cancel_event=threading.Event(),
                key=key,
                run_id=latest_run_id,
            )
            self._active[key] = handle
        if superseded is not None:
            _terminate_task_process(superseded.process)
        thread = threading.Thread(
            target=self._run,
            kwargs={"key": key, "retry": retry, "handle": handle},
            daemon=True,
            name=f"source-ingestion-{source_id}",
        )
        handle.thread = thread
        thread.start()
        return True

    def recover_active(self) -> int:
        from app.services.source_ingestion_service import source_ingestion_service

        candidates: list[tuple[SourceIngestionJob, SourceTaskKey, object]] = []
        for key in self.job_store.list_active_scopes():
            owner_user_id, package_id, source_id = key
            job = self.job_store.latest_for_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            record = source_ingestion_service.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if job is not None and record is not None:
                candidates.append((job, key, record))
        candidates.sort(key=lambda item: (item[0].updated_at, item[0].id), reverse=True)
        recovered = 0
        seen_content: set[tuple[str, str, str]] = set()
        for job, key, record in candidates:
            owner_user_id, package_id, source_id = key
            metadata = getattr(record, "metadata", {})
            content_hash = str(metadata.get("content_hash") or source_id)
            identity = (owner_user_id, package_id, content_hash)
            if identity in seen_content:
                self.job_store.save(
                    job.model_copy(
                        update={
                            "status": "failed",
                            "progress": 100,
                            "error": "Superseded by recovery of the same content hash.",
                            "cancel_requested": True,
                            "phase_history": [*job.phase_history, "superseded_on_recovery"],
                        }
                    ),
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                )
                source_ingestion_service.store.save_source(
                    record.model_copy(
                        update={
                            "status": "failed",
                            "error": "A newer task for the same file content was recovered.",
                        }
                    )
                )
                continue
            seen_content.add(identity)
            recovered += int(
                self.submit(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
            )
        return recovered

    def is_active(self, key: SourceTaskKey) -> bool:
        with self._lock:
            return key in self._active

    def cancel(self, key: SourceTaskKey, *, timeout_seconds: float = 5.0) -> bool:
        started_at = time.monotonic()
        with self._lock:
            handle = self._active.get(key)
        if handle is None:
            return True
        handle.cancel_event.set()
        owner_user_id, package_id, source_id = key
        self.job_store.mark_cancel_requested(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        _terminate_task_process(handle.process)
        thread = handle.thread
        if thread is not None and thread is not threading.current_thread():
            remaining = max(0.0, timeout_seconds - (time.monotonic() - started_at))
            thread.join(timeout=remaining)
        return thread is None or not thread.is_alive()

    def _run(self, *, key: SourceTaskKey, retry: bool, handle: SourceTaskHandle) -> None:
        owner_user_id, package_id, source_id = key
        _CURRENT_SOURCE_TASK.handle = handle
        try:
            from app.services.source_ingestion_service import source_ingestion_service

            record = source_ingestion_service.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if record is not None:
                handle.content_hash = str(record.metadata.get("content_hash") or "")
                _write_task_manifest(handle)
            weight = self.job_store.coordinator.processing_capacity or 1
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
                result = operation(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
                while (
                    not handle.cancel_event.is_set()
                    and result is not None
                    and result.status == "ready"
                    and result.ingestion_job is not None
                    and result.ingestion_job.status == "indexing"
                    and result.ingestion_job.phase_history
                    and result.ingestion_job.phase_history[-1] == "background_catalog_refine"
                ):
                    result = source_ingestion_service.continue_catalog_refine(
                        owner_user_id=owner_user_id,
                        package_id=package_id,
                        source_id=source_id,
                    )
        except Exception:
            if not handle.cancel_event.is_set():
                logger.exception("Source ingestion task failed for %s", source_id)
        finally:
            _CURRENT_SOURCE_TASK.handle = None
            with self._lock:
                if self._active.get(key) is handle:
                    self._active.pop(key, None)


source_ingestion_task_manager = SourceIngestionTaskManager()

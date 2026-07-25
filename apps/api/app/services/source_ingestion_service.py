from __future__ import annotations

import hashlib
import fcntl
import ipaddress
import os
import re
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    CoursePackage,
    SourceIngestionJob,
    SourceIngestionRecord,
)
from app.services.ai_model_catalog import default_text_selection
from app.services.open_notebook_adapter import (
    OpenNotebookAdapter,
    OpenNotebookAdapterError,
    open_notebook_adapter,
)
from app.services.open_notebook_source_backend import (
    OpenNotebookSourceBackend,
    status_from_open_notebook,
)
from app.services.source_evidence_store import (
    SourceEvidenceStore,
    source_evidence_store,
)
from app.services.source_directory_extractor import supports_directory_catalog
from app.services.source_directory_processor import (
    CATALOG_SCHEMA_VERSION,
    SourceDirectoryProcessor,
)
from app.services.source_ingestion_jobs import (
    SourceIngestionJobStore,
    source_ingestion_job_store,
)
from app.services.source_codex_progress import SourceCodexProgressTracker
from app.services.source_structure_indexer import (
    SourceStructureIndexer,
    source_structure_indexer,
)
from app.services.source_structure_store import (
    SourceStructureStore,
    source_structure_store,
)
from app.services.source_url_snapshot import (
    SourceUrlSnapshotError,
    fetch_url_source_snapshot,
)
from app.services.youtube_transcript_adapter import (
    YouTubeTranscriptAdapter,
    YouTubeTranscriptAdapterError,
    is_youtube_url,
    youtube_transcript_adapter,
)
from app.services import workspace_state


SUPPORTED_FILE_MIME_PREFIXES = (
    "application/pdf",
    "application/epub+zip",
    "application/vnd.openxmlformats-officedocument",
    "text/",
    "audio/",
    "video/",
    "image/",
)

_DIRECTORY_CATALOG_LOCKS_GUARD = threading.Lock()
_DIRECTORY_CATALOG_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_DIRECTORY_CATALOG_LOCK_DEPTH = threading.local()


@contextmanager
def _directory_catalog_processing_slot(
    *,
    database_path: Path,
    record: SourceIngestionRecord,
) -> Iterator[None]:
    key = (
        str(database_path.resolve()),
        record.owner_user_id,
        record.package_id,
        record.id,
    )
    with _DIRECTORY_CATALOG_LOCKS_GUARD:
        lock = _DIRECTORY_CATALOG_LOCKS.setdefault(key, threading.RLock())
    with lock:
        depths = getattr(_DIRECTORY_CATALOG_LOCK_DEPTH, "values", None)
        if depths is None:
            depths = {}
            _DIRECTORY_CATALOG_LOCK_DEPTH.values = depths
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        lock_directory = database_path.parent / "source-ingestion-locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()
        with (lock_directory / f"{lock_name}.lock").open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class SourceIngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceImportAutomationOutcome:
    artifact_ids: list[str]
    errors: list[str]


SourceImportAutomationRunner = Callable[..., SourceImportAutomationOutcome]


class SourceIngestionService:
    def __init__(
        self,
        *,
        adapter: OpenNotebookAdapter = open_notebook_adapter,
        source_backend: str | None = None,
        youtube_adapter: YouTubeTranscriptAdapter = youtube_transcript_adapter,
        store: SourceEvidenceStore = source_evidence_store,
        job_store: SourceIngestionJobStore = source_ingestion_job_store,
        structure_indexer: SourceStructureIndexer | None = None,
        structure_store: SourceStructureStore | None = None,
        directory_processor: SourceDirectoryProcessor | None = None,
        import_automation_runner: SourceImportAutomationRunner | None = None,
        media_transcription_provider: object | None = None,
    ) -> None:
        self.adapter = adapter
        self.open_notebook_backend = OpenNotebookSourceBackend(
            adapter=adapter,
            store=store,
            local_path=source_local_path,
        )
        self.source_backend = (source_backend or os.getenv("OPENCLASS_SOURCE_BACKEND", "native")).strip().lower()
        if self.source_backend not in {"open_notebook", "native"}:
            raise ValueError("OPENCLASS_SOURCE_BACKEND must be open_notebook or native")
        self.youtube_adapter = youtube_adapter
        self.store = store
        self.job_store = job_store
        self.structure_store = structure_store or _structure_store_for_source_store(store)
        self.structure_indexer = structure_indexer or SourceStructureIndexer(store=self.structure_store)
        self.directory_processor = directory_processor or SourceDirectoryProcessor(store=self.structure_store)
        # Explicit indexer injection remains a compatibility seam for parser
        # tests and legacy adapters. The platform singleton does not inject one,
        # so every supported UI file upload uses codex_directory_v1.
        self.directory_catalog_enabled = directory_processor is not None or structure_indexer is None
        self.import_automation_runner = import_automation_runner
        self.media_transcription_provider = media_transcription_provider

    def list_sources(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionRecord]:
        records = self.store.list_sources(owner_user_id=owner_user_id, package_id=package_id)
        if self.source_backend == "open_notebook":
            records = [
                record
                if _uses_directory_catalog(record)
                else self._refresh_open_notebook_source(record)
                for record in records
            ]
        return [self._attach_job(self.structure_store.attach_summary(record)) for record in records]

    def add_url_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        normalized_uri = _validate_public_url(source_uri)
        if is_youtube_url(normalized_uri):
            return self._save_youtube_transcript_source(
                owner_user_id=owner_user_id,
                package=package,
                source_uri=normalized_uri,
                title=title,
            )
        if self.source_backend == "open_notebook":
            return self._add_url_source_open_notebook(
                owner_user_id=owner_user_id,
                package=package,
                source_uri=normalized_uri,
                title=title,
            )
        display_title = title.strip() or normalized_uri
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="web_url",
            source_uri=normalized_uri,
            file_name="",
            mime_type="text/html",
            size_bytes=0,
            status="fetching",
            metadata={"adapter": "openclass_native_url"},
        )
        job = self._start_job(record, adapter="openclass_native_url", phase="fetching", progress=10)
        try:
            snapshot_metadata = fetch_url_source_snapshot(record, normalized_uri)
        except SourceUrlSnapshotError as exc:
            failed = self.store.save_source(self._failed_record(record, str(exc), phase="fetch_url"))
            self._finish_job(
                job,
                record=failed,
                status="failed",
                progress=100,
                error=str(exc),
                phase="failed",
            )
            return failed
        local_path = Path(str(snapshot_metadata.get("local_source_path") or ""))
        size_bytes = local_path.stat().st_size if local_path.is_file() else 0
        ready = record.model_copy(
            update={
                "status": "ready",
                "size_bytes": size_bytes,
                "metadata": {
                    **record.metadata,
                    **snapshot_metadata,
                    "content_hash": _file_content_hash(local_path),
                    "native_index_version": 1,
                },
            }
        )
        return self._save_and_index(ready, job)

    def add_file_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        file_name: str,
        content: bytes,
        mime_type: str,
        title: str = "",
        catalog_model: AIModelSelection | None = None,
    ) -> SourceIngestionRecord:
        queued = self.queue_file_source(
            owner_user_id=owner_user_id,
            package=package,
            file_name=file_name,
            content=content,
            mime_type=mime_type,
            title=title,
            catalog_model=catalog_model,
        )
        return self.process_file_source(
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_id=queued.id,
        )

    def queue_file_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        file_name: str,
        content: bytes,
        mime_type: str,
        title: str = "",
        catalog_model: AIModelSelection | None = None,
    ) -> SourceIngestionRecord:
        if not file_name.strip():
            raise SourceIngestionError("File name is required.")
        if not _supported_mime(mime_type, file_name):
            raise SourceIngestionError("This file type is not supported by the native source importer.")
        display_title = title.strip() or file_name
        source_type = _source_type_for_upload(mime_type=mime_type, file_name=file_name)
        selected_catalog_model = catalog_model or default_text_selection()
        use_directory_catalog = (
            source_type == "local_file"
            and _supported_directory_file_name(file_name)
            and self.directory_catalog_enabled
        )
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type=source_type,
            source_uri=None,
            file_name=file_name,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(content),
            status=(
                "parsing"
                if use_directory_catalog
                else "queued"
                if self.source_backend == "open_notebook"
                else "parsing"
            ),
            metadata={
                "adapter": (
                    "codex_directory_v1"
                    if use_directory_catalog
                    else "open_notebook"
                    if self.source_backend == "open_notebook"
                    else "openclass_native"
                ),
                "package_title": package.title,
                **(
                    {
                        "catalog_pipeline": CATALOG_SCHEMA_VERSION,
                        "catalog_model": selected_catalog_model.model_dump(mode="json"),
                    }
                    if use_directory_catalog
                    else {}
                ),
            },
        )
        file_metadata = _save_local_source_file(record, content)
        queued = self.store.save_source(
            record.model_copy(
                update={
                    "metadata": {
                        **record.metadata,
                        **file_metadata,
                        "content_hash": hashlib.sha256(content).hexdigest(),
                        "native_index_version": 1,
                    }
                }
            )
        )
        self._start_job(
            queued,
            adapter=str(queued.metadata.get("adapter") or "openclass_native"),
            phase="uploaded",
            progress=15,
        )
        return self._attach_job(self.structure_store.attach_summary(queued))

    def process_file_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionRecord:
        record = self.store.get_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        if record is None:
            raise SourceIngestionError("Source not found.")
        with _directory_catalog_processing_slot(
            database_path=self.store.path,
            record=record,
        ):
            current = self.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if current is None:
                raise SourceIngestionError("Source not found.")
            if current.status in {"ready", "failed"}:
                return self._attach_job(self.structure_store.attach_summary(current))
            return self._process_file_source_unlocked(current)

    def _process_file_source_unlocked(
        self,
        record: SourceIngestionRecord,
    ) -> SourceIngestionRecord:
        owner_user_id = record.owner_user_id
        package_id = record.package_id
        source_id = record.id
        use_directory_catalog = _uses_directory_catalog(record)
        if use_directory_catalog:
            record = _as_directory_catalog_record(record)
        elif self.source_backend == "native":
            record = _detach_open_notebook_state(record)
        job = self.job_store.latest_for_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        if job is None:
            job = self._start_job(
                record,
                adapter=str(record.metadata.get("adapter") or "openclass_native"),
                phase="parsing",
                progress=20,
            )
        else:
            job = self._update_job(
                job,
                record=record,
                status="parsing",
                progress=20,
                phase="parsing",
            )

        file_metadata = dict(record.metadata)
        if record.source_type in {"audio_file", "video_file"}:
            original_path = Path(str(file_metadata.get("local_source_path") or ""))
            if self.media_transcription_provider is None:
                error = "Media transcription requires an explicitly configured adapter."
                failed = self.store.save_source(
                    self._failed_record(
                        record.model_copy(update={"metadata": {**record.metadata, **file_metadata}}),
                        error,
                        phase="transcription",
                    )
                )
                self._finish_job(
                    job,
                    record=failed,
                    status="failed",
                    progress=100,
                    phase="failed",
                    error=error,
                )
                return self._attach_job(self.structure_store.attach_summary(failed))
            try:
                transcript = self.media_transcription_provider.transcribe(
                    original_path,
                    mime_type=record.mime_type,
                )
            except Exception as exc:
                failed = self.store.save_source(
                    self._failed_record(
                        record.model_copy(update={"metadata": {**record.metadata, **file_metadata}}),
                        str(exc),
                        phase="transcription",
                    )
                )
                self._finish_job(
                    job,
                    record=failed,
                    status="failed",
                    progress=100,
                    phase="failed",
                    error=str(exc),
                )
                return self._attach_job(self.structure_store.attach_summary(failed))
            transcript_record = record.model_copy(
                update={
                    "file_name": f"{Path(record.file_name).stem}-transcript.txt",
                    "mime_type": "text/plain",
                }
            )
            transcript_metadata = _save_local_source_text(transcript_record, transcript.text)
            file_metadata = {
                **transcript_metadata,
                "original_source_path": str(original_path),
                "original_mime_type": record.mime_type,
                "transcription_provider": transcript.provider,
                "transcription_model": transcript.model,
                "transcript_language": transcript.language,
            }
            record = transcript_record
        ready = record.model_copy(
            update={
                "status": "parsing",
                "metadata": {
                    **record.metadata,
                    **file_metadata,
                    "native_index_version": 1,
                },
            }
        )
        if self.source_backend == "open_notebook" and not use_directory_catalog:
            synced = self.open_notebook_backend.sync_file(ready)
            if synced.status == "failed":
                self._finish_job(
                    job,
                    record=synced,
                    status="failed",
                    progress=100,
                    phase="failed",
                    error=synced.error,
                )
                return self._attach_job(self.structure_store.attach_summary(synced))
            self._update_open_notebook_job(job, synced)
            return self._attach_job(self.structure_store.attach_summary(synced))
        return self._save_and_index(ready, job)

    def add_text_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        text: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        content = text.strip()
        if not content:
            raise SourceIngestionError("Pasted text is empty.")
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=title.strip() or _text_title(content),
            source_type="pasted_text",
            file_name="pasted-text.md",
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            status="queued" if self.source_backend == "open_notebook" else "ready",
            metadata={
                "adapter": "open_notebook" if self.source_backend == "open_notebook" else "openclass_native_text",
                "native_index_version": 1,
                "package_title": package.title,
            },
        )
        metadata = _save_local_source_text(record, content)
        record = record.model_copy(
            update={
                "metadata": {
                    **record.metadata,
                    **metadata,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            }
        )
        if self.source_backend == "open_notebook":
            record = self.open_notebook_backend.sync_file(record)
            if record.status == "failed":
                job = self._start_job(record, adapter="open_notebook", phase="failed", progress=100)
                return self._attach_job(self.structure_store.attach_summary(record))
            job = self._start_job(
                record,
                adapter="open_notebook",
                phase="open_notebook_processing",
                progress=35,
            )
            self._update_open_notebook_job(job, record)
            return self._attach_job(self.structure_store.attach_summary(record))
        job = self._start_job(
            record,
            adapter=str(record.metadata.get("adapter") or "openclass_native_text"),
            phase="parsing",
            progress=25,
        )
        return self._save_and_index(record, job)

    def remove_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        if supports_directory_catalog(record):
            with _directory_catalog_processing_slot(
                database_path=self.store.path,
                record=record,
            ):
                current = self.store.get_source(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
                if current is None:
                    return None
                return self._remove_source_unlocked(current)
        return self._remove_source_unlocked(record)

    def _remove_source_unlocked(self, record: SourceIngestionRecord) -> SourceIngestionRecord | None:
        if self.source_backend == "open_notebook" and record.open_notebook_source_id:
            try:
                self.adapter.delete_source(record.open_notebook_source_id)
            except OpenNotebookAdapterError:
                # Local deletion remains possible when the sidecar is temporarily unavailable.
                pass
        self.structure_store.delete_for_source(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        removed = self.store.delete_source(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        self.job_store.delete_for_source(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        _delete_local_source_file(record)
        return removed

    def rename_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        title: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        normalized = title.strip()
        if record is None or not normalized:
            return None
        if supports_directory_catalog(record):
            with _directory_catalog_processing_slot(
                database_path=self.store.path,
                record=record,
            ):
                current = self.store.get_source(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
                if current is None:
                    return None
                return self._rename_source_unlocked(current, normalized)
        return self._rename_source_unlocked(record, normalized)

    def _rename_source_unlocked(
        self,
        record: SourceIngestionRecord,
        title: str,
    ) -> SourceIngestionRecord:
        return self.structure_store.attach_summary(
            self.store.save_source(record.model_copy(update={"title": title}))
        )

    def update_source_content(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        content: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        normalized = content.strip()
        if not normalized:
            raise SourceIngestionError("Source content cannot be empty.")
        if supports_directory_catalog(record):
            with _directory_catalog_processing_slot(
                database_path=self.store.path,
                record=record,
            ):
                current = self.store.get_source(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
                if current is None:
                    return None
                return self._update_source_content_unlocked(current, normalized)
        return self._update_source_content_unlocked(record, normalized)

    def _update_source_content_unlocked(
        self,
        record: SourceIngestionRecord,
        normalized: str,
    ) -> SourceIngestionRecord:

        current_path = source_local_path(record)
        metadata = dict(record.metadata)
        is_native_text = record.source_type == "pasted_text"
        if not is_native_text and "original_source_path" not in metadata and current_path is not None:
            metadata["original_source_path"] = str(current_path)
            metadata["original_mime_type"] = record.mime_type
            metadata["original_file_name"] = record.file_name

        editable_record = record.model_copy(
            update={
                "file_name": record.file_name if is_native_text else f"{Path(record.file_name or record.id).stem}-edited.md",
                "mime_type": "text/markdown",
                "size_bytes": len(normalized.encode("utf-8")),
                "status": "ready",
                "error": "",
                "metadata": metadata,
            }
        )
        local_metadata = _save_local_source_text(editable_record, normalized)
        editable_record = editable_record.model_copy(
            update={
                "metadata": {
                    **metadata,
                    **local_metadata,
                    "adapter": "openclass_native_text",
                    "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    "content_edited": True,
                    "native_index_version": 1,
                }
            }
        )
        use_directory_catalog = _uses_directory_catalog(editable_record)
        if use_directory_catalog:
            editable_record = _as_directory_catalog_record(editable_record)
        job = self._start_job(
            editable_record,
            adapter=(
                "codex_directory_v1"
                if use_directory_catalog
                else "open_notebook"
                if self.source_backend == "open_notebook"
                else "openclass_native_text"
            ),
            phase="parsing",
            progress=25,
        )
        if self.source_backend == "open_notebook" and not use_directory_catalog:
            editable_record = self.open_notebook_backend.replace_file(editable_record)
            if editable_record.status == "failed":
                self._finish_job(
                    job,
                    record=editable_record,
                    status="failed",
                    progress=100,
                    phase="failed",
                    error=editable_record.error,
                )
                return self._attach_job(self.structure_store.attach_summary(editable_record))
            self.structure_store.delete_for_source(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_id=record.id,
            )
            self._update_open_notebook_job(job, editable_record)
            return self._attach_job(self.structure_store.attach_summary(editable_record))
        return self._save_and_index(editable_record, job, rebuild=True)

    def retry_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        with _directory_catalog_processing_slot(
            database_path=self.store.path,
            record=record,
        ):
            current = self.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if current is None:
                return None
            return self._retry_source_unlocked(current)

    def _retry_source_unlocked(self, record: SourceIngestionRecord) -> SourceIngestionRecord | None:
        record = _repair_local_source_storage(record)
        local_path = source_local_path(record)
        if local_path is None:
            raise SourceIngestionError("Source content is unavailable; import the source again.")
        retrying = record.model_copy(update={"status": "queued", "error": ""})
        use_directory_catalog = _uses_directory_catalog(retrying)
        if use_directory_catalog:
            retrying = _as_directory_catalog_record(retrying)
        elif self.source_backend == "native":
            retrying = _detach_open_notebook_state(retrying)
        self.store.save_source(retrying)
        job = self._start_job(
            retrying,
            adapter=(
                "codex_directory_v1"
                if use_directory_catalog
                else "open_notebook"
                if self.source_backend == "open_notebook"
                else str(record.metadata.get("adapter") or "openclass_native")
            ),
            phase=(
                "parsing"
                if use_directory_catalog
                else "queued"
                if self.source_backend == "open_notebook"
                else "parsing"
            ),
            progress=20,
        )
        if self.source_backend == "open_notebook" and not use_directory_catalog:
            retrying = self.open_notebook_backend.replace_file(retrying)
            if retrying.status == "failed":
                self._finish_job(
                    job,
                    record=retrying,
                    status="failed",
                    progress=100,
                    phase="failed",
                    error=retrying.error,
                )
                return self._attach_job(self.structure_store.attach_summary(retrying))
            self.structure_store.delete_for_source(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_id=record.id,
            )
            self._update_open_notebook_job(job, retrying)
            return self._attach_job(self.structure_store.attach_summary(retrying))
        return self._save_and_index(retrying, job, rebuild=True)

    def rebuild_catalog(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        catalog_model: AIModelSelection | None = None,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        if record is None:
            return None
        with _directory_catalog_processing_slot(
            database_path=self.store.path,
            record=record,
        ):
            current = self.store.get_source(
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_id=source_id,
            )
            if current is None:
                return None
            return self._rebuild_catalog_unlocked(
                current,
                catalog_model=catalog_model,
            )

    def _rebuild_catalog_unlocked(
        self,
        record: SourceIngestionRecord,
        *,
        catalog_model: AIModelSelection | None,
    ) -> SourceIngestionRecord:
        if not supports_directory_catalog(record):
            raise SourceIngestionError("This source format does not support a directory catalog.")
        record = _repair_local_source_storage(record)
        local_path = source_local_path(record)
        if local_path is None:
            raise SourceIngestionError("Source content is unavailable; import the source again.")
        selected_model = catalog_model
        if selected_model is None:
            try:
                selected_model = AIModelSelection.model_validate(record.metadata.get("catalog_model"))
            except Exception:
                selected_model = default_text_selection()
        rebuilding = _as_directory_catalog_record(record).model_copy(
            update={
                "status": "parsing",
                "error": "",
                "metadata": {
                    **record.metadata,
                    "catalog_pipeline": CATALOG_SCHEMA_VERSION,
                    "catalog_model": selected_model.model_dump(mode="json"),
                },
            }
        )
        self.store.save_source(rebuilding)
        job = self._start_job(
            rebuilding,
            adapter="codex_directory_v1",
            phase="reading_directory_metadata",
            progress=20,
        )
        return self._save_and_index(rebuilding, job, rebuild=True)

    def list_jobs(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionJob]:
        return self.job_store.list(owner_user_id=owner_user_id, package_id=package_id)

    def source_content(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> tuple[SourceIngestionRecord, str] | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        path = source_local_path(record)
        if path is None:
            return record, ""
        if record.mime_type.startswith("text/") and record.mime_type != "text/html":
            return record, path.read_text(encoding="utf-8", errors="replace")
        view = self.structure_store.get_structure_view(source=record, chunk_limit=5000)
        return record, "\n\n".join(chunk.text for chunk in view.chunks)

    def search_open_notebook(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        limit: int = 8,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Search only the Open Notebook index for an explicitly scoped request."""
        if self.source_backend != "open_notebook" or not query.strip():
            return []
        records = self.store.ready_sources(
            owner_user_id=owner_user_id,
            package_id=package_id,
        )
        records = [record for record in records if not _uses_directory_catalog(record)]
        try:
            return self.open_notebook_backend.search(
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=query,
                records=records,
                limit=limit,
                source_ids=source_ids,
            )
        except RuntimeError as exc:
            raise SourceIngestionError(str(exc)) from exc

    def _add_url_source_open_notebook(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str,
    ) -> SourceIngestionRecord:
        display_title = title.strip() or source_uri
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="web_url",
            source_uri=source_uri,
            file_name="",
            mime_type="text/html",
            size_bytes=0,
            status="queued",
            metadata={"adapter": "open_notebook", "package_title": package.title},
        )
        job = self._start_job(
            record,
            adapter="open_notebook",
            phase="queued",
            progress=10,
        )
        notebook_id, error = self.open_notebook_backend.resolve_notebook(
            owner_user_id=owner_user_id,
            package_id=package.id,
            package_title=package.title,
        )
        if error:
            failed = self.store.save_source(self.open_notebook_backend.failed_record(record, error, phase="create_notebook"))
            self._finish_job(
                job,
                record=failed,
                status="failed",
                progress=100,
                phase="failed",
                error=error,
            )
            return self._attach_job(self.structure_store.attach_summary(failed))
        try:
            result = self.adapter.add_url_source(
                notebook_id=notebook_id,
                source_uri=source_uri,
                title=display_title,
            )
        except OpenNotebookAdapterError as exc:
            error = self.open_notebook_backend.format_error(exc)
            failed = self.store.save_source(self.open_notebook_backend.failed_record(record, error, phase="add_source"))
            self._finish_job(
                job,
                record=failed,
                status="failed",
                progress=100,
                phase="failed",
                error=error,
            )
            return self._attach_job(self.structure_store.attach_summary(failed))

        remote_status = status_from_open_notebook(result.status)
        metadata: dict[str, object] = {
            **record.metadata,
            "source_processing_owner": "open_notebook",
            "open_notebook_sync_status": remote_status,
            "open_notebook_response": result.raw or {},
        }
        saved = self.store.save_source(record.model_copy(
            update={
                "status": remote_status,
                "open_notebook_notebook_id": notebook_id,
                "open_notebook_source_id": result.source_id,
                "open_notebook_command_id": result.command_id,
                "metadata": metadata,
            }
        ))
        self._update_open_notebook_job(job, saved)
        return self._attach_job(saved)

    def _refresh_open_notebook_source(
        self,
        record: SourceIngestionRecord,
    ) -> SourceIngestionRecord:
        if (
            record.open_notebook_source_id
            and record.metadata.get("source_processing_owner") != "open_notebook"
        ):
            record = self.store.save_source(
                record.model_copy(
                    update={
                        "metadata": {
                            **record.metadata,
                            "source_processing_owner": "open_notebook",
                        }
                    }
                )
            )
        refreshed = self.open_notebook_backend.refresh(record, mirror_status=True)
        job = self.job_store.latest_for_source(
            owner_user_id=refreshed.owner_user_id,
            package_id=refreshed.package_id,
            source_id=refreshed.id,
        )
        if job is not None:
            self._update_open_notebook_job(job, refreshed)
        return refreshed

    def _update_open_notebook_job(
        self,
        job: SourceIngestionJob,
        record: SourceIngestionRecord,
    ) -> None:
        if record.status == "ready":
            phase, progress = "ready", 100
        elif record.status == "failed":
            phase, progress = "failed", 100
        else:
            phase, progress = "open_notebook_processing", 35
        self._update_job(
            job,
            record=record,
            status=record.status,
            progress=progress,
            phase=phase,
            error=record.error,
        )

    def _start_job(
        self,
        record: SourceIngestionRecord,
        *,
        adapter: str,
        phase: str,
        progress: int,
    ) -> SourceIngestionJob:
        return self.job_store.save(
            SourceIngestionJob(
                resource_id=record.id,
                source_type=record.source_type,
                source_uri=record.source_uri,
                adapter=adapter,
                status=record.status,
                progress=progress,
                phase_history=[phase],
            ),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )

    def _finish_job(
        self,
        job: SourceIngestionJob,
        *,
        record: SourceIngestionRecord,
        status: str,
        progress: int,
        phase: str,
        error: str = "",
    ) -> SourceIngestionJob:
        return self._update_job(
            job,
            record=record,
            status=status,
            progress=progress,
            phase=phase,
            error=error,
        )

    def _update_job(
        self,
        job: SourceIngestionJob,
        *,
        record: SourceIngestionRecord,
        status: str,
        progress: int,
        phase: str,
        error: str = "",
        agent_activity: list[AgentActivityEvent] | None = None,
    ) -> SourceIngestionJob:
        phases = job.phase_history
        if not phases or phases[-1] != phase:
            phases = [*phases, phase]
        return self.job_store.save(
            job.model_copy(
                update={
                    "status": status,
                    "progress": max(job.progress, min(100, progress)),
                    "error": error,
                    "phase_history": phases,
                    "agent_activity": (
                        job.agent_activity if agent_activity is None else agent_activity
                    ),
                }
            ),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )

    def _attach_job(self, record: SourceIngestionRecord) -> SourceIngestionRecord:
        job = self.job_store.latest_for_source(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        return record.model_copy(update={"ingestion_job": job})

    def _save_and_index(
        self,
        record: SourceIngestionRecord,
        job: SourceIngestionJob,
        *,
        rebuild: bool = False,
    ) -> SourceIngestionRecord:
        if not _uses_directory_catalog(record):
            return self._save_and_index_unlocked(record, job, rebuild=rebuild)
        with _directory_catalog_processing_slot(
            database_path=self.store.path,
            record=record,
        ):
            current = self.store.get_source(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_id=record.id,
            )
            if current is None:
                raise SourceIngestionError(
                    "Source was removed before directory processing could start."
                )
            if not rebuild:
                completed = self._reuse_completed_directory_catalog(record, job)
                if completed is not None:
                    return completed
            return self._save_and_index_unlocked(record, job, rebuild=rebuild)

    def _reuse_completed_directory_catalog(
        self,
        record: SourceIngestionRecord,
        job: SourceIngestionJob,
    ) -> SourceIngestionRecord | None:
        structure = self.structure_store.get_structure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        path = source_local_path(record)
        actual_hash = _file_content_hash(path) if path is not None else ""
        metadata_hash = str(record.metadata.get("content_hash") or "").strip()
        if not (
            structure is not None
            and structure.status in {"ready", "linear_only"}
            and structure.catalog_schema_version == CATALOG_SCHEMA_VERSION
            and actual_hash
            and metadata_hash == actual_hash
            and structure.source_content_hash == actual_hash
        ):
            return None
        completed = self.store.save_source(
            record.model_copy(update={"status": "ready", "error": ""})
        )
        latest_job = self.job_store.latest_for_source(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        ) or job
        self._update_job(
            latest_job,
            record=completed,
            status="ready",
            progress=100,
            phase="ready",
        )
        return self._attach_job(self.structure_store.attach_summary(completed))

    def _save_and_index_unlocked(
        self,
        record: SourceIngestionRecord,
        job: SourceIngestionJob,
        *,
        rebuild: bool = False,
    ) -> SourceIngestionRecord:
        saved = self.store.save_source(record.model_copy(update={"status": "parsing", "error": ""}))
        indexing_job = self._update_job(
            job,
            record=saved,
            status="parsing",
            progress=25,
            phase="parsing",
        )
        activity_by_id: dict[str, AgentActivityEvent] = {}
        activity_order: list[str] = []
        codex_progress_tracker: SourceCodexProgressTracker | None = None

        def report_progress(phase: str, progress: int) -> None:
            nonlocal saved, indexing_job
            next_status = "indexing" if progress >= 60 else "parsing"
            if saved.status != next_status:
                saved = self.store.save_source(saved.model_copy(update={"status": next_status}))
            indexing_job = self._update_job(
                indexing_job,
                record=saved,
                status=next_status,
                progress=progress,
                phase=phase,
            )

        def report_codex_activity(event: AgentActivityEvent) -> None:
            nonlocal saved, indexing_job
            progress = indexing_job.progress
            phase = indexing_job.phase_history[-1] if indexing_job.phase_history else "parsing"
            if codex_progress_tracker is not None:
                observation = codex_progress_tracker.observe(event)
                event = observation.event
                progress = observation.progress
                phase = observation.phase
            next_status = "indexing" if progress >= 60 else "parsing"
            if saved.status != next_status:
                saved = self.store.save_source(saved.model_copy(update={"status": next_status}))
            if event.id not in activity_by_id:
                activity_order.append(event.id)
            activity_by_id[event.id] = event
            current_activity = [activity_by_id[event_id] for event_id in activity_order]
            indexing_job = self._update_job(
                indexing_job,
                record=saved,
                status=next_status,
                progress=progress,
                phase=phase,
                agent_activity=current_activity,
            )

        use_directory_catalog = (
            saved.metadata.get("catalog_pipeline") == CATALOG_SCHEMA_VERSION
            and supports_directory_catalog(saved)
        )
        previous_structure = (
            self.structure_store.get_structure(
                owner_user_id=saved.owner_user_id,
                package_id=saved.package_id,
                source_id=saved.id,
            )
            if use_directory_catalog
            else None
        )
        try:
            if use_directory_catalog:
                local_path = source_local_path(saved)
                if local_path is None:
                    raise SourceIngestionError("Source file is unavailable for directory cataloging.")
                codex_progress_tracker = SourceCodexProgressTracker(local_path)
                raw_catalog_model = saved.metadata.get("catalog_model")
                try:
                    catalog_model = AIModelSelection.model_validate(raw_catalog_model)
                except Exception as exc:
                    raise SourceIngestionError("The selected catalog model is invalid.") from exc
                structure = self.directory_processor.process(
                    record=saved,
                    path=local_path,
                    catalog_model=catalog_model,
                    progress_callback=report_progress,
                    activity_callback=report_codex_activity,
                )
            else:
                structure = (
                    self.structure_indexer.rebuild_structure(saved, progress_callback=report_progress)
                    if rebuild
                    else self.structure_indexer.ensure_structure(saved, progress_callback=report_progress)
                )
        except Exception as exc:
            preserve_previous_catalog = (
                previous_structure is not None
                and previous_structure.status in {"ready", "linear_only"}
            )
            if preserve_previous_catalog:
                self.structure_store.record_rebuild_failure(
                    structure=previous_structure,
                    error=str(exc),
                )
            failed = self.store.save_source(
                saved.model_copy(
                    update={
                        "status": "ready" if preserve_previous_catalog else "failed",
                        "error": "" if preserve_previous_catalog else str(exc),
                    }
                )
            )
            self._update_job(
                indexing_job,
                record=failed,
                status="failed",
                progress=100,
                error=str(exc),
                phase="failed",
            )
            return self._attach_job(self.structure_store.attach_summary(failed))
        final_status = "ready" if structure.status in {"ready", "linear_only"} else "failed"
        error = structure.error if final_status == "failed" else ""
        automation_outcome = SourceImportAutomationOutcome(artifact_ids=[], errors=[])
        if (
            final_status == "ready"
            and self.import_automation_runner is not None
            and not use_directory_catalog
        ):
            transforming_job = self._update_job(
                indexing_job,
                record=saved,
                status="indexing",
                progress=97,
                phase="transforming",
            )
            indexing_job = transforming_job
            try:
                automation_outcome = self.import_automation_runner(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                )
            except Exception as exc:
                automation_outcome = SourceImportAutomationOutcome(
                    artifact_ids=[],
                    errors=[str(exc)],
                )
            if automation_outcome.errors:
                error = "Automatic import transformation failed: " + "; ".join(automation_outcome.errors)
        final_record = self.store.save_source(
            saved.model_copy(
                update={
                    "status": final_status,
                    "error": error,
                    "metadata": {
                        **saved.metadata,
                        "import_transformation_status": (
                            "not_applicable"
                            if use_directory_catalog
                            else "failed"
                            if automation_outcome.errors
                            else "ready"
                            if automation_outcome.artifact_ids
                            else "not_configured"
                        ),
                        "import_transformation_artifact_ids": automation_outcome.artifact_ids,
                    },
                }
            )
        )
        self._update_job(
            indexing_job,
            record=final_record,
            status=final_status,
            progress=100,
            error=error,
            phase=final_status,
        )
        return self._attach_job(self.structure_store.attach_summary(final_record))

    def _failed_record(self, record: SourceIngestionRecord, error: str, *, phase: str) -> SourceIngestionRecord:
        return record.model_copy(
            update={
                "status": "failed",
                "error": error,
                "metadata": {
                    **record.metadata,
                    "error_phase": phase,
                },
            }
        )

    def _save_youtube_transcript_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str,
    ) -> SourceIngestionRecord:
        display_title = title.strip() or source_uri
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="video_url",
            source_uri=source_uri,
            file_name="youtube-transcript.txt",
            mime_type="text/plain",
            size_bytes=0,
            status="fetching",
            metadata={
                "adapter": "youtube_transcript",
                "media_provider": "youtube",
                "media_kind": "video",
            },
        )
        try:
            transcript = self.youtube_adapter.extract(source_uri, title=title)
        except YouTubeTranscriptAdapterError as exc:
            failed = self.store.save_source(self._failed_record(record, str(exc), phase="youtube_transcript"))
            job = self._start_job(
                failed,
                adapter="youtube_transcript",
                phase="transcription",
                progress=100,
            )
            self._finish_job(
                job,
                record=failed,
                status="failed",
                progress=100,
                phase="failed",
                error=str(exc),
            )
            return failed
        transcript_file_name = _safe_file_name(f"{transcript.video_id or record.id}-transcript.txt")
        record_for_file = record.model_copy(update={"title": transcript.title, "file_name": transcript_file_name})
        transcript_bytes = transcript.text.encode("utf-8")
        ready = record_for_file.model_copy(
            update={
                "status": "ready",
                "error": "",
                "size_bytes": len(transcript_bytes),
                "metadata": {
                    **record_for_file.metadata,
                    **transcript.metadata,
                    **_save_local_source_text(record_for_file, transcript.text),
                    "content_hash": hashlib.sha256(transcript_bytes).hexdigest(),
                    "native_index_version": 1,
                },
            }
        )
        if self.source_backend == "open_notebook":
            ready = ready.model_copy(
                update={
                    "metadata": {
                        **ready.metadata,
                        "adapter": "open_notebook",
                        "package_title": package.title,
                    }
                }
            )
            ready = self.open_notebook_backend.sync_file(ready)
            if ready.status == "failed":
                job = self._start_job(ready, adapter="open_notebook", phase="failed", progress=100)
                return self._attach_job(self.structure_store.attach_summary(ready))
            job = self._start_job(
                ready,
                adapter="open_notebook",
                phase="open_notebook_processing",
                progress=35,
            )
            self._update_open_notebook_job(job, ready)
            return self._attach_job(self.structure_store.attach_summary(ready))
        job = self._start_job(
            ready,
            adapter=str(ready.metadata.get("adapter") or "youtube_transcript"),
            phase="parsing",
            progress=25,
        )
        return self._save_and_index(ready, job)


def _supported_mime(mime_type: str, file_name: str) -> bool:
    lowered = file_name.lower()
    if lowered.endswith(
        (
            ".pdf",
            ".epub",
            ".docx",
            ".pptx",
            ".xlsx",
            ".csv",
            ".txt",
            ".md",
            ".markdown",
            ".html",
            ".htm",
            ".json",
            ".xml",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".mp3",
            ".m4a",
            ".wav",
            ".ogg",
            ".mp4",
            ".mov",
            ".webm",
            ".mpeg",
        )
    ):
        return True
    return any((mime_type or "").startswith(prefix) for prefix in SUPPORTED_FILE_MIME_PREFIXES)


def _supported_directory_file_name(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in {
        ".pdf",
        ".epub",
        ".docx",
        ".pptx",
        ".xlsx",
        ".csv",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".json",
        ".xml",
    }


def _save_local_source_file(record: SourceIngestionRecord, content: bytes) -> dict[str, str]:
    source_dir = workspace_state.UPLOAD_DIR / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / _stored_source_file_name(record)
    path.write_bytes(content)
    return {"local_source_path": str(path)}


def _save_local_source_text(record: SourceIngestionRecord, text: str) -> dict[str, str]:
    return _save_local_source_file(record, text.encode("utf-8"))


def source_local_path(record: SourceIngestionRecord) -> Path | None:
    raw_path = record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
    if allowed_root not in path.parents or not path.is_file():
        return None
    return path


def source_download_path(record: SourceIngestionRecord) -> Path | None:
    raw_path = record.metadata.get("original_source_path") or record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
    return path if allowed_root in path.parents and path.is_file() else None


def _stored_source_file_name(record: SourceIngestionRecord) -> str:
    suffix = Path(record.file_name).suffix.lower()
    return f"{record.id}{suffix}"


def _repair_local_source_storage(record: SourceIngestionRecord) -> SourceIngestionRecord:
    raw_path = record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return record
    current_path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
    if allowed_root not in current_path.parents or not current_path.is_file():
        return record
    expected_path = allowed_root / _stored_source_file_name(record)
    if current_path == expected_path:
        return record
    if expected_path.exists():
        raise SourceIngestionError(
            "The stable storage target for this source already exists."
        )
    try:
        current_path.replace(expected_path)
    except OSError as exc:
        raise SourceIngestionError(
            "The stored source could not be moved to its stable identity path."
        ) from exc
    return record.model_copy(
        update={
            "metadata": {
                **record.metadata,
                "local_source_path": str(expected_path),
            }
        }
    )


def _delete_local_source_file(record: SourceIngestionRecord) -> None:
    paths = [source_local_path(record)]
    raw_original = record.metadata.get("original_source_path")
    if isinstance(raw_original, str) and raw_original.strip():
        candidate = Path(raw_original).expanduser().resolve()
        allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
        if allowed_root in candidate.parents and candidate.is_file():
            paths.append(candidate)
    for path in dict.fromkeys(item for item in paths if item is not None):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _file_content_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_type_for_upload(*, mime_type: str, file_name: str) -> str:
    normalized_mime = (mime_type or "").lower()
    suffix = Path(file_name).suffix.lower()
    if normalized_mime.startswith("audio/") or suffix in {
        ".mp3",
        ".m4a",
        ".wav",
        ".ogg",
    }:
        return "audio_file"
    if normalized_mime.startswith("video/") or suffix in {
        ".mp4",
        ".mov",
        ".webm",
        ".mpeg",
    }:
        return "video_file"
    return "local_file"


def _text_title(text: str, limit: int = 80) -> str:
    first_line = next((line.strip("# *\t") for line in text.splitlines() if line.strip()), "")
    return first_line[:limit] or "Pasted text"


def _detach_open_notebook_state(record: SourceIngestionRecord) -> SourceIngestionRecord:
    metadata = {
        key: value
        for key, value in record.metadata.items()
        if key != "source_processing_owner"
        and not key.startswith("open_notebook_")
        and not key.startswith("last_open_notebook_")
    }
    metadata["adapter"] = (
        "openclass_native_text"
        if record.source_type == "pasted_text" or record.mime_type == "text/markdown"
        else "openclass_native"
    )
    return record.model_copy(
        update={
            "open_notebook_notebook_id": "",
            "open_notebook_source_id": "",
            "open_notebook_command_id": "",
            "metadata": metadata,
        }
    )


def _uses_directory_catalog(record: SourceIngestionRecord) -> bool:
    return (
        record.metadata.get("catalog_pipeline") == CATALOG_SCHEMA_VERSION
        and supports_directory_catalog(record)
    )


def _as_directory_catalog_record(record: SourceIngestionRecord) -> SourceIngestionRecord:
    detached = _detach_open_notebook_state(record)
    return detached.model_copy(
        update={
            "metadata": {
                **detached.metadata,
                "adapter": "codex_directory_v1",
                "catalog_pipeline": CATALOG_SCHEMA_VERSION,
            }
        }
    )


def _safe_file_name(file_name: str) -> str:
    name = Path(file_name).name.strip() or "source"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)


def _structure_store_for_source_store(
    store: SourceEvidenceStore,
) -> SourceStructureStore:
    if getattr(store, "_path", None) is None:
        return source_structure_store
    return SourceStructureStore(store.path)


def _validate_public_url(raw_uri: str) -> str:
    uri = raw_uri.strip()
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceIngestionError("Only http and https URLs are supported.")
    hostname = parsed.hostname or ""
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SourceIngestionError("Localhost URLs are not allowed for source ingestion.")
    try:
        for info in socket.getaddrinfo(hostname, None):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local:
                raise SourceIngestionError("Private network URLs are not allowed for source ingestion.")
    except socket.gaierror as exc:
        raise SourceIngestionError("URL hostname could not be resolved.") from exc
    return uri


source_ingestion_service = SourceIngestionService()

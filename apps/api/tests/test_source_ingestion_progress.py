from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.models import (
    AgentActivityEvent,
    CoursePackage,
    SourceIngestionJob,
    SourceIngestionRecord,
    SourceStructure,
)
from app.services.open_notebook_adapter import OpenNotebookSourceResult
from app.services import workspace_state
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionCoordinator, SourceIngestionJobStore
from app.services.source_ingestion_service import SourceIngestionService
from app.services import source_ingestion_service as ingestion_module
from app.services import source_codex_progress
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


def test_source_ingestion_defaults_to_native_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_SOURCE_BACKEND", raising=False)
    database = tmp_path / "openclass.sqlite3"

    service = SourceIngestionService(
        store=SourceEvidenceStore(database),
        job_store=SourceIngestionJobStore(database),
        structure_store=SourceStructureStore(database),
    )

    assert service.source_backend == "native"


def test_local_source_storage_does_not_truncate_the_original_file_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    original_name = f"{'very-long-title-' * 80}.pdf"
    record = SourceIngestionRecord(
        id="source_long_name",
        owner_user_id="user_1",
        package_id="course_1",
        title=original_name,
        source_type="local_file",
        file_name=original_name,
        mime_type="application/pdf",
        size_bytes=3,
    )

    metadata = ingestion_module._save_local_source_file(record, b"pdf")
    stored_path = Path(metadata["local_source_path"])

    assert record.file_name == original_name
    assert stored_path.name == "source_long_name.pdf"
    assert stored_path.read_bytes() == b"pdf"


def test_retry_storage_repair_restores_suffix_lost_by_legacy_name_truncation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    source_dir = upload_dir / "sources"
    source_dir.mkdir(parents=True)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    legacy_path = source_dir / "source_legacy_long_title."
    legacy_path.write_bytes(b"valid pdf bytes")
    record = SourceIngestionRecord(
        id="source_legacy",
        owner_user_id="user_1",
        package_id="course_1",
        title="Long source",
        source_type="local_file",
        file_name=f"{'long-title-' * 80}.pdf",
        mime_type="application/pdf",
        size_bytes=legacy_path.stat().st_size,
        metadata={"local_source_path": str(legacy_path)},
    )

    repaired = ingestion_module._repair_local_source_storage(record)
    repaired_path = Path(str(repaired.metadata["local_source_path"]))

    assert repaired_path.name == "source_legacy.pdf"
    assert repaired_path.read_bytes() == b"valid pdf bytes"
    assert not legacy_path.exists()


def test_source_ingestion_job_persists_live_codex_activity(tmp_path) -> None:
    store = SourceIngestionJobStore(tmp_path / "openclass.sqlite3")
    event = AgentActivityEvent(
        id="activity_1",
        turn_id="turn_1",
        stage="execute_role",
        label="OpenClass 工作进展",
        status="running",
        role="OpenClass",
        metadata={"detail": "正在读取原文件并提取目录层级"},
    )
    saved = store.save(
        SourceIngestionJob(
            resource_id="source_1",
            adapter="codex_directory_v1",
            status="parsing",
            progress=64,
            phase_history=["normalizing_directory"],
            agent_activity=[event],
        ),
        owner_user_id="user_1",
        package_id="course_1",
    )

    restored = store.latest_for_source(
        owner_user_id="user_1",
        package_id="course_1",
        source_id="source_1",
    )

    assert restored is not None
    assert restored.id == saved.id
    assert restored.agent_activity == [event]


def test_native_retry_detaches_failed_open_notebook_source(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)
    upload_dir = tmp_path / "uploads"
    source_dir = upload_dir / "sources"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "scan.pdf"
    source_path.write_bytes(b"local source bytes")
    received_records = []

    class NativeRetryIndexer:
        def rebuild_structure(self, record, *, progress_callback=None):
            received_records.append(record)
            if progress_callback is not None:
                progress_callback("persisting", 94)
            return structure_store.save_structure_bundle(
                structure=SourceStructure(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                    status="linear_only",
                    strategy="linear_text",
                ),
                chapters=[],
                chunks=[],
            )

    record = source_store.save_source(
        SourceIngestionRecord(
            id="source_remote_failed",
            owner_user_id="user_1",
            package_id="course_1",
            title="Scanned source",
            source_type="local_file",
            file_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=source_path.stat().st_size,
            status="failed",
            error="OpenNotebook unavailable",
            open_notebook_notebook_id="notebook:remote",
            open_notebook_source_id="source:remote",
            open_notebook_command_id="command:remote",
            metadata={
                "local_source_path": str(source_path),
                "adapter": "open_notebook",
                "source_processing_owner": "open_notebook",
                "open_notebook_sync_status": "failed",
                "open_notebook_sync_warning": "unavailable",
            },
        )
    )
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    service = SourceIngestionService(
        source_backend="native",
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=NativeRetryIndexer(),
    )

    retried = service.retry_source(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert retried is not None
    assert retried.status == "ready"
    assert retried.open_notebook_notebook_id == ""
    assert retried.open_notebook_source_id == ""
    assert retried.open_notebook_command_id == ""
    assert retried.metadata["adapter"] == "openclass_native"
    assert "source_processing_owner" not in retried.metadata
    assert not any(key.startswith("open_notebook_") for key in retried.metadata)
    assert received_records[0].metadata["adapter"] == "openclass_native"
    assert "source_processing_owner" not in received_records[0].metadata
    assert not any(
        key.startswith("open_notebook_") for key in received_records[0].metadata
    )


def test_source_ingestion_coordinator_allows_bounded_parallel_work() -> None:
    coordinator = SourceIngestionCoordinator(processing_capacity=2)
    release_workers = threading.Event()
    two_workers_entered = threading.Event()
    state_lock = threading.Lock()
    active_workers = 0
    max_active_workers = 0

    def run_worker() -> None:
        nonlocal active_workers, max_active_workers
        with coordinator.processing_slot():
            with state_lock:
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
                if active_workers == 2:
                    two_workers_entered.set()
            assert release_workers.wait(timeout=5)
            with state_lock:
                active_workers -= 1

    workers = [threading.Thread(target=run_worker) for _ in range(3)]
    for worker in workers:
        worker.start()

    assert two_workers_entered.wait(timeout=5)
    with state_lock:
        assert active_workers == 2
    assert coordinator.processing_weight(size_bytes=1, source_type="local_file") == 1
    assert coordinator.processing_weight(
        size_bytes=64 * 1024 * 1024,
        source_type="local_file",
    ) == 2

    release_workers.set()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert max_active_workers == 2


def test_source_structure_write_retries_transient_database_lock(tmp_path, monkeypatch) -> None:
    coordinator = SourceIngestionCoordinator(lock_retry_delays=(0.0,))
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3", coordinator=coordinator)
    structure = SourceStructure(
        owner_user_id="user_retry",
        package_id="course_retry",
        source_ingestion_id="source_retry",
        status="linear_only",
        strategy="linear_text",
    )
    original_save = structure_store._save_structure_bundle
    attempts = 0

    def flaky_save(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_save(**kwargs)

    monkeypatch.setattr(structure_store, "_save_structure_bundle", flaky_save)

    saved = structure_store.save_structure_bundle(
        structure=structure,
        chapters=[],
        chunks=[],
    )

    assert attempts == 2
    assert saved.status == "linear_only"
    assert structure_store.get_structure(
        owner_user_id="user_retry",
        package_id="course_retry",
        source_id="source_retry",
    ) is not None


def test_concurrent_file_ingestion_finishes_without_locked_database(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    coordinator = SourceIngestionCoordinator(processing_capacity=2, lock_retry_delays=(0.0, 0.0))
    source_store = SourceEvidenceStore(database, coordinator=coordinator)
    structure_store = SourceStructureStore(database, coordinator=coordinator)
    job_store = SourceIngestionJobStore(database, coordinator=coordinator)
    service = SourceIngestionService(
        source_backend="native",
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=SourceStructureIndexer(
            store=structure_store,
            coordinator=coordinator,
        ),
    )
    package = CoursePackage(id="course_concurrent", title="Concurrent", summary="", lessons=[])
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    queued_sources = [
        service.queue_file_source(
            owner_user_id="user_concurrent",
            package=package,
            file_name=f"source-{index}.md",
            content=f"# Source {index}\n\nConcurrent body {index}".encode(),
            mime_type="text/markdown",
        )
        for index in range(4)
    ]
    start_together = threading.Barrier(len(queued_sources) + 1)
    errors: list[Exception] = []

    def process(source_id: str) -> None:
        try:
            start_together.wait(timeout=5)
            service.process_file_source(
                owner_user_id="user_concurrent",
                package_id=package.id,
                source_id=source_id,
            )
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=process, args=(source.id,)) for source in queued_sources]
    for worker in workers:
        worker.start()
    start_together.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()

    assert errors == []
    completed = service.list_sources(owner_user_id="user_concurrent", package_id=package.id)
    assert len(completed) == len(queued_sources)
    assert all(source.status == "ready" for source in completed)
    assert all(source.error == "" for source in completed)


def test_file_ingestion_exposes_durable_progress_while_indexing(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)
    reached_page_scan = threading.Event()
    allow_completion = threading.Event()

    class BlockingIndexer:
        def ensure_structure(self, record, *, progress_callback=None):
            assert progress_callback is not None
            progress_callback("reading_pages", 47)
            reached_page_scan.set()
            assert allow_completion.wait(timeout=5)
            progress_callback("extracting_visuals", 82)
            return structure_store.save_structure_bundle(
                structure=SourceStructure(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                    status="linear_only",
                    strategy="linear_text",
                ),
                chapters=[],
                chunks=[],
            )

    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    service = SourceIngestionService(
        source_backend="native",
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=BlockingIndexer(),
    )
    package = CoursePackage(id="course_progress", title="Progress", summary="", lessons=[])

    queued = service.queue_file_source(
        owner_user_id="user_progress",
        package=package,
        file_name="source.md",
        content=b"# Source\n\nBody",
        mime_type="text/markdown",
    )
    assert queued.status == "parsing"
    assert queued.ingestion_job is not None
    assert queued.ingestion_job.progress == 15

    worker = threading.Thread(
        target=service.process_file_source,
        kwargs={
            "owner_user_id": "user_progress",
            "package_id": package.id,
            "source_id": queued.id,
        },
    )
    worker.start()
    assert reached_page_scan.wait(timeout=5)

    processing = service.list_sources(owner_user_id="user_progress", package_id=package.id)[0]
    assert processing.status == "parsing"
    assert processing.ingestion_job is not None
    assert processing.ingestion_job.progress == 47
    assert processing.ingestion_job.phase_history[-1] == "reading_pages"

    allow_completion.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    completed = service.list_sources(owner_user_id="user_progress", package_id=package.id)[0]
    assert completed.status == "ready"
    assert completed.ingestion_job is not None
    assert completed.ingestion_job.progress == 100
    assert completed.ingestion_job.phase_history[-1] == "ready"


def test_directory_ingestion_persists_real_codex_work_progress(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)
    reached_node_mapping = threading.Event()
    allow_completion = threading.Event()

    class BlockingDirectoryProcessor:
        def process(
            self,
            *,
            record,
            path,
            catalog_model,
            progress_callback=None,
            activity_callback=None,
        ):
            del path, catalog_model
            assert progress_callback is not None
            assert activity_callback is not None
            progress_callback("source_codex_investigation", 30)
            activity_callback(
                AgentActivityEvent(
                    id="mapping_34",
                    turn_id="turn_1",
                    stage="execute_role",
                    label="OpenClass 工作进展",
                    status="completed",
                    role="OpenClass",
                    metadata={
                        "kind": "commentary",
                        "detail": (
                            'OPENCLASS_PROGRESS {"phase":"map_nodes","completed":34,'
                            '"total":68,"unit":"nodes","detail":"matching headings"}'
                        ),
                    },
                )
            )
            reached_node_mapping.set()
            assert allow_completion.wait(timeout=5)
            return structure_store.save_structure_bundle(
                structure=SourceStructure(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                    status="ready",
                    strategy="codex_directory_v1",
                ),
                chapters=[],
                chunks=[],
            )

    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 100)
    service = SourceIngestionService(
        source_backend="native",
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        directory_processor=BlockingDirectoryProcessor(),
    )
    package = CoursePackage(id="course_codex_progress", title="Progress", summary="", lessons=[])
    queued = service.queue_file_source(
        owner_user_id="user_codex_progress",
        package=package,
        file_name="source.pdf",
        content=b"pdf",
        mime_type="application/pdf",
    )

    worker = threading.Thread(
        target=service.process_file_source,
        kwargs={
            "owner_user_id": queued.owner_user_id,
            "package_id": queued.package_id,
            "source_id": queued.id,
        },
    )
    worker.start()
    assert reached_node_mapping.wait(timeout=5)

    processing = service.list_sources(
        owner_user_id=queued.owner_user_id,
        package_id=queued.package_id,
    )[0]
    assert processing.status == "indexing"
    assert processing.ingestion_job is not None
    assert processing.ingestion_job.progress == 65
    assert processing.ingestion_job.phase_history[-1] == "source_codex_mapping_nodes"
    live_progress = processing.ingestion_job.agent_activity[-1].metadata["source_progress"]
    assert live_progress["label"] == "正在映射目录节点：34/68 个目录节点"
    assert live_progress["detail"] == "matching headings · 文件共 100 页"

    allow_completion.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    completed = service.list_sources(
        owner_user_id=queued.owner_user_id,
        package_id=queued.package_id,
    )[0]
    assert completed.status == "ready"
    assert completed.ingestion_job is not None
    assert completed.ingestion_job.progress == 100


def test_open_notebook_mode_skips_local_structure_pipeline(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)

    class FakeOpenNotebookAdapter:
        api_url = "http://notebook.test"

        def create_notebook(self, **_kwargs) -> str:
            return "notebook:test"

        def upload_file_source(self, **_kwargs) -> OpenNotebookSourceResult:
            return OpenNotebookSourceResult(
                source_id="source:remote",
                command_id="command:remote",
                status="processing",
            )

        def get_command(self, _command_id: str) -> dict[str, object]:
            return {"status": "completed", "result": {"source_id": "source:remote"}}

    class RejectingIndexer:
        def ensure_structure(self, *_args, **_kwargs):
            raise AssertionError("OpenNotebook mode must not call the local structure indexer")

    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    service = SourceIngestionService(
        adapter=FakeOpenNotebookAdapter(),
        source_backend="open_notebook",
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=RejectingIndexer(),
    )
    package = CoursePackage(id="course_open_notebook", title="Notebook", summary="", lessons=[])

    queued = service.queue_file_source(
        owner_user_id="user_open_notebook",
        package=package,
        file_name="source.md",
        content=b"# Source\n\nBody",
        mime_type="text/markdown",
    )
    processing = service.process_file_source(
        owner_user_id="user_open_notebook",
        package_id=package.id,
        source_id=queued.id,
    )

    assert processing.status == "parsing"
    assert processing.metadata["source_processing_owner"] == "open_notebook"
    assert structure_store.get_structure(
        owner_user_id="user_open_notebook",
        package_id=package.id,
        source_id=queued.id,
    ) is None

    completed = service.list_sources(
        owner_user_id="user_open_notebook",
        package_id=package.id,
    )[0]
    assert completed.status == "ready"
    assert completed.ingestion_job is not None
    assert completed.ingestion_job.progress == 100
    assert completed.ingestion_job.phase_history[-1] == "ready"

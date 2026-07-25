from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import threading
import time
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import BoardDocument, SourceIngestionJob, SourceIngestionRecord, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import source_ingestion_service as source_ingestion_module
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.rich_document import build_document, rich_structure_counts
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_ingestion_service
from app.services.source_ingestion_jobs import SourceIngestionJobStore, SourceIngestionTaskManager
from app.services.source_directory_processor import DirectoryNormalizationResult
from app.services.youtube_transcript_adapter import YouTubeTranscript


TEST_USER = UserView(
    id="user_smoke",
    email="smoke@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"

    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(documents_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(source_ingestion_service, "source_backend", "native")
    monkeypatch.setattr(
        source_ingestion_service.directory_processor,
        "normalizer_factory",
        lambda _record: _PassthroughDirectoryNormalizer(),
    )
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


class _PassthroughDirectoryNormalizer:
    def normalize(self, *, record, candidates, selection):
        return DirectoryNormalizationResult(
            candidates=tuple(candidates),
            turn_count=1 if candidates else 0,
            metadata={"test_adapter": "passthrough"},
        )


def _document_with_text(document: dict, text: str) -> dict:
    next_document = deepcopy(document)
    next_document["content_text"] = text
    next_document["content_html"] = f"<p>{text}</p>"
    next_document["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return next_document


def _wait_for_source_status(
    api_client: TestClient,
    package_id: str,
    source_id: str,
    status: str,
    *,
    timeout: float = 3.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api_client.get(f"/api/packages/{package_id}/sources")
        assert response.status_code == 200
        source = next(item for item in response.json() if item["id"] == source_id)
        if source["status"] == status:
            return source
        time.sleep(0.01)
    raise AssertionError(f"source {source_id} did not reach {status}")


def test_source_task_manager_recovers_persisted_work_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    job_store = SourceIngestionJobStore(tmp_path / "source-tasks.sqlite3")
    active = job_store.save(
        SourceIngestionJob(resource_id="source_pending", status="parsing", progress=30),
        owner_user_id="user_test",
        package_id="course_test",
    )
    job_store.save(
        SourceIngestionJob(resource_id="source_ready", status="ready", progress=100),
        owner_user_id="user_test",
        package_id="course_test",
    )
    manager = SourceIngestionTaskManager(job_store)
    started = threading.Event()
    release = threading.Event()

    def hold_task(*, key, retry):
        assert key == ("user_test", "course_test", active.resource_id)
        assert retry is False
        started.set()
        release.wait(timeout=1)
        with manager._lock:
            manager._active.discard(key)

    monkeypatch.setattr(manager, "_run", hold_task)

    assert manager.recover_active() == 1
    assert started.wait(timeout=1)
    assert manager.submit(
        owner_user_id="user_test",
        package_id="course_test",
        source_id="source_pending",
    ) is False
    release.set()


def test_health_reports_provider_neutral_board_and_realtime_status(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(main_module, "codex_app_server_available", lambda: True)
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "false")

    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json()["workflow"] == {"status": "provider_neutral_board"}
    assert response.json()["deepseek"]["access"] == "shared_unmetered"
    assert response.json()["realtime"] == {"status": "disabled", "provider": "openai"}
    assert response.json()["codex"] == {"enabled": True, "available": True}
    assert "openai" not in response.json()
    assert not any(route.path.startswith("/api/realtime") for route in main_module.app.routes)
    assert any(route.path == "/api/lessons/{lesson_id}/realtime/connect" for route in main_module.app.routes)
    assert any(route.path == "/api/lessons/{lesson_id}/realtime/tools" for route in main_module.app.routes)
    assert not any("/research" in route.path for route in main_module.app.routes)
    evidence_routes = [route.path for route in main_module.app.routes if "/evidence/" in route.path]
    assert evidence_routes == []


def _docx_text_nodes(content: bytes) -> list[str]:
    with ZipFile(BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return [node.text or "" for node in root.findall(".//w:t", ns)]


def test_workspace_document_history_flow(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Smoke package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Smoke lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    package = generated.json()
    lesson = package["lessons"][0]

    first_document = _document_with_text(lesson["board_document"], "First smoke version")
    first_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": first_document,
            "label": "First smoke save",
            "message": "Saved first smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert first_save.status_code == 200
    first_commit_id = first_save.json()["lessons"][0]["history_graph"]["commits"][-1]["id"]

    second_document = _document_with_text(first_document, "Second smoke version")
    second_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": second_document,
            "label": "Second smoke save",
            "message": "Saved second smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert second_save.status_code == 200
    assert second_save.json()["lessons"][0]["board_document"]["content_text"] == "Second smoke version"

    search = api_client.get("/api/documents/search", params={"q": "Second smoke", "limit": 5})
    assert search.status_code == 200
    assert search.json()["results"]

    restored = api_client.post(
        f"/api/lessons/{lesson['id']}/restore",
        json={"commit_id": first_commit_id, "label": "Restore first smoke version"},
    )
    assert restored.status_code == 200
    assert restored.json()["lessons"][0]["board_document"]["content_text"] == "First smoke version"


def test_autosave_rejects_unintended_table_loss_and_accepts_explicit_removal(
    api_client: TestClient,
) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Structured save", "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]
    structured = build_document(
        title="Structured save",
        document_id=lesson["board_document"]["id"],
        content_text="# Overview\n\n## Details\n\n| A | B |\n|---|---|\n| 1 | 2 |",
    )
    saved = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": structured.model_dump(mode="json"),
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert saved.status_code == 200
    saved_lesson = saved.json()["lessons"][0]
    saved_commit_id = saved_lesson["history_graph"]["branches"]["main"]["head_commit_id"]

    flattened = build_document(
        title="Structured save",
        document_id=structured.id,
        content_text="# Overview\n\n## Details\n\n| A | B | |---|---| | 1 | 2 |",
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "Overview"}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Details"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "| A | B | |---|---| | 1 | 2 |"}],
                },
            ],
        },
    )
    rejected = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": flattened.model_dump(mode="json"),
            "base_commit_id": saved_commit_id,
            "metadata": {"kind": "auto_document_save", "autosave": True},
        },
    )
    assert rejected.status_code == 200
    rejected_lesson = rejected.json()["lessons"][0]
    assert rich_structure_counts(BoardDocument.model_validate(rejected_lesson["board_document"]))["table"] == 1
    assert rejected_lesson["history_graph"]["branches"]["main"]["head_commit_id"] == saved_commit_id

    accepted = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": flattened.model_dump(mode="json"),
            "base_commit_id": saved_commit_id,
            "metadata": {
                "kind": "auto_document_save",
                "autosave": True,
                "structure_removal_intent": True,
            },
        },
    )
    assert accepted.status_code == 200
    assert rich_structure_counts(
        BoardDocument.model_validate(accepted.json()["lessons"][0]["board_document"])
    )["table"] == 0


def test_export_docx_rejects_empty_board_document(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Empty export package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Empty export lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    exported = api_client.get(f"/api/lessons/{lesson['id']}/document/export-docx")

    assert exported.status_code == 409
    assert "当前板书文档为空" in exported.text


def test_export_docx_uses_head_snapshot_when_current_document_is_empty(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Snapshot export package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Snapshot export lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]
    saved_document = _document_with_text(lesson["board_document"], "Head snapshot survives export")
    saved = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": saved_document,
            "label": "Save export source",
            "message": "Saved export source",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert saved.status_code == 200

    store = workspace_state.get_store()
    empty_doc = {"type": "doc", "content": [{"type": "paragraph"}]}
    with store._connect() as conn:
        with conn:
            conn.execute(
                """
                UPDATE lessons
                SET board_document_title = title,
                    board_content_json = ?,
                    board_content_html = '',
                    board_content_text = ''
                WHERE id = ?
                """,
                (json.dumps(empty_doc), lesson["id"]),
            )

    exported = api_client.get(f"/api/lessons/{lesson['id']}/document/export-docx")

    assert exported.status_code == 200
    assert exported.headers["cache-control"].startswith("no-store")
    assert "Head snapshot survives export" in "".join(_docx_text_nodes(exported.content))


def test_resource_upload_endpoint_is_not_exposed(api_client: TestClient) -> None:
    upload = api_client.post("/api/resources/upload")

    assert upload.status_code == 404


def test_lesson_resource_upload_endpoint_is_not_exposed(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Resource lesson", "start_blank": True},
    )
    assert created_workspace.status_code == 200
    lesson = created_workspace.json()["lessons"][0]

    upload = api_client.post(
        f"/api/lessons/{lesson['id']}/resources/upload",
        files={"file": ("resource.md", "# 第一章\n这是资料正文。".encode("utf-8"), "text/markdown")},
    )

    assert upload.status_code == 404


def test_native_url_source_import_and_delete(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]
    def _fake_snapshot(record: SourceIngestionRecord, source_uri: str) -> dict[str, str]:
        source_dir = workspace_state.UPLOAD_DIR / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = source_dir / f"{record.id}.html"
        snapshot_path.write_text("<h1>Native source</h1><p>Native indexed body.</p>", encoding="utf-8")
        return {"local_source_path": str(snapshot_path)}

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_module, "fetch_url_source_snapshot", _fake_snapshot)

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://example.com/source", "title": "示例网页"},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["open_notebook_source_id"] == ""
    assert source["metadata"]["adapter"] == "openclass_native_url"
    assert source["structure_status"] == "ready"

    listed = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "示例网页"
    assert listed.json()[0]["structure_status"] == "ready"

    structure = api_client.get(f"/api/packages/{package_id}/sources/{source['id']}/structure")
    assert structure.status_code == 200
    assert structure.json()["structure"]["strategy"] == "markdown_heading"
    assert structure.json()["chapters"]
    assert structure.json()["chunks"]

    deleted = api_client.delete(f"/api/packages/{package_id}/sources/{source['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["id"] == source["id"]

    listed_after_delete = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed_after_delete.status_code == 200
    assert listed_after_delete.json() == []


def test_source_import_uses_persisted_directory_catalog(
    api_client: TestClient,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Unavailable source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        files={"file": ("source.md", b"# title", "text/markdown")},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "parsing"
    assert source["ingestion_job"]["progress"] == 15
    assert source["error"] == ""
    assert source["open_notebook_notebook_id"] == ""
    assert source["metadata"]["adapter"] == "codex_directory_v1"
    assert source["metadata"]["content_hash"]
    assert source["metadata"]["catalog_pipeline"] == "codex_directory_v1"
    assert "open_notebook_sync_status" not in source["metadata"]
    assert source["structure_status"] == "pending"

    source_id = source["id"]
    completed = _wait_for_source_status(api_client, package_id, source_id, "ready")
    assert completed["ingestion_job"]["progress"] == 100
    assert completed["structure_has_verified_toc"] is True
    catalog = api_client.get(f"/api/packages/{package_id}/sources/{source_id}/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["strategy"] == "codex_directory_v1"
    assert catalog.json()["catalog_version"] == 1
    assert catalog.json()["chapters"][0]["range"]["kind"] == "text_lines"
    batch_catalog = api_client.get(f"/api/packages/{package_id}/sources/catalogs")
    assert batch_catalog.status_code == 200
    assert [item["source"]["id"] for item in batch_catalog.json()["catalogs"]] == [
        source_id
    ]
    rebuilt_catalog = api_client.post(
        f"/api/packages/{package_id}/sources/{source_id}/catalog/rebuild"
    )
    assert rebuilt_catalog.status_code == 200
    assert rebuilt_catalog.json()["catalog_version"] == 2
    structure = api_client.get(f"/api/packages/{package_id}/sources/{source_id}/structure")
    assert structure.status_code == 200
    assert structure.json()["chunks"] == []
    assert structure.json()["visuals"] == []


def test_url_source_uses_native_local_snapshot(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "URL fallback package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    def _fake_snapshot(record: SourceIngestionRecord, source_uri: str) -> dict[str, str]:
        snapshot_path = tmp_path / f"{record.id}.txt"
        snapshot_path.write_text(
            "Local webpage concept.\nThis snapshot remains usable without Open Notebook.",
            encoding="utf-8",
        )
        return {"local_source_path": str(snapshot_path)}

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_module, "fetch_url_source_snapshot", _fake_snapshot)

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://example.com/article", "title": "示例网页"},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["error"] == ""
    assert source["source_type"] == "web_url"
    assert source["metadata"]["adapter"] == "openclass_native_url"
    assert source["metadata"]["content_hash"]
    assert source["structure_status"] == "linear_only"


def test_youtube_url_source_uses_transcript_adapter(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "YouTube source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    class _FakeYouTubeAdapter:
        def extract(self, source_uri: str, *, title: str = "") -> YouTubeTranscript:
            return YouTubeTranscript(
                title=title or "Transcript source",
                video_id="video_123",
                language="en",
                text=(
                    "Title: Transcript source\n"
                    "Source: https://www.youtube.com/watch?v=video_123\n"
                    "Media type: YouTube video\n"
                    "Transcript:\n"
                    "[00:00] This transcript is indexed as local source text."
                ),
                metadata={
                    "adapter": "youtube_transcript",
                    "media_provider": "youtube",
                    "media_kind": "video",
                    "video_id": "video_123",
                    "transcript_language": "en",
                },
            )

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_service, "youtube_adapter", _FakeYouTubeAdapter())

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://www.youtube.com/watch?v=video_123", "title": "视频字幕"},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["source_type"] == "video_url"
    assert source["mime_type"] == "text/plain"
    assert source["metadata"]["adapter"] == "youtube_transcript"
    assert source["metadata"]["video_id"] == "video_123"
    assert source["structure_status"] == "linear_only"

    structure = api_client.get(f"/api/packages/{package_id}/sources/{source['id']}/structure")
    assert structure.status_code == 200
    structure_payload = structure.json()
    assert structure_payload["structure"]["status"] == "linear_only"
    assert structure_payload["chunks"]
    assert "indexed as local source text" in structure_payload["chunks"][0]["text"]


def test_failed_legacy_source_can_be_retried_into_native_index(
    api_client: TestClient,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Recover source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]
    source_dir = workspace_state.UPLOAD_DIR / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    local_path = source_dir / "recover.md"
    local_path.write_text("# Recovered\n\nRecovered local file body.", encoding="utf-8")
    source_evidence_store.save_source(
        SourceIngestionRecord(
            owner_user_id=TEST_USER.id,
            package_id=package_id,
            title="recover.md",
            source_type="local_file",
            file_name="recover.md",
            mime_type="text/markdown",
            status="failed",
            error="Open Notebook 服务未启动或不可达：http://localhost:5055。",
            metadata={"local_source_path": str(local_path), "adapter": "open_notebook"},
        )
    )

    listed = api_client.get(f"/api/packages/{package_id}/sources")

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "failed"

    retried = api_client.post(f"/api/packages/{package_id}/sources/{listed.json()[0]['id']}/retry")

    assert retried.status_code == 200
    recovered = _wait_for_source_status(
        api_client,
        package_id,
        listed.json()[0]["id"],
        "ready",
    )
    assert recovered["status"] == "ready"
    assert recovered["error"] == ""
    assert recovered["structure_status"] == "ready"

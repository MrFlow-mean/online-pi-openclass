from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import threading
import time
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

import app.main as main_module
from app.models import (
    BoardDocument,
    CoursePackage,
    EvidenceBundle,
    LearningSourceGrounding,
    LearningSourceReference,
    PublicationReview,
    SourceChapter,
    SourceIngestionJob,
    SourceIngestionRecord,
    SourceRange,
    SourceStructure,
    SourceStructureView,
    UserView,
)
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import source_ingestion_service as source_ingestion_module
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services import publication_review as publication_review_service
from app.services import source_range_reader
from app.services.publication_review import (
    PublicationSourceUnit,
    review_project_publication,
    scan_publication_units,
)
from app.services.publication_review_agent import default_publication_review_selection
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
OTHER_USER = UserView(
    id="user_searcher",
    email="searcher@example.com",
    role="user",
    display_name="Searcher",
    created_at="2026-01-02T00:00:00+00:00",
)
GUEST_USER = UserView(
    id="guest_smoke",
    email="guest_smoke@guest.openclass.local",
    role="guest",
    created_at="2026-01-03T00:00:00+00:00",
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


class _PublicationReviewAdapter:
    def __init__(self, decisions: list[dict[str, object]]) -> None:
        self.decisions = decisions

    def parse_structured(self, **_kwargs):
        return type("Result", (), {"output_parsed": {"decisions": self.decisions}})()


class _DirectoryPublicationAgentAdapter:
    def __init__(self) -> None:
        self.scanned_text = ""

    def parse_structured(self, **kwargs):
        payload = json.loads(str(kwargs["user_prompt"]))
        schema_name = kwargs["schema"].__name__
        if schema_name == "_ScopeBatchDecision":
            return type(
                "Result",
                (),
                {
                    "output_parsed": {
                        "decisions": [
                            {
                                "unit_id": unit["unit_id"],
                                "region": "body",
                                "reason": "Substantive directory range.",
                            }
                            for unit in payload["directory_units"]
                        ]
                    }
                },
            )()
        units = payload["units"]
        self.scanned_text = "\n".join(str(unit["text"]) for unit in units)
        decisions = []
        for unit in units:
            text = str(unit["text"])
            declaration = "Copyright 2026. All rights reserved." in text
            decisions.append(
                {
                    "unit_id": unit["unit_id"],
                    "region": "non_body",
                    "copyright_declaration": declaration,
                    "evidence_excerpt": (
                        "Copyright 2026. All rights reserved." if declaration else ""
                    ),
                    "reason": "Original non-body range review.",
                }
            )
        return type("Result", (), {"output_parsed": {"decisions": decisions}})()


def _publication_unit(*, unit_id: str, text: str) -> PublicationSourceUnit:
    return PublicationSourceUnit(
        source_id="source_test",
        source_title="Uploaded material",
        unit_id=unit_id,
        order_index=0,
        total_units=1,
        location="page 2",
        section_path=[],
        text=text,
    )


def test_publication_scan_blocks_grounded_copyright_declaration_in_non_body_content() -> None:
    review = scan_publication_units(
        units=[_publication_unit(unit_id="front-1", text="Copyright 2026. All rights reserved.")],
        source_count=1,
        source_fingerprint="fingerprint",
        adapter=_PublicationReviewAdapter(
            [
                {
                    "unit_id": "front-1",
                    "region": "non_body",
                    "copyright_declaration": True,
                    "evidence_excerpt": "Copyright 2026. All rights reserved.",
                    "reason": "Rights statement in front matter.",
                }
            ]
        ),
    )

    assert review.status == "blocked"
    assert review.findings[0].source_id == "source_test"
    assert review.findings[0].evidence_excerpt == "Copyright 2026. All rights reserved."


@pytest.mark.parametrize(
    ("region", "declaration", "text", "excerpt"),
    [
        ("body", True, "The lesson compares copyright systems.", "copyright systems"),
        ("non_body", False, "Preface by the author.", ""),
    ],
)
def test_publication_scan_does_not_block_body_discussion_or_non_body_without_declaration(
    region: str,
    declaration: bool,
    text: str,
    excerpt: str,
) -> None:
    review = scan_publication_units(
        units=[_publication_unit(unit_id="unit-1", text=text)],
        source_count=1,
        source_fingerprint="fingerprint",
        adapter=_PublicationReviewAdapter(
            [
                {
                    "unit_id": "unit-1",
                    "region": region,
                    "copyright_declaration": declaration,
                    "evidence_excerpt": excerpt,
                    "reason": "Test decision.",
                }
            ]
        ),
    )

    assert review.status == "approved"
    assert review.findings == []


def _write_publication_pdf(path: Path, pages: list[str]) -> str:
    document = canvas.Canvas(str(path))
    for text in pages:
        document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_view_for_publication_pdf(
    *,
    source: SourceIngestionRecord,
    content_hash: str,
    mapping_status: str = "verified",
) -> SourceStructureView:
    structure = SourceStructure(
        id="structure_publication",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
        strategy="codex_directory_v1",
        has_verified_toc=True,
        catalog_version=4,
        source_content_hash=content_hash,
        catalog_schema_version="codex_directory_v1",
    )
    chapter = SourceChapter(
        id="chapter_body",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        title="Main authored section",
        path=["Main authored section"],
        mapping_status=mapping_status,
        range=SourceRange(kind="pdf_pages", start=2, end=2, display_label="page 2"),
        source_content_hash=content_hash,
        catalog_version=4,
        confidence=1.0,
    )
    return SourceStructureView(source=source, structure=structure, chapters=[chapter])


def test_publication_review_pi_agent_defaults_to_deepseek_v4_flash() -> None:
    selection = default_publication_review_selection()

    assert selection.agent_backend == "pi"
    assert selection.provider == "deepseek"
    assert selection.model == "deepseek-v4-flash"
    assert selection.access_method == "shared_api"


def test_publication_review_reads_original_non_body_ranges_and_blocks_copyright(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "publication.pdf"
    content_hash = _write_publication_pdf(
        path,
        [
            "Copyright 2026. All rights reserved.",
            "BODY_COPYRIGHT_DISCUSSION is substantive body content only.",
            "Back matter without a rights declaration.",
        ],
    )
    package = _publication_package_with_reference("source_pdf")
    source = SourceIngestionRecord(
        id="source_pdf",
        owner_user_id=TEST_USER.id,
        package_id=package.id,
        title="Uploaded publication",
        source_type="local_file",
        file_name=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"content_hash": content_hash},
    )
    view = _catalog_view_for_publication_pdf(source=source, content_hash=content_hash)
    adapter = _DirectoryPublicationAgentAdapter()
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [source],
    )
    monkeypatch.setattr(
        publication_review_service.source_structure_store,
        "get_structure_view",
        lambda **_kwargs: view,
    )
    monkeypatch.setattr(source_range_reader, "_source_path", lambda _source: path)

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=adapter,
    )

    assert review.status == "blocked"
    assert review.agent_backend == "pi"
    assert review.provider == "deepseek"
    assert review.model == "deepseek-v4-flash"
    assert review.findings[0].source_title == "Uploaded publication"
    assert review.findings[0].evidence_excerpt == "Copyright 2026. All rights reserved."
    assert "BODY_COPYRIGHT_DISCUSSION" not in adapter.scanned_text


def test_publication_review_allows_when_original_non_body_ranges_have_no_declaration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "publication-clear.pdf"
    content_hash = _write_publication_pdf(
        path,
        [
            "Front matter without a rights declaration.",
            "BODY_COPYRIGHT_DISCUSSION remains inside substantive body content.",
            "Index entries without a rights declaration.",
        ],
    )
    package = _publication_package_with_reference("source_pdf_clear")
    source = SourceIngestionRecord(
        id="source_pdf_clear",
        owner_user_id=TEST_USER.id,
        package_id=package.id,
        title="Uploaded source",
        source_type="local_file",
        file_name=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"content_hash": content_hash},
    )
    view = _catalog_view_for_publication_pdf(source=source, content_hash=content_hash)
    adapter = _DirectoryPublicationAgentAdapter()
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [source],
    )
    monkeypatch.setattr(
        publication_review_service.source_structure_store,
        "get_structure_view",
        lambda **_kwargs: view,
    )
    monkeypatch.setattr(source_range_reader, "_source_path", lambda _source: path)

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=adapter,
    )

    assert review.status == "approved"
    assert review.findings == []
    assert "BODY_COPYRIGHT_DISCUSSION" not in adapter.scanned_text


def test_publication_review_fails_closed_when_directory_body_ranges_are_unverified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "publication-unverified.pdf"
    content_hash = _write_publication_pdf(path, ["Front matter", "Body", "Back matter"])
    package = _publication_package_with_reference("source_pdf_unverified")
    source = SourceIngestionRecord(
        id="source_pdf_unverified",
        owner_user_id=TEST_USER.id,
        package_id=package.id,
        title="Unverified source",
        source_type="local_file",
        file_name=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"content_hash": content_hash},
    )
    view = _catalog_view_for_publication_pdf(
        source=source,
        content_hash=content_hash,
        mapping_status="unmapped",
    )
    adapter = _DirectoryPublicationAgentAdapter()
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [source],
    )
    monkeypatch.setattr(
        publication_review_service.source_structure_store,
        "get_structure_view",
        lambda **_kwargs: view,
    )
    monkeypatch.setattr(source_range_reader, "_source_path", lambda _source: path)

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=adapter,
    )

    assert review.status == "error"
    assert "未验证的正文范围" in review.message
    assert adapter.scanned_text == ""


def _publication_package_with_reference(source_id: str | None) -> CoursePackage:
    lesson = create_empty_lesson("Reference-aware publication")
    if source_id is not None:
        requirement = build_requirements("Reference-aware publication")
        requirement.source_grounding = LearningSourceGrounding(
            requested_by_user=True,
            confirmation_status="confirmed",
            confirmed_references=[
                LearningSourceReference(
                    evidence_bundle_id="bundle_reference",
                    source_ingestion_id=source_id,
                )
            ],
        )
        lesson.learning_requirements = requirement
    return CoursePackage(
        id="course_publication",
        title="Reference-aware publication",
        summary="",
        lessons=[lesson],
    )


def _publication_source(*, source_id: str, status: str = "ready") -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=source_id,
        owner_user_id=TEST_USER.id,
        package_id="course_publication",
        title=f"Source {source_id}",
        source_type="pasted_text",
        mime_type="text/plain",
        size_bytes=20,
        status=status,
    )


def test_publication_review_allows_uploaded_but_unreferenced_sources_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _publication_package_with_reference(None)
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [_publication_source(source_id="unused", status="failed")],
    )

    class _UnexpectedAdapter:
        def parse_structured(self, **_kwargs):
            raise AssertionError("Unreferenced sources must not be sent to the publication AI.")

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=_UnexpectedAdapter(),
    )

    assert review.status == "approved"
    assert review.scanned_source_count == 0
    assert review.message == "课程没有引用上传资料，可以公开。"


def test_publication_review_scans_only_sources_referenced_by_the_lesson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _publication_package_with_reference("used")
    used = _publication_source(source_id="used")
    unused = _publication_source(source_id="unused", status="failed")
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [used, unused],
    )
    scanned_source_ids: list[str] = []
    progress_stages: list[str] = []

    def fake_units(source: SourceIngestionRecord) -> list[PublicationSourceUnit]:
        scanned_source_ids.append(source.id)
        return [_publication_unit(unit_id=f"{source.id}-body", text="Referenced body text.")]

    monkeypatch.setattr(publication_review_service, "_source_ingestion_units", fake_units)
    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=_PublicationReviewAdapter(
            [
                {
                    "unit_id": "used-body",
                    "region": "body",
                    "copyright_declaration": False,
                    "evidence_excerpt": "",
                    "reason": "Main content.",
                }
            ]
        ),
        progress_callback=lambda progress: progress_stages.append(progress.stage),
    )

    assert review.status == "approved"
    assert review.scanned_source_count == 1
    assert scanned_source_ids == ["used"]
    assert progress_stages == [
        "checking_references",
        "reading_sources",
        "reading_sources",
        "reviewing_units",
        "reviewing_units",
        "verifying_sources",
    ]


def test_publication_review_resolves_historical_source_bundle_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _publication_package_with_reference(None)
    package.lessons[0].history_graph.commits[0].metadata.update(
        {
            "verified_source_reference_used": True,
            "verified_source_bundle_ids": ["bundle_history"],
        }
    )
    used = _publication_source(source_id="used")
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [used],
    )
    monkeypatch.setattr(
        publication_review_service.source_evidence_store,
        "get_bundle",
        lambda **_kwargs: EvidenceBundle(
            id="bundle_history",
            owner_user_id=TEST_USER.id,
            package_id=package.id,
            lesson_id=package.lessons[0].id,
            status="consumed",
            metadata={"source_ingestion_id": "used"},
        ),
    )
    monkeypatch.setattr(
        publication_review_service,
        "_source_ingestion_units",
        lambda source: [_publication_unit(unit_id=f"{source.id}-body", text="Referenced text.")],
    )

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=_PublicationReviewAdapter(
            [
                {
                    "unit_id": "used-body",
                    "region": "body",
                    "copyright_declaration": False,
                    "evidence_excerpt": "",
                    "reason": "Main content.",
                }
            ]
        ),
    )

    assert review.status == "approved"
    assert review.scanned_source_count == 1


def test_publication_review_fails_closed_when_a_reference_cannot_be_traced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _publication_package_with_reference("missing")
    monkeypatch.setattr(
        publication_review_service.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [_publication_source(source_id="unused")],
    )

    review = review_project_publication(
        owner_user_id=TEST_USER.id,
        package=package,
        lesson_id=package.lessons[0].id,
        adapter=_PublicationReviewAdapter([]),
    )

    assert review.status == "error"
    assert "引用记录" in review.message
    assert "无法追溯" in review.message


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


def test_standalone_lesson_can_be_renamed(api_client: TestClient) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Original lesson", "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    renamed = api_client.post(
        f"/api/lessons/{lesson['id']}/rename",
        json={"title": "  Renamed lesson  "},
    )

    assert renamed.status_code == 200
    renamed_lesson = next(
        item
        for package in renamed.json()["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert renamed_lesson["title"] == "Renamed lesson"
    assert renamed_lesson["slug"] == lesson["slug"]
    assert renamed_lesson["updated_at"] >= lesson["updated_at"]

    rejected = api_client.post(
        f"/api/lessons/{lesson['id']}/rename",
        json={"title": "   "},
    )
    assert rejected.status_code == 400


def test_standalone_lessons_and_packages_have_revocable_public_visibility(
    api_client: TestClient,
) -> None:
    workspace = api_client.get("/api/workspace").json()
    standalone_package = workspace["packages"][0]
    assert standalone_package["visibility"] == "private"

    standalone_lesson_response = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Standalone project", "start_blank": True},
    )
    assert standalone_lesson_response.status_code == 200
    standalone_lesson = standalone_lesson_response.json()["lessons"][0]
    assert standalone_lesson["visibility"] == "private"
    assert api_client.get(f"/api/public/lessons/{standalone_lesson['id']}").status_code == 404

    published_lesson = api_client.post(
        f"/api/lessons/{standalone_lesson['id']}/visibility",
        json={"visibility": "public"},
    )
    assert published_lesson.status_code == 200
    published_lesson_data = next(
        lesson
        for package in published_lesson.json()["packages"]
        for lesson in package["lessons"]
        if lesson["id"] == standalone_lesson["id"]
    )
    assert published_lesson_data["visibility"] == "public"
    assert published_lesson_data["publication_review"]["status"] == "approved"

    public_lesson = api_client.get(f"/api/public/lessons/{standalone_lesson['id']}")
    assert public_lesson.status_code == 200
    assert public_lesson.json()["title"] == "Standalone project"
    assert "history_graph" not in public_lesson.json()

    private_lesson = api_client.post(
        f"/api/lessons/{standalone_lesson['id']}/visibility",
        json={"visibility": "private"},
    )
    assert private_lesson.status_code == 200
    assert api_client.get(f"/api/public/lessons/{standalone_lesson['id']}").status_code == 404

    created_package = api_client.post(
        "/api/packages",
        json={"title": "Public package", "summary": "Package summary"},
    )
    assert created_package.status_code == 200
    package_id = created_package.json()["active_package_id"]
    package_data = next(item for item in created_package.json()["packages"] if item["id"] == package_id)
    assert package_data["visibility"] == "private"

    packaged_lesson_response = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Packaged lesson", "target_package_id": package_id, "start_blank": True},
    )
    assert packaged_lesson_response.status_code == 200
    packaged_lesson = packaged_lesson_response.json()["lessons"][0]
    assert (
        api_client.post(
            f"/api/lessons/{packaged_lesson['id']}/visibility",
            json={"visibility": "public"},
        ).status_code
        == 400
    )
    assert api_client.get(f"/api/public/packages/{package_id}").status_code == 404

    published_package = api_client.post(
        f"/api/packages/{package_id}",
        json={"visibility": "public"},
    )
    assert published_package.status_code == 200
    public_package = api_client.get(f"/api/public/packages/{package_id}")
    assert public_package.status_code == 200
    assert public_package.json()["title"] == "Public package"
    assert [lesson["title"] for lesson in public_package.json()["lessons"]] == ["Packaged lesson"]
    assert "history_graph" not in public_package.json()["lessons"][0]

    private_package = api_client.post(
        f"/api/packages/{package_id}",
        json={"visibility": "private"},
    )
    assert private_package.status_code == 200
    assert api_client.get(f"/api/public/packages/{package_id}").status_code == 404


def test_guest_workspace_can_stay_private_but_cannot_publish(
    api_client: TestClient,
) -> None:
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: GUEST_USER
    standalone_lesson_response = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Guest Studio trial", "start_blank": True},
    )
    assert standalone_lesson_response.status_code == 200
    standalone_lesson = standalone_lesson_response.json()["lessons"][0]

    rejected_lesson = api_client.post(
        f"/api/lessons/{standalone_lesson['id']}/visibility",
        json={"visibility": "public"},
    )
    assert rejected_lesson.status_code == 403
    assert rejected_lesson.json()["detail"] == "请先注册或登录后再公开项目"

    rejected_stream = api_client.post(
        f"/api/lessons/{standalone_lesson['id']}/visibility/stream",
        json={"visibility": "public"},
    )
    assert rejected_stream.status_code == 403

    created_package = api_client.post(
        "/api/packages",
        json={"title": "Guest private package", "summary": "Studio trial"},
    )
    assert created_package.status_code == 200
    package_id = created_package.json()["active_package_id"]

    rejected_package = api_client.post(
        f"/api/packages/{package_id}",
        json={"visibility": "public"},
    )
    assert rejected_package.status_code == 403

    private_package = api_client.post(
        f"/api/packages/{package_id}",
        json={"visibility": "private"},
    )
    assert private_package.status_code == 200
    package = next(item for item in private_package.json()["packages"] if item["id"] == package_id)
    assert package["visibility"] == "private"


def test_public_course_search_returns_real_projects_from_other_users(
    api_client: TestClient,
) -> None:
    public_response = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Discoverable public project", "start_blank": True},
    )
    public_lesson = public_response.json()["lessons"][0]
    assert api_client.post(
        f"/api/lessons/{public_lesson['id']}/visibility",
        json={"visibility": "public"},
    ).status_code == 200

    private_response = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Discoverable private project", "start_blank": True},
    )
    assert private_response.status_code == 200

    owned_search_response = api_client.get(
        "/api/courses/search",
        params={"q": "Discoverable project"},
    )
    assert owned_search_response.status_code == 200
    owned_payload = owned_search_response.json()
    assert {
        (result["title"], result["visibility"])
        for result in owned_payload["owned_courses"]
    } == {
        ("Discoverable public project", "public"),
        ("Discoverable private project", "private"),
    }
    assert owned_payload["public_courses"] == []

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: OTHER_USER
    search_response = api_client.get(
        "/api/courses/search",
        params={"q": "Discoverable project"},
    )

    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert search_payload["owned_courses"] == []
    results = search_payload["public_courses"]
    assert [(result["kind"], result["title"]) for result in results] == [
        ("lesson", "Discoverable public project")
    ]
    assert results[0]["lesson_count"] == 1
    assert "board_document" not in results[0]

    public_only_response = api_client.get(
        "/api/public/courses/search",
        params={"q": "Discoverable project"},
    )
    assert public_only_response.status_code == 200
    assert [result["title"] for result in public_only_response.json()] == [
        "Discoverable public project"
    ]


def test_public_course_stars_are_private_persistent_and_searchable(
    api_client: TestClient,
) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Star-worthy public lesson", "start_blank": True},
    )
    assert generated.status_code == 200
    source_lesson = generated.json()["lessons"][0]
    assert api_client.post(
        f"/api/lessons/{source_lesson['id']}/visibility",
        json={"visibility": "public"},
    ).status_code == 200

    viewer = UserView(
        id="user_public_star_viewer",
        email="star-viewer@example.com",
        role="user",
        created_at="2026-01-04T00:00:00+00:00",
    )
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: viewer

    initial_search = api_client.get(
        "/api/courses/search",
        params={"q": "Star-worthy"},
    )
    assert initial_search.status_code == 200
    assert initial_search.json()["public_courses"][0]["is_starred"] is False

    starred = api_client.put(
        f"/api/public/courses/lesson/{source_lesson['id']}/star"
    )
    assert starred.status_code == 200
    assert starred.json() == {
        "id": source_lesson["id"],
        "kind": "lesson",
        "is_starred": True,
    }

    repeated_search = api_client.get(
        "/api/courses/search",
        params={"q": "Star-worthy"},
    )
    assert repeated_search.json()["public_courses"][0]["is_starred"] is True
    starred_courses = api_client.get("/api/public/courses/stars")
    assert starred_courses.status_code == 200
    assert [course["id"] for course in starred_courses.json()] == [source_lesson["id"]]

    unstarred = api_client.delete(
        f"/api/public/courses/lesson/{source_lesson['id']}/star"
    )
    assert unstarred.status_code == 200
    assert unstarred.json()["is_starred"] is False
    assert api_client.get("/api/public/courses/stars").json() == []


def test_standalone_lesson_publication_stream_reports_real_review_stage(
    api_client: TestClient,
) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Streaming publication review", "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    response = api_client.post(
        f"/api/lessons/{lesson['id']}/visibility/stream",
        json={"visibility": "public"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0] == {
        "type": "progress",
        "progress": {
            "stage": "checking_references",
            "completed_items": 0,
            "total_items": 0,
            "batch_index": 0,
            "batch_count": 0,
        },
    }
    assert events[-1]["type"] == "result"
    published_lesson = next(
        item
        for package in events[-1]["workspace"]["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert published_lesson["visibility"] == "public"
    assert published_lesson["publication_review"]["status"] == "approved"


def test_public_lesson_fork_is_personal_idempotent_and_restorable(
    api_client: TestClient,
) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Shared lesson", "start_blank": True},
    )
    assert generated.status_code == 200
    source_lesson = generated.json()["lessons"][0]
    source_document = _document_with_text(
        source_lesson["board_document"],
        "Public source version",
    )
    saved_source = api_client.post(
        f"/api/lessons/{source_lesson['id']}/document/save",
        json={
            "document": source_document,
            "label": "Publishable version",
            "message": "Saved the public source version",
        },
    )
    assert saved_source.status_code == 200
    published = api_client.post(
        f"/api/lessons/{source_lesson['id']}/visibility",
        json={"visibility": "public"},
    )
    assert published.status_code == 200

    viewer = UserView(
        id="user_public_viewer",
        email="viewer@example.com",
        role="user",
        created_at="2026-01-02T00:00:00+00:00",
    )
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: viewer

    first_fork = api_client.post(f"/api/public/lessons/{source_lesson['id']}/fork")
    assert first_fork.status_code == 200
    personal_package = first_fork.json()
    assert personal_package["is_standalone"] is True
    personal_lesson = next(
        lesson
        for lesson in personal_package["lessons"]
        if lesson["id"] == personal_package["active_lesson_id"]
    )
    assert personal_lesson["id"] != source_lesson["id"]
    assert personal_lesson["visibility"] == "private"
    assert personal_lesson["board_document"]["content_text"] == "Public source version"
    initial_commit = personal_lesson["history_graph"]["commits"][0]
    assert initial_commit["metadata"]["forked_from_public_lesson_id"] == source_lesson["id"]

    repeated_fork = api_client.post(f"/api/public/lessons/{source_lesson['id']}/fork")
    assert repeated_fork.status_code == 200
    assert repeated_fork.json()["active_lesson_id"] == personal_lesson["id"]
    assert sum(
        lesson["id"] == personal_lesson["id"]
        for lesson in repeated_fork.json()["lessons"]
    ) == 1

    personal_document = _document_with_text(
        personal_lesson["board_document"],
        "Public source version\n\nMy follow-up notes",
    )
    saved_personal = api_client.post(
        f"/api/lessons/{personal_lesson['id']}/document/save",
        json={
            "document": personal_document,
            "label": "Personal follow-up",
            "message": "Recorded the viewer's follow-up",
        },
    )
    assert saved_personal.status_code == 200
    assert saved_personal.json()["lessons"][0]["board_document"]["content_text"].endswith(
        "My follow-up notes"
    )

    restored = api_client.post(
        f"/api/lessons/{personal_lesson['id']}/restore",
        json={
            "commit_id": initial_commit["id"],
            "label": "Restore public source baseline",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["lessons"][0]["board_document"]["content_text"] == "Public source version"

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    owner_workspace = api_client.get("/api/workspace")
    assert owner_workspace.status_code == 200
    persisted_source = next(
        lesson
        for package in owner_workspace.json()["packages"]
        for lesson in package["lessons"]
        if lesson["id"] == source_lesson["id"]
    )
    assert persisted_source["board_document"]["content_text"] == "Public source version"


def test_public_package_downloads_all_lessons_into_viewer_standalone_courses(
    api_client: TestClient,
) -> None:
    created_package = api_client.post(
        "/api/packages",
        json={"title": "Downloadable package", "summary": "Two public lessons"},
    )
    assert created_package.status_code == 200
    package_id = created_package.json()["active_package_id"]

    source_lessons = []
    for title in ["First downloadable lesson", "Second downloadable lesson"]:
        generated = api_client.post(
            "/api/lessons/generate",
            json={
                "topic": title,
                "target_package_id": package_id,
                "start_blank": True,
            },
        )
        assert generated.status_code == 200
        source_lessons.append(generated.json()["lessons"][-1])

    published = api_client.post(
        f"/api/packages/{package_id}",
        json={"visibility": "public"},
    )
    assert published.status_code == 200

    viewer = UserView(
        id="user_package_downloader",
        email="package-downloader@example.com",
        role="user",
        created_at="2026-01-03T00:00:00+00:00",
    )
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: viewer

    downloaded = api_client.post(f"/api/public/packages/{package_id}/fork")
    assert downloaded.status_code == 200
    standalone_package = downloaded.json()
    assert standalone_package["is_standalone"] is True
    assert [lesson["title"] for lesson in standalone_package["lessons"]] == [
        "First downloadable lesson",
        "Second downloadable lesson",
    ]
    assert all(lesson["visibility"] == "private" for lesson in standalone_package["lessons"])
    active_lesson = next(
        lesson
        for lesson in standalone_package["lessons"]
        if lesson["id"] == standalone_package["active_lesson_id"]
    )
    assert active_lesson["title"] == "First downloadable lesson"
    assert {
        lesson["history_graph"]["commits"][0]["metadata"]["forked_from_public_lesson_id"]
        for lesson in standalone_package["lessons"]
    } == {lesson["id"] for lesson in source_lessons}

    repeated_download = api_client.post(f"/api/public/packages/{package_id}/fork")
    assert repeated_download.status_code == 200
    assert len(repeated_download.json()["lessons"]) == 2
    assert repeated_download.json()["active_lesson_id"] == active_lesson["id"]


def test_publication_gate_keeps_project_private_when_review_finds_copyright(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Private until reviewed", "start_blank": True},
    )
    lesson = created.json()["lessons"][0]
    monkeypatch.setattr(
        workspace_router,
        "review_project_publication",
        lambda **_kwargs: PublicationReview(
            status="blocked",
            scanned_source_count=1,
            scanned_unit_count=4,
            findings=[
                {
                    "source_id": "source_test",
                    "source_title": "Uploaded material",
                    "location": "page 2",
                    "evidence_excerpt": "All rights reserved.",
                    "reason": "Rights statement in front matter.",
                }
            ],
            message="Copyright declaration found.",
        ),
    )

    response = api_client.post(
        f"/api/lessons/{lesson['id']}/visibility",
        json={"visibility": "public"},
    )

    assert response.status_code == 200
    reviewed = next(
        item
        for package in response.json()["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert reviewed["visibility"] == "private"
    assert reviewed["publication_review"]["status"] == "blocked"
    assert reviewed["publication_review"]["findings"][0]["location"] == "page 2"
    assert api_client.get(f"/api/public/lessons/{lesson['id']}").status_code == 404


def test_source_upload_revokes_publication_and_requires_a_fresh_review(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Publication invalidation", "start_blank": True},
    )
    lesson = created.json()["lessons"][0]
    standalone_package = created.json()["id"]
    published = api_client.post(
        f"/api/lessons/{lesson['id']}/visibility",
        json={"visibility": "public"},
    )
    assert published.status_code == 200
    assert api_client.get(f"/api/public/lessons/{lesson['id']}").status_code == 200

    uploaded = api_client.post(
        f"/api/packages/{standalone_package}/sources",
        data={"title": "Changed source", "text": "Newly uploaded source text."},
    )

    assert uploaded.status_code == 200
    workspace = api_client.get("/api/workspace").json()
    updated_lesson = next(
        item
        for package in workspace["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert updated_lesson["visibility"] == "private"
    assert updated_lesson["publication_review"]["status"] == "not_started"
    assert api_client.get(f"/api/public/lessons/{lesson['id']}").status_code == 404

    republished = api_client.post(
        f"/api/lessons/{lesson['id']}/visibility",
        json={"visibility": "public"},
    )
    assert republished.status_code == 200
    republished_lesson = next(
        item
        for package in republished.json()["packages"]
        for item in package["lessons"]
        if item["id"] == lesson["id"]
    )
    assert republished_lesson["visibility"] == "public"
    assert republished_lesson["publication_review"]["status"] == "approved"
    assert republished_lesson["publication_review"]["scanned_source_count"] == 0
    assert republished_lesson["publication_review"]["message"] == "课程没有引用上传资料，可以公开。"
    assert api_client.get(f"/api/public/lessons/{lesson['id']}").status_code == 200


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

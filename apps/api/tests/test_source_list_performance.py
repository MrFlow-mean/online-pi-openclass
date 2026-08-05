from app.models import SourceIngestionRecord, UserView
from app.routers import sources as sources_router
from app.services.source_ingestion_service import SourceIngestionService


def _user() -> UserView:
    return UserView(
        id="owner",
        email="owner@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _repository_source() -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id="source_repo",
        owner_user_id="owner",
        package_id="course_owned",
        title="Repository",
        source_type="code_repository",
        source_uri="https://github.com/example/repository",
        file_name="repository.zip",
        mime_type="application/zip",
        size_bytes=1,
        status="ready",
        structure_status="pending",
    )


def test_source_list_uses_direct_package_ownership_check(monkeypatch) -> None:
    class Store:
        def package_belongs_to_user(self, owner_user_id: str, package_id: str) -> bool:
            return (owner_user_id, package_id) == ("owner", "course_owned")

    source = _repository_source()
    monkeypatch.setattr(sources_router.workspace_state, "get_course_store", lambda: Store())
    monkeypatch.setattr(
        sources_router.workspace_state,
        "load_workspace_for_user",
        lambda *_args: (_ for _ in ()).throw(AssertionError("workspace loader must not run")),
    )
    monkeypatch.setattr(
        sources_router.source_ingestion_service,
        "list_sources",
        lambda **_kwargs: [source],
    )

    assert sources_router.list_package_sources("course_owned", user=_user()) == [source]


def test_ready_repository_uses_repository_map_instead_of_document_structure() -> None:
    source = _repository_source()

    class SourceStore:
        def list_sources(self, **_kwargs):
            return [source]

    class StructureStore:
        def attach_summary(self, record):
            return record

    class RepositoryStore:
        def has_snapshot(self, *, source):
            return True

    class JobStore:
        def latest_for_source(self, **_kwargs):
            return None

    service = SourceIngestionService.__new__(SourceIngestionService)
    service.store = SourceStore()
    service.source_backend = "native"
    service.structure_store = StructureStore()
    service.repository_store = RepositoryStore()
    service.job_store = JobStore()

    listed = service.list_sources(owner_user_id="owner", package_id="course_owned")

    assert listed[0].status == "ready"
    assert listed[0].structure_status == "ready"
    assert listed[0].structure_strategy == "code_repository_v1"

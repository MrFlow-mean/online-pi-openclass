from __future__ import annotations

import hashlib
import hmac
import stat
import zipfile
from pathlib import Path

import pytest

from app.models import RepositoryFileEntry, RepositoryMapNode, RepositorySnapshot, SourceIngestionRecord
from app.services.github_app import GitHubAppError, GitHubAppService
from app.services.repository_source import (
    RepositorySourceError,
    SafeRepositoryArchive,
    parse_github_url,
    read_repository_file_range,
)
from app.services.repository_store import RepositoryStore


def test_parse_github_repository_tree_blob_and_commit_urls() -> None:
    root = parse_github_url("https://github.com/openai/openai-python")
    tree = parse_github_url("https://github.com/openai/openai-python/tree/feature/nested/src/openai")
    blob = parse_github_url("https://github.com/openai/openai-python/blob/main/README.md")
    commit = parse_github_url("https://github.com/openai/openai-python/commit/" + "a" * 40)

    assert (root.owner, root.name, root.view_kind) == ("openai", "openai-python", "repository")
    assert tree.view_kind == "tree" and tree.tail == ("feature", "nested", "src", "openai")
    assert blob.view_kind == "blob" and blob.tail == ("main", "README.md")
    assert commit.view_kind == "commit" and commit.tail == ("a" * 40,)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/openai/openai-python/issues/1",
        "https://github.com/openai/openai-python/pull/1",
        "https://example.com/openai/openai-python",
    ],
)
def test_parse_github_url_rejects_unsupported_targets(url: str) -> None:
    with pytest.raises(RepositorySourceError):
        parse_github_url(url)


def test_safe_repository_archive_reads_regular_files(tmp_path: Path) -> None:
    archive_path = tmp_path / "repository.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project-root/README.md", "one\ntwo\nthree\n")

    with SafeRepositoryArchive(archive_path) as archive:
        assert archive.prefix == "project-root"
        assert archive.read("project-root/README.md") == b"one\ntwo\nthree\n"


def test_safe_repository_archive_rejects_symlinks(tmp_path: Path) -> None:
    archive_path = tmp_path / "repository.zip"
    link = zipfile.ZipInfo("project-root/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, "../outside")

    with pytest.raises(RepositorySourceError, match="symbolic link"):
        SafeRepositoryArchive(archive_path)


def test_repository_store_round_trip_and_verified_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "openclass.db"
    archive_path = tmp_path / "uploads" / "sources" / "source.repository.zip"
    archive_path.parent.mkdir(parents=True)
    content = b"alpha\nbeta\ngamma\n"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("root/src/example.py", content)
    archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    monkeypatch.setattr("app.services.repository_source.workspace_state.UPLOAD_DIR", tmp_path / "uploads")

    snapshot = RepositorySnapshot(
        owner_user_id="user-1",
        package_id="package-1",
        source_ingestion_id="source-1",
        owner="owner",
        name="repo",
        resolved_commit_sha="a" * 40,
        archive_path=str(archive_path),
        archive_hash=archive_hash,
        manifest_hash="b" * 64,
    )
    file = RepositoryFileEntry(
        source_ingestion_id="source-1",
        path="src/example.py",
        content_hash=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        line_count=3,
        text_status="ready",
        archive_entry="root/src/example.py",
    )
    node = RepositoryMapNode(
        source_ingestion_id="source-1",
        tree_kind="project",
        node_kind="file",
        title="example.py",
        path="src/example.py",
        selectable=True,
    )
    store = RepositoryStore(path=database_path)
    store.save_repository(snapshot=snapshot, files=[file], nodes=[node])
    source = SourceIngestionRecord(
        owner_user_id="user-1",
        package_id="package-1",
        id="source-1",
        title="owner/repo",
        source_type="code_repository",
        status="ready",
    )

    view = store.get_map(source=source)
    assert view is not None
    assert view.snapshot.resolved_commit_sha == "a" * 40
    assert view.total_file_count == 1
    assert read_repository_file_range(
        snapshot=snapshot,
        file=file,
        line_start=2,
        line_end=3,
    ) == "beta\ngamma"


def test_github_webhook_signature_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLASS_GITHUB_APP_WEBHOOK_SECRET", "secret")
    service = GitHubAppService(store=RepositoryStore(path=Path(":memory:")))
    body = b'{"action":"deleted"}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    service.verify_webhook(body, signature)
    with pytest.raises(GitHubAppError):
        service.verify_webhook(body, "sha256=bad")

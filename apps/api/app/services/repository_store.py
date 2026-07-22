from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.models import (
    GitHubInstallationView,
    RepositoryFileEntry,
    RepositoryMapNode,
    RepositoryMapView,
    RepositoryNodeEvidence,
    RepositorySnapshot,
    SourceIngestionRecord,
    now_iso,
)
from app.services import workspace_state
from app.services.source_ingestion_jobs import SourceIngestionCoordinator, source_ingestion_coordinator


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class RepositoryStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        coordinator: SourceIngestionCoordinator = source_ingestion_coordinator,
    ) -> None:
        self._path = path
        self.coordinator = coordinator

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
            self._initialize(conn)
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS repository_snapshots (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                source_ingestion_id TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                repository_id INTEGER,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                visibility TEXT NOT NULL,
                requested_ref TEXT NOT NULL,
                resolved_commit_sha TEXT NOT NULL,
                scope_path TEXT NOT NULL,
                scope_kind TEXT NOT NULL,
                default_branch TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                archive_hash TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                license_spdx TEXT NOT NULL,
                supersedes_source_id TEXT,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_repository_snapshots_scope
                ON repository_snapshots(owner_user_id, package_id, source_ingestion_id);

            CREATE TABLE IF NOT EXISTS repository_files (
                id TEXT PRIMARY KEY,
                source_ingestion_id TEXT NOT NULL,
                path TEXT NOT NULL,
                blob_sha TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                language TEXT NOT NULL,
                text_status TEXT NOT NULL,
                skip_reason TEXT NOT NULL,
                archive_entry TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(source_ingestion_id, path)
            );
            CREATE INDEX IF NOT EXISTS idx_repository_files_source
                ON repository_files(source_ingestion_id, order_index);

            CREATE TABLE IF NOT EXISTS repository_map_nodes (
                id TEXT PRIMARY KEY,
                source_ingestion_id TEXT NOT NULL,
                tree_kind TEXT NOT NULL,
                node_kind TEXT NOT NULL,
                parent_id TEXT,
                title TEXT NOT NULL,
                path TEXT NOT NULL,
                description TEXT NOT NULL,
                level INTEGER NOT NULL,
                order_index INTEGER NOT NULL,
                selectable INTEGER NOT NULL,
                coverage_status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_repository_nodes_source
                ON repository_map_nodes(source_ingestion_id, tree_kind, order_index);

            CREATE TABLE IF NOT EXISTS repository_node_evidence (
                node_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                path TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                order_index INTEGER NOT NULL,
                PRIMARY KEY(node_id, file_id, line_start, line_end)
            );
            CREATE INDEX IF NOT EXISTS idx_repository_node_evidence_node
                ON repository_node_evidence(node_id, order_index);

            CREATE TABLE IF NOT EXISTS github_connection_states (
                state TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                next_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS github_app_installations (
                owner_user_id TEXT NOT NULL,
                installation_id INTEGER NOT NULL,
                account_id INTEGER,
                account_login TEXT NOT NULL,
                account_type TEXT NOT NULL,
                repository_selection TEXT NOT NULL,
                status TEXT NOT NULL,
                permissions_json TEXT NOT NULL DEFAULT '{}',
                repository_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_user_id, installation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_github_installations_id
                ON github_app_installations(installation_id, status);
            """
        )

    def save_repository(
        self,
        *,
        snapshot: RepositorySnapshot,
        files: list[RepositoryFileEntry],
        nodes: list[RepositoryMapNode],
    ) -> None:
        def operation() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM repository_node_evidence WHERE node_id IN (SELECT id FROM repository_map_nodes WHERE source_ingestion_id = ?)", (snapshot.source_ingestion_id,))
                conn.execute("DELETE FROM repository_map_nodes WHERE source_ingestion_id = ?", (snapshot.source_ingestion_id,))
                conn.execute("DELETE FROM repository_files WHERE source_ingestion_id = ?", (snapshot.source_ingestion_id,))
                conn.execute("DELETE FROM repository_snapshots WHERE source_ingestion_id = ?", (snapshot.source_ingestion_id,))
                conn.execute(
                    """
                    INSERT INTO repository_snapshots(
                        id, owner_user_id, package_id, source_ingestion_id, provider, repository_id,
                        owner, name, visibility, requested_ref, resolved_commit_sha, scope_path,
                        scope_kind, default_branch, archive_path, archive_hash, manifest_hash,
                        license_spdx, supersedes_source_id, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.id, snapshot.owner_user_id, snapshot.package_id,
                        snapshot.source_ingestion_id, snapshot.provider, snapshot.repository_id,
                        snapshot.owner, snapshot.name, snapshot.visibility, snapshot.requested_ref,
                        snapshot.resolved_commit_sha, snapshot.scope_path, snapshot.scope_kind,
                        snapshot.default_branch, snapshot.archive_path, snapshot.archive_hash,
                        snapshot.manifest_hash, snapshot.license_spdx, snapshot.supersedes_source_id,
                        snapshot.created_at, _dumps(snapshot.metadata),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO repository_files(
                        id, source_ingestion_id, path, blob_sha, content_hash, size_bytes,
                        line_count, language, text_status, skip_reason, archive_entry,
                        order_index, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.id, item.source_ingestion_id, item.path, item.blob_sha,
                            item.content_hash, item.size_bytes, item.line_count, item.language,
                            item.text_status, item.skip_reason, item.archive_entry,
                            item.order_index, _dumps(item.metadata),
                        )
                        for item in files
                    ],
                )
                for node in nodes:
                    conn.execute(
                        """
                        INSERT INTO repository_map_nodes(
                            id, source_ingestion_id, tree_kind, node_kind, parent_id, title,
                            path, description, level, order_index, selectable, coverage_status,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            node.id, node.source_ingestion_id, node.tree_kind, node.node_kind,
                            node.parent_id, node.title, node.path, node.description, node.level,
                            node.order_index, int(node.selectable), node.coverage_status,
                            _dumps(node.metadata),
                        ),
                    )
                    conn.executemany(
                        """
                        INSERT INTO repository_node_evidence(
                            node_id, file_id, path, line_start, line_end, reason,
                            confidence, order_index
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                node.id, evidence.file_id, evidence.path, evidence.line_start,
                                evidence.line_end, evidence.reason, evidence.confidence, index,
                            )
                            for index, evidence in enumerate(node.evidence)
                        ],
                    )

        self.coordinator.run_write(self.path, operation)

    def get_snapshot(self, *, owner_user_id: str, package_id: str, source_id: str) -> RepositorySnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_snapshots WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                (owner_user_id, package_id, source_id),
            ).fetchone()
        return self._snapshot(row) if row is not None else None

    def get_file(self, *, source_id: str, file_id: str) -> RepositoryFileEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_files WHERE source_ingestion_id = ? AND id = ?",
                (source_id, file_id),
            ).fetchone()
        return self._file(row) if row is not None else None

    def files_for_source(self, source_id: str) -> list[RepositoryFileEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM repository_files WHERE source_ingestion_id = ? ORDER BY order_index",
                (source_id,),
            ).fetchall()
        return [self._file(row) for row in rows]

    def get_node(self, *, source_id: str, node_id: str) -> RepositoryMapNode | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_map_nodes WHERE source_ingestion_id = ? AND id = ?",
                (source_id, node_id),
            ).fetchone()
            if row is None:
                return None
            return self._node(conn, row)

    def get_map(self, *, source: SourceIngestionRecord) -> RepositoryMapView | None:
        snapshot = self.get_snapshot(
            owner_user_id=source.owner_user_id,
            package_id=source.package_id,
            source_id=source.id,
        )
        if snapshot is None:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM repository_map_nodes WHERE source_ingestion_id = ? ORDER BY tree_kind, order_index",
                (source.id,),
            ).fetchall()
            nodes = [self._node(conn, row) for row in rows]
            counts = conn.execute(
                """
                SELECT count(*) AS total,
                       sum(CASE WHEN text_status = 'ready' THEN 1 ELSE 0 END) AS readable,
                       sum(CASE WHEN json_extract(metadata_json, '$.analyzed') = 1 THEN 1 ELSE 0 END) AS analyzed
                FROM repository_files WHERE source_ingestion_id = ?
                """,
                (source.id,),
            ).fetchone()
        readable = int(counts["readable"] or 0)
        analyzed = int(counts["analyzed"] or 0)
        return RepositoryMapView(
            source=source,
            snapshot=snapshot,
            project_nodes=[node for node in nodes if node.tree_kind == "project"],
            learning_nodes=[node for node in nodes if node.tree_kind == "learning"],
            analyzed_file_count=analyzed,
            readable_file_count=readable,
            total_file_count=int(counts["total"] or 0),
            coverage_ratio=(analyzed / readable if readable else 0.0),
            warnings=list(snapshot.metadata.get("warnings") or []),
        )

    def delete_source(self, source_id: str) -> None:
        def operation() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM repository_node_evidence WHERE node_id IN (SELECT id FROM repository_map_nodes WHERE source_ingestion_id = ?)", (source_id,))
                conn.execute("DELETE FROM repository_map_nodes WHERE source_ingestion_id = ?", (source_id,))
                conn.execute("DELETE FROM repository_files WHERE source_ingestion_id = ?", (source_id,))
                conn.execute("DELETE FROM repository_snapshots WHERE source_ingestion_id = ?", (source_id,))

        self.coordinator.run_write(self.path, operation)

    def create_connection_state(self, *, state: str, owner_user_id: str, next_path: str, created_at: str, expires_at: str) -> None:
        def operation() -> None:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO github_connection_states(state, owner_user_id, next_path, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (state, owner_user_id, next_path, created_at, expires_at),
                )

        self.coordinator.run_write(self.path, operation)

    def consume_connection_state(self, state: str, *, now: str) -> sqlite3.Row | None:
        result: sqlite3.Row | None = None

        def operation() -> None:
            nonlocal result
            with self._connect() as conn:
                result = conn.execute(
                    "SELECT * FROM github_connection_states WHERE state = ? AND consumed_at IS NULL AND expires_at >= ?",
                    (state, now),
                ).fetchone()
                if result is not None:
                    conn.execute("UPDATE github_connection_states SET consumed_at = ? WHERE state = ?", (now, state))

        self.coordinator.run_write(self.path, operation)
        return result

    def save_installation(self, *, owner_user_id: str, installation: GitHubInstallationView) -> None:
        def operation() -> None:
            with self._connect() as conn:
                now = now_iso()
                conn.execute(
                    """
                    INSERT INTO github_app_installations(
                        owner_user_id, installation_id, account_id, account_login, account_type,
                        repository_selection, status, permissions_json, repository_count,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_user_id, installation_id) DO UPDATE SET
                        account_id=excluded.account_id,
                        account_login=excluded.account_login,
                        account_type=excluded.account_type,
                        repository_selection=excluded.repository_selection,
                        status=excluded.status,
                        permissions_json=excluded.permissions_json,
                        repository_count=excluded.repository_count,
                        updated_at=excluded.updated_at
                    """,
                    (
                        owner_user_id, installation.installation_id, installation.account_id,
                        installation.account_login, installation.account_type,
                        installation.repository_selection, installation.status,
                        _dumps(installation.permissions), installation.repository_count,
                        now, installation.updated_at,
                    ),
                )

        self.coordinator.run_write(self.path, operation)

    def list_installations(self, owner_user_id: str) -> list[GitHubInstallationView]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM github_app_installations WHERE owner_user_id = ? ORDER BY updated_at DESC",
                (owner_user_id,),
            ).fetchall()
        return [self._installation(row) for row in rows]

    def owners_for_installation(self, installation_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT owner_user_id FROM github_app_installations WHERE installation_id = ?",
                (installation_id,),
            ).fetchall()
        return [str(row["owner_user_id"]) for row in rows]

    def set_installation_status(self, *, installation_id: int, status: str) -> None:
        def operation() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE github_app_installations SET status = ?, updated_at = ? WHERE installation_id = ?",
                    (status, now_iso(), installation_id),
                )

        self.coordinator.run_write(self.path, operation)

    def disconnect_user(self, owner_user_id: str) -> None:
        def operation() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE github_app_installations SET status = 'disconnected', updated_at = ? WHERE owner_user_id = ?",
                    (now_iso(), owner_user_id),
                )

        self.coordinator.run_write(self.path, operation)

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> RepositorySnapshot:
        return RepositorySnapshot(
            id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            source_ingestion_id=row["source_ingestion_id"], provider=row["provider"],
            repository_id=row["repository_id"], owner=row["owner"], name=row["name"],
            visibility=row["visibility"], requested_ref=row["requested_ref"],
            resolved_commit_sha=row["resolved_commit_sha"], scope_path=row["scope_path"],
            scope_kind=row["scope_kind"], default_branch=row["default_branch"],
            archive_path=row["archive_path"], archive_hash=row["archive_hash"],
            manifest_hash=row["manifest_hash"], license_spdx=row["license_spdx"],
            supersedes_source_id=row["supersedes_source_id"], created_at=row["created_at"],
            metadata=_loads(row["metadata_json"], {}),
        )

    @staticmethod
    def _file(row: sqlite3.Row) -> RepositoryFileEntry:
        return RepositoryFileEntry(
            id=row["id"], source_ingestion_id=row["source_ingestion_id"], path=row["path"],
            blob_sha=row["blob_sha"], content_hash=row["content_hash"], size_bytes=row["size_bytes"],
            line_count=row["line_count"], language=row["language"], text_status=row["text_status"],
            skip_reason=row["skip_reason"], archive_entry=row["archive_entry"],
            order_index=row["order_index"], metadata=_loads(row["metadata_json"], {}),
        )

    @staticmethod
    def _node(conn: sqlite3.Connection, row: sqlite3.Row) -> RepositoryMapNode:
        evidence_rows = conn.execute(
            "SELECT * FROM repository_node_evidence WHERE node_id = ? ORDER BY order_index",
            (row["id"],),
        ).fetchall()
        return RepositoryMapNode(
            id=row["id"], source_ingestion_id=row["source_ingestion_id"], tree_kind=row["tree_kind"],
            node_kind=row["node_kind"], parent_id=row["parent_id"], title=row["title"], path=row["path"],
            description=row["description"], level=row["level"], order_index=row["order_index"],
            selectable=bool(row["selectable"]), coverage_status=row["coverage_status"],
            evidence=[
                RepositoryNodeEvidence(
                    file_id=item["file_id"], path=item["path"], line_start=item["line_start"],
                    line_end=item["line_end"], reason=item["reason"], confidence=item["confidence"],
                )
                for item in evidence_rows
            ],
            metadata=_loads(row["metadata_json"], {}),
        )

    @staticmethod
    def _installation(row: sqlite3.Row) -> GitHubInstallationView:
        return GitHubInstallationView(
            installation_id=row["installation_id"], account_id=row["account_id"],
            account_login=row["account_login"], account_type=row["account_type"],
            repository_selection=row["repository_selection"], status=row["status"],
            permissions=_loads(row["permissions_json"], {}), repository_count=row["repository_count"],
            updated_at=row["updated_at"],
        )


repository_store = RepositoryStore()

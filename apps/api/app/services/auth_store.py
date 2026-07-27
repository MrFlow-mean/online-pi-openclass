from __future__ import annotations

import sqlite3
import threading
import hashlib
import base64
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.services.board_asset_identity import (
    rewrite_board_asset_html,
    rewrite_board_asset_json,
    rewrite_board_asset_markdown,
    stable_board_asset_id,
    stable_board_asset_reference_id,
)


def _workspace_setting_key(prefix: str, owner_user_id: str) -> str:
    return f"{prefix}:{owner_user_id}"


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    quoted_table = _quoted_identifier(table_name)
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_xinfo({quoted_table})").fetchall()
    }


class AuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn:
            with conn:
                yield conn

    def _initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    phone TEXT,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'admin')),
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id);

                CREATE TABLE IF NOT EXISTS auth_identities (
                    provider TEXT NOT NULL,
                    provider_subject TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    email TEXT,
                    display_name TEXT,
                    avatar_url TEXT,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT,
                    PRIMARY KEY (provider, provider_subject)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_identities_user
                    ON auth_identities(user_id);

                CREATE TABLE IF NOT EXISTS auth_oauth_states (
                    state TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    next_path TEXT NOT NULL,
                    frontend_origin TEXT,
                    guest_user_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_guest_sessions (
                    token_hash TEXT PRIMARY KEY,
                    guest_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_guest_sessions_guest
                    ON auth_guest_sessions(guest_user_id);

                CREATE TABLE IF NOT EXISTS auth_email_challenges (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT 'login',
                    code_salt TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_email_challenges_email_created
                    ON auth_email_challenges(email, created_at DESC);

                CREATE TABLE IF NOT EXISTS account_lifecycle_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_account_lifecycle_events_user_created
                    ON account_lifecycle_events(user_id, created_at DESC);
                """
            )
            self._ensure_user_column(conn, "display_name", "TEXT")
            self._ensure_user_column(conn, "avatar_url", "TEXT")
            self._ensure_user_column(conn, "phone", "TEXT")
            self._ensure_user_column(conn, "email_verified_at", "TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL"
            )
            self._ensure_table_column(conn, "auth_oauth_states", "frontend_origin", "TEXT")
            self._ensure_table_column(conn, "auth_oauth_states", "guest_user_id", "TEXT")
            self._ensure_table_column(conn, "auth_oauth_states", "code_verifier", "TEXT")
            self._ensure_table_column(conn, "auth_email_challenges", "purpose", "TEXT NOT NULL DEFAULT 'login'")
            self._ensure_table_column(conn, "auth_sessions", "expires_at", "TEXT")
            self._ensure_table_column(conn, "auth_guest_sessions", "expires_at", "TEXT")
            self.ensure_email_identities(conn)

    def _ensure_user_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        self._ensure_table_column(conn, "users", name, definition)

    def _ensure_table_column(self, conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def ensure_email_identities(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT users.*
            FROM users
            LEFT JOIN auth_identities
                ON auth_identities.user_id = users.id
                AND auth_identities.provider = 'email'
            WHERE auth_identities.user_id IS NULL
                AND users.phone IS NULL
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO auth_identities(
                    provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at
                )
                VALUES ('email', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["email"],
                    row["id"],
                    row["email"],
                    row["display_name"] or row["email"].split("@", 1)[0],
                    row["avatar_url"],
                    row["created_at"],
                    row["last_login_at"],
                ),
            )

    def user_count(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT count(*) FROM users").fetchone()[0])

    def find_user_by_email(self, conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def find_user_by_phone(self, conn: sqlite3.Connection, phone: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()

    def find_user_by_id(self, conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def principal_id_exists(self, conn: sqlite3.Connection, principal_id: str) -> bool:
        return bool(
            conn.execute("SELECT 1 FROM users WHERE id = ? LIMIT 1", (principal_id,)).fetchone()
            or conn.execute(
                "SELECT 1 FROM auth_guest_sessions WHERE guest_user_id = ? LIMIT 1",
                (principal_id,),
            ).fetchone()
        )

    def create_password_user(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        email: str,
        phone: str | None,
        password_salt: str,
        password_hash: str,
        role: str,
        display_name: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(id, email, phone, password_salt, password_hash, role, display_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, phone, password_salt, password_hash, role, display_name, created_at),
        )

    def create_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
        user_id: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None = None,
        created_at: str,
        last_login_at: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_identities(
                provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at),
        )

    def touch_password_login(self, conn: sqlite3.Connection, *, user_id: str, provider: str, now: str) -> None:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
        conn.execute(
            """
            UPDATE auth_identities
            SET last_login_at = ?
            WHERE user_id = ? AND provider = ?
            """,
            (now, user_id, provider),
        )

    def find_user_by_oauth_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT users.*
            FROM auth_identities
            JOIN users ON users.id = auth_identities.user_id
            WHERE auth_identities.provider = ? AND auth_identities.provider_subject = ?
            """,
            (provider, provider_subject),
        ).fetchone()

    def create_oauth_user(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        email: str,
        password_salt: str,
        password_hash: str,
        role: str,
        display_name: str,
        avatar_url: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(
                id, email, password_salt, password_hash, role, display_name, avatar_url, created_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, password_salt, password_hash, role, display_name, avatar_url, now, now),
        )

    def touch_oauth_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE auth_identities
            SET email = ?, display_name = ?, avatar_url = ?, last_login_at = ?
            WHERE provider = ? AND provider_subject = ?
            """,
            (email, display_name, avatar_url, now, provider, provider_subject),
        )

    def touch_oauth_user_profile(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        display_name: str | None,
        avatar_url: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE users
            SET
                last_login_at = ?,
                display_name = COALESCE(NULLIF(?, ''), display_name),
                avatar_url = COALESCE(NULLIF(?, ''), avatar_url)
            WHERE id = ?
            """,
            (now, display_name or "", avatar_url or "", user_id),
        )

    def find_user_by_session_token(self, conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT users.*,
                   auth_sessions.created_at AS session_created_at,
                   auth_sessions.last_seen_at AS session_last_seen_at,
                   auth_sessions.expires_at AS session_expires_at
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    def find_guest_session_by_token(self, conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()

    def touch_session(self, conn: sqlite3.Connection, token_hash: str, now: str) -> None:
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))

    def touch_guest_session(self, conn: sqlite3.Connection, token_hash: str, now: str) -> None:
        conn.execute("UPDATE auth_guest_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))

    def create_guest_session(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        guest_user_id: str,
        now: str,
        expires_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_guest_sessions(token_hash, guest_user_id, created_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash, guest_user_id, now, now, expires_at),
        )

    def list_users(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute("SELECT * FROM users ORDER BY created_at DESC, email").fetchall()

    def admin_stats(self, conn: sqlite3.Connection) -> sqlite3.Row:
        return conn.execute(
            """
            SELECT
                (SELECT count(*) FROM users) AS users,
                (SELECT count(*) FROM users WHERE role = 'admin') AS admins,
                (SELECT count(*) FROM course_packages) AS packages,
                (SELECT count(*) FROM lessons) AS lessons,
                (SELECT count(*) FROM resources) AS resources
            """
        ).fetchone()

    def create_oauth_state(
        self,
        conn: sqlite3.Connection,
        *,
        state: str,
        provider: str,
        next_path: str,
        frontend_origin: str,
        guest_user_id: str | None,
        code_verifier: str | None,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_oauth_states(
                state, provider, next_path, frontend_origin, guest_user_id, code_verifier, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (state, provider, next_path, frontend_origin, guest_user_id, code_verifier, created_at),
        )

    def guest_user_id_for_token_hash(self, conn: sqlite3.Connection, token_hash: str) -> str | None:
        row = conn.execute(
            "SELECT guest_user_id FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return row["guest_user_id"] if row is not None else None

    def delete_guest_sessions(self, conn: sqlite3.Connection, *, guest_user_id: str) -> None:
        conn.execute("DELETE FROM auth_guest_sessions WHERE guest_user_id = ?", (guest_user_id,))

    def claim_guest_workspace(self, conn: sqlite3.Connection, *, guest_user_id: str, user_id: str) -> None:
        self._claim_guest_board_assets(
            conn,
            guest_user_id=guest_user_id,
            user_id=user_id,
        )
        owner_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for row in owner_tables:
            table_name = str(row["name"])
            if table_name in {"board_assets", "board_asset_refs"}:
                continue
            quoted_table = _quoted_identifier(table_name)
            columns = {
                str(column["name"])
                for column in conn.execute(f"PRAGMA table_xinfo({quoted_table})").fetchall()
            }
            if "owner_user_id" not in columns:
                continue
            conn.execute(
                f"UPDATE {quoted_table} SET owner_user_id = ? WHERE owner_user_id = ?",
                (user_id, guest_user_id),
            )

        workspace_settings_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace_settings'"
        ).fetchone()
        if workspace_settings_exists is not None:
            for prefix in ("active_package_id", "workspace_revision"):
                guest_setting_key = _workspace_setting_key(prefix, guest_user_id)
                user_setting_key = _workspace_setting_key(prefix, user_id)
                guest_row = conn.execute(
                    "SELECT value FROM workspace_settings WHERE key = ?",
                    (guest_setting_key,),
                ).fetchone()
                if prefix == "workspace_revision":
                    user_row = conn.execute(
                        "SELECT value FROM workspace_settings WHERE key = ?",
                        (user_setting_key,),
                    ).fetchone()
                    try:
                        guest_revision = int(guest_row["value"]) if guest_row is not None else 0
                    except (TypeError, ValueError):
                        guest_revision = 0
                    try:
                        user_revision = int(user_row["value"]) if user_row is not None else 0
                    except (TypeError, ValueError):
                        user_revision = 0
                    conn.execute(
                        "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                        (user_setting_key, str(max(guest_revision, user_revision) + 1)),
                    )
                    if guest_row is not None:
                        conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
                    continue
                if guest_row is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                    (user_setting_key, str(guest_row["value"])),
                )
                conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
        self.delete_guest_sessions(conn, guest_user_id=guest_user_id)

    def _claim_guest_board_assets(
        self,
        conn: sqlite3.Connection,
        *,
        guest_user_id: str,
        user_id: str,
    ) -> None:
        if not _table_exists(conn, "board_assets"):
            return
        asset_columns = _table_columns(conn, "board_assets")
        required_columns = {"id", "owner_user_id", "content_hash"}
        if not required_columns.issubset(asset_columns):
            return

        guest_assets = conn.execute(
            """
            SELECT id, content_hash
            FROM board_assets
            WHERE owner_user_id = ?
            ORDER BY id
            """,
            (guest_user_id,),
        ).fetchall()
        for guest_asset in guest_assets:
            old_asset_id = str(guest_asset["id"])
            content_hash = str(guest_asset["content_hash"])
            target_asset_id = stable_board_asset_id(
                owner_user_id=user_id,
                content_hash=content_hash,
            )
            existing_target = conn.execute(
                """
                SELECT id
                FROM board_assets
                WHERE owner_user_id = ? AND content_hash = ?
                LIMIT 1
                """,
                (user_id, content_hash),
            ).fetchone()
            if existing_target is not None:
                existing_target_id = str(existing_target["id"])
                if existing_target_id != target_asset_id:
                    raise sqlite3.IntegrityError(
                        "Existing board asset does not use the stable target-owner identity."
                    )
            else:
                identity_collision = conn.execute(
                    "SELECT 1 FROM board_assets WHERE id = ? LIMIT 1",
                    (target_asset_id,),
                ).fetchone()
                if identity_collision is not None:
                    raise sqlite3.IntegrityError("Stable board asset identity collision.")
                self._copy_board_asset_for_owner(
                    conn,
                    old_asset_id=old_asset_id,
                    new_asset_id=target_asset_id,
                    owner_user_id=user_id,
                )

            self._rewrite_claimed_board_asset_references(
                conn,
                owner_user_id=guest_user_id,
                old_asset_id=old_asset_id,
                new_asset_id=target_asset_id,
            )
            self._claim_guest_board_asset_refs(
                conn,
                guest_user_id=guest_user_id,
                user_id=user_id,
                old_asset_id=old_asset_id,
                new_asset_id=target_asset_id,
            )
            conn.execute(
                "DELETE FROM board_assets WHERE id = ? AND owner_user_id = ?",
                (old_asset_id, guest_user_id),
            )

    @staticmethod
    def _copy_board_asset_for_owner(
        conn: sqlite3.Connection,
        *,
        old_asset_id: str,
        new_asset_id: str,
        owner_user_id: str,
    ) -> None:
        columns = [
            str(row["name"])
            for row in conn.execute("PRAGMA table_xinfo(board_assets)").fetchall()
            if int(row["hidden"]) == 0
        ]
        quoted_columns = ", ".join(_quoted_identifier(column) for column in columns)
        select_expressions: list[str] = []
        replacement_values: list[str] = []
        for column in columns:
            if column == "id":
                select_expressions.append("?")
                replacement_values.append(new_asset_id)
            elif column == "owner_user_id":
                select_expressions.append("?")
                replacement_values.append(owner_user_id)
            else:
                select_expressions.append(_quoted_identifier(column))
        select_values = ", ".join(select_expressions)
        conn.execute(
            f"""
            INSERT INTO board_assets({quoted_columns})
            SELECT {select_values}
            FROM board_assets
            WHERE id = ?
            """,
            (*replacement_values, old_asset_id),
        )

    @staticmethod
    def _claim_guest_board_asset_refs(
        conn: sqlite3.Connection,
        *,
        guest_user_id: str,
        user_id: str,
        old_asset_id: str,
        new_asset_id: str,
    ) -> None:
        if not _table_exists(conn, "board_asset_refs"):
            return
        required_columns = {
            "id",
            "asset_id",
            "owner_user_id",
            "lesson_id",
            "document_id",
            "source_visual_id",
        }
        if not required_columns.issubset(_table_columns(conn, "board_asset_refs")):
            return
        references = conn.execute(
            """
            SELECT id, lesson_id, document_id, source_visual_id
            FROM board_asset_refs
            WHERE asset_id = ? AND owner_user_id = ?
            ORDER BY id
            """,
            (old_asset_id, guest_user_id),
        ).fetchall()
        for reference in references:
            existing = conn.execute(
                """
                SELECT id
                FROM board_asset_refs
                WHERE asset_id = ?
                  AND owner_user_id = ?
                  AND lesson_id = ?
                  AND document_id = ?
                  AND source_visual_id = ?
                LIMIT 1
                """,
                (
                    new_asset_id,
                    user_id,
                    reference["lesson_id"],
                    reference["document_id"],
                    reference["source_visual_id"],
                ),
            ).fetchone()
            reference_id = str(reference["id"])
            if existing is not None:
                conn.execute("DELETE FROM board_asset_refs WHERE id = ?", (reference_id,))
                continue
            target_reference_id = stable_board_asset_reference_id(
                asset_id=new_asset_id,
                owner_user_id=user_id,
                lesson_id=str(reference["lesson_id"]),
                document_id=str(reference["document_id"]),
                source_visual_id=str(reference["source_visual_id"]),
            )
            conn.execute(
                """
                UPDATE board_asset_refs
                SET id = ?, asset_id = ?, owner_user_id = ?
                WHERE id = ?
                """,
                (target_reference_id, new_asset_id, user_id, reference_id),
            )

    @staticmethod
    def _rewrite_claimed_board_asset_references(
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        old_asset_id: str,
        new_asset_id: str,
    ) -> None:
        if _table_exists(conn, "lessons") and _table_exists(conn, "course_packages"):
            lesson_columns = {
                "board_content_json",
                "board_content_html",
                "board_content_text",
            }
            if lesson_columns.issubset(_table_columns(conn, "lessons")):
                lessons = conn.execute(
                    """
                    SELECT lessons.id, lessons.board_content_json,
                           lessons.board_content_html, lessons.board_content_text
                    FROM lessons
                    JOIN course_packages ON course_packages.id = lessons.package_id
                    WHERE course_packages.owner_user_id = ?
                    """,
                    (owner_user_id,),
                ).fetchall()
                for lesson in lessons:
                    rewritten = (
                        rewrite_board_asset_json(
                            str(lesson["board_content_json"]),
                            old_asset_id=old_asset_id,
                            new_asset_id=new_asset_id,
                        ),
                        rewrite_board_asset_html(
                            str(lesson["board_content_html"]),
                            old_asset_id=old_asset_id,
                            new_asset_id=new_asset_id,
                        ),
                        rewrite_board_asset_markdown(
                            str(lesson["board_content_text"]),
                            old_asset_id=old_asset_id,
                            new_asset_id=new_asset_id,
                        ),
                    )
                    original = (
                        str(lesson["board_content_json"]),
                        str(lesson["board_content_html"]),
                        str(lesson["board_content_text"]),
                    )
                    if rewritten != original:
                        conn.execute(
                            """
                            UPDATE lessons
                            SET board_content_json = ?, board_content_html = ?, board_content_text = ?
                            WHERE id = ?
                            """,
                            (*rewritten, lesson["id"]),
                        )

        if not (
            _table_exists(conn, "lesson_commits")
            and _table_exists(conn, "lessons")
            and _table_exists(conn, "course_packages")
        ):
            return
        commit_columns = {
            "operations_json",
            "snapshot_content_json",
            "snapshot_content_html",
            "snapshot_content_text",
            "metadata_json",
        }
        if not commit_columns.issubset(_table_columns(conn, "lesson_commits")):
            return
        commits = conn.execute(
            """
            SELECT lesson_commits.id, lesson_commits.operations_json,
                   lesson_commits.snapshot_content_json,
                   lesson_commits.snapshot_content_html,
                   lesson_commits.snapshot_content_text,
                   lesson_commits.metadata_json
            FROM lesson_commits
            JOIN lessons ON lessons.id = lesson_commits.lesson_id
            JOIN course_packages ON course_packages.id = lessons.package_id
            WHERE course_packages.owner_user_id = ?
            """,
            (owner_user_id,),
        ).fetchall()
        for commit in commits:
            rewritten = (
                rewrite_board_asset_json(
                    str(commit["operations_json"]),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_json(
                    str(commit["snapshot_content_json"]),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_html(
                    str(commit["snapshot_content_html"]),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_markdown(
                    str(commit["snapshot_content_text"]),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_json(
                    str(commit["metadata_json"]),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
            )
            original = (
                str(commit["operations_json"]),
                str(commit["snapshot_content_json"]),
                str(commit["snapshot_content_html"]),
                str(commit["snapshot_content_text"]),
                str(commit["metadata_json"]),
            )
            if rewritten != original:
                conn.execute(
                    """
                    UPDATE lesson_commits
                    SET operations_json = ?, snapshot_content_json = ?,
                        snapshot_content_html = ?, snapshot_content_text = ?, metadata_json = ?
                    WHERE id = ?
                    """,
                    (*rewritten, commit["id"]),
                )

    def identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM auth_identities
            WHERE user_id = ?
            ORDER BY
                CASE provider
                    WHEN 'email' THEN 0
                    WHEN 'phone' THEN 1
                    WHEN 'google' THEN 2
                    WHEN 'apple' THEN 3
                    WHEN 'github' THEN 4
                    WHEN 'microsoft' THEN 5
                    WHEN 'x' THEN 6
                    ELSE 9
                END,
                created_at
            """,
            (user_id,),
        ).fetchall()

    def create_session(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        user_id: str,
        now: str,
        expires_at: str,
    ) -> None:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_hash, user_id, now, now, expires_at),
        )

    def delete_session(self, conn: sqlite3.Connection, token_hash: str) -> bool:
        formal = conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        guest = conn.execute("DELETE FROM auth_guest_sessions WHERE token_hash = ?", (token_hash,))
        return formal.rowcount > 0 or guest.rowcount > 0

    def delete_user_sessions(self, conn: sqlite3.Connection, user_id: str) -> int:
        cursor = conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return cursor.rowcount

    def update_password(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        password_salt: str,
        password_hash: str,
    ) -> None:
        conn.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (password_salt, password_hash, user_id),
        )

    def mark_email_verified(self, conn: sqlite3.Connection, *, user_id: str, now: str) -> None:
        conn.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
            (now, user_id),
        )

    def create_lifecycle_event(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: str,
        user_id: str,
        kind: str,
        metadata_json: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO account_lifecycle_events(id, user_id, kind, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, user_id, kind, metadata_json, created_at),
        )

    def export_account_data(self, conn: sqlite3.Connection, *, user_id: str, email: str) -> dict[str, list[dict[str, object]]]:
        """Export rows directly scoped to an account without credential material."""
        exported: dict[str, list[dict[str, object]]] = {}
        sensitive_columns = {
            "password_salt",
            "password_hash",
            "token_hash",
            "code_salt",
            "code_hash",
            "code_verifier",
        }
        ownership_columns = (
            "owner_user_id",
            "user_id",
            "author_user_id",
            "creator_user_id",
            "contributor_user_id",
            "source_owner_user_id",
            "accepted_by_user_id",
        )
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for table_row in tables:
            table = str(table_row["name"])
            if table.startswith("fts_") or table.endswith(("_data", "_idx", "_docsize", "_config")):
                continue
            columns = _table_columns(conn, table)
            clauses = [f"{_quoted_identifier(column)} = ?" for column in ownership_columns if column in columns]
            params: list[str] = [user_id] * len(clauses)
            if table == "users" and "id" in columns:
                clauses.append('"id" = ?')
                params.append(user_id)
            if table == "auth_email_challenges" and "email" in columns:
                clauses.append('"email" = ?')
                params.append(email)
            if table == "workspace_settings" and "key" in columns:
                clauses.append('"key" LIKE ?')
                params.append(f"%:{user_id}")
            if not clauses:
                continue
            quoted_table = _quoted_identifier(table)
            rows = conn.execute(
                f"SELECT * FROM {quoted_table} WHERE " + " OR ".join(clauses),
                params,
            ).fetchall()
            if not rows:
                continue
            exported[table] = [
                {
                    key: (
                        {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
                        if isinstance(value, bytes)
                        else value
                    )
                    for key, value in dict(row).items()
                    if key not in sensitive_columns
                }
                for row in rows
            ]
        return exported

    def delete_account_data(self, conn: sqlite3.Connection, *, user_id: str, email: str) -> dict[str, object]:
        """Erase private account data and anonymize records that must keep shared or financial integrity."""
        pseudonym = f"deleted_{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:24]}"
        deleted_counts: dict[str, int] = {}
        anonymized_counts: dict[str, int] = {}

        financial_tables = {
            "credit_ledger",
            "paypal_orders",
            "credit_reservations",
            "openrouter_limit_grants",
        }
        for table in financial_tables:
            if not _table_exists(conn, table) or "user_id" not in _table_columns(conn, table):
                continue
            cursor = conn.execute(
                f"UPDATE {_quoted_identifier(table)} SET user_id = ? WHERE user_id = ?",
                (pseudonym, user_id),
            )
            if cursor.rowcount:
                anonymized_counts[table] = cursor.rowcount

        for table in ("credit_wallets", "openrouter_user_keys"):
            if not _table_exists(conn, table) or "user_id" not in _table_columns(conn, table):
                continue
            cursor = conn.execute(f"DELETE FROM {_quoted_identifier(table)} WHERE user_id = ?", (user_id,))
            if cursor.rowcount:
                deleted_counts[table] = cursor.rowcount

        shared_actor_columns = {
            "community_spaces": ("creator_user_id",),
            "community_posts": ("author_user_id", "author_display_name"),
            "community_comments": ("author_user_id", "author_display_name"),
            "community_answers": ("author_user_id", "author_display_name"),
            "community_accepted_answers": ("accepted_by_user_id",),
        }
        for table, actor_columns in shared_actor_columns.items():
            if not _table_exists(conn, table):
                continue
            columns = _table_columns(conn, table)
            id_column = actor_columns[0]
            if id_column not in columns:
                continue
            assignments = [f"{_quoted_identifier(id_column)} = ?"]
            params: list[str] = [pseudonym]
            for display_column in actor_columns[1:]:
                if display_column in columns:
                    assignments.append(f"{_quoted_identifier(display_column)} = ?")
                    params.append("已注销用户")
            params.append(user_id)
            cursor = conn.execute(
                f"UPDATE {_quoted_identifier(table)} SET {', '.join(assignments)} WHERE {_quoted_identifier(id_column)} = ?",
                params,
            )
            if cursor.rowcount:
                anonymized_counts[table] = cursor.rowcount

        for table in ("community_reactions", "community_answer_votes", "community_follows"):
            if not _table_exists(conn, table) or "user_id" not in _table_columns(conn, table):
                continue
            cursor = conn.execute(f"DELETE FROM {_quoted_identifier(table)} WHERE user_id = ?", (user_id,))
            if cursor.rowcount:
                deleted_counts[table] = cursor.rowcount

        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        excluded = {
            "users",
            "auth_sessions",
            "auth_identities",
            "account_lifecycle_events",
            *financial_tables,
            *shared_actor_columns,
            "credit_wallets",
            "openrouter_user_keys",
            "community_reactions",
            "community_answer_votes",
            "community_follows",
        }
        for table_row in tables:
            table = str(table_row["name"])
            if table in excluded or table.startswith("fts_"):
                continue
            columns = _table_columns(conn, table)
            if "owner_user_id" not in columns:
                continue
            cursor = conn.execute(
                f"DELETE FROM {_quoted_identifier(table)} WHERE owner_user_id = ?",
                (user_id,),
            )
            if cursor.rowcount:
                deleted_counts[table] = cursor.rowcount

        if _table_exists(conn, "lesson_contributions"):
            columns = _table_columns(conn, "lesson_contributions")
            for column in ("source_owner_user_id", "contributor_user_id"):
                if column not in columns:
                    continue
                cursor = conn.execute(
                    f"UPDATE lesson_contributions SET {_quoted_identifier(column)} = ? WHERE {_quoted_identifier(column)} = ?",
                    (pseudonym, user_id),
                )
                if cursor.rowcount:
                    anonymized_counts[f"lesson_contributions.{column}"] = cursor.rowcount

        if _table_exists(conn, "workspace_settings"):
            cursor = conn.execute("DELETE FROM workspace_settings WHERE key LIKE ?", (f"%:{user_id}",))
            if cursor.rowcount:
                deleted_counts["workspace_settings"] = cursor.rowcount
        conn.execute("DELETE FROM auth_email_challenges WHERE email = ?", (email,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return {
            "pseudonym": pseudonym,
            "deleted_counts": deleted_counts,
            "anonymized_counts": anonymized_counts,
        }

    def latest_email_challenge(
        self,
        conn: sqlite3.Connection,
        *,
        email: str,
        purpose: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM auth_email_challenges WHERE email = ? AND purpose = ? ORDER BY created_at DESC LIMIT 1",
            (email, purpose),
        ).fetchone()

    def create_email_challenge(
        self,
        conn: sqlite3.Connection,
        *,
        challenge_id: str,
        email: str,
        purpose: str,
        code_salt: str,
        code_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_email_challenges(id, email, purpose, code_salt, code_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (challenge_id, email, purpose, code_salt, code_hash, created_at, expires_at),
        )

    def find_email_challenge(self, conn: sqlite3.Connection, challenge_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM auth_email_challenges WHERE id = ?",
            (challenge_id,),
        ).fetchone()

    def increment_email_challenge_attempts(self, conn: sqlite3.Connection, challenge_id: str) -> None:
        conn.execute(
            "UPDATE auth_email_challenges SET attempts = attempts + 1 WHERE id = ?",
            (challenge_id,),
        )

    def consume_email_challenge(self, conn: sqlite3.Connection, *, challenge_id: str, now: str) -> bool:
        cursor = conn.execute(
            "UPDATE auth_email_challenges SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
            (now, challenge_id),
        )
        return cursor.rowcount == 1

    def delete_email_challenge(self, conn: sqlite3.Connection, challenge_id: str) -> None:
        conn.execute("DELETE FROM auth_email_challenges WHERE id = ?", (challenge_id,))

    def consume_oauth_state(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        state: str,
    ) -> sqlite3.Row | None:
        row = conn.execute(
            "SELECT * FROM auth_oauth_states WHERE state = ? AND provider = ?",
            (state, provider),
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM auth_oauth_states WHERE state = ?", (state,))
        return row

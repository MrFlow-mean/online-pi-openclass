from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.models import (
    BoardDocument,
    BoardSegmentKind,
    BranchRef,
    CommitRecord,
    CourseGraphEdge,
    CoursePackage,
    DocumentSegmentSearchResult,
    Lesson,
    LessonContribution,
    LessonContributionEvent,
    LessonContributionRevision,
    LessonHistoryGraph,
    LessonMergeSession,
    LessonRuntimeSnapshot,
    LibraryChapter,
    PublicationReview,
    PublicCourseSearchResult,
    PublishedCoursePackageVersion,
    PublishedLessonVersion,
    ResourceLibraryItem,
    ResourcePageStructure,
    ResourceSourceUnit,
    SourceIngestionJob,
    WorkspaceState,
)
from app.services.document_segment_store import DocumentSegmentStore
from app.services.published_courses import (
    capture_lesson_version,
    published_lesson_copy,
    published_package_copy,
    upload_package_version,
)
from app.services.rich_document import upgrade_markdown_like_document

SCHEMA_VERSION = 16
_CHAT_INPUT_EVENT_RETENTION_PER_USER = 512

ChatInputEventClaimStatus = Literal["owned", "waiting", "completed"]


@dataclass(frozen=True)
class LessonRuntimeState:
    lesson_id: str
    current_branch: str
    head_commit_id: str
    runtime_snapshot: LessonRuntimeSnapshot
    commit_metadata: dict[str, Any]


@dataclass(frozen=True)
class LessonMutationResult:
    package_id: str
    active_package_id: str | None
    active_lesson_id: str | None
    open_lesson_ids: list[str]
    workspace_tab_order: list[str]
    workspace_revision: int
    created_lesson: Lesson | None = None
    deleted_lesson_id: str | None = None
    graph_edge: CourseGraphEdge | None = None


@dataclass(frozen=True)
class LessonDocumentContext:
    package_id: str
    lesson: Lesson
    workspace_revision: int


def _active_package_setting_key(owner_user_id: str | None) -> str:
    if owner_user_id:
        return f"active_package_id:{owner_user_id}"
    return "active_package_id"


def _workspace_revision_setting_key(owner_user_id: str) -> str:
    return f"workspace_revision:{owner_user_id}"


def _chat_input_event_user_prefix(owner_user_id: str) -> str:
    user_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    return f"chat_input_event:{user_hash}:"


def _chat_input_event_setting_key(
    owner_user_id: str,
    session_id: str,
    input_event_id: str,
) -> str:
    identity = json.dumps(
        [owner_user_id, session_id, input_event_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    event_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{_chat_input_event_user_prefix(owner_user_id)}{event_hash}"


def _escaped_like_pattern(value: str) -> str:
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _course_search_result(row: sqlite3.Row) -> PublicCourseSearchResult:
    published = (
        _loads(row["published_version_json"], {})
        if "published_version_json" in row.keys()
        else {}
    )
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tags in (row["tags_group"] or "").split(chr(31)):
        try:
            values = json.loads(raw_tags)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                tags.append(value)
    title = row["title"]
    summary = row["summary"]
    lesson_count = int(row["lesson_count"])
    updated_at = row["updated_at"]
    if isinstance(published, dict) and published:
        title = str(published.get("title") or title)
        summary = str(published.get("summary") or summary)
        updated_at = published.get("published_at") or updated_at
        if row["kind"] == "lesson":
            tags = [value for value in published.get("tags", []) if isinstance(value, str)][:8]
            lesson_count = 1
        else:
            published_lessons = published.get("lessons", [])
            if isinstance(published_lessons, list):
                lesson_count = len(published_lessons)
                seen.clear()
                tags = []
                for lesson in published_lessons:
                    if not isinstance(lesson, dict):
                        continue
                    for value in lesson.get("tags", []):
                        if isinstance(value, str) and value and value not in seen:
                            seen.add(value)
                            tags.append(value)
    return PublicCourseSearchResult(
        id=row["id"],
        kind=row["kind"],
        owner_display_name=row["owner_display_name"],
        owner_avatar_url=row["owner_avatar_url"],
        title=title,
        summary=summary,
        tags=tags[:8],
        lesson_count=lesson_count,
        updated_at=updated_at,
        visibility=row["visibility"],
        star_count=int(row["star_count"]) if "star_count" in row.keys() else 0,
    )


class SqliteCourseStore:
    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        self.path = path
        self.legacy_json_path = legacy_json_path
        self._lock = threading.RLock()
        self._document_segments = DocumentSegmentStore()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def load(self) -> WorkspaceState:
        with self._lock:
            with self._connect() as conn:
                if self._has_any_packages(conn):
                    return self._read_workspace(conn)

            legacy_workspace = self._load_legacy_workspace()
            workspace = legacy_workspace or build_initial_workspace_state()
            self.save(workspace)
            if legacy_workspace is not None:
                self._archive_legacy_json()
            return workspace

    def save(self, workspace: WorkspaceState) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    self._replace_workspace(conn, workspace)

    def load_for_user(self, owner_user_id: str) -> WorkspaceState:
        workspace, _ = self.load_for_user_with_revision(owner_user_id)
        return workspace

    def load_public_package(self, package_id: str) -> CoursePackage | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM course_packages
                    WHERE id = ?
                      AND visibility = 'public'
                      AND published_version_json IS NOT NULL
                      AND sort_order > 0
                    """,
                    (package_id,),
                ).fetchone()
                if row is None:
                    return None
                return published_package_copy(
                    self._read_package(conn, row, owner_user_id=row["owner_user_id"])
                )

    def load_public_lesson(self, lesson_id: str) -> Lesson | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT lessons.*
                    FROM lessons
                    WHERE lessons.id = ?
                    """,
                    (lesson_id,),
                ).fetchone()
                if row is None:
                    return None
                package_row = conn.execute(
                    "SELECT * FROM course_packages WHERE id = ?",
                    (row["package_id"],),
                ).fetchone()
                if package_row is None:
                    return None
                if package_row["sort_order"] == 0:
                    if row["visibility"] != "public" or not row["published_version_json"]:
                        return None
                    return published_lesson_copy(self._read_lesson(conn, row))
                if package_row["visibility"] != "public" or not package_row["published_version_json"]:
                    return None
                package = published_package_copy(
                    self._read_package(conn, package_row, owner_user_id=package_row["owner_user_id"])
                )
                if package is None:
                    return None
                return next((lesson for lesson in package.lessons if lesson.id == lesson_id), None)

    def search_public_courses(
        self,
        query: str,
        *,
        exclude_owner_user_id: str,
        limit: int = 30,
    ) -> list[PublicCourseSearchResult]:
        terms = [term.casefold() for term in query.split() if term][:12]
        if not terms:
            return []

        with self._lock:
            with self._connect() as conn:
                user_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                has_user_profiles = {"id", "display_name", "avatar_url"}.issubset(user_columns)
                user_join = (
                    "LEFT JOIN users ON users.id = course_packages.owner_user_id"
                    if has_user_profiles
                    else ""
                )
                owner_name = (
                    "COALESCE(NULLIF(users.display_name, ''), 'OpenClass 用户')"
                    if has_user_profiles
                    else "'OpenClass 用户'"
                )
                owner_avatar = "users.avatar_url" if has_user_profiles else "NULL"

                package_term_clauses: list[str] = []
                package_params: list[Any] = [exclude_owner_user_id]
                lesson_term_clauses: list[str] = []
                lesson_params: list[Any] = [exclude_owner_user_id]
                for term in terms:
                    pattern = _escaped_like_pattern(term)
                    package_term_clauses.append(
                        f"""
                        (
                            LOWER(course_packages.published_version_json) LIKE ? ESCAPE '!'
                            OR LOWER({owner_name}) LIKE ? ESCAPE '!'
                        )
                        """
                    )
                    package_params.extend([pattern] * 2)
                    lesson_term_clauses.append(
                        f"""
                        (
                            LOWER(lessons.published_version_json) LIKE ? ESCAPE '!'
                            OR LOWER({owner_name}) LIKE ? ESCAPE '!'
                        )
                        """
                    )
                    lesson_params.extend([pattern] * 2)

                package_params.append(limit)
                package_rows = conn.execute(
                    f"""
                    SELECT
                        course_packages.id,
                        'package' AS kind,
                        {owner_name} AS owner_display_name,
                        {owner_avatar} AS owner_avatar_url,
                        COALESCE(json_extract(course_packages.published_version_json, '$.title'), course_packages.title) AS title,
                        COALESCE(json_extract(course_packages.published_version_json, '$.summary'), course_packages.summary) AS summary,
                        json_array_length(json_extract(course_packages.published_version_json, '$.lessons')) AS lesson_count,
                        json_extract(course_packages.published_version_json, '$.published_at') AS updated_at,
                        GROUP_CONCAT(lessons.tags_json, CHAR(31)) AS tags_group,
                        course_packages.visibility,
                        course_packages.published_version_json,
                        (
                            SELECT COUNT(*)
                            FROM public_course_stars
                            WHERE course_kind = 'package'
                              AND course_id = course_packages.id
                        ) AS star_count
                    FROM course_packages
                    {user_join}
                    LEFT JOIN lessons ON lessons.package_id = course_packages.id
                    WHERE course_packages.visibility = 'public'
                      AND course_packages.published_version_json IS NOT NULL
                      AND course_packages.sort_order > 0
                      AND (
                          course_packages.owner_user_id IS NULL
                          OR course_packages.owner_user_id != ?
                      )
                      AND {" AND ".join(package_term_clauses)}
                    GROUP BY
                        course_packages.id,
                        owner_display_name,
                        owner_avatar_url,
                        course_packages.title,
                        course_packages.summary
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    package_params,
                ).fetchall()

                lesson_params.append(limit)
                lesson_rows = conn.execute(
                    f"""
                    SELECT
                        lessons.id,
                        'lesson' AS kind,
                        {owner_name} AS owner_display_name,
                        {owner_avatar} AS owner_avatar_url,
                        COALESCE(json_extract(lessons.published_version_json, '$.title'), lessons.title) AS title,
                        COALESCE(json_extract(lessons.published_version_json, '$.summary'), lessons.summary) AS summary,
                        1 AS lesson_count,
                        json_extract(lessons.published_version_json, '$.published_at') AS updated_at,
                        lessons.tags_json AS tags_group,
                        lessons.visibility,
                        lessons.published_version_json,
                        (
                            SELECT COUNT(*)
                            FROM public_course_stars
                            WHERE course_kind = 'lesson'
                              AND course_id = lessons.id
                        ) AS star_count
                    FROM lessons
                    JOIN course_packages ON course_packages.id = lessons.package_id
                    {user_join}
                    WHERE course_packages.sort_order = 0
                      AND lessons.visibility = 'public'
                      AND lessons.published_version_json IS NOT NULL
                      AND (
                          course_packages.owner_user_id IS NULL
                          OR course_packages.owner_user_id != ?
                      )
                      AND {" AND ".join(lesson_term_clauses)}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    lesson_params,
                ).fetchall()

                results = [
                    _course_search_result(row)
                    for row in [*package_rows, *lesson_rows]
                ]
                starred_keys = {
                    (row["course_kind"], row["course_id"])
                    for row in conn.execute(
                        """
                        SELECT course_kind, course_id
                        FROM public_course_stars
                        WHERE owner_user_id = ?
                        """,
                        (exclude_owner_user_id,),
                    ).fetchall()
                }
                results = [
                    item.model_copy(update={"is_starred": (item.kind, item.id) in starred_keys})
                    for item in results
                ]
                results.sort(key=lambda item: item.updated_at or "", reverse=True)
                return results[:limit]

    def list_public_courses(
        self,
        *,
        exclude_owner_user_id: str | None,
        sort: str,
        limit: int = 50,
    ) -> list[PublicCourseSearchResult]:
        if sort not in {"popular", "recent"}:
            raise ValueError("Unsupported public course sort")

        with self._lock:
            with self._connect() as conn:
                user_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                has_user_profiles = {"id", "display_name", "avatar_url"}.issubset(user_columns)
                user_join = (
                    "LEFT JOIN users ON users.id = course_packages.owner_user_id"
                    if has_user_profiles
                    else ""
                )
                owner_name = (
                    "COALESCE(NULLIF(users.display_name, ''), 'OpenClass 用户')"
                    if has_user_profiles
                    else "'OpenClass 用户'"
                )
                owner_avatar = "users.avatar_url" if has_user_profiles else "NULL"
                order_clause = (
                    "star_count DESC, updated_at DESC, title COLLATE NOCASE"
                    if sort == "popular"
                    else "updated_at DESC, star_count DESC, title COLLATE NOCASE"
                )
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM (
                        SELECT
                            course_packages.id,
                            'package' AS kind,
                            {owner_name} AS owner_display_name,
                            {owner_avatar} AS owner_avatar_url,
                            COALESCE(json_extract(course_packages.published_version_json, '$.title'), course_packages.title) AS title,
                            COALESCE(json_extract(course_packages.published_version_json, '$.summary'), course_packages.summary) AS summary,
                            json_array_length(json_extract(course_packages.published_version_json, '$.lessons')) AS lesson_count,
                            json_extract(course_packages.published_version_json, '$.published_at') AS updated_at,
                            GROUP_CONCAT(lessons.tags_json, CHAR(31)) AS tags_group,
                            course_packages.visibility,
                            course_packages.published_version_json,
                            (
                                SELECT COUNT(*)
                                FROM public_course_stars
                                WHERE course_kind = 'package'
                                  AND course_id = course_packages.id
                            ) AS star_count
                        FROM course_packages
                        {user_join}
                        LEFT JOIN lessons ON lessons.package_id = course_packages.id
                        WHERE course_packages.visibility = 'public'
                          AND course_packages.published_version_json IS NOT NULL
                          AND course_packages.sort_order > 0
                          AND (
                              ? IS NULL
                              OR course_packages.owner_user_id IS NULL
                              OR course_packages.owner_user_id != ?
                          )
                        GROUP BY
                            course_packages.id,
                            owner_display_name,
                            owner_avatar_url,
                            course_packages.title,
                            course_packages.summary

                        UNION ALL

                        SELECT
                            lessons.id,
                            'lesson' AS kind,
                            {owner_name} AS owner_display_name,
                            {owner_avatar} AS owner_avatar_url,
                            COALESCE(json_extract(lessons.published_version_json, '$.title'), lessons.title) AS title,
                            COALESCE(json_extract(lessons.published_version_json, '$.summary'), lessons.summary) AS summary,
                            1 AS lesson_count,
                            json_extract(lessons.published_version_json, '$.published_at') AS updated_at,
                            lessons.tags_json AS tags_group,
                            lessons.visibility,
                            lessons.published_version_json,
                            (
                                SELECT COUNT(*)
                                FROM public_course_stars
                                WHERE course_kind = 'lesson'
                                  AND course_id = lessons.id
                            ) AS star_count
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        {user_join}
                        WHERE course_packages.sort_order = 0
                          AND lessons.visibility = 'public'
                          AND lessons.published_version_json IS NOT NULL
                          AND (
                              ? IS NULL
                              OR course_packages.owner_user_id IS NULL
                              OR course_packages.owner_user_id != ?
                          )
                    )
                    ORDER BY {order_clause}
                    LIMIT ?
                    """,
                    (
                        exclude_owner_user_id,
                        exclude_owner_user_id,
                        exclude_owner_user_id,
                        exclude_owner_user_id,
                        limit,
                    ),
                ).fetchall()
                starred_keys = (
                    {
                        (row["course_kind"], row["course_id"])
                        for row in conn.execute(
                            """
                            SELECT course_kind, course_id
                            FROM public_course_stars
                            WHERE owner_user_id = ?
                            """,
                            (exclude_owner_user_id,),
                        ).fetchall()
                    }
                    if exclude_owner_user_id
                    else set()
                )
                return [
                    _course_search_result(row).model_copy(
                        update={
                            "is_starred": (row["kind"], row["id"]) in starred_keys,
                        }
                    )
                    for row in rows
                ]

    def set_public_course_star(
        self,
        *,
        owner_user_id: str,
        course_kind: str,
        course_id: str,
        is_starred: bool,
    ) -> None:
        if course_kind not in {"lesson", "package"}:
            raise ValueError("Unsupported public course kind")

        with self._lock:
            with self._connect() as conn:
                if course_kind == "package":
                    target = conn.execute(
                        """
                        SELECT 1
                        FROM course_packages
                        WHERE id = ?
                          AND visibility = 'public'
                          AND published_version_json IS NOT NULL
                          AND sort_order > 0
                          AND (owner_user_id IS NULL OR owner_user_id != ?)
                        """,
                        (course_id, owner_user_id),
                    ).fetchone()
                else:
                    target = conn.execute(
                        """
                        SELECT 1
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        WHERE lessons.id = ?
                          AND course_packages.sort_order = 0
                          AND lessons.visibility = 'public'
                          AND lessons.published_version_json IS NOT NULL
                          AND (
                              course_packages.owner_user_id IS NULL
                              OR course_packages.owner_user_id != ?
                          )
                        """,
                        (course_id, owner_user_id),
                    ).fetchone()
                if target is None:
                    raise ValueError("Public course not found")

                with conn:
                    if is_starred:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO public_course_stars(
                                owner_user_id,
                                course_kind,
                                course_id,
                                created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (owner_user_id, course_kind, course_id, datetime.now().astimezone().isoformat()),
                        )
                    else:
                        conn.execute(
                            """
                            DELETE FROM public_course_stars
                            WHERE owner_user_id = ? AND course_kind = ? AND course_id = ?
                            """,
                            (owner_user_id, course_kind, course_id),
                        )

    def list_starred_public_courses(
        self,
        *,
        owner_user_id: str,
        limit: int = 100,
    ) -> list[PublicCourseSearchResult]:
        with self._lock:
            with self._connect() as conn:
                user_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                has_user_profiles = {"id", "display_name", "avatar_url"}.issubset(user_columns)
                user_join = (
                    "LEFT JOIN users ON users.id = course_packages.owner_user_id"
                    if has_user_profiles
                    else ""
                )
                owner_name = (
                    "COALESCE(NULLIF(users.display_name, ''), 'OpenClass 用户')"
                    if has_user_profiles
                    else "'OpenClass 用户'"
                )
                owner_avatar = "users.avatar_url" if has_user_profiles else "NULL"
                rows = conn.execute(
                    f"""
                    SELECT * FROM (
                        SELECT
                            course_packages.id,
                            'package' AS kind,
                            {owner_name} AS owner_display_name,
                            {owner_avatar} AS owner_avatar_url,
                            COALESCE(json_extract(course_packages.published_version_json, '$.title'), course_packages.title) AS title,
                            COALESCE(json_extract(course_packages.published_version_json, '$.summary'), course_packages.summary) AS summary,
                            json_array_length(json_extract(course_packages.published_version_json, '$.lessons')) AS lesson_count,
                            json_extract(course_packages.published_version_json, '$.published_at') AS updated_at,
                            GROUP_CONCAT(lessons.tags_json, CHAR(31)) AS tags_group,
                            course_packages.visibility,
                            course_packages.published_version_json,
                            stars.created_at AS starred_at
                        FROM public_course_stars AS stars
                        JOIN course_packages
                          ON stars.course_kind = 'package'
                         AND stars.course_id = course_packages.id
                        {user_join}
                        LEFT JOIN lessons ON lessons.package_id = course_packages.id
                        WHERE stars.owner_user_id = ?
                          AND course_packages.visibility = 'public'
                          AND course_packages.published_version_json IS NOT NULL
                          AND course_packages.sort_order > 0
                        GROUP BY course_packages.id, stars.created_at

                        UNION ALL

                        SELECT
                            lessons.id,
                            'lesson' AS kind,
                            {owner_name} AS owner_display_name,
                            {owner_avatar} AS owner_avatar_url,
                            COALESCE(json_extract(lessons.published_version_json, '$.title'), lessons.title) AS title,
                            COALESCE(json_extract(lessons.published_version_json, '$.summary'), lessons.summary) AS summary,
                            1 AS lesson_count,
                            json_extract(lessons.published_version_json, '$.published_at') AS updated_at,
                            lessons.tags_json AS tags_group,
                            lessons.visibility,
                            lessons.published_version_json,
                            stars.created_at AS starred_at
                        FROM public_course_stars AS stars
                        JOIN lessons
                          ON stars.course_kind = 'lesson'
                         AND stars.course_id = lessons.id
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        {user_join}
                        WHERE stars.owner_user_id = ?
                          AND course_packages.sort_order = 0
                          AND lessons.visibility = 'public'
                          AND lessons.published_version_json IS NOT NULL
                    )
                    ORDER BY starred_at DESC
                    LIMIT ?
                    """,
                    (owner_user_id, owner_user_id, limit),
                ).fetchall()
                return [
                    _course_search_result(row).model_copy(update={"is_starred": True})
                    for row in rows
                ]

    def search_owned_courses(
        self,
        query: str,
        *,
        owner_user_id: str,
        limit: int = 30,
    ) -> list[PublicCourseSearchResult]:
        terms = [term.casefold() for term in query.split() if term][:12]
        if not terms:
            return []

        with self._lock:
            with self._connect() as conn:
                user_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                has_user_profiles = {"id", "display_name", "avatar_url"}.issubset(user_columns)
                user_join = (
                    "LEFT JOIN users ON users.id = course_packages.owner_user_id"
                    if has_user_profiles
                    else ""
                )
                owner_name = (
                    "COALESCE(NULLIF(users.display_name, ''), 'OpenClass 用户')"
                    if has_user_profiles
                    else "'OpenClass 用户'"
                )
                owner_avatar = "users.avatar_url" if has_user_profiles else "NULL"

                package_term_clauses: list[str] = []
                package_params: list[Any] = [owner_user_id]
                lesson_term_clauses: list[str] = []
                lesson_params: list[Any] = [owner_user_id]
                for term in terms:
                    pattern = _escaped_like_pattern(term)
                    package_term_clauses.append(
                        """
                        (
                            LOWER(course_packages.title) LIKE ? ESCAPE '!'
                            OR LOWER(course_packages.summary) LIKE ? ESCAPE '!'
                            OR EXISTS (
                                SELECT 1
                                FROM lessons AS search_lessons
                                WHERE search_lessons.package_id = course_packages.id
                                  AND (
                                      LOWER(search_lessons.title) LIKE ? ESCAPE '!'
                                      OR LOWER(search_lessons.summary) LIKE ? ESCAPE '!'
                                      OR LOWER(search_lessons.tags_json) LIKE ? ESCAPE '!'
                                      OR LOWER(search_lessons.board_document_title) LIKE ? ESCAPE '!'
                                      OR LOWER(search_lessons.board_content_text) LIKE ? ESCAPE '!'
                                  )
                            )
                        )
                        """
                    )
                    package_params.extend([pattern] * 7)
                    lesson_term_clauses.append(
                        """
                        (
                            LOWER(lessons.title) LIKE ? ESCAPE '!'
                            OR LOWER(lessons.summary) LIKE ? ESCAPE '!'
                            OR LOWER(lessons.tags_json) LIKE ? ESCAPE '!'
                            OR LOWER(lessons.board_document_title) LIKE ? ESCAPE '!'
                            OR LOWER(lessons.board_content_text) LIKE ? ESCAPE '!'
                        )
                        """
                    )
                    lesson_params.extend([pattern] * 5)

                package_params.append(limit)
                package_rows = conn.execute(
                    f"""
                    SELECT
                        course_packages.id,
                        'package' AS kind,
                        {owner_name} AS owner_display_name,
                        {owner_avatar} AS owner_avatar_url,
                        course_packages.title,
                        course_packages.summary,
                        COUNT(lessons.id) AS lesson_count,
                        MAX(lessons.updated_at) AS updated_at,
                        GROUP_CONCAT(lessons.tags_json, CHAR(31)) AS tags_group,
                        course_packages.visibility,
                        0 AS star_count
                    FROM course_packages
                    {user_join}
                    LEFT JOIN lessons ON lessons.package_id = course_packages.id
                    WHERE course_packages.owner_user_id = ?
                      AND course_packages.sort_order > 0
                      AND {" AND ".join(package_term_clauses)}
                    GROUP BY
                        course_packages.id,
                        owner_display_name,
                        owner_avatar_url,
                        course_packages.title,
                        course_packages.summary,
                        course_packages.visibility
                    ORDER BY COALESCE(MAX(lessons.updated_at), '') DESC
                    LIMIT ?
                    """,
                    package_params,
                ).fetchall()

                lesson_params.append(limit)
                lesson_rows = conn.execute(
                    f"""
                    SELECT
                        lessons.id,
                        'lesson' AS kind,
                        {owner_name} AS owner_display_name,
                        {owner_avatar} AS owner_avatar_url,
                        lessons.title,
                        lessons.summary,
                        1 AS lesson_count,
                        lessons.updated_at,
                        lessons.tags_json AS tags_group,
                        lessons.visibility,
                        0 AS star_count
                    FROM lessons
                    JOIN course_packages ON course_packages.id = lessons.package_id
                    {user_join}
                    WHERE course_packages.owner_user_id = ?
                      AND course_packages.sort_order = 0
                      AND {" AND ".join(lesson_term_clauses)}
                    ORDER BY lessons.updated_at DESC
                    LIMIT ?
                    """,
                    lesson_params,
                ).fetchall()

                results = [
                    _course_search_result(row)
                    for row in [*package_rows, *lesson_rows]
                ]
                results.sort(key=lambda item: item.updated_at or "", reverse=True)
                return results[:limit]

    def load_for_user_with_revision(self, owner_user_id: str) -> tuple[WorkspaceState, int]:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    if self._has_unowned_packages(conn) and self._is_registered_user(conn, owner_user_id):
                        self._claim_unowned_workspace(
                            conn,
                            self._legacy_workspace_owner_candidate(conn) or owner_user_id,
                        )
                    if not self._has_user_packages(conn, owner_user_id):
                        self._replace_workspace(
                            conn,
                            build_empty_account_workspace_state(),
                            owner_user_id=owner_user_id,
                        )
                    return (
                        self._read_workspace(conn, owner_user_id=owner_user_id),
                        self._workspace_revision(conn, owner_user_id),
                    )

    def load_lesson_runtime_state_for_user(
        self,
        owner_user_id: str,
        lesson_id: str,
    ) -> LessonRuntimeState | None:
        """Read the active runtime snapshot without selecting the board document."""

        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT lessons.id, lessons.current_branch,
                       lesson_branches.head_commit_id,
                       lesson_commits.runtime_snapshot_json,
                       lesson_commits.metadata_json
                FROM lessons
                JOIN course_packages
                  ON course_packages.id = lessons.package_id
                JOIN lesson_branches
                  ON lesson_branches.lesson_id = lessons.id
                 AND lesson_branches.name = lessons.current_branch
                JOIN lesson_commits
                  ON lesson_commits.id = lesson_branches.head_commit_id
                WHERE lessons.id = ?
                  AND course_packages.owner_user_id = ?
                """,
                (lesson_id, owner_user_id),
            ).fetchone()
            if row is None:
                return None
            metadata = _loads(row["metadata_json"], {})
            runtime = (
                LessonRuntimeSnapshot.model_validate(
                    _loads(row["runtime_snapshot_json"], {})
                )
                if row["runtime_snapshot_json"]
                else _runtime_snapshot_from_legacy_metadata(metadata)
            )
            return LessonRuntimeState(
                lesson_id=str(row["id"]),
                current_branch=str(row["current_branch"]),
                head_commit_id=str(row["head_commit_id"]),
                runtime_snapshot=runtime,
                commit_metadata=dict(metadata),
            )

    def claim_chat_input_event(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        input_event_id: str,
        fingerprint: str,
        claim_id: str,
    ) -> tuple[ChatInputEventClaimStatus, dict[str, Any] | None]:
        """Atomically own, wait for, or reuse one persisted chat input event."""

        setting_key = _chat_input_event_setting_key(
            owner_user_id,
            session_id,
            input_event_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                raw_record = _setting(conn, setting_key)
                if raw_record is None:
                    conn.execute(
                        "INSERT INTO workspace_settings(key, value) VALUES (?, ?)",
                        (
                            setting_key,
                            _dumps(
                                {
                                    "version": 1,
                                    "state": "running",
                                    "fingerprint": fingerprint,
                                    "claim_id": claim_id,
                                    "claimed_at": _store_timestamp(),
                                }
                            ),
                        ),
                    )
                    conn.commit()
                    return "owned", None

                record = _chat_input_event_record(raw_record)
                _require_chat_input_fingerprint(record, fingerprint)
                state = record.get("state")
                if state == "running":
                    conn.commit()
                    return "waiting", None
                if state == "completed":
                    response = record.get("response")
                    if not isinstance(response, dict):
                        raise RuntimeError(
                            "The persisted chat input event response is invalid."
                        )
                    conn.commit()
                    return "completed", dict(response)
                raise RuntimeError("The persisted chat input event state is invalid.")
            except Exception:
                conn.rollback()
                raise

    def complete_chat_input_event(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        input_event_id: str,
        fingerprint: str,
        claim_id: str,
        response: dict[str, Any],
    ) -> None:
        setting_key = _chat_input_event_setting_key(
            owner_user_id,
            session_id,
            input_event_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                raw_record = _setting(conn, setting_key)
                if raw_record is None:
                    raise RuntimeError("The persisted chat input event claim is missing.")
                record = _chat_input_event_record(raw_record)
                _require_chat_input_fingerprint(record, fingerprint)
                if record.get("state") == "completed":
                    conn.commit()
                    return
                if (
                    record.get("state") != "running"
                    or record.get("claim_id") != claim_id
                ):
                    raise RuntimeError(
                        "The persisted chat input event claim is no longer owned."
                    )
                conn.execute(
                    "UPDATE workspace_settings SET value = ? WHERE key = ?",
                    (
                        _dumps(
                            {
                                "version": 1,
                                "state": "completed",
                                "fingerprint": fingerprint,
                                "completed_at": _store_timestamp(),
                                "response": response,
                            }
                        ),
                        setting_key,
                    ),
                )
                _prune_completed_chat_input_events(
                    conn,
                    owner_user_id=owner_user_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def release_chat_input_event(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        input_event_id: str,
        fingerprint: str,
        claim_id: str,
    ) -> None:
        """Release only the caller's failed running claim so a retry can own it."""

        setting_key = _chat_input_event_setting_key(
            owner_user_id,
            session_id,
            input_event_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                raw_record = _setting(conn, setting_key)
                if raw_record is None:
                    conn.commit()
                    return
                record = _chat_input_event_record(raw_record)
                _require_chat_input_fingerprint(record, fingerprint)
                if (
                    record.get("state") == "running"
                    and record.get("claim_id") == claim_id
                ):
                    conn.execute(
                        "DELETE FROM workspace_settings WHERE key = ?",
                        (setting_key,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def save_for_user(self, owner_user_id: str, workspace: WorkspaceState) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    self._replace_workspace(conn, workspace, owner_user_id=owner_user_id)
                    self._advance_workspace_revision(conn, owner_user_id)

    def save_for_user_if_revision(
        self,
        owner_user_id: str,
        workspace: WorkspaceState,
        *,
        expected_revision: int,
    ) -> bool:
        """Atomically replace a user workspace only when its revision is unchanged."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if self._workspace_revision(conn, owner_user_id) != expected_revision:
                        conn.rollback()
                        return False
                    self._replace_workspace(conn, workspace, owner_user_id=owner_user_id)
                    self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def package_belongs_to_user(self, owner_user_id: str, package_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT 1 FROM course_packages WHERE id = ? AND owner_user_id = ?",
                    (package_id, owner_user_id),
                ).fetchone() is not None

    def create_lesson_for_user(
        self,
        owner_user_id: str,
        lesson: Lesson,
        *,
        target_package_id: str | None,
        branch_from_lesson_id: str | None,
    ) -> LessonMutationResult:
        """Insert one lesson and its initial history without loading or replacing the workspace."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self._ensure_user_workspace_for_targeted_write(conn, owner_user_id)
                    source_package_id: str | None = None
                    if branch_from_lesson_id:
                        source_row = conn.execute(
                            """
                            SELECT lessons.package_id
                            FROM lessons
                            JOIN course_packages ON course_packages.id = lessons.package_id
                            WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                            """,
                            (branch_from_lesson_id, owner_user_id),
                        ).fetchone()
                        if source_row is None:
                            raise KeyError(f"Unknown lesson {branch_from_lesson_id}")
                        source_package_id = str(source_row["package_id"])

                    if target_package_id:
                        package_row = conn.execute(
                            "SELECT id FROM course_packages WHERE id = ? AND owner_user_id = ?",
                            (target_package_id, owner_user_id),
                        ).fetchone()
                    elif source_package_id:
                        package_row = conn.execute(
                            "SELECT id FROM course_packages WHERE id = ? AND owner_user_id = ?",
                            (source_package_id, owner_user_id),
                        ).fetchone()
                    else:
                        package_row = conn.execute(
                            """
                            SELECT id FROM course_packages
                            WHERE owner_user_id = ?
                            ORDER BY sort_order, id
                            LIMIT 1
                            """,
                            (owner_user_id,),
                        ).fetchone()
                    if package_row is None:
                        raise KeyError(f"Unknown course package {target_package_id or ''}".rstrip())
                    package_id = str(package_row["id"])
                    if source_package_id is not None and source_package_id != package_id:
                        raise ValueError("Branch source lesson must be in the target package")

                    lesson_sort_order = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lessons WHERE package_id = ?",
                            (package_id,),
                        ).fetchone()[0]
                    )
                    self._insert_lesson(conn, package_id, lesson, lesson_sort_order)
                    open_sort_order = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM package_open_lessons WHERE package_id = ?",
                            (package_id,),
                        ).fetchone()[0]
                    )
                    tab_sort_order = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM package_tab_order WHERE package_id = ?",
                            (package_id,),
                        ).fetchone()[0]
                    )
                    conn.execute(
                        "INSERT INTO package_open_lessons(package_id, lesson_id, sort_order) VALUES (?, ?, ?)",
                        (package_id, lesson.id, open_sort_order),
                    )
                    conn.execute(
                        "INSERT INTO package_tab_order(package_id, lesson_id, sort_order) VALUES (?, ?, ?)",
                        (package_id, lesson.id, tab_sort_order),
                    )
                    conn.execute(
                        "UPDATE course_packages SET active_lesson_id = ? WHERE id = ?",
                        (lesson.id, package_id),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                        (_active_package_setting_key(owner_user_id), package_id),
                    )

                    edge: CourseGraphEdge | None = None
                    if branch_from_lesson_id:
                        edge = CourseGraphEdge(
                            source_lesson_id=branch_from_lesson_id,
                            target_lesson_id=lesson.id,
                            relationship="deep_dive",
                        )
                        edge_sort_order = int(
                            conn.execute(
                                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM course_graph_edges WHERE package_id = ?",
                                (package_id,),
                            ).fetchone()[0]
                        )
                        conn.execute(
                            """
                            INSERT INTO course_graph_edges(
                                id, package_id, sort_order, source_lesson_id, target_lesson_id, relationship
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                edge.id,
                                package_id,
                                edge_sort_order,
                                edge.source_lesson_id,
                                edge.target_lesson_id,
                                edge.relationship,
                            ),
                        )

                    revision = self._advance_workspace_revision(conn, owner_user_id)
                    result = LessonMutationResult(
                        package_id=package_id,
                        active_package_id=package_id,
                        active_lesson_id=lesson.id,
                        open_lesson_ids=_ordered_values(conn, "package_open_lessons", package_id),
                        workspace_tab_order=_ordered_values(conn, "package_tab_order", package_id),
                        workspace_revision=revision,
                        created_lesson=lesson,
                        graph_edge=edge,
                    )
                    conn.commit()
                    return result
                except Exception:
                    conn.rollback()
                    raise

    def close_lesson_for_user(self, owner_user_id: str, lesson_id: str) -> LessonMutationResult:
        """Close one lesson tab while preserving every lesson and history row."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT lessons.package_id, course_packages.active_lesson_id
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (lesson_id, owner_user_id),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"Unknown lesson {lesson_id}")
                    package_id = str(row["package_id"])
                    conn.execute(
                        "DELETE FROM package_open_lessons WHERE package_id = ? AND lesson_id = ?",
                        (package_id, lesson_id),
                    )
                    conn.execute(
                        "DELETE FROM package_tab_order WHERE package_id = ? AND lesson_id = ?",
                        (package_id, lesson_id),
                    )
                    tab_order = _ordered_values(conn, "package_tab_order", package_id)
                    active_lesson_id = row["active_lesson_id"]
                    if active_lesson_id == lesson_id:
                        active_lesson_id = tab_order[0] if tab_order else None
                        conn.execute(
                            "UPDATE course_packages SET active_lesson_id = ? WHERE id = ?",
                            (active_lesson_id, package_id),
                        )
                    revision = self._advance_workspace_revision(conn, owner_user_id)
                    result = LessonMutationResult(
                        package_id=package_id,
                        active_package_id=_setting(conn, _active_package_setting_key(owner_user_id)),
                        active_lesson_id=active_lesson_id,
                        open_lesson_ids=_ordered_values(conn, "package_open_lessons", package_id),
                        workspace_tab_order=tab_order,
                        workspace_revision=revision,
                    )
                    conn.commit()
                    return result
                except Exception:
                    conn.rollback()
                    raise

    def delete_lesson_for_user(self, owner_user_id: str, lesson_id: str) -> LessonMutationResult:
        """Delete one owned lesson and only records directly associated with it."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT lessons.package_id, course_packages.active_lesson_id
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (lesson_id, owner_user_id),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"Unknown lesson {lesson_id}")
                    package_id = str(row["package_id"])
                    self._document_segments.delete_for_lesson(conn, lesson_id)
                    conn.execute(
                        "DELETE FROM course_graph_edges WHERE package_id = ? AND (source_lesson_id = ? OR target_lesson_id = ?)",
                        (package_id, lesson_id, lesson_id),
                    )
                    conn.execute(
                        "DELETE FROM package_open_lessons WHERE package_id = ? AND lesson_id = ?",
                        (package_id, lesson_id),
                    )
                    conn.execute(
                        "DELETE FROM package_tab_order WHERE package_id = ? AND lesson_id = ?",
                        (package_id, lesson_id),
                    )
                    contribution_rows = conn.execute(
                        "SELECT id FROM lesson_contributions WHERE source_lesson_id = ? OR contributor_lesson_id = ?",
                        (lesson_id, lesson_id),
                    ).fetchall()
                    for contribution_row in contribution_rows:
                        conn.execute(
                            "DELETE FROM lesson_contributions WHERE id = ?",
                            (contribution_row["id"],),
                        )
                    conn.execute(
                        "DELETE FROM lesson_merge_sessions WHERE owner_user_id = ? AND lesson_id = ?",
                        (owner_user_id, lesson_id),
                    )
                    conn.execute(
                        "DELETE FROM public_course_stars WHERE course_kind = 'lesson' AND course_id = ?",
                        (lesson_id,),
                    )
                    conn.execute(
                        "DELETE FROM resources WHERE package_id = ? AND scope_lesson_id = ?",
                        (package_id, lesson_id),
                    )
                    conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))

                    tab_order = _ordered_values(conn, "package_tab_order", package_id)
                    active_lesson_id = row["active_lesson_id"]
                    if active_lesson_id == lesson_id:
                        active_lesson_id = tab_order[0] if tab_order else None
                        conn.execute(
                            "UPDATE course_packages SET active_lesson_id = ? WHERE id = ?",
                            (active_lesson_id, package_id),
                        )
                    revision = self._advance_workspace_revision(conn, owner_user_id)
                    result = LessonMutationResult(
                        package_id=package_id,
                        active_package_id=_setting(conn, _active_package_setting_key(owner_user_id)),
                        active_lesson_id=active_lesson_id,
                        open_lesson_ids=_ordered_values(conn, "package_open_lessons", package_id),
                        workspace_tab_order=tab_order,
                        workspace_revision=revision,
                        deleted_lesson_id=lesson_id,
                    )
                    conn.commit()
                    return result
                except Exception:
                    conn.rollback()
                    raise

    def load_lesson_document_context_for_user(
        self,
        owner_user_id: str,
        lesson_id: str,
    ) -> LessonDocumentContext | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT lessons.*
                    FROM lessons
                    JOIN course_packages ON course_packages.id = lessons.package_id
                    WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                    """,
                    (lesson_id, owner_user_id),
                ).fetchone()
                if row is None:
                    return None
                return LessonDocumentContext(
                    package_id=str(row["package_id"]),
                    lesson=self._read_lesson(conn, row, owner_user_id=owner_user_id),
                    workspace_revision=self._workspace_revision(conn, owner_user_id),
                )

    def save_document_for_user_if_head(
        self,
        owner_user_id: str,
        lesson: Lesson,
        *,
        expected_branch_name: str,
        expected_head_commit_id: str,
    ) -> int | None:
        """Persist the current document and append its one new commit atomically."""
        commit = lesson.history_graph.commits[-1]
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT lessons.current_branch, lesson_branches.head_commit_id
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        LEFT JOIN lesson_branches
                          ON lesson_branches.lesson_id = lessons.id
                         AND lesson_branches.name = lessons.current_branch
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (lesson.id, owner_user_id),
                    ).fetchone()
                    if (
                        row is None
                        or row["current_branch"] != expected_branch_name
                        or row["head_commit_id"] != expected_head_commit_id
                    ):
                        conn.rollback()
                        return None
                    next_sort_order = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lesson_commits WHERE lesson_id = ?",
                            (lesson.id,),
                        ).fetchone()[0]
                    )
                    document = lesson.board_document
                    conn.execute(
                        """
                        UPDATE lessons SET
                            board_document_id = ?, board_document_title = ?, board_content_json = ?,
                            board_content_html = ?, board_content_text = ?, board_page_settings_json = ?,
                            board_teaching_guide_json = NULL, board_teaching_progress_json = NULL,
                            learning_requirements_json = NULL, board_task_requirements_json = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            document.id,
                            document.title,
                            _dumps(document.content_json),
                            document.content_html,
                            document.content_text,
                            _dumps(document.page_settings.model_dump(mode="json")),
                            lesson.updated_at,
                            lesson.id,
                        ),
                    )
                    self._insert_commit(conn, lesson.id, commit, next_sort_order)
                    conn.execute(
                        "UPDATE lesson_branches SET head_commit_id = ? WHERE lesson_id = ? AND name = ?",
                        (commit.id, lesson.id, expected_branch_name),
                    )
                    self._document_segments.replace_segments(conn, lesson.id, document)
                    revision = self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return revision
                except Exception:
                    conn.rollback()
                    raise

    def save_lesson_for_user_if_head(
        self,
        owner_user_id: str,
        lesson: Lesson,
        *,
        expected_branch_name: str,
        expected_head_commit_id: str,
    ) -> bool:
        """Atomically replace one lesson only when its persisted branch head is unchanged."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT lessons.package_id, lessons.sort_order, lessons.current_branch,
                               lesson_branches.head_commit_id
                        FROM lessons
                        JOIN course_packages
                          ON course_packages.id = lessons.package_id
                        LEFT JOIN lesson_branches
                          ON lesson_branches.lesson_id = lessons.id
                         AND lesson_branches.name = lessons.current_branch
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (lesson.id, owner_user_id),
                    ).fetchone()
                    if (
                        row is None
                        or row["current_branch"] != expected_branch_name
                        or row["head_commit_id"] != expected_head_commit_id
                    ):
                        conn.rollback()
                        return False
                    conn.execute("DELETE FROM lessons WHERE id = ?", (lesson.id,))
                    self._insert_lesson(
                        conn,
                        row["package_id"],
                        lesson,
                        int(row["sort_order"]),
                    )
                    self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def append_non_document_commit_for_user_if_head(
        self,
        owner_user_id: str,
        lesson_id: str,
        commit: CommitRecord,
        *,
        expected_branch_name: str,
        expected_head_commit_id: str,
        lesson_updated_at: str,
    ) -> bool:
        """Append a history-only commit without rewriting the live board document."""
        if commit.operations:
            raise ValueError("A non-document commit cannot contain document operations")
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT lessons.current_branch, lessons.board_document_id,
                               lessons.board_document_title, lessons.board_content_json,
                               lessons.board_content_html, lessons.board_content_text,
                               lessons.board_page_settings_json, lesson_branches.head_commit_id
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        LEFT JOIN lesson_branches
                          ON lesson_branches.lesson_id = lessons.id
                         AND lesson_branches.name = lessons.current_branch
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (lesson_id, owner_user_id),
                    ).fetchone()
                    if (
                        row is None
                        or row["current_branch"] != expected_branch_name
                        or row["head_commit_id"] != expected_head_commit_id
                    ):
                        conn.rollback()
                        return False
                    next_sort_order = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lesson_commits WHERE lesson_id = ?",
                            (lesson_id,),
                        ).fetchone()[0]
                    )
                    conn.execute(
                        """
                        INSERT INTO lesson_commits(
                            id, lesson_id, sort_order, label, message, branch_name, created_at,
                            operations_json, snapshot_document_id, snapshot_title, snapshot_content_json,
                            snapshot_content_html, snapshot_content_text, snapshot_page_settings_json,
                            runtime_snapshot_json, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            commit.id,
                            lesson_id,
                            next_sort_order,
                            commit.label,
                            commit.message,
                            commit.branch_name,
                            commit.created_at,
                            "[]",
                            row["board_document_id"],
                            row["board_document_title"],
                            row["board_content_json"],
                            row["board_content_html"],
                            row["board_content_text"],
                            row["board_page_settings_json"],
                            _dumps_optional(commit.runtime_snapshot),
                            _dumps(commit.metadata),
                        ),
                    )
                    for parent_index, parent_id in enumerate(commit.parent_ids):
                        conn.execute(
                            """
                            INSERT INTO lesson_commit_parents(commit_id, parent_id, sort_order)
                            VALUES (?, ?, ?)
                            """,
                            (commit.id, parent_id, parent_index),
                        )
                    conn.execute(
                        """
                        UPDATE lesson_branches SET head_commit_id = ?
                        WHERE lesson_id = ? AND name = ?
                        """,
                        (commit.id, lesson_id, expected_branch_name),
                    )
                    conn.execute(
                        "UPDATE lessons SET updated_at = ? WHERE id = ?",
                        (lesson_updated_at, lesson_id),
                    )
                    self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def load_merge_session_for_user(
        self,
        owner_user_id: str,
        session_id: str,
    ) -> LessonMergeSession | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM lesson_merge_sessions
                    WHERE id = ? AND owner_user_id = ?
                    """,
                    (session_id, owner_user_id),
                ).fetchone()
                return _merge_session_from_row(row) if row is not None else None

    def load_active_merge_session_for_user(
        self,
        owner_user_id: str,
        lesson_id: str,
    ) -> LessonMergeSession | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM lesson_merge_sessions
                    WHERE owner_user_id = ? AND lesson_id = ?
                      AND status IN ('draft', 'ai_running', 'ready', 'stale', 'failed')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (owner_user_id, lesson_id),
                ).fetchone()
                return _merge_session_from_row(row) if row is not None else None

    def save_merge_session_for_user(self, session: LessonMergeSession) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    owner_row = conn.execute(
                        """
                        SELECT 1
                        FROM lessons
                        JOIN course_packages ON course_packages.id = lessons.package_id
                        WHERE lessons.id = ? AND course_packages.owner_user_id = ?
                        """,
                        (session.lesson_id, session.owner_user_id),
                    ).fetchone()
                    if owner_row is None:
                        raise ValueError("Merge session lesson is not owned by the current user.")
                    _upsert_merge_session(conn, session)

    def save_merge_session_for_user_if_version(
        self,
        session: LessonMergeSession,
        *,
        expected_version: int,
    ) -> bool:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    stored = conn.execute(
                        """
                        SELECT version
                        FROM lesson_merge_sessions
                        WHERE id = ? AND owner_user_id = ?
                        """,
                        (session.id, session.owner_user_id),
                    ).fetchone()
                    if stored is None or int(stored["version"]) != expected_version:
                        conn.rollback()
                        return False
                    _upsert_merge_session(conn, session)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def save_workspace_and_merge_session_for_user_if_revision(
        self,
        owner_user_id: str,
        workspace: WorkspaceState,
        session: LessonMergeSession,
        *,
        expected_revision: int,
    ) -> bool:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if self._workspace_revision(conn, owner_user_id) != expected_revision:
                        conn.rollback()
                        return False
                    stored = conn.execute(
                        """
                        SELECT version, target_head_commit_id, source_head_commit_id
                        FROM lesson_merge_sessions
                        WHERE id = ? AND owner_user_id = ?
                        """,
                        (session.id, owner_user_id),
                    ).fetchone()
                    if stored is None or int(stored["version"]) != session.version - 1:
                        conn.rollback()
                        return False
                    self._replace_workspace(conn, workspace, owner_user_id=owner_user_id)
                    _upsert_merge_session(conn, session)
                    self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def load_lesson_with_owner(self, lesson_id: str) -> tuple[str, Lesson, bool] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT lessons.*, course_packages.owner_user_id,
                           course_packages.visibility AS package_visibility,
                           course_packages.sort_order AS package_sort_order
                    FROM lessons
                    JOIN course_packages ON course_packages.id = lessons.package_id
                    WHERE lessons.id = ?
                    """,
                    (lesson_id,),
                ).fetchone()
                if row is None or not row["owner_user_id"]:
                    return None
                is_public = (
                    (row["visibility"] == "public" and int(row["package_sort_order"]) == 0)
                    or row["package_visibility"] == "public"
                )
                return str(row["owner_user_id"]), self._read_lesson(conn, row), is_public

    def load_lesson_contribution(
        self,
        contribution_id: str,
    ) -> tuple[LessonContribution, LessonContributionRevision, list[LessonContributionEvent]] | None:
        with self._lock:
            with self._connect() as conn:
                return _load_contribution_bundle(conn, contribution_id)

    def list_lesson_contributions(
        self,
        *,
        user_id: str,
        role: str,
        status: str | None = None,
    ) -> list[tuple[LessonContribution, LessonContributionRevision, list[LessonContributionEvent]]]:
        owner_column = "source_owner_user_id" if role == "received" else "contributor_user_id"
        params: list[str] = [user_id]
        status_clause = ""
        if status:
            status_clause = " AND status = ?"
            params.append(status)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id FROM lesson_contributions
                    WHERE {owner_column} = ?{status_clause}
                    ORDER BY updated_at DESC
                    """,
                    tuple(params),
                ).fetchall()
                return [
                    bundle
                    for row in rows
                    if (bundle := _load_contribution_bundle(conn, str(row["id"]))) is not None
                ]

    def find_active_lesson_contribution(
        self,
        *,
        contributor_user_id: str,
        contributor_lesson_id: str,
        source_lesson_id: str,
    ) -> tuple[LessonContribution, LessonContributionRevision, list[LessonContributionEvent]] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id FROM lesson_contributions
                    WHERE contributor_user_id = ? AND contributor_lesson_id = ?
                      AND source_lesson_id = ? AND status IN ('open', 'merge_draft')
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (contributor_user_id, contributor_lesson_id, source_lesson_id),
                ).fetchone()
                return _load_contribution_bundle(conn, str(row["id"])) if row is not None else None

    def create_lesson_contribution(
        self,
        contribution: LessonContribution,
        revision: LessonContributionRevision,
        event: LessonContributionEvent,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    _insert_contribution(conn, contribution)
                    _insert_contribution_revision(conn, revision)
                    _insert_contribution_event(conn, event)

    def save_lesson_contribution_if_version(
        self,
        contribution: LessonContribution,
        *,
        expected_version: int,
        revision: LessonContributionRevision | None = None,
        events: list[LessonContributionEvent] | None = None,
    ) -> bool:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if not _update_contribution_if_version(
                        conn, contribution, expected_version=expected_version
                    ):
                        conn.rollback()
                        return False
                    if revision is not None:
                        _insert_contribution_revision(conn, revision)
                    for event in events or []:
                        _insert_contribution_event(conn, event)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def save_workspace_merge_session_and_contribution_if_revision(
        self,
        owner_user_id: str,
        workspace: WorkspaceState,
        session: LessonMergeSession,
        contribution: LessonContribution,
        *,
        expected_workspace_revision: int,
        expected_contribution_version: int,
        events: list[LessonContributionEvent],
        guard_session_id: str | None = None,
        expected_session_version: int | None = None,
    ) -> bool:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if self._workspace_revision(conn, owner_user_id) != expected_workspace_revision:
                        conn.rollback()
                        return False
                    if guard_session_id is not None:
                        stored_session = conn.execute(
                            "SELECT version FROM lesson_merge_sessions WHERE id = ? AND owner_user_id = ?",
                            (guard_session_id, owner_user_id),
                        ).fetchone()
                        if (
                            stored_session is None
                            or expected_session_version is None
                            or int(stored_session["version"]) != expected_session_version
                        ):
                            conn.rollback()
                            return False
                    if not _update_contribution_if_version(
                        conn,
                        contribution,
                        expected_version=expected_contribution_version,
                    ):
                        conn.rollback()
                        return False
                    self._replace_workspace(conn, workspace, owner_user_id=owner_user_id)
                    _upsert_merge_session(conn, session)
                    for event in events:
                        _insert_contribution_event(conn, event)
                    self._advance_workspace_revision(conn, owner_user_id)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def save_merge_session_and_contribution_if_versions(
        self,
        session: LessonMergeSession,
        contribution: LessonContribution,
        *,
        expected_session_version: int,
        expected_contribution_version: int,
        events: list[LessonContributionEvent],
    ) -> bool:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    stored_session = conn.execute(
                        "SELECT version FROM lesson_merge_sessions WHERE id = ? AND owner_user_id = ?",
                        (session.id, session.owner_user_id),
                    ).fetchone()
                    if (
                        stored_session is None
                        or int(stored_session["version"]) != expected_session_version
                        or not _update_contribution_if_version(
                            conn,
                            contribution,
                            expected_version=expected_contribution_version,
                        )
                    ):
                        conn.rollback()
                        return False
                    _upsert_merge_session(conn, session)
                    for event in events:
                        _insert_contribution_event(conn, event)
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

    def search_document_segments(
        self,
        query: str = "",
        *,
        owner_user_id: str | None = None,
        kind: BoardSegmentKind | None = None,
        limit: int = 20,
    ) -> list[DocumentSegmentSearchResult]:
        with self._lock:
            with self._connect() as conn:
                return self._document_segments.search(
                    conn,
                    query,
                    owner_user_id=owner_user_id,
                    kind=kind,
                    limit=limit,
                )

    def _initialize(self) -> None:
        with self._lock:
            with self._connect() as conn:
                self._create_schema(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS public_course_stars (
                owner_user_id TEXT NOT NULL,
                course_kind TEXT NOT NULL CHECK (course_kind IN ('lesson', 'package')),
                course_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (owner_user_id, course_kind, course_id)
            );

            CREATE INDEX IF NOT EXISTS idx_public_course_stars_owner_created
                ON public_course_stars(owner_user_id, created_at);

            CREATE TABLE IF NOT EXISTS course_packages (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private',
                publication_review_json TEXT,
                published_version_json TEXT,
                sort_order INTEGER NOT NULL,
                active_lesson_id TEXT
            );

            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                slug TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private',
                publication_review_json TEXT,
                published_version_json TEXT,
                board_document_id TEXT NOT NULL,
                board_document_title TEXT NOT NULL,
                board_content_json TEXT NOT NULL,
                board_content_html TEXT NOT NULL,
                board_content_text TEXT NOT NULL,
                board_page_settings_json TEXT NOT NULL,
                board_teaching_guide_json TEXT,
                board_teaching_progress_json TEXT,
                learning_requirements_json TEXT,
                board_task_requirements_json TEXT,
                teaching_guide_json TEXT NOT NULL,
                current_branch TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lessons_package
                ON lessons(package_id, sort_order);

            CREATE TABLE IF NOT EXISTS board_document_segments (
                lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                document_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                heading_path_json TEXT NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                parent_id TEXT,
                before_segment_id TEXT,
                after_segment_id TEXT,
                PRIMARY KEY (lesson_id, segment_id)
            );

            CREATE INDEX IF NOT EXISTS idx_board_document_segments_lesson
                ON board_document_segments(lesson_id, order_index);

            CREATE INDEX IF NOT EXISTS idx_board_document_segments_kind
                ON board_document_segments(kind, lesson_id);

            CREATE INDEX IF NOT EXISTS idx_board_document_segments_hash
                ON board_document_segments(text_hash);

            CREATE TABLE IF NOT EXISTS package_open_lessons (
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (package_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS package_tab_order (
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (package_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS course_graph_edges (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                source_lesson_id TEXT NOT NULL,
                target_lesson_id TEXT NOT NULL,
                relationship TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lesson_commits (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                label TEXT NOT NULL,
                message TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                operations_json TEXT NOT NULL,
                snapshot_document_id TEXT NOT NULL,
                snapshot_title TEXT NOT NULL,
                snapshot_content_json TEXT NOT NULL,
                snapshot_content_html TEXT NOT NULL,
                snapshot_content_text TEXT NOT NULL,
                snapshot_page_settings_json TEXT NOT NULL,
                runtime_snapshot_json TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lesson_commits_lesson
                ON lesson_commits(lesson_id, sort_order);

            CREATE TABLE IF NOT EXISTS lesson_commit_parents (
                commit_id TEXT NOT NULL REFERENCES lesson_commits(id) ON DELETE CASCADE,
                parent_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (commit_id, sort_order)
            );

            CREATE TABLE IF NOT EXISTS lesson_branches (
                lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                head_commit_id TEXT NOT NULL,
                base_commit_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (lesson_id, name)
            );

            CREATE TABLE IF NOT EXISTS lesson_merge_sessions (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                status TEXT NOT NULL,
                target_branch_name TEXT NOT NULL,
                source_branch_name TEXT NOT NULL,
                base_commit_id TEXT NOT NULL,
                target_head_commit_id TEXT NOT NULL,
                source_head_commit_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lesson_merge_sessions_owner_lesson
                ON lesson_merge_sessions(owner_user_id, lesson_id, updated_at);

            CREATE TABLE IF NOT EXISTS lesson_contributions (
                id TEXT PRIMARY KEY,
                source_lesson_id TEXT NOT NULL,
                source_owner_user_id TEXT NOT NULL,
                contributor_lesson_id TEXT NOT NULL,
                contributor_user_id TEXT NOT NULL,
                source_title TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                current_revision INTEGER NOT NULL,
                current_revision_id TEXT NOT NULL,
                source_author_json TEXT NOT NULL,
                contributor_json TEXT NOT NULL,
                merge_session_id TEXT,
                merged_commit_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_lesson_contributions_source_owner
                ON lesson_contributions(source_owner_user_id, status, updated_at);

            CREATE INDEX IF NOT EXISTS idx_lesson_contributions_contributor
                ON lesson_contributions(contributor_user_id, status, updated_at);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_contributions_active_copy
                ON lesson_contributions(source_lesson_id, contributor_lesson_id)
                WHERE status IN ('open', 'merge_draft');

            CREATE TABLE IF NOT EXISTS lesson_contribution_revisions (
                id TEXT PRIMARY KEY,
                contribution_id TEXT NOT NULL REFERENCES lesson_contributions(id) ON DELETE CASCADE,
                revision_number INTEGER NOT NULL,
                source_commit_id TEXT NOT NULL,
                contributor_commit_id TEXT NOT NULL,
                base_document_json TEXT NOT NULL,
                proposed_document_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(contribution_id, revision_number)
            );

            CREATE TABLE IF NOT EXISTS lesson_contribution_events (
                id TEXT PRIMARY KEY,
                contribution_id TEXT NOT NULL REFERENCES lesson_contributions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                body TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lesson_contribution_events_timeline
                ON lesson_contribution_events(contribution_id, created_at, id);

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                scope_lesson_id TEXT,
                concept_index_json TEXT NOT NULL,
                extracted_text_available INTEGER NOT NULL,
                text_content TEXT,
                source_path TEXT,
                source_type TEXT NOT NULL DEFAULT 'local_file',
                source_uri TEXT,
                ingestion_status TEXT NOT NULL DEFAULT 'ready',
                ingestion_error TEXT NOT NULL DEFAULT '',
                ingestion_progress INTEGER NOT NULL DEFAULT 100,
                ingestion_adapter TEXT NOT NULL DEFAULT '',
                ingestion_job_json TEXT,
                parser_provider TEXT NOT NULL DEFAULT 'native',
                parser_artifacts_path TEXT,
                parser_message TEXT NOT NULL DEFAULT '',
                parse_warnings_json TEXT NOT NULL DEFAULT '[]',
                source_units_json TEXT NOT NULL DEFAULT '[]',
                page_structure_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_resources_package
                ON resources(package_id, sort_order);

            CREATE TABLE IF NOT EXISTS resource_chapters (
                id TEXT NOT NULL,
                resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                level INTEGER NOT NULL,
                page_range TEXT,
                page_start INTEGER,
                page_end INTEGER,
                summary TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                prerequisites_json TEXT NOT NULL,
                parent_id TEXT,
                parent_title TEXT,
                path_json TEXT NOT NULL,
                locator_hint TEXT,
                order_index INTEGER NOT NULL,
                scan_strategy TEXT NOT NULL,
                body_start_order INTEGER,
                body_end_order INTEGER,
                body_page_start INTEGER,
                body_page_end INTEGER,
                body_match_status TEXT NOT NULL DEFAULT '',
                body_match_confidence REAL NOT NULL DEFAULT 0,
                body_match_reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (resource_id, id)
            );
            """
        )
        self._migrate_schema(conn)
        self._document_segments.create_fts_schema(conn)
        self._document_segments.backfill(conn, _document_from_row)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_course_packages_owner
                ON course_packages(owner_user_id, sort_order)
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        package_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(course_packages)").fetchall()
        }
        if "published_version_json" not in package_columns:
            conn.execute("ALTER TABLE course_packages ADD COLUMN published_version_json TEXT")
        lesson_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "board_teaching_progress_json" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN board_teaching_progress_json TEXT")
        if "board_task_requirements_json" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN board_task_requirements_json TEXT")
        if "published_version_json" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN published_version_json TEXT")
        commit_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(lesson_commits)").fetchall()
        }
        if "runtime_snapshot_json" not in commit_columns:
            conn.execute("ALTER TABLE lesson_commits ADD COLUMN runtime_snapshot_json TEXT")
        resource_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(resources)").fetchall()
        }
        if "scope_lesson_id" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN scope_lesson_id TEXT")
        if "parser_provider" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN parser_provider TEXT NOT NULL DEFAULT 'native'")
        if "parser_artifacts_path" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN parser_artifacts_path TEXT")
        if "parser_message" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN parser_message TEXT NOT NULL DEFAULT ''")
        if "parse_warnings_json" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN parse_warnings_json TEXT NOT NULL DEFAULT '[]'")
        if "source_units_json" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN source_units_json TEXT NOT NULL DEFAULT '[]'")
        if "page_structure_json" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN page_structure_json TEXT")
        if "source_type" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN source_type TEXT NOT NULL DEFAULT 'local_file'")
        if "source_uri" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN source_uri TEXT")
        if "ingestion_status" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN ingestion_status TEXT NOT NULL DEFAULT 'ready'")
        if "ingestion_error" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN ingestion_error TEXT NOT NULL DEFAULT ''")
        if "ingestion_progress" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN ingestion_progress INTEGER NOT NULL DEFAULT 100")
        if "ingestion_adapter" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN ingestion_adapter TEXT NOT NULL DEFAULT ''")
        if "ingestion_job_json" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN ingestion_job_json TEXT")
        chapter_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(resource_chapters)").fetchall()
        }
        if "body_start_order" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_start_order INTEGER")
        if "body_end_order" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_end_order INTEGER")
        if "body_page_start" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_page_start INTEGER")
        if "body_page_end" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_page_end INTEGER")
        if "body_match_status" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_match_status TEXT NOT NULL DEFAULT ''")
        if "body_match_confidence" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_match_confidence REAL NOT NULL DEFAULT 0")
        if "body_match_reason" not in chapter_columns:
            conn.execute("ALTER TABLE resource_chapters ADD COLUMN body_match_reason TEXT NOT NULL DEFAULT ''")
        package_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(course_packages)").fetchall()
        }
        if "owner_user_id" not in package_columns:
            conn.execute("ALTER TABLE course_packages ADD COLUMN owner_user_id TEXT")
        if "visibility" not in package_columns:
            conn.execute("ALTER TABLE course_packages ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        if "publication_review_json" not in package_columns:
            conn.execute("ALTER TABLE course_packages ADD COLUMN publication_review_json TEXT")
            conn.execute("UPDATE course_packages SET visibility = 'private'")
        if "visibility" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        if "publication_review_json" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN publication_review_json TEXT")
            conn.execute("UPDATE lessons SET visibility = 'private'")
        self._backfill_published_versions(conn)

    def _backfill_published_versions(self, conn: sqlite3.Connection) -> None:
        standalone_rows = conn.execute(
            """
            SELECT lessons.*
            FROM lessons
            JOIN course_packages ON course_packages.id = lessons.package_id
            WHERE course_packages.sort_order = 0
              AND lessons.visibility = 'public'
              AND lessons.published_version_json IS NULL
            """
        ).fetchall()
        for row in standalone_rows:
            lesson = self._read_lesson(conn, row)
            version = capture_lesson_version(lesson)
            conn.execute(
                "UPDATE lessons SET published_version_json = ? WHERE id = ?",
                (_dumps(version.model_dump(mode="json")), lesson.id),
            )

        package_rows = conn.execute(
            """
            SELECT * FROM course_packages
            WHERE sort_order > 0
              AND visibility = 'public'
              AND published_version_json IS NULL
            """
        ).fetchall()
        for row in package_rows:
            package = self._read_package(conn, row, owner_user_id=row["owner_user_id"])
            upload_package_version(package)
            conn.execute(
                "UPDATE course_packages SET published_version_json = ? WHERE id = ?",
                (_dumps(package.published_version.model_dump(mode="json")), package.id),
            )

    def _has_any_packages(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute("SELECT 1 FROM course_packages LIMIT 1").fetchone()
        return row is not None

    def _workspace_revision(self, conn: sqlite3.Connection, owner_user_id: str) -> int:
        raw = _setting(conn, _workspace_revision_setting_key(owner_user_id))
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    def _advance_workspace_revision(self, conn: sqlite3.Connection, owner_user_id: str) -> int:
        revision = self._workspace_revision(conn, owner_user_id) + 1
        conn.execute(
            "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
            (_workspace_revision_setting_key(owner_user_id), str(revision)),
        )
        return revision

    def _has_user_packages(self, conn: sqlite3.Connection, owner_user_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM course_packages WHERE owner_user_id = ? LIMIT 1",
            (owner_user_id,),
        ).fetchone()
        return row is not None

    def _ensure_user_workspace_for_targeted_write(
        self,
        conn: sqlite3.Connection,
        owner_user_id: str,
    ) -> None:
        if self._has_unowned_packages(conn) and self._is_registered_user(conn, owner_user_id):
            self._claim_unowned_workspace(
                conn,
                self._legacy_workspace_owner_candidate(conn) or owner_user_id,
            )
        if self._has_user_packages(conn, owner_user_id):
            return
        workspace = build_empty_account_workspace_state()
        conn.execute(
            "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
            (_active_package_setting_key(owner_user_id), workspace.active_package_id or ""),
        )
        for package_index, package in enumerate(workspace.packages):
            self._insert_package(
                conn,
                package,
                package_index,
                owner_user_id=owner_user_id,
            )

    def _is_registered_user(self, conn: sqlite3.Connection, owner_user_id: str) -> bool:
        users_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if users_table is None:
            return not owner_user_id.startswith("guest_")
        return conn.execute(
            "SELECT 1 FROM users WHERE id = ? LIMIT 1",
            (owner_user_id,),
        ).fetchone() is not None

    def _has_unowned_packages(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM course_packages WHERE owner_user_id IS NULL LIMIT 1"
        ).fetchone()
        return row is not None

    def _claim_unowned_workspace(self, conn: sqlite3.Connection, owner_user_id: str) -> None:
        conn.execute(
            "UPDATE course_packages SET owner_user_id = ? WHERE owner_user_id IS NULL",
            (owner_user_id,),
        )
        active_package_id = _setting(conn, "active_package_id")
        if active_package_id:
            conn.execute(
                "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                (_active_package_setting_key(owner_user_id), active_package_id),
            )
            conn.execute("DELETE FROM workspace_settings WHERE key = ?", ("active_package_id",))

    def _legacy_workspace_owner_candidate(self, conn: sqlite3.Connection) -> str | None:
        if not _table_exists(conn, "users"):
            return None
        row = conn.execute(
            """
            SELECT id
            FROM users
            ORDER BY
                CASE role WHEN 'admin' THEN 0 ELSE 1 END,
                created_at,
                id
            LIMIT 1
            """
        ).fetchone()
        return row["id"] if row is not None else None

    def _read_workspace(self, conn: sqlite3.Connection, *, owner_user_id: str | None = None) -> WorkspaceState:
        active_package_id = _setting(conn, _active_package_setting_key(owner_user_id))
        where_clause = ""
        params: tuple[str, ...] = ()
        if owner_user_id is not None:
            where_clause = "WHERE owner_user_id = ?"
            params = (owner_user_id,)
        packages = [
            self._read_package(conn, package_row, owner_user_id=owner_user_id)
            for package_row in conn.execute(
                f"""
                SELECT * FROM course_packages
                {where_clause}
                ORDER BY sort_order, id
                """,
                params,
            ).fetchall()
        ]
        return WorkspaceState(packages=packages, active_package_id=active_package_id)

    def _read_package(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        owner_user_id: str | None = None,
    ) -> CoursePackage:
        package_id = row["id"]
        lessons = [
            self._read_lesson(conn, lesson_row, owner_user_id=owner_user_id)
            for lesson_row in conn.execute(
                """
                SELECT * FROM lessons
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        course_graph = [
            CourseGraphEdge(
                id=edge_row["id"],
                source_lesson_id=edge_row["source_lesson_id"],
                target_lesson_id=edge_row["target_lesson_id"],
                relationship=edge_row["relationship"],
            )
            for edge_row in conn.execute(
                """
                SELECT * FROM course_graph_edges
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        resources = [
            self._read_resource(conn, resource_row)
            for resource_row in conn.execute(
                """
                SELECT * FROM resources
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        open_lesson_ids = _ordered_values(conn, "package_open_lessons", package_id)
        workspace_tab_order = _ordered_values(conn, "package_tab_order", package_id)
        return CoursePackage(
            id=package_id,
            title=row["title"],
            summary=row["summary"],
            visibility=row["visibility"],
            publication_review=PublicationReview.model_validate(
                _loads(row["publication_review_json"], {})
            ),
            published_version=(
                PublishedCoursePackageVersion.model_validate(
                    _loads(row["published_version_json"], {})
                )
                if row["published_version_json"]
                else None
            ),
            lessons=lessons,
            course_graph=course_graph,
            resources=resources,
            open_lesson_ids=open_lesson_ids,
            active_lesson_id=row["active_lesson_id"],
            workspace_tab_order=workspace_tab_order,
        )

    def _read_lesson(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        owner_user_id: str | None = None,
    ) -> Lesson:
        lesson_id = row["id"]
        commits = [
            self._read_commit(conn, commit_row)
            for commit_row in conn.execute(
                """
                SELECT * FROM lesson_commits
                WHERE lesson_id = ?
                ORDER BY sort_order, id
                """,
                (lesson_id,),
            ).fetchall()
        ]
        branches = {
            branch_row["name"]: BranchRef(
                name=branch_row["name"],
                head_commit_id=branch_row["head_commit_id"],
                base_commit_id=branch_row["base_commit_id"],
                created_at=branch_row["created_at"],
            )
            for branch_row in conn.execute(
                """
                SELECT * FROM lesson_branches
                WHERE lesson_id = ?
                ORDER BY name
                """,
                (lesson_id,),
            ).fetchall()
        }
        history_graph = LessonHistoryGraph(
            branches=branches,
            commits=commits,
            current_branch=row["current_branch"],
        )
        return Lesson(
            id=lesson_id,
            title=row["title"],
            slug=row["slug"],
            summary=row["summary"],
            tags=_loads(row["tags_json"], []),
            visibility=row["visibility"],
            publication_review=PublicationReview.model_validate(
                _loads(row["publication_review_json"], {})
            ),
            published_version=(
                PublishedLessonVersion.model_validate(
                    _loads(row["published_version_json"], {})
                )
                if row["published_version_json"]
                else None
            ),
            board_document=_document_from_row(row, "board"),
            board_teaching_guide=_loads_optional(row["board_teaching_guide_json"]),
            board_teaching_progress=_loads_optional(row["board_teaching_progress_json"]),
            learning_requirements=None,
            board_task_requirements=None,
            teaching_guide=_loads(row["teaching_guide_json"], {}),
            history_graph=history_graph,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _read_commit(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CommitRecord:
        parent_ids = [
            parent_row["parent_id"]
            for parent_row in conn.execute(
                """
                SELECT parent_id FROM lesson_commit_parents
                WHERE commit_id = ?
                ORDER BY sort_order
                """,
                (row["id"],),
            ).fetchall()
        ]
        return CommitRecord(
            id=row["id"],
            label=row["label"],
            message=row["message"],
            branch_name=row["branch_name"],
            created_at=row["created_at"],
            parent_ids=parent_ids,
            operations=_loads(row["operations_json"], []),
            snapshot=_document_from_row(row, "snapshot"),
            runtime_snapshot=(
                LessonRuntimeSnapshot.model_validate(_loads(row["runtime_snapshot_json"], {}))
                if "runtime_snapshot_json" in row.keys() and row["runtime_snapshot_json"]
                else _runtime_snapshot_from_legacy_metadata(_loads(row["metadata_json"], {}))
            ),
            metadata=_loads(row["metadata_json"], {}),
        )

    def _read_resource(self, conn: sqlite3.Connection, row: sqlite3.Row) -> ResourceLibraryItem:
        raw_page_structure = _loads(row["page_structure_json"], None) if row["page_structure_json"] else None
        raw_ingestion_job = _loads(row["ingestion_job_json"], None) if row["ingestion_job_json"] else None
        chapters = [
            LibraryChapter(
                id=chapter_row["id"],
                title=chapter_row["title"],
                level=chapter_row["level"],
                page_range=chapter_row["page_range"],
                page_start=chapter_row["page_start"],
                page_end=chapter_row["page_end"],
                summary=chapter_row["summary"],
                keywords=_loads(chapter_row["keywords_json"], []),
                prerequisites=_loads(chapter_row["prerequisites_json"], []),
                parent_id=chapter_row["parent_id"],
                parent_title=chapter_row["parent_title"],
                path=_loads(chapter_row["path_json"], []),
                locator_hint=chapter_row["locator_hint"],
                order_index=chapter_row["order_index"],
                scan_strategy=chapter_row["scan_strategy"],
                body_start_order=chapter_row["body_start_order"],
                body_end_order=chapter_row["body_end_order"],
                body_page_start=chapter_row["body_page_start"],
                body_page_end=chapter_row["body_page_end"],
                body_match_status=chapter_row["body_match_status"] or "",
                body_match_confidence=float(chapter_row["body_match_confidence"] or 0),
                body_match_reason=chapter_row["body_match_reason"] or "",
            )
            for chapter_row in conn.execute(
                """
                SELECT * FROM resource_chapters
                WHERE resource_id = ?
                ORDER BY sort_order, id
                """,
                (row["id"],),
            ).fetchall()
        ]
        return ResourceLibraryItem(
            id=row["id"],
            name=row["name"],
            mime_type=row["mime_type"],
            resource_type=row["resource_type"],
            size_bytes=row["size_bytes"],
            uploaded_at=row["uploaded_at"],
            scope_lesson_id=row["scope_lesson_id"],
            outline=chapters,
            concept_index=_loads(row["concept_index_json"], {}),
            extracted_text_available=bool(row["extracted_text_available"]),
            text_content=row["text_content"],
            source_path=row["source_path"],
            source_type=row["source_type"] or "local_file",
            source_uri=row["source_uri"],
            ingestion_status=row["ingestion_status"] or "ready",
            ingestion_error=row["ingestion_error"] or "",
            ingestion_progress=int(row["ingestion_progress"]) if row["ingestion_progress"] is not None else 100,
            ingestion_adapter=row["ingestion_adapter"] or "",
            ingestion_job=(
                SourceIngestionJob.model_validate(raw_ingestion_job)
                if isinstance(raw_ingestion_job, dict)
                else None
            ),
            parser_provider=row["parser_provider"] or "native",
            parser_artifacts_path=row["parser_artifacts_path"],
            parser_message=row["parser_message"] or "",
            parse_warnings=_loads(row["parse_warnings_json"], []),
            source_units=[
                ResourceSourceUnit.model_validate(unit)
                for unit in _loads(row["source_units_json"], [])
                if isinstance(unit, dict)
            ],
            page_structure=(
                ResourcePageStructure.model_validate(raw_page_structure)
                if isinstance(raw_page_structure, dict)
                else None
            ),
        )

    def _replace_workspace(
        self,
        conn: sqlite3.Connection,
        workspace: WorkspaceState,
        *,
        owner_user_id: str | None = None,
    ) -> None:
        setting_key = _active_package_setting_key(owner_user_id)
        self._document_segments.delete_for_owner(conn, owner_user_id)
        if owner_user_id is None:
            conn.execute("DELETE FROM workspace_settings")
            conn.execute("DELETE FROM course_packages")
        else:
            conn.execute("DELETE FROM workspace_settings WHERE key = ?", (setting_key,))
            conn.execute("DELETE FROM course_packages WHERE owner_user_id = ?", (owner_user_id,))
        conn.execute(
            "INSERT INTO workspace_settings(key, value) VALUES (?, ?)",
            (setting_key, workspace.active_package_id or ""),
        )
        for package_index, package in enumerate(workspace.packages):
            self._insert_package(conn, package, package_index, owner_user_id=owner_user_id)

    def _insert_package(
        self,
        conn: sqlite3.Connection,
        package: CoursePackage,
        package_index: int,
        *,
        owner_user_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO course_packages(
                id, owner_user_id, title, summary, visibility, publication_review_json,
                published_version_json, sort_order, active_lesson_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                package.id,
                owner_user_id,
                package.title,
                package.summary,
                package.visibility,
                _dumps(package.publication_review.model_dump(mode="json")),
                _dumps_optional(package.published_version),
                package_index,
                package.active_lesson_id,
            ),
        )
        for index, lesson_id in enumerate(package.open_lesson_ids):
            conn.execute(
                """
                INSERT INTO package_open_lessons(package_id, lesson_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (package.id, lesson_id, index),
            )
        for index, lesson_id in enumerate(package.workspace_tab_order):
            conn.execute(
                """
                INSERT INTO package_tab_order(package_id, lesson_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (package.id, lesson_id, index),
            )
        for lesson_index, lesson in enumerate(package.lessons):
            self._insert_lesson(conn, package.id, lesson, lesson_index)
        for edge_index, edge in enumerate(package.course_graph):
            conn.execute(
                """
                INSERT INTO course_graph_edges(
                    id, package_id, sort_order, source_lesson_id, target_lesson_id, relationship
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.id,
                    package.id,
                    edge_index,
                    edge.source_lesson_id,
                    edge.target_lesson_id,
                    edge.relationship,
                ),
            )
        for resource_index, resource in enumerate(package.resources):
            self._insert_resource(conn, package.id, resource, resource_index)

    def _insert_lesson(
        self,
        conn: sqlite3.Connection,
        package_id: str,
        lesson: Lesson,
        lesson_index: int,
    ) -> None:
        document = lesson.board_document
        conn.execute(
            """
            INSERT INTO lessons(
                id, package_id, sort_order, title, slug, summary, tags_json, visibility,
                publication_review_json, published_version_json,
                board_document_id, board_document_title, board_content_json,
                board_content_html, board_content_text, board_page_settings_json,
                board_teaching_guide_json, board_teaching_progress_json, learning_requirements_json,
                board_task_requirements_json, teaching_guide_json,
                current_branch, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lesson.id,
                package_id,
                lesson_index,
                lesson.title,
                lesson.slug,
                lesson.summary,
                _dumps(lesson.tags),
                lesson.visibility,
                _dumps(lesson.publication_review.model_dump(mode="json")),
                _dumps_optional(lesson.published_version),
                document.id,
                document.title,
                _dumps(document.content_json),
                document.content_html,
                document.content_text,
                _dumps(document.page_settings.model_dump(mode="json")),
                _dumps_optional(lesson.board_teaching_guide),
                _dumps_optional(lesson.board_teaching_progress),
                _dumps_optional(lesson.learning_requirements),
                _dumps_optional(lesson.board_task_requirements),
                _dumps(lesson.teaching_guide.model_dump(mode="json")),
                lesson.history_graph.current_branch,
                lesson.created_at,
                lesson.updated_at,
            ),
        )
        self._document_segments.replace_segments(conn, lesson.id, document)
        for commit_index, commit in enumerate(lesson.history_graph.commits):
            self._insert_commit(conn, lesson.id, commit, commit_index)
        for branch in lesson.history_graph.branches.values():
            conn.execute(
                """
                INSERT INTO lesson_branches(
                    lesson_id, name, head_commit_id, base_commit_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (lesson.id, branch.name, branch.head_commit_id, branch.base_commit_id, branch.created_at),
            )

    def _insert_commit(
        self,
        conn: sqlite3.Connection,
        lesson_id: str,
        commit: CommitRecord,
        commit_index: int,
    ) -> None:
        snapshot = commit.snapshot
        conn.execute(
            """
            INSERT INTO lesson_commits(
                id, lesson_id, sort_order, label, message, branch_name, created_at,
                operations_json, snapshot_document_id, snapshot_title, snapshot_content_json,
                snapshot_content_html, snapshot_content_text, snapshot_page_settings_json,
                runtime_snapshot_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit.id,
                lesson_id,
                commit_index,
                commit.label,
                commit.message,
                commit.branch_name,
                commit.created_at,
                _dumps([operation.model_dump(mode="json") for operation in commit.operations]),
                snapshot.id,
                snapshot.title,
                _dumps(snapshot.content_json),
                snapshot.content_html,
                snapshot.content_text,
                _dumps(snapshot.page_settings.model_dump(mode="json")),
                _dumps_optional(commit.runtime_snapshot),
                _dumps(commit.metadata),
            ),
        )
        for parent_index, parent_id in enumerate(commit.parent_ids):
            conn.execute(
                """
                INSERT INTO lesson_commit_parents(commit_id, parent_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (commit.id, parent_id, parent_index),
            )

    def _insert_resource(
        self,
        conn: sqlite3.Connection,
        package_id: str,
        resource: ResourceLibraryItem,
        resource_index: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO resources(
                id, package_id, sort_order, name, mime_type, resource_type, size_bytes,
                uploaded_at, scope_lesson_id, concept_index_json, extracted_text_available, text_content, source_path,
                source_type, source_uri, ingestion_status, ingestion_error, ingestion_progress, ingestion_adapter,
                ingestion_job_json,
                parser_provider, parser_artifacts_path, parser_message, parse_warnings_json, source_units_json,
                page_structure_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource.id,
                package_id,
                resource_index,
                resource.name,
                resource.mime_type,
                resource.resource_type,
                resource.size_bytes,
                resource.uploaded_at,
                resource.scope_lesson_id,
                _dumps(resource.concept_index),
                int(resource.extracted_text_available),
                resource.text_content,
                resource.source_path,
                resource.source_type,
                resource.source_uri,
                resource.ingestion_status,
                resource.ingestion_error,
                resource.ingestion_progress,
                resource.ingestion_adapter,
                _dumps(resource.ingestion_job.model_dump(mode="json")) if resource.ingestion_job is not None else None,
                resource.parser_provider,
                resource.parser_artifacts_path,
                resource.parser_message,
                _dumps(resource.parse_warnings),
                _dumps([unit.model_dump(mode="json") for unit in resource.source_units]),
                _dumps(resource.page_structure.model_dump(mode="json")) if resource.page_structure is not None else None,
            ),
        )
        for chapter_index, chapter in enumerate(resource.outline):
            conn.execute(
                """
                INSERT INTO resource_chapters(
                    id, resource_id, sort_order, title, level, page_range, page_start, page_end,
                    summary, keywords_json, prerequisites_json, parent_id, parent_title, path_json,
                    locator_hint, order_index, scan_strategy, body_start_order, body_end_order,
                    body_page_start, body_page_end, body_match_status, body_match_confidence, body_match_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter.id,
                    resource.id,
                    chapter_index,
                    chapter.title,
                    chapter.level,
                    chapter.page_range,
                    chapter.page_start,
                    chapter.page_end,
                    chapter.summary,
                    _dumps(chapter.keywords),
                    _dumps(chapter.prerequisites),
                    chapter.parent_id,
                    chapter.parent_title,
                    _dumps(chapter.path),
                    chapter.locator_hint,
                    chapter.order_index,
                    chapter.scan_strategy,
                    chapter.body_start_order,
                    chapter.body_end_order,
                    chapter.body_page_start,
                    chapter.body_page_end,
                    chapter.body_match_status,
                    chapter.body_match_confidence,
                    chapter.body_match_reason,
                ),
            )

    def _load_legacy_workspace(self) -> WorkspaceState | None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return None
        raw_text = self.legacy_json_path.read_text(encoding="utf-8")
        try:
            raw_data = json.loads(raw_text)
            if _contains_legacy_blocks(raw_data):
                self._backup_legacy_json(raw_text, "legacy-blocks-backup")
                return build_initial_workspace_state()
            if isinstance(raw_data, dict) and isinstance(raw_data.get("packages"), list):
                return WorkspaceState.model_validate(raw_data)
            package = CoursePackage.model_validate(raw_data)
            return WorkspaceState(packages=[package], active_package_id=package.id)
        except Exception:
            self._backup_legacy_json(raw_text, "invalid-backup")
            return None

    def _backup_legacy_json(self, raw_text: str, suffix: str) -> None:
        if self.legacy_json_path is None:
            return
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.legacy_json_path.with_name(f"{self.legacy_json_path.stem}.{suffix}-{timestamp}.json")
        backup_path.write_text(raw_text, encoding="utf-8")

    def _archive_legacy_json(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive_path = self.legacy_json_path.with_name(f"{self.legacy_json_path.stem}.migrated-{timestamp}.json")
        self.legacy_json_path.replace(archive_path)


def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM workspace_settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] == "":
        return None
    return row["value"]


def _chat_input_event_record(raw_record: str) -> dict[str, Any]:
    record = _loads(raw_record, {})
    if not isinstance(record, dict) or record.get("version") != 1:
        raise RuntimeError("The persisted chat input event record is invalid.")
    return record


def _require_chat_input_fingerprint(
    record: dict[str, Any],
    fingerprint: str,
) -> None:
    if record.get("fingerprint") != fingerprint:
        raise ValueError("The chat input event id was reused for a different request.")


def _store_timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _prune_completed_chat_input_events(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
) -> None:
    prefix = _chat_input_event_user_prefix(owner_user_id)
    rows = conn.execute(
        "SELECT key, value FROM workspace_settings WHERE key LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    completed: list[tuple[str, str]] = []
    for row in rows:
        try:
            record = _chat_input_event_record(row["value"])
        except (RuntimeError, json.JSONDecodeError, TypeError):
            continue
        if record.get("state") == "completed":
            completed.append((str(record.get("completed_at") or ""), str(row["key"])))
    completed.sort(reverse=True)
    for _completed_at, setting_key in completed[_CHAT_INPUT_EVENT_RETENTION_PER_USER:]:
        conn.execute(
            "DELETE FROM workspace_settings WHERE key = ?",
            (setting_key,),
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _ordered_values(conn: sqlite3.Connection, table: str, package_id: str) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT lesson_id FROM {table}
        WHERE package_id = ?
        ORDER BY sort_order, lesson_id
        """,
        (package_id,),
    ).fetchall()
    return [row["lesson_id"] for row in rows]


def _document_from_row(row: sqlite3.Row, prefix: str) -> BoardDocument:
    title_key = "board_document_title" if prefix == "board" else f"{prefix}_title"
    return upgrade_markdown_like_document(
        BoardDocument(
            id=row[f"{prefix}_document_id"],
            title=row[title_key],
            content_json=_loads(row[f"{prefix}_content_json"], {"type": "doc", "content": [{"type": "paragraph"}]}),
            content_html=row[f"{prefix}_content_html"],
            content_text=row[f"{prefix}_content_text"],
            page_settings=_loads(row[f"{prefix}_page_settings_json"], {}),
        )
    )


def _runtime_snapshot_from_legacy_metadata(metadata: dict[str, Any]) -> LessonRuntimeSnapshot:
    requirement = metadata.get("active_requirement_sheet_after")
    board_task = metadata.get("active_board_task_sheet_after")
    return LessonRuntimeSnapshot(
        learning_requirements=(
            requirement if isinstance(requirement, dict) else None
        ),
        board_task_requirements=(
            board_task if isinstance(board_task, dict) else None
        ),
    )


def _merge_session_payload(session: LessonMergeSession) -> dict[str, Any]:
    payload = session.model_dump(mode="json")
    payload["owner_user_id"] = session.owner_user_id
    payload["merge_blueprint"] = session.merge_blueprint
    return payload


def _merge_session_from_row(row: sqlite3.Row) -> LessonMergeSession:
    payload = _loads(row["payload_json"], {})
    payload.update(
        {
            "owner_user_id": row["owner_user_id"],
            "status": row["status"],
            "version": int(row["version"]),
            "updated_at": row["updated_at"],
        }
    )
    return LessonMergeSession.model_validate(payload)


def _upsert_merge_session(conn: sqlite3.Connection, session: LessonMergeSession) -> None:
    conn.execute(
        """
        INSERT INTO lesson_merge_sessions(
            id, owner_user_id, lesson_id, status, target_branch_name,
            source_branch_name, base_commit_id, target_head_commit_id,
            source_head_commit_id, version, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            target_branch_name = excluded.target_branch_name,
            source_branch_name = excluded.source_branch_name,
            base_commit_id = excluded.base_commit_id,
            target_head_commit_id = excluded.target_head_commit_id,
            source_head_commit_id = excluded.source_head_commit_id,
            version = excluded.version,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        WHERE lesson_merge_sessions.owner_user_id = excluded.owner_user_id
        """,
        (
            session.id,
            session.owner_user_id,
            session.lesson_id,
            session.status,
            session.target_branch_name,
            session.source_branch_name,
            session.base_commit_id,
            session.target_head_commit_id,
            session.source_head_commit_id,
            session.version,
            _dumps(_merge_session_payload(session)),
            session.created_at,
            session.updated_at,
        ),
    )


def _insert_contribution(conn: sqlite3.Connection, contribution: LessonContribution) -> None:
    conn.execute(
        """
        INSERT INTO lesson_contributions(
            id, source_lesson_id, source_owner_user_id, contributor_lesson_id,
            contributor_user_id, source_title, title, description, status, version,
            current_revision, current_revision_id, source_author_json, contributor_json,
            merge_session_id, merged_commit_id, created_at, updated_at, closed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            contribution.id,
            contribution.source_lesson_id,
            contribution.source_owner_user_id,
            contribution.contributor_lesson_id,
            contribution.contributor_user_id,
            contribution.source_title,
            contribution.title,
            contribution.description,
            contribution.status,
            contribution.version,
            contribution.current_revision,
            contribution.current_revision_id,
            _dumps(contribution.source_author.model_dump(mode="json")),
            _dumps(contribution.contributor.model_dump(mode="json")),
            contribution.merge_session_id,
            contribution.merged_commit_id,
            contribution.created_at,
            contribution.updated_at,
            contribution.closed_at,
        ),
    )


def _update_contribution_if_version(
    conn: sqlite3.Connection,
    contribution: LessonContribution,
    *,
    expected_version: int,
) -> bool:
    updated = conn.execute(
        """
        UPDATE lesson_contributions SET
            title = ?, description = ?, status = ?, version = ?,
            current_revision = ?, current_revision_id = ?,
            merge_session_id = ?, merged_commit_id = ?,
            updated_at = ?, closed_at = ?
        WHERE id = ? AND version = ?
        """,
        (
            contribution.title,
            contribution.description,
            contribution.status,
            contribution.version,
            contribution.current_revision,
            contribution.current_revision_id,
            contribution.merge_session_id,
            contribution.merged_commit_id,
            contribution.updated_at,
            contribution.closed_at,
            contribution.id,
            expected_version,
        ),
    )
    return updated.rowcount == 1


def _insert_contribution_revision(conn: sqlite3.Connection, revision: LessonContributionRevision) -> None:
    conn.execute(
        """
        INSERT INTO lesson_contribution_revisions(
            id, contribution_id, revision_number, source_commit_id,
            contributor_commit_id, base_document_json, proposed_document_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision.id,
            revision.contribution_id,
            revision.revision_number,
            revision.source_commit_id,
            revision.contributor_commit_id,
            _dumps(revision.base_document.model_dump(mode="json")),
            _dumps(revision.proposed_document.model_dump(mode="json")),
            revision.created_at,
        ),
    )


def _insert_contribution_event(conn: sqlite3.Connection, event: LessonContributionEvent) -> None:
    conn.execute(
        """
        INSERT INTO lesson_contribution_events(
            id, contribution_id, kind, actor_json, body, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            event.contribution_id,
            event.kind,
            _dumps(event.actor.model_dump(mode="json")),
            event.body,
            _dumps(event.metadata),
            event.created_at,
        ),
    )


def _load_contribution_bundle(
    conn: sqlite3.Connection,
    contribution_id: str,
) -> tuple[LessonContribution, LessonContributionRevision, list[LessonContributionEvent]] | None:
    row = conn.execute(
        "SELECT * FROM lesson_contributions WHERE id = ?",
        (contribution_id,),
    ).fetchone()
    if row is None:
        return None
    contribution = LessonContribution.model_validate(
        {
            **dict(row),
            "source_author": _loads(row["source_author_json"], {}),
            "contributor": _loads(row["contributor_json"], {}),
        }
    )
    revision_row = conn.execute(
        "SELECT * FROM lesson_contribution_revisions WHERE id = ? AND contribution_id = ?",
        (contribution.current_revision_id, contribution.id),
    ).fetchone()
    if revision_row is None:
        raise ValueError(f"Contribution {contribution.id} has no current revision")
    revision = LessonContributionRevision.model_validate(
        {
            **dict(revision_row),
            "base_document": _loads(revision_row["base_document_json"], {}),
            "proposed_document": _loads(revision_row["proposed_document_json"], {}),
        }
    )
    event_rows = conn.execute(
        """
        SELECT * FROM lesson_contribution_events
        WHERE contribution_id = ? ORDER BY created_at, id
        """,
        (contribution.id,),
    ).fetchall()
    events = [
        LessonContributionEvent.model_validate(
            {
                **dict(event_row),
                "actor": _loads(event_row["actor_json"], {}),
                "metadata": _loads(event_row["metadata_json"], {}),
            }
        )
        for event_row in event_rows
    ]
    return contribution, revision, events


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _dumps_optional(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return _dumps(value.model_dump(mode="json"))
    return _dumps(value)


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


def _loads_optional(raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    return json.loads(raw)


def _contains_legacy_blocks(raw_data: object) -> bool:
    if not isinstance(raw_data, dict):
        return False
    lessons: list[object] = []
    raw_lessons = raw_data.get("lessons")
    if isinstance(raw_lessons, list):
        lessons.extend(raw_lessons)
    raw_packages = raw_data.get("packages")
    if isinstance(raw_packages, list):
        for package in raw_packages:
            if isinstance(package, dict) and isinstance(package.get("lessons"), list):
                lessons.extend(package["lessons"])
    for lesson in lessons:
        if isinstance(lesson, dict):
            board_document = lesson.get("board_document")
            if isinstance(board_document, dict) and isinstance(board_document.get("blocks"), list):
                return True
    return False


def build_initial_workspace_state() -> WorkspaceState:
    return build_empty_account_workspace_state()


def build_empty_account_workspace_state() -> WorkspaceState:
    package = CoursePackage(
        title="OpenClass Course Workspace",
        summary="把 lesson 当作可编辑、可分支、可讲解的课程资产。",
        lessons=[],
        course_graph=[],
        open_lesson_ids=[],
        active_lesson_id=None,
        workspace_tab_order=[],
    )
    return WorkspaceState(packages=[package], active_package_id=package.id)

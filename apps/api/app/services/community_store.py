from __future__ import annotations

import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models import (
    CommunityCommentView,
    CommunityPostCreate,
    CommunityPostDetail,
    CommunityPostView,
    CommunitySpaceCreate,
    CommunitySpaceView,
    UserView,
    new_id,
    now_iso,
)


class CommunityNotFoundError(ValueError):
    pass


class CommunityConflictError(ValueError):
    pass


class CommunityValidationError(ValueError):
    pass


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    result: list[str] = []
    previous_was_separator = False
    for character in normalized:
        if character.isalnum():
            result.append(character)
            previous_was_separator = False
        elif result and not previous_was_separator:
            result.append("-")
            previous_was_separator = True
    return "".join(result).strip("-")[:80]


def _normalize_tag(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", value).lstrip("#").strip().casefold().split()
    )


def _author_name(user: UserView) -> str:
    if user.display_name and user.display_name.strip():
        return user.display_name.strip()
    local_part = user.email.partition("@")[0].strip()
    return local_part or "OpenClass learner"


class CommunityStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._lock:
            with self._connect() as conn, conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS community_spaces (
                        id TEXT PRIMARY KEY,
                        slug TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        creator_user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS community_posts (
                        id TEXT PRIMARY KEY,
                        community_id TEXT NOT NULL REFERENCES community_spaces(id) ON DELETE CASCADE,
                        author_user_id TEXT NOT NULL,
                        author_display_name TEXT NOT NULL,
                        post_type TEXT NOT NULL CHECK(post_type IN ('question', 'discussion', 'resource', 'study_note')),
                        title TEXT NOT NULL,
                        body TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS community_post_tags (
                        post_id TEXT NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
                        normalized_tag TEXT NOT NULL,
                        display_tag TEXT NOT NULL,
                        PRIMARY KEY (post_id, normalized_tag)
                    );

                    CREATE TABLE IF NOT EXISTS community_comments (
                        id TEXT PRIMARY KEY,
                        post_id TEXT NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
                        parent_comment_id TEXT REFERENCES community_comments(id) ON DELETE CASCADE,
                        author_user_id TEXT NOT NULL,
                        author_display_name TEXT NOT NULL,
                        body TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS community_reactions (
                        post_id TEXT NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        value INTEGER NOT NULL CHECK(value IN (-1, 1)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (post_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS community_follows (
                        community_id TEXT NOT NULL REFERENCES community_spaces(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (community_id, user_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_community_posts_space_created
                        ON community_posts(community_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_community_tags_tag
                        ON community_post_tags(normalized_tag, post_id);
                    CREATE INDEX IF NOT EXISTS idx_community_comments_post_created
                        ON community_comments(post_id, created_at);
                    """
                )

    def create_space(self, payload: CommunitySpaceCreate, user: UserView) -> CommunitySpaceView:
        requested_slug = payload.slug or payload.name
        slug = _slugify(requested_slug)
        if not slug:
            raise CommunityValidationError("社区名称需要包含文字或数字")
        timestamp = now_iso()
        space_id = new_id("community")
        with self._lock:
            with self._connect() as conn, conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO community_spaces(
                            id, slug, name, description, creator_user_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            space_id,
                            slug,
                            payload.name,
                            payload.description,
                            user.id,
                            timestamp,
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise CommunityConflictError("该社区名称或地址已存在") from exc
        return self.get_space(slug)

    def list_spaces(self, *, sort: str = "active") -> list[CommunitySpaceView]:
        order_sql = {
            "active": "post_count DESC, follower_count DESC, space.updated_at DESC",
            "new": "space.created_at DESC",
            "popular": "follower_count DESC, post_count DESC, space.name COLLATE NOCASE",
        }.get(sort)
        if order_sql is None:
            raise CommunityValidationError("不支持的社区排序方式")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT space.*,
                       COUNT(DISTINCT post.id) AS post_count,
                       COUNT(DISTINCT follow.user_id) AS follower_count
                FROM community_spaces AS space
                LEFT JOIN community_posts AS post ON post.community_id = space.id
                LEFT JOIN community_follows AS follow ON follow.community_id = space.id
                GROUP BY space.id
                ORDER BY {order_sql}
                """
            ).fetchall()
            return [self._space_from_row(row) for row in rows]

    def get_space(self, slug: str) -> CommunitySpaceView:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT space.*,
                       COUNT(DISTINCT post.id) AS post_count,
                       COUNT(DISTINCT follow.user_id) AS follower_count
                FROM community_spaces AS space
                LEFT JOIN community_posts AS post ON post.community_id = space.id
                LEFT JOIN community_follows AS follow ON follow.community_id = space.id
                WHERE space.slug = ?
                GROUP BY space.id
                """,
                (_slugify(slug),),
            ).fetchone()
            if row is None:
                raise CommunityNotFoundError("社区不存在")
            return self._space_from_row(row)

    def create_post(self, payload: CommunityPostCreate, user: UserView) -> CommunityPostView:
        timestamp = now_iso()
        post_id = new_id("post")
        with self._lock:
            with self._connect() as conn, conn:
                space = conn.execute(
                    "SELECT id FROM community_spaces WHERE slug = ?",
                    (_slugify(payload.community_slug),),
                ).fetchone()
                if space is None:
                    raise CommunityNotFoundError("社区不存在")
                conn.execute(
                    """
                    INSERT INTO community_posts(
                        id, community_id, author_user_id, author_display_name,
                        post_type, title, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post_id,
                        space["id"],
                        user.id,
                        _author_name(user),
                        payload.post_type,
                        payload.title,
                        payload.body,
                        timestamp,
                        timestamp,
                    ),
                )
                for display_tag in payload.tags:
                    normalized_tag = _normalize_tag(display_tag)
                    if normalized_tag:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO community_post_tags(
                                post_id, normalized_tag, display_tag
                            ) VALUES (?, ?, ?)
                            """,
                            (post_id, normalized_tag, display_tag),
                        )
                conn.execute(
                    "UPDATE community_spaces SET updated_at = ? WHERE id = ?",
                    (timestamp, space["id"]),
                )
        return self.get_post(post_id, viewer_user_id=user.id).post

    def list_posts(
        self,
        *,
        community_slug: str = "",
        tag: str = "",
        query: str = "",
        sort: str = "recent",
        viewer_user_id: str | None = None,
        limit: int = 50,
    ) -> list[CommunityPostView]:
        clauses: list[str] = []
        params: list[object] = []
        if community_slug:
            clauses.append("space.slug = ?")
            params.append(_slugify(community_slug))
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM community_post_tags filter_tag "
                "WHERE filter_tag.post_id = post.id AND filter_tag.normalized_tag = ?)"
            )
            params.append(_normalize_tag(tag))
        if query.strip():
            clauses.append("(post.title LIKE ? OR post.body LIKE ?)")
            term = f"%{query.strip()}%"
            params.extend([term, term])
        if sort == "unanswered":
            clauses.append("post.post_type = 'question'")
            clauses.append("NOT EXISTS (SELECT 1 FROM community_comments c WHERE c.post_id = post.id)")
            order_sql = "post.created_at DESC"
        elif sort == "hot":
            order_sql = "vote_score DESC, comment_count DESC, post.created_at DESC"
        elif sort == "recent":
            order_sql = "post.created_at DESC"
        else:
            raise CommunityValidationError("不支持的帖子排序方式")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 100)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT post.*, space.slug AS community_slug, space.name AS community_name,
                       COALESCE((SELECT SUM(value) FROM community_reactions reaction
                                 WHERE reaction.post_id = post.id), 0) AS vote_score,
                       (SELECT COUNT(*) FROM community_comments comment
                        WHERE comment.post_id = post.id) AS comment_count
                FROM community_posts AS post
                JOIN community_spaces AS space ON space.id = post.community_id
                {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._post_from_row(conn, row, viewer_user_id) for row in rows]

    def get_post(self, post_id: str, *, viewer_user_id: str | None = None) -> CommunityPostDetail:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT post.*, space.slug AS community_slug, space.name AS community_name,
                       COALESCE((SELECT SUM(value) FROM community_reactions reaction
                                 WHERE reaction.post_id = post.id), 0) AS vote_score,
                       (SELECT COUNT(*) FROM community_comments comment
                        WHERE comment.post_id = post.id) AS comment_count
                FROM community_posts AS post
                JOIN community_spaces AS space ON space.id = post.community_id
                WHERE post.id = ?
                """,
                (post_id,),
            ).fetchone()
            if row is None:
                raise CommunityNotFoundError("帖子不存在")
            comments = conn.execute(
                """
                SELECT * FROM community_comments
                WHERE post_id = ?
                ORDER BY created_at, id
                """,
                (post_id,),
            ).fetchall()
            return CommunityPostDetail(
                post=self._post_from_row(conn, row, viewer_user_id),
                comments=[CommunityCommentView(**dict(comment)) for comment in comments],
            )

    def add_comment(
        self,
        post_id: str,
        *,
        body: str,
        parent_comment_id: str | None,
        user: UserView,
    ) -> CommunityCommentView:
        timestamp = now_iso()
        comment_id = new_id("comment")
        with self._lock:
            with self._connect() as conn, conn:
                if conn.execute("SELECT 1 FROM community_posts WHERE id = ?", (post_id,)).fetchone() is None:
                    raise CommunityNotFoundError("帖子不存在")
                if parent_comment_id:
                    parent = conn.execute(
                        "SELECT post_id FROM community_comments WHERE id = ?",
                        (parent_comment_id,),
                    ).fetchone()
                    if parent is None or parent["post_id"] != post_id:
                        raise CommunityValidationError("回复目标不属于当前帖子")
                conn.execute(
                    """
                    INSERT INTO community_comments(
                        id, post_id, parent_comment_id, author_user_id,
                        author_display_name, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        post_id,
                        parent_comment_id,
                        user.id,
                        _author_name(user),
                        body,
                        timestamp,
                        timestamp,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM community_comments WHERE id = ?",
                    (comment_id,),
                ).fetchone()
                assert row is not None
                return CommunityCommentView(**dict(row))

    def set_vote(self, post_id: str, *, user_id: str, value: int) -> tuple[int, int]:
        if value not in {-1, 0, 1}:
            raise CommunityValidationError("投票值必须是 -1、0 或 1")
        timestamp = now_iso()
        with self._lock:
            with self._connect() as conn, conn:
                if conn.execute("SELECT 1 FROM community_posts WHERE id = ?", (post_id,)).fetchone() is None:
                    raise CommunityNotFoundError("帖子不存在")
                if value == 0:
                    conn.execute(
                        "DELETE FROM community_reactions WHERE post_id = ? AND user_id = ?",
                        (post_id, user_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO community_reactions(post_id, user_id, value, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(post_id, user_id) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (post_id, user_id, value, timestamp, timestamp),
                    )
                score = int(
                    conn.execute(
                        "SELECT COALESCE(SUM(value), 0) FROM community_reactions WHERE post_id = ?",
                        (post_id,),
                    ).fetchone()[0]
                )
                return value, score

    def set_follow(self, community_slug: str, *, user_id: str, following: bool) -> tuple[str, int]:
        with self._lock:
            with self._connect() as conn, conn:
                space = conn.execute(
                    "SELECT id FROM community_spaces WHERE slug = ?",
                    (_slugify(community_slug),),
                ).fetchone()
                if space is None:
                    raise CommunityNotFoundError("社区不存在")
                if following:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO community_follows(community_id, user_id, created_at)
                        VALUES (?, ?, ?)
                        """,
                        (space["id"], user_id, now_iso()),
                    )
                else:
                    conn.execute(
                        "DELETE FROM community_follows WHERE community_id = ? AND user_id = ?",
                        (space["id"], user_id),
                    )
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM community_follows WHERE community_id = ?",
                        (space["id"],),
                    ).fetchone()[0]
                )
                return str(space["id"]), count

    def _space_from_row(self, row: sqlite3.Row) -> CommunitySpaceView:
        return CommunitySpaceView(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"],
            creator_user_id=row["creator_user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            post_count=int(row["post_count"]),
            follower_count=int(row["follower_count"]),
        )

    def _post_from_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        viewer_user_id: str | None,
    ) -> CommunityPostView:
        tags = [
            tag["display_tag"]
            for tag in conn.execute(
                "SELECT display_tag FROM community_post_tags WHERE post_id = ? ORDER BY rowid",
                (row["id"],),
            ).fetchall()
        ]
        viewer_vote = 0
        if viewer_user_id:
            vote = conn.execute(
                "SELECT value FROM community_reactions WHERE post_id = ? AND user_id = ?",
                (row["id"], viewer_user_id),
            ).fetchone()
            viewer_vote = int(vote["value"]) if vote is not None else 0
        return CommunityPostView(
            id=row["id"],
            community_id=row["community_id"],
            community_slug=row["community_slug"],
            community_name=row["community_name"],
            author_user_id=row["author_user_id"],
            author_display_name=row["author_display_name"],
            post_type=row["post_type"],
            title=row["title"],
            body=row["body"],
            tags=tags,
            vote_score=int(row["vote_score"]),
            comment_count=int(row["comment_count"]),
            viewer_vote=viewer_vote,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

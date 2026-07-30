from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from app.models import LessonContribution, UserView, new_id, now_iso
from app.project_collaboration_models import (
    CheckStatus,
    IncomingProjectInvitationView,
    IntegrationAppView,
    OrganizationView,
    ProjectAuditEventView,
    ProjectBoardColumnView,
    ProjectCapabilityView,
    ProjectCheckView,
    ProjectGovernanceSummaryView,
    ProjectGovernanceView,
    ProjectInvitationView,
    ProjectKind,
    ProjectLabelView,
    ProjectMemberView,
    ProjectMilestoneView,
    ProjectPolicyView,
    ProjectReviewView,
    ProjectRole,
    ProjectWebhookDeliveryView,
    ProjectWebhookView,
    ProjectWorkItemView,
    ReviewDecision,
    TeamView,
)
from app.services.course_store import SqliteCourseStore
from app.services.workspace_state import find_lesson_package, get_package

ROLE_PRIORITY: dict[ProjectRole, int] = {
    "viewer": 1,
    "reviewer": 2,
    "editor": 3,
    "maintainer": 4,
    "owner": 5,
}


class ProjectCollaborationError(ValueError):
    pass


class ProjectCollaborationNotFoundError(ProjectCollaborationError):
    pass


class ProjectCollaborationPermissionError(ProjectCollaborationError):
    pass


class ProjectCollaborationConflictError(ProjectCollaborationError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def capabilities_for_role(role: ProjectRole) -> ProjectCapabilityView:
    return ProjectCapabilityView(
        view_project=True,
        edit_project=role in {"owner", "maintainer", "editor"},
        review_changes=role in {"owner", "maintainer", "reviewer"},
        merge_changes=role == "owner",
        manage_members=role in {"owner", "maintainer"},
        manage_rules=role in {"owner", "maintainer"},
        manage_work_items=role in {"owner", "maintainer", "editor", "reviewer"},
        manage_integrations=role in {"owner", "maintainer"},
    )


class ProjectCollaborationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_members (
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    added_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_kind, project_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_members_user
                    ON project_members(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS project_invitations (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    invited_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_invitations_pending
                    ON project_invitations(project_kind, project_id, email)
                    WHERE status = 'pending';

                CREATE TABLE IF NOT EXISTS project_policies (
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    protect_default_branch INTEGER NOT NULL DEFAULT 0,
                    required_approvals INTEGER NOT NULL DEFAULT 0,
                    require_passing_checks INTEGER NOT NULL DEFAULT 0,
                    dismiss_stale_approvals INTEGER NOT NULL DEFAULT 1,
                    updated_by_user_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_kind, project_id)
                );

                CREATE TABLE IF NOT EXISTS project_reviews (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    contribution_id TEXT NOT NULL,
                    reviewer_user_id TEXT NOT NULL,
                    reviewer_display_name TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    body TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(contribution_id, reviewer_user_id)
                );

                CREATE TABLE IF NOT EXISTS project_checks (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    contribution_id TEXT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    required INTEGER NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_checks_target
                    ON project_checks(project_kind, project_id, contribution_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS project_labels (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color TEXT NOT NULL,
                    description TEXT NOT NULL,
                    UNIQUE(project_kind, project_id, name)
                );

                CREATE TABLE IF NOT EXISTS project_milestones (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    due_at TEXT,
                    closed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS project_board_columns (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    UNIQUE(project_kind, project_id, name)
                );

                CREATE TABLE IF NOT EXISTS project_work_items (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    state TEXT NOT NULL,
                    author_user_id TEXT NOT NULL,
                    assignee_user_id TEXT,
                    milestone_id TEXT,
                    label_ids_json TEXT NOT NULL,
                    board_column_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_kind, project_id, number)
                );

                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    created_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS organization_members (
                    organization_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(organization_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, slug)
                );
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS team_project_grants (
                    team_id TEXT NOT NULL,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, project_kind, project_id)
                );

                CREATE TABLE IF NOT EXISTS project_webhooks (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    events_json TEXT NOT NULL,
                    secret TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_delivery_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_webhook_deliveries (
                    id TEXT PRIMARY KEY,
                    webhook_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    next_attempt_at TEXT,
                    response_status INTEGER,
                    response_body TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS integration_apps (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    permissions_json TEXT NOT NULL,
                    callback_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_integration_installations (
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    installed_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_kind, project_id, app_id)
                );

                CREATE TABLE IF NOT EXISTS project_audit_events (
                    id TEXT PRIMARY KEY,
                    project_kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_audit_timeline
                    ON project_audit_events(project_kind, project_id, created_at DESC);
                """
            )
            delivery_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(project_webhook_deliveries)").fetchall()
            }
            if "idempotency_key" not in delivery_columns:
                conn.execute("ALTER TABLE project_webhook_deliveries ADD COLUMN idempotency_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_webhook_delivery_idempotency ON project_webhook_deliveries(webhook_id, idempotency_key)"
            )

    def project_record(self, kind: ProjectKind, project_id: str) -> tuple[str, str]:
        with self._connect() as conn:
            if kind == "package":
                row = conn.execute(
                    "SELECT owner_user_id, title FROM course_packages WHERE id = ? AND sort_order > 0",
                    (project_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT course_packages.owner_user_id, lessons.title
                    FROM lessons JOIN course_packages ON course_packages.id = lessons.package_id
                    WHERE lessons.id = ?
                    """,
                    (project_id,),
                ).fetchone()
        if row is None:
            raise ProjectCollaborationNotFoundError("项目不存在。")
        return str(row["owner_user_id"]), str(row["title"])

    def _audit(
        self,
        conn: sqlite3.Connection,
        kind: ProjectKind,
        project_id: str,
        actor_user_id: str,
        event_kind: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        event_id = new_id("projectevent")
        created_at = now_iso()
        conn.execute(
            "INSERT INTO project_audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                kind,
                project_id,
                actor_user_id,
                event_kind,
                json.dumps(metadata or {}, ensure_ascii=False),
                created_at,
            ),
        )
        self._queue_webhook_event(
            conn,
            kind,
            project_id,
            event_kind,
            {
                "event_id": event_id,
                "event": event_kind,
                "project_kind": kind,
                "project_id": project_id,
                "actor_user_id": actor_user_id,
                "metadata": metadata or {},
                "created_at": created_at,
            },
            idempotency_key=event_id,
        )

    def _queue_webhook_event(
        self,
        conn: sqlite3.Connection,
        kind: ProjectKind,
        project_id: str,
        event: str,
        payload: dict[str, object],
        *,
        idempotency_key: str,
    ) -> None:
        timestamp = now_iso()
        hooks = conn.execute(
            "SELECT id, events_json FROM project_webhooks WHERE project_kind = ? AND project_id = ? AND status = 'active'",
            (kind, project_id),
        ).fetchall()
        for hook in hooks:
            subscribed = json.loads(hook["events_json"])
            if event not in subscribed and "*" not in subscribed and "project.updated" not in subscribed and not any(
                item.endswith(".*") and event.startswith(item[:-1]) for item in subscribed
            ):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO project_webhook_deliveries(
                    id, webhook_id, event, idempotency_key, payload_json, status,
                    attempts, next_attempt_at, response_status, response_body, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, NULL, NULL, ?, ?)
                """,
                (
                    new_id("webhookdelivery"),
                    hook["id"],
                    event,
                    idempotency_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    @staticmethod
    def _webhook_delivery_enabled() -> bool:
        return os.getenv("OPENCLASS_WEBHOOK_DELIVERY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _assert_public_delivery_target(url: str) -> None:
        hostname = urlparse(url).hostname
        if not hostname:
            raise ProjectCollaborationConflictError("Webhook 地址无效。")
        for family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
            address = sockaddr[0]
            if not ipaddress.ip_address(address).is_global:
                raise ProjectCollaborationConflictError("Webhook 地址解析到了私有网络。")

    def deliver_pending_webhooks(self, *, limit: int = 20) -> int:
        if not self._webhook_delivery_enabled():
            return 0
        delivered = 0
        current = datetime.now(timezone.utc)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT deliveries.*, hooks.url, hooks.secret
                FROM project_webhook_deliveries deliveries
                JOIN project_webhooks hooks ON hooks.id = deliveries.webhook_id
                WHERE deliveries.status IN ('queued', 'retrying') AND deliveries.attempts < 5
                  AND (deliveries.next_attempt_at IS NULL OR deliveries.next_attempt_at <= ?)
                ORDER BY deliveries.created_at LIMIT ?
                """,
                (current.isoformat(), limit),
            ).fetchall()
            for row in rows:
                payload = row["payload_json"].encode("utf-8")
                signature = "sha256=" + hmac.new(row["secret"].encode("utf-8"), payload, hashlib.sha256).hexdigest()
                attempts = int(row["attempts"]) + 1
                status = "delivered"
                response_status: int | None = None
                response_body = ""
                next_attempt_at: str | None = None
                try:
                    self._assert_public_delivery_target(row["url"])
                    request = urllib.request.Request(
                        row["url"],
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "OpenClass-Webhook/1.0",
                            "X-OpenClass-Event": row["event"],
                            "X-OpenClass-Delivery": row["id"],
                            "X-OpenClass-Signature-256": signature,
                            "Idempotency-Key": row["idempotency_key"] or row["id"],
                        },
                        method="POST",
                    )
                    opener = urllib.request.build_opener(_RejectRedirects())
                    with opener.open(request, timeout=8) as response:
                        response_status = response.status
                        response_body = response.read(2048).decode("utf-8", "replace")
                    if response_status < 200 or response_status >= 300:
                        raise urllib.error.HTTPError(row["url"], response_status, "Webhook rejected", {}, None)
                except Exception as exc:
                    response_body = str(exc)[:2000]
                    status = "failed" if attempts >= 5 else "retrying"
                    if status == "retrying":
                        next_attempt_at = (current + timedelta(minutes=2 ** (attempts - 1))).isoformat()
                with conn:
                    conn.execute(
                        """
                        UPDATE project_webhook_deliveries SET status = ?, attempts = ?,
                            next_attempt_at = ?, response_status = ?, response_body = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (status, attempts, next_attempt_at, response_status, response_body, now_iso(), row["id"]),
                    )
                    conn.execute(
                        "UPDATE project_webhooks SET last_delivery_status = ?, updated_at = ? WHERE id = ?",
                        (status, now_iso(), row["webhook_id"]),
                    )
                delivered += status == "delivered"
        return delivered

    def webhook_deliveries(
        self,
        kind: ProjectKind,
        project_id: str,
        user: UserView,
        *,
        limit: int = 50,
    ) -> list[ProjectWebhookDeliveryView]:
        self.require_capability(kind, project_id, user, "manage_integrations")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT deliveries.* FROM project_webhook_deliveries deliveries
                JOIN project_webhooks hooks ON hooks.id = deliveries.webhook_id
                WHERE hooks.project_kind = ? AND hooks.project_id = ?
                ORDER BY deliveries.created_at DESC LIMIT ?
                """,
                (kind, project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [
            ProjectWebhookDeliveryView(
                id=row["id"],
                webhook_id=row["webhook_id"],
                event=row["event"],
                status=row["status"],
                attempts=row["attempts"],
                next_attempt_at=row["next_attempt_at"],
                response_status=row["response_status"],
                response_body=row["response_body"] or "",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def record_event(
        self,
        kind: ProjectKind,
        project_id: str,
        actor_user_id: str,
        event: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.project_record(kind, project_id)
        with self._lock, self._connect() as conn, conn:
            self._audit(conn, kind, project_id, actor_user_id, event, metadata)

    def ensure_owner(self, kind: ProjectKind, project_id: str, user: UserView | None = None) -> None:
        owner_user_id, _ = self.project_record(kind, project_id)
        timestamp = now_iso()
        email = user.email if user and user.id == owner_user_id else ""
        display_name = (user.display_name or user.email) if user and user.id == owner_user_id else "项目所有者"
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO project_members(
                    project_kind, project_id, user_id, email, display_name, role,
                    added_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'owner', ?, ?, ?)
                ON CONFLICT(project_kind, project_id, user_id) DO UPDATE SET
                    email = CASE WHEN excluded.email <> '' THEN excluded.email ELSE project_members.email END,
                    display_name = CASE WHEN excluded.email <> '' THEN excluded.display_name ELSE project_members.display_name END,
                    role = 'owner', updated_at = excluded.updated_at
                """,
                (kind, project_id, owner_user_id, email, display_name, owner_user_id, timestamp, timestamp),
            )

    def viewer_role(self, kind: ProjectKind, project_id: str, user_id: str) -> ProjectRole | None:
        owner_user_id, _ = self.project_record(kind, project_id)
        if user_id == owner_user_id:
            return "owner"
        roles: list[ProjectRole] = []
        with self._connect() as conn:
            direct = conn.execute(
                "SELECT role FROM project_members WHERE project_kind = ? AND project_id = ? AND user_id = ?",
                (kind, project_id, user_id),
            ).fetchone()
            if direct:
                roles.append(direct["role"])
            team_rows = conn.execute(
                """
                SELECT grants.role FROM team_project_grants grants
                JOIN team_members members ON members.team_id = grants.team_id
                WHERE grants.project_kind = ? AND grants.project_id = ? AND members.user_id = ?
                """,
                (kind, project_id, user_id),
            ).fetchall()
            roles.extend(row["role"] for row in team_rows)
        return max(roles, key=lambda role: ROLE_PRIORITY[role]) if roles else None

    def require_capability(
        self,
        kind: ProjectKind,
        project_id: str,
        user: UserView,
        capability: str,
    ) -> ProjectRole:
        self.ensure_owner(kind, project_id, user)
        role = self.viewer_role(kind, project_id, user.id)
        if role is None or not getattr(capabilities_for_role(role), capability):
            raise ProjectCollaborationPermissionError("你没有执行此项目操作的权限。")
        return role

    def invite(self, kind: ProjectKind, project_id: str, user: UserView, email: str, role: ProjectRole) -> None:
        self.require_capability(kind, project_id, user, "manage_members")
        if role == "owner":
            raise ProjectCollaborationPermissionError("不能通过邀请转移项目所有权。")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            try:
                conn.execute(
                    """
                    INSERT INTO project_invitations(
                        id, project_kind, project_id, email, role, status,
                        invited_by_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (new_id("projectinvite"), kind, project_id, email, role, user.id, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectCollaborationConflictError("该邮箱已有待处理邀请。") from exc
            self._audit(conn, kind, project_id, user.id, "member.invited", {"email": email, "role": role})

    def accept_invitation(self, invitation_id: str, user: UserView) -> tuple[ProjectKind, str]:
        with self._lock, self._connect() as conn, conn:
            row = conn.execute("SELECT * FROM project_invitations WHERE id = ?", (invitation_id,)).fetchone()
            if row is None or row["status"] != "pending":
                raise ProjectCollaborationNotFoundError("邀请不存在或已经处理。")
            if str(row["email"]).lower() != user.email.lower():
                raise ProjectCollaborationPermissionError("该邀请不属于当前账号。")
            timestamp = now_iso()
            conn.execute(
                "UPDATE project_invitations SET status = 'accepted', updated_at = ? WHERE id = ?",
                (timestamp, invitation_id),
            )
            conn.execute(
                """
                INSERT INTO project_members(
                    project_kind, project_id, user_id, email, display_name, role,
                    added_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_kind, project_id, user_id) DO UPDATE SET
                    email = excluded.email, display_name = excluded.display_name,
                    role = excluded.role, updated_at = excluded.updated_at
                """,
                (
                    row["project_kind"],
                    row["project_id"],
                    user.id,
                    user.email,
                    user.display_name or user.email,
                    row["role"],
                    row["invited_by_user_id"],
                    timestamp,
                    timestamp,
                ),
            )
            self._audit(
                conn,
                row["project_kind"],
                row["project_id"],
                user.id,
                "member.joined",
                {"role": row["role"]},
            )
            return row["project_kind"], row["project_id"]

    def incoming_invitations(self, user: UserView) -> list[IncomingProjectInvitationView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM project_invitations
                WHERE lower(email) = lower(?) AND status = 'pending'
                ORDER BY created_at DESC
                """,
                (user.email,),
            ).fetchall()
        invitations: list[IncomingProjectInvitationView] = []
        for row in rows:
            try:
                _owner_user_id, title = self.project_record(row["project_kind"], row["project_id"])
            except ProjectCollaborationError:
                continue
            invitations.append(
                IncomingProjectInvitationView(
                    id=row["id"],
                    project_kind=row["project_kind"],
                    project_id=row["project_id"],
                    project_title=title,
                    email=row["email"],
                    role=row["role"],
                    status=row["status"],
                    invited_by_user_id=row["invited_by_user_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return invitations

    def revoke_invitation(self, kind: ProjectKind, project_id: str, invitation_id: str, user: UserView) -> None:
        self.require_capability(kind, project_id, user, "manage_members")
        with self._lock, self._connect() as conn, conn:
            updated = conn.execute(
                """
                UPDATE project_invitations SET status = 'revoked', updated_at = ?
                WHERE id = ? AND project_kind = ? AND project_id = ? AND status = 'pending'
                """,
                (now_iso(), invitation_id, kind, project_id),
            ).rowcount
            if not updated:
                raise ProjectCollaborationNotFoundError("待处理邀请不存在。")
            self._audit(conn, kind, project_id, user.id, "member.invitation_revoked", {"invitation_id": invitation_id})

    def update_member(
        self,
        kind: ProjectKind,
        project_id: str,
        target_user_id: str,
        role: ProjectRole | None,
        user: UserView,
    ) -> None:
        self.require_capability(kind, project_id, user, "manage_members")
        owner_user_id, _ = self.project_record(kind, project_id)
        if target_user_id == owner_user_id:
            raise ProjectCollaborationPermissionError("不能修改或移除项目所有者。")
        if role == "owner":
            raise ProjectCollaborationPermissionError("不能在成员设置中转移所有权。")
        with self._lock, self._connect() as conn, conn:
            if role is None:
                updated = conn.execute(
                    "DELETE FROM project_members WHERE project_kind = ? AND project_id = ? AND user_id = ?",
                    (kind, project_id, target_user_id),
                ).rowcount
                event_kind = "member.removed"
            else:
                updated = conn.execute(
                    """
                    UPDATE project_members SET role = ?, updated_at = ?
                    WHERE project_kind = ? AND project_id = ? AND user_id = ?
                    """,
                    (role, now_iso(), kind, project_id, target_user_id),
                ).rowcount
                event_kind = "member.role_changed"
            if not updated:
                raise ProjectCollaborationNotFoundError("项目成员不存在。")
            self._audit(conn, kind, project_id, user.id, event_kind, {"user_id": target_user_id, "role": role})

    def policy(self, kind: ProjectKind, project_id: str) -> ProjectPolicyView:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_policies WHERE project_kind = ? AND project_id = ?",
                (kind, project_id),
            ).fetchone()
        return self._policy_view(row) if row else ProjectPolicyView()

    def update_policy(self, kind: ProjectKind, project_id: str, user: UserView, policy: ProjectPolicyView) -> None:
        self.require_capability(kind, project_id, user, "manage_rules")
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO project_policies VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_kind, project_id) DO UPDATE SET
                    protect_default_branch = excluded.protect_default_branch,
                    required_approvals = excluded.required_approvals,
                    require_passing_checks = excluded.require_passing_checks,
                    dismiss_stale_approvals = excluded.dismiss_stale_approvals,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    kind,
                    project_id,
                    int(policy.protect_default_branch),
                    policy.required_approvals,
                    int(policy.require_passing_checks),
                    int(policy.dismiss_stale_approvals),
                    user.id,
                    now_iso(),
                ),
            )
            self._audit(conn, kind, project_id, user.id, "policy.updated", policy.model_dump())

    @staticmethod
    def _policy_view(row: sqlite3.Row) -> ProjectPolicyView:
        return ProjectPolicyView(
            protect_default_branch=bool(row["protect_default_branch"]),
            required_approvals=int(row["required_approvals"]),
            require_passing_checks=bool(row["require_passing_checks"]),
            dismiss_stale_approvals=bool(row["dismiss_stale_approvals"]),
        )

    def review(
        self,
        kind: ProjectKind,
        project_id: str,
        contribution: LessonContribution,
        user: UserView,
        decision: ReviewDecision,
        body: str,
        revision_number: int,
    ) -> ProjectReviewView:
        self.require_capability(kind, project_id, user, "review_changes")
        if contribution.contributor_user_id == user.id:
            raise ProjectCollaborationPermissionError("提交者不能批准自己的改进方案。")
        if contribution.source_lesson_id != project_id and kind == "lesson":
            raise ProjectCollaborationNotFoundError("改进方案不属于该项目。")
        if kind == "package":
            with self._connect() as conn:
                belongs_to_package = conn.execute(
                    "SELECT 1 FROM lessons WHERE id = ? AND package_id = ?",
                    (contribution.source_lesson_id, project_id),
                ).fetchone()
            if belongs_to_package is None:
                raise ProjectCollaborationNotFoundError("改进方案不属于该项目。")
        timestamp = now_iso()
        review_id = new_id("projectreview")
        with self._lock, self._connect() as conn, conn:
            existing = conn.execute(
                "SELECT id, created_at FROM project_reviews WHERE contribution_id = ? AND reviewer_user_id = ?",
                (contribution.id, user.id),
            ).fetchone()
            if existing:
                review_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE project_reviews SET decision = ?, body = ?, revision_number = ?,
                        reviewer_display_name = ?, updated_at = ? WHERE id = ?
                    """,
                    (decision, body.strip(), revision_number, user.display_name or user.email, timestamp, review_id),
                )
            else:
                created_at = timestamp
                conn.execute(
                    "INSERT INTO project_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        review_id,
                        kind,
                        project_id,
                        contribution.id,
                        user.id,
                        user.display_name or user.email,
                        decision,
                        body.strip(),
                        revision_number,
                        timestamp,
                        timestamp,
                    ),
                )
            self._audit(conn, kind, project_id, user.id, "review.submitted", {"contribution_id": contribution.id, "decision": decision})
        return ProjectReviewView(
            id=review_id,
            contribution_id=contribution.id,
            reviewer_user_id=user.id,
            reviewer_display_name=user.display_name or user.email,
            decision=decision,
            body=body.strip(),
            revision_number=revision_number,
            created_at=created_at,
            updated_at=timestamp,
        )

    def reviews(self, contribution_id: str) -> list[ProjectReviewView]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_reviews WHERE contribution_id = ? ORDER BY updated_at DESC",
                (contribution_id,),
            ).fetchall()
        return [self._review_view(row) for row in rows]

    @staticmethod
    def _review_view(row: sqlite3.Row) -> ProjectReviewView:
        return ProjectReviewView(
            id=row["id"], contribution_id=row["contribution_id"], reviewer_user_id=row["reviewer_user_id"],
            reviewer_display_name=row["reviewer_display_name"], decision=row["decision"], body=row["body"],
            revision_number=row["revision_number"], created_at=row["created_at"], updated_at=row["updated_at"]
        )

    def replace_checks(
        self,
        kind: ProjectKind,
        project_id: str,
        contribution_id: str | None,
        checks: list[tuple[str, CheckStatus, str, dict[str, object]]],
        actor_user_id: str,
    ) -> list[ProjectCheckView]:
        timestamp = now_iso()
        views: list[ProjectCheckView] = []
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                "DELETE FROM project_checks WHERE project_kind = ? AND project_id = ? AND contribution_id IS ?",
                (kind, project_id, contribution_id),
            )
            for name, status, summary, details in checks:
                check_id = new_id("projectcheck")
                conn.execute(
                    "INSERT INTO project_checks VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (check_id, kind, project_id, contribution_id, name, status, summary, json.dumps(details, ensure_ascii=False), timestamp, timestamp),
                )
                views.append(ProjectCheckView(id=check_id, contribution_id=contribution_id, name=name, status=status, summary=summary, required=True, details=details, created_at=timestamp, updated_at=timestamp))
            self._audit(conn, kind, project_id, actor_user_id, "checks.completed", {"contribution_id": contribution_id, "passed": all(item.status == "passed" for item in views)})
        return views

    def checks(self, kind: ProjectKind, project_id: str, contribution_id: str | None = None) -> list[ProjectCheckView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM project_checks
                WHERE project_kind = ? AND project_id = ? AND contribution_id IS ?
                ORDER BY created_at, id
                """,
                (kind, project_id, contribution_id),
            ).fetchall()
        return [ProjectCheckView(id=row["id"], contribution_id=row["contribution_id"], name=row["name"], status=row["status"], summary=row["summary"], required=bool(row["required"]), details=json.loads(row["details_json"]), created_at=row["created_at"], updated_at=row["updated_at"]) for row in rows]

    def assert_merge_allowed(self, contribution: LessonContribution) -> None:
        kind: ProjectKind = "lesson"
        project_id = contribution.source_lesson_id
        policy = self.policy(kind, project_id)
        if not policy.protect_default_branch and not policy.required_approvals and not policy.require_passing_checks:
            return
        reviews = self.reviews(contribution.id)
        current_reviews = (
            [review for review in reviews if review.revision_number == contribution.current_revision]
            if policy.dismiss_stale_approvals
            else reviews
        )
        if any(review.decision == "request_changes" for review in current_reviews):
            raise ProjectCollaborationConflictError("当前改进方案仍有请求修改的审查结论。")
        approvals = sum(review.decision == "approve" for review in current_reviews)
        if approvals < policy.required_approvals:
            raise ProjectCollaborationConflictError(f"当前需要 {policy.required_approvals} 个批准，已有 {approvals} 个。")
        if policy.require_passing_checks:
            checks = self.checks(kind, project_id, contribution.id)
            if not checks or any(check.required and check.status != "passed" for check in checks):
                raise ProjectCollaborationConflictError("必需的课程检查尚未全部通过。")

    def add_label(self, kind: ProjectKind, project_id: str, user: UserView, name: str, color: str, description: str) -> None:
        self.require_capability(kind, project_id, user, "manage_work_items")
        with self._lock, self._connect() as conn, conn:
            try:
                conn.execute("INSERT INTO project_labels VALUES (?, ?, ?, ?, ?, ?)", (new_id("projectlabel"), kind, project_id, name.strip(), color, description.strip()))
            except sqlite3.IntegrityError as exc:
                raise ProjectCollaborationConflictError("标签名称已经存在。") from exc
            self._audit(conn, kind, project_id, user.id, "label.created", {"name": name})

    def add_milestone(self, kind: ProjectKind, project_id: str, user: UserView, title: str, description: str, due_at: str | None) -> None:
        self.require_capability(kind, project_id, user, "manage_work_items")
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT INTO project_milestones VALUES (?, ?, ?, ?, ?, ?, 0)", (new_id("projectmilestone"), kind, project_id, title.strip(), description.strip(), due_at))
            self._audit(conn, kind, project_id, user.id, "milestone.created", {"title": title})

    def replace_board(self, kind: ProjectKind, project_id: str, user: UserView, columns: list[str]) -> None:
        self.require_capability(kind, project_id, user, "manage_work_items")
        with self._lock, self._connect() as conn, conn:
            existing = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM project_board_columns WHERE project_kind = ? AND project_id = ?", (kind, project_id)).fetchall()}
            keep_ids: list[str] = []
            for position, name in enumerate(columns):
                column_id = existing.get(name, new_id("projectcolumn"))
                keep_ids.append(column_id)
                conn.execute(
                    """
                    INSERT INTO project_board_columns VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_kind, project_id, name) DO UPDATE SET position = excluded.position
                    """,
                    (column_id, kind, project_id, name, position),
                )
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                conn.execute(f"DELETE FROM project_board_columns WHERE project_kind = ? AND project_id = ? AND id NOT IN ({placeholders})", (kind, project_id, *keep_ids))
                conn.execute(
                    f"UPDATE project_work_items SET board_column_id = ? WHERE project_kind = ? AND project_id = ? AND board_column_id IS NOT NULL AND board_column_id NOT IN ({placeholders})",
                    (keep_ids[0], kind, project_id, *keep_ids),
                )
            else:
                conn.execute(
                    "DELETE FROM project_board_columns WHERE project_kind = ? AND project_id = ?",
                    (kind, project_id),
                )
                conn.execute(
                    "UPDATE project_work_items SET board_column_id = NULL WHERE project_kind = ? AND project_id = ?",
                    (kind, project_id),
                )
            self._audit(conn, kind, project_id, user.id, "board.updated", {"columns": columns})

    def add_work_item(
        self,
        kind: ProjectKind,
        project_id: str,
        user: UserView,
        *,
        title: str,
        body: str,
        assignee_user_id: str | None,
        milestone_id: str | None,
        label_ids: list[str],
    ) -> None:
        self.require_capability(kind, project_id, user, "manage_work_items")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            next_number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM project_work_items WHERE project_kind = ? AND project_id = ?", (kind, project_id)).fetchone()[0]
            first_column = conn.execute("SELECT id FROM project_board_columns WHERE project_kind = ? AND project_id = ? ORDER BY position LIMIT 1", (kind, project_id)).fetchone()
            conn.execute(
                "INSERT INTO project_work_items VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                (new_id("projectissue"), kind, project_id, next_number, title.strip(), body.strip(), user.id, assignee_user_id, milestone_id, json.dumps(label_ids), first_column["id"] if first_column else None, timestamp, timestamp),
            )
            self._audit(conn, kind, project_id, user.id, "work_item.created", {"number": next_number, "title": title})

    def update_work_item(self, kind: ProjectKind, project_id: str, item_id: str, user: UserView, changes: dict[str, object]) -> None:
        self.require_capability(kind, project_id, user, "manage_work_items")
        allowed = {"title", "body", "state", "assignee_user_id", "milestone_id", "board_column_id"}
        assignments: list[str] = []
        values: list[object] = []
        for key, value in changes.items():
            if key == "label_ids":
                assignments.append("label_ids_json = ?")
                values.append(json.dumps(value))
            elif key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.append(now_iso())
        values.extend([item_id, kind, project_id])
        with self._lock, self._connect() as conn, conn:
            updated = conn.execute(f"UPDATE project_work_items SET {', '.join(assignments)} WHERE id = ? AND project_kind = ? AND project_id = ?", values).rowcount
            if not updated:
                raise ProjectCollaborationNotFoundError("任务不存在。")
            self._audit(conn, kind, project_id, user.id, "work_item.updated", {"id": item_id, "changes": list(changes)})

    def add_webhook(self, kind: ProjectKind, project_id: str, user: UserView, url: str, events: list[str], secret: str) -> None:
        self.require_capability(kind, project_id, user, "manage_integrations")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT INTO project_webhooks VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?)", (new_id("projectwebhook"), kind, project_id, url, json.dumps(events), secret, timestamp, timestamp))
            self._audit(conn, kind, project_id, user.id, "webhook.created", {"url": url, "events": events})

    def add_integration_app(self, user: UserView, name: str, description: str, permissions: list[str], callback_url: str | None) -> str:
        app_id = new_id("integrationapp")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT INTO integration_apps VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (app_id, user.id, name.strip(), description.strip(), json.dumps(permissions), callback_url, timestamp, timestamp))
        return app_id

    def install_integration(self, kind: ProjectKind, project_id: str, user: UserView, app_id: str) -> None:
        self.require_capability(kind, project_id, user, "manage_integrations")
        with self._lock, self._connect() as conn, conn:
            if conn.execute("SELECT 1 FROM integration_apps WHERE id = ?", (app_id,)).fetchone() is None:
                raise ProjectCollaborationNotFoundError("应用不存在。")
            conn.execute("INSERT OR IGNORE INTO project_integration_installations VALUES (?, ?, ?, ?, ?)", (kind, project_id, app_id, user.id, now_iso()))
            self._audit(conn, kind, project_id, user.id, "integration.installed", {"app_id": app_id})

    def create_organization(self, user: UserView, name: str, slug: str) -> OrganizationView:
        organization_id = new_id("organization")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            try:
                conn.execute("INSERT INTO organizations VALUES (?, ?, ?, ?, ?, ?)", (organization_id, name.strip(), slug, user.id, timestamp, timestamp))
            except sqlite3.IntegrityError as exc:
                raise ProjectCollaborationConflictError("组织标识已经存在。") from exc
            conn.execute("INSERT INTO organization_members VALUES (?, ?, 'owner', ?)", (organization_id, user.id, timestamp))
        return OrganizationView(id=organization_id, name=name.strip(), slug=slug, viewer_role="owner")

    def create_team(self, organization_id: str, user: UserView, name: str, slug: str) -> TeamView:
        self._require_organization_owner(organization_id, user.id)
        team_id = new_id("team")
        timestamp = now_iso()
        with self._lock, self._connect() as conn, conn:
            try:
                conn.execute("INSERT INTO teams VALUES (?, ?, ?, ?, ?, ?)", (team_id, organization_id, name.strip(), slug, timestamp, timestamp))
            except sqlite3.IntegrityError as exc:
                raise ProjectCollaborationConflictError("团队标识已经存在。") from exc
            conn.execute("INSERT INTO team_members VALUES (?, ?, ?)", (team_id, user.id, timestamp))
        return TeamView(id=team_id, organization_id=organization_id, name=name.strip(), slug=slug, member_count=1)

    def add_team_member(self, organization_id: str, team_id: str, user_id: str, user: UserView) -> None:
        self._require_organization_owner(organization_id, user.id)
        with self._lock, self._connect() as conn, conn:
            if conn.execute("SELECT 1 FROM teams WHERE id = ? AND organization_id = ?", (team_id, organization_id)).fetchone() is None:
                raise ProjectCollaborationNotFoundError("团队不存在。")
            conn.execute("INSERT OR IGNORE INTO team_members VALUES (?, ?, ?)", (team_id, user_id, now_iso()))

    def grant_team(self, organization_id: str, team_id: str, kind: ProjectKind, project_id: str, role: ProjectRole, user: UserView) -> None:
        self._require_organization_owner(organization_id, user.id)
        self.require_capability(kind, project_id, user, "manage_members")
        if role == "owner":
            raise ProjectCollaborationPermissionError("团队不能成为项目所有者。")
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT INTO team_project_grants VALUES (?, ?, ?, ?, ?) ON CONFLICT(team_id, project_kind, project_id) DO UPDATE SET role = excluded.role", (team_id, kind, project_id, role, now_iso()))
            self._audit(conn, kind, project_id, user.id, "team.granted", {"team_id": team_id, "role": role})

    def _require_organization_owner(self, organization_id: str, user_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT role FROM organization_members WHERE organization_id = ? AND user_id = ?", (organization_id, user_id)).fetchone()
        if row is None or row["role"] != "owner":
            raise ProjectCollaborationPermissionError("只有组织所有者可以执行此操作。")

    def list_governed_projects(self, user: UserView) -> list[ProjectGovernanceSummaryView]:
        candidates: set[tuple[ProjectKind, str]] = set()
        with self._connect() as conn:
            for row in conn.execute("SELECT project_kind, project_id FROM project_members WHERE user_id = ?", (user.id,)).fetchall():
                candidates.add((row["project_kind"], row["project_id"]))
            for row in conn.execute("""
                SELECT grants.project_kind, grants.project_id FROM team_project_grants grants
                JOIN team_members members ON members.team_id = grants.team_id WHERE members.user_id = ?
            """, (user.id,)).fetchall():
                candidates.add((row["project_kind"], row["project_id"]))
            for row in conn.execute("SELECT id FROM course_packages WHERE owner_user_id = ? AND sort_order > 0", (user.id,)).fetchall():
                candidates.add(("package", row["id"]))
            for row in conn.execute("""
                SELECT lessons.id FROM lessons JOIN course_packages ON course_packages.id = lessons.package_id
                WHERE course_packages.owner_user_id = ? AND course_packages.sort_order = 0
            """, (user.id,)).fetchall():
                candidates.add(("lesson", row["id"]))
        results: list[ProjectGovernanceSummaryView] = []
        for kind, project_id in candidates:
            try:
                view = self.governance(kind, project_id, user)
                results.append(ProjectGovernanceSummaryView(**view.model_dump(exclude={"members", "invitations", "policy", "labels", "milestones", "work_items", "board_columns", "webhooks", "integrations", "teams"})))
            except ProjectCollaborationError:
                continue
        return sorted(results, key=lambda item: item.title.lower())

    def governance(self, kind: ProjectKind, project_id: str, user: UserView) -> ProjectGovernanceView:
        self.ensure_owner(kind, project_id, user)
        owner_user_id, title = self.project_record(kind, project_id)
        role = self.viewer_role(kind, project_id, user.id)
        if role is None:
            raise ProjectCollaborationPermissionError("你不是该项目的成员。")
        with self._connect() as conn:
            if kind == "lesson":
                lesson_ids = [project_id]
            else:
                lesson_ids = [
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM lessons WHERE package_id = ? ORDER BY sort_order, created_at",
                        (project_id,),
                    ).fetchall()
                ]
            members = [ProjectMemberView(**dict(row)) for row in conn.execute("SELECT user_id, email, display_name, role, added_by_user_id, created_at, updated_at FROM project_members WHERE project_kind = ? AND project_id = ? ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'maintainer' THEN 1 WHEN 'editor' THEN 2 WHEN 'reviewer' THEN 3 ELSE 4 END, created_at", (kind, project_id)).fetchall()]
            invitation_rows = conn.execute("SELECT id, email, role, status, invited_by_user_id, created_at, updated_at FROM project_invitations WHERE project_kind = ? AND project_id = ? ORDER BY created_at DESC", (kind, project_id)).fetchall()
            invitations = [ProjectInvitationView(**dict(row)) for row in invitation_rows] if capabilities_for_role(role).manage_members else []
            labels = [ProjectLabelView(**dict(row)) for row in conn.execute("SELECT id, name, color, description FROM project_labels WHERE project_kind = ? AND project_id = ? ORDER BY name", (kind, project_id)).fetchall()]
            milestones = [ProjectMilestoneView(id=row["id"], title=row["title"], description=row["description"], due_at=row["due_at"], closed=bool(row["closed"])) for row in conn.execute("SELECT * FROM project_milestones WHERE project_kind = ? AND project_id = ? ORDER BY closed, due_at, title", (kind, project_id)).fetchall()]
            columns = [ProjectBoardColumnView(id=row["id"], name=row["name"], position=row["position"]) for row in conn.execute("SELECT * FROM project_board_columns WHERE project_kind = ? AND project_id = ? ORDER BY position", (kind, project_id)).fetchall()]
            work_items = [ProjectWorkItemView(id=row["id"], number=row["number"], title=row["title"], body=row["body"], state=row["state"], author_user_id=row["author_user_id"], assignee_user_id=row["assignee_user_id"], milestone_id=row["milestone_id"], label_ids=json.loads(row["label_ids_json"]), board_column_id=row["board_column_id"], created_at=row["created_at"], updated_at=row["updated_at"]) for row in conn.execute("SELECT * FROM project_work_items WHERE project_kind = ? AND project_id = ? ORDER BY state <> 'closed', number DESC", (kind, project_id)).fetchall()]
            audit_events = [
                ProjectAuditEventView(
                    id=row["id"],
                    actor_user_id=row["actor_user_id"],
                    kind=row["kind"],
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                )
                for row in conn.execute(
                    "SELECT id, actor_user_id, kind, metadata_json, created_at FROM project_audit_events WHERE project_kind = ? AND project_id = ? ORDER BY created_at DESC LIMIT 100",
                    (kind, project_id),
                ).fetchall()
            ]
            webhooks = [ProjectWebhookView(id=row["id"], url=row["url"], events=json.loads(row["events_json"]), status=row["status"], last_delivery_status=row["last_delivery_status"], created_at=row["created_at"], updated_at=row["updated_at"]) for row in conn.execute("SELECT * FROM project_webhooks WHERE project_kind = ? AND project_id = ? ORDER BY created_at DESC", (kind, project_id)).fetchall()] if capabilities_for_role(role).manage_integrations else []
            apps = [IntegrationAppView(id=row["id"], name=row["name"], description=row["description"], permissions=json.loads(row["permissions_json"]), callback_url=row["callback_url"], installed=bool(row["installed"]), created_at=row["created_at"], updated_at=row["updated_at"]) for row in conn.execute("""
                SELECT apps.*, CASE WHEN installs.app_id IS NULL THEN 0 ELSE 1 END AS installed
                FROM integration_apps apps LEFT JOIN project_integration_installations installs
                ON installs.app_id = apps.id AND installs.project_kind = ? AND installs.project_id = ?
                WHERE apps.owner_user_id = ? OR installs.app_id IS NOT NULL ORDER BY apps.name
            """, (kind, project_id, user.id)).fetchall()]
            teams = [TeamView(id=row["id"], organization_id=row["organization_id"], name=row["name"], slug=row["slug"], member_count=row["member_count"], project_role=row["project_role"]) for row in conn.execute("""
                SELECT teams.id, teams.organization_id, teams.name, teams.slug,
                    COUNT(team_members.user_id) AS member_count, grants.role AS project_role
                FROM teams JOIN organization_members om ON om.organization_id = teams.organization_id
                LEFT JOIN team_members ON team_members.team_id = teams.id
                LEFT JOIN team_project_grants grants ON grants.team_id = teams.id AND grants.project_kind = ? AND grants.project_id = ?
                WHERE om.user_id = ? GROUP BY teams.id ORDER BY teams.name
            """, (kind, project_id, user.id)).fetchall()]
        return ProjectGovernanceView(
            project_kind=kind,
            project_id=project_id,
            title=title,
            owner_user_id=owner_user_id,
            viewer_role=role,
            capabilities=capabilities_for_role(role),
            member_count=len(members),
            pending_invitation_count=sum(item.status == "pending" for item in invitations),
            open_work_item_count=sum(item.state != "closed" for item in work_items),
            lesson_ids=lesson_ids,
            members=members,
            invitations=invitations,
            policy=self.policy(kind, project_id),
            labels=labels,
            milestones=milestones,
            work_items=work_items,
            board_columns=columns,
            webhooks=webhooks,
            integrations=apps,
            teams=teams,
            audit_events=audit_events,
        )


def run_project_checks(
    collaboration: ProjectCollaborationStore,
    course_store: SqliteCourseStore,
    kind: ProjectKind,
    project_id: str,
    user: UserView,
    contribution_id: str | None,
) -> list[ProjectCheckView]:
    collaboration.require_capability(kind, project_id, user, "review_changes")
    owner_user_id, _ = collaboration.project_record(kind, project_id)
    workspace = course_store.load_for_user(owner_user_id)
    if kind == "lesson":
        package, lesson = find_lesson_package(workspace, project_id)
        lessons = [lesson]
    else:
        package = get_package(workspace, project_id)
        lessons = package.lessons
    checks: list[tuple[str, CheckStatus, str, dict[str, object]]] = []
    integrity_ok = bool(lessons) and all(lesson.history_graph.current_branch in lesson.history_graph.branches for lesson in lessons)
    checks.append(("course_integrity", "passed" if integrity_ok else "failed", "课程历史结构完整。" if integrity_ok else "课程历史结构不完整。", {"lesson_count": len(lessons)}))
    failed_resources = [resource.id for resource in package.resources if resource.ingestion_status == "failed"]
    checks.append(("source_access", "passed" if not failed_resources else "failed", "资料均可继续使用。" if not failed_resources else "存在解析失败的资料。", {"failed_resource_ids": failed_resources}))
    unsafe = [lesson.id for lesson in lessons if lesson.visibility == "public" and lesson.publication_review.status != "approved"]
    checks.append(("publication_safety", "passed" if not unsafe else "failed", "公开状态符合发布审查要求。" if not unsafe else "存在未通过发布审查的公开课程。", {"unsafe_lesson_ids": unsafe}))
    empty = [lesson.id for lesson in lessons if not (lesson.board_document.content_text or "").strip()]
    checks.append(("export_readiness", "passed" if not empty else "failed", "课程内容可以导出。" if not empty else "存在空白课程，暂不可作为完整资产导出。", {"empty_lesson_ids": empty}))
    return collaboration.replace_checks(kind, project_id, contribution_id, checks, user.id)

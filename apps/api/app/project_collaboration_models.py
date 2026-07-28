from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


ProjectKind = Literal["lesson", "package"]
ProjectRole = Literal["owner", "maintainer", "editor", "reviewer", "viewer"]
InvitationStatus = Literal["pending", "accepted", "revoked"]
ReviewDecision = Literal["comment", "approve", "request_changes"]
CheckStatus = Literal["queued", "running", "passed", "failed"]
WorkItemState = Literal["open", "in_progress", "review", "closed"]
WebhookStatus = Literal["active", "paused"]


class ProjectCapabilityView(BaseModel):
    view_project: bool = False
    edit_project: bool = False
    review_changes: bool = False
    merge_changes: bool = False
    manage_members: bool = False
    manage_rules: bool = False
    manage_work_items: bool = False
    manage_integrations: bool = False


class ProjectMemberView(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: ProjectRole
    added_by_user_id: str
    created_at: str
    updated_at: str


class ProjectInvitationView(BaseModel):
    id: str
    email: str
    role: ProjectRole
    status: InvitationStatus
    invited_by_user_id: str
    created_at: str
    updated_at: str


class IncomingProjectInvitationView(ProjectInvitationView):
    project_kind: ProjectKind
    project_id: str
    project_title: str


class ProjectPolicyView(BaseModel):
    protect_default_branch: bool = False
    required_approvals: int = Field(default=0, ge=0, le=20)
    require_passing_checks: bool = False
    dismiss_stale_approvals: bool = True


class ProjectReviewView(BaseModel):
    id: str
    contribution_id: str
    reviewer_user_id: str
    reviewer_display_name: str
    decision: ReviewDecision
    body: str
    revision_number: int = Field(ge=1)
    created_at: str
    updated_at: str


class ProjectCheckView(BaseModel):
    id: str
    contribution_id: str | None = None
    name: str
    status: CheckStatus
    summary: str
    required: bool = True
    details: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ProjectLabelView(BaseModel):
    id: str
    name: str
    color: str
    description: str = ""


class ProjectMilestoneView(BaseModel):
    id: str
    title: str
    description: str = ""
    due_at: str | None = None
    closed: bool = False


class ProjectWorkItemView(BaseModel):
    id: str
    number: int
    title: str
    body: str = ""
    state: WorkItemState = "open"
    author_user_id: str
    assignee_user_id: str | None = None
    milestone_id: str | None = None
    label_ids: list[str] = Field(default_factory=list)
    board_column_id: str | None = None
    created_at: str
    updated_at: str


class ProjectBoardColumnView(BaseModel):
    id: str
    name: str
    position: int = Field(ge=0)


class OrganizationView(BaseModel):
    id: str
    name: str
    slug: str
    viewer_role: Literal["owner", "member"]


class TeamView(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str
    member_count: int = 0
    project_role: ProjectRole | None = None


class ProjectWebhookView(BaseModel):
    id: str
    url: str
    events: list[str] = Field(default_factory=list)
    status: WebhookStatus = "active"
    last_delivery_status: str | None = None
    created_at: str
    updated_at: str


class ProjectWebhookDeliveryView(BaseModel):
    id: str
    webhook_id: str
    event: str
    status: str
    attempts: int = 0
    next_attempt_at: str | None = None
    response_status: int | None = None
    response_body: str = ""
    created_at: str
    updated_at: str


class ProjectAuditEventView(BaseModel):
    id: str
    actor_user_id: str
    kind: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: str


class IntegrationAppView(BaseModel):
    id: str
    name: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    callback_url: str | None = None
    installed: bool = False
    created_at: str
    updated_at: str


class ProjectGovernanceSummaryView(BaseModel):
    project_kind: ProjectKind
    project_id: str
    title: str
    owner_user_id: str
    viewer_role: ProjectRole
    capabilities: ProjectCapabilityView
    member_count: int = 0
    pending_invitation_count: int = 0
    open_work_item_count: int = 0


class ProjectGovernanceView(ProjectGovernanceSummaryView):
    lesson_ids: list[str] = Field(default_factory=list)
    members: list[ProjectMemberView] = Field(default_factory=list)
    invitations: list[ProjectInvitationView] = Field(default_factory=list)
    policy: ProjectPolicyView = Field(default_factory=ProjectPolicyView)
    labels: list[ProjectLabelView] = Field(default_factory=list)
    milestones: list[ProjectMilestoneView] = Field(default_factory=list)
    work_items: list[ProjectWorkItemView] = Field(default_factory=list)
    board_columns: list[ProjectBoardColumnView] = Field(default_factory=list)
    webhooks: list[ProjectWebhookView] = Field(default_factory=list)
    integrations: list[IntegrationAppView] = Field(default_factory=list)
    teams: list[TeamView] = Field(default_factory=list)
    audit_events: list[ProjectAuditEventView] = Field(default_factory=list)


class CreateProjectInvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: ProjectRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("请输入有效邮箱地址")
        return normalized


class UpdateProjectMemberRequest(BaseModel):
    role: ProjectRole


class UpdateProjectPolicyRequest(ProjectPolicyView):
    pass


class CreateProjectReviewRequest(BaseModel):
    decision: ReviewDecision
    body: str = Field(default="", max_length=4000)
    revision_number: int = Field(ge=1)


class CreateProjectWorkItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=12000)
    assignee_user_id: str | None = None
    milestone_id: str | None = None
    label_ids: list[str] = Field(default_factory=list, max_length=20)


class UpdateProjectWorkItemRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=12000)
    state: WorkItemState | None = None
    assignee_user_id: str | None = None
    milestone_id: str | None = None
    label_ids: list[str] | None = Field(default=None, max_length=20)
    board_column_id: str | None = None


class CreateProjectLabelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#6b7280", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str = Field(default="", max_length=300)


class CreateProjectMilestoneRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    due_at: str | None = None


class UpdateProjectBoardRequest(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=20)

    @field_validator("columns")
    @classmethod
    def normalize_columns(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("看板至少需要一列")
        if len(set(normalized)) != len(normalized):
            raise ValueError("看板列名称不能重复")
        return normalized


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")


class AddTeamMemberRequest(BaseModel):
    user_id: str


class GrantTeamProjectRequest(BaseModel):
    project_kind: ProjectKind
    project_id: str
    role: ProjectRole


class CreateProjectWebhookRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    events: list[str] = Field(default_factory=lambda: ["project.updated"], min_length=1, max_length=30)
    secret: str = Field(min_length=16, max_length=256)


class CreateIntegrationAppRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    permissions: list[str] = Field(default_factory=list, max_length=30)
    callback_url: str | None = Field(default=None, max_length=2048)


class InstallIntegrationRequest(BaseModel):
    app_id: str

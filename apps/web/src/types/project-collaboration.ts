export type ProjectKind = "lesson" | "package";
export type ProjectRole = "owner" | "maintainer" | "editor" | "reviewer" | "viewer";
export type WorkItemState = "open" | "in_progress" | "review" | "closed";

export type ProjectCapabilities = {
  view_project: boolean;
  edit_project: boolean;
  review_changes: boolean;
  merge_changes: boolean;
  manage_members: boolean;
  manage_rules: boolean;
  manage_work_items: boolean;
  manage_integrations: boolean;
};

export type ProjectMember = {
  user_id: string;
  email: string;
  display_name: string;
  role: ProjectRole;
  added_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export type ProjectInvitation = {
  id: string;
  email: string;
  role: ProjectRole;
  status: "pending" | "accepted" | "revoked";
  invited_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export type IncomingProjectInvitation = ProjectInvitation & {
  project_kind: ProjectKind;
  project_id: string;
  project_title: string;
};

export type ProjectPolicy = {
  protect_default_branch: boolean;
  required_approvals: number;
  require_passing_checks: boolean;
  dismiss_stale_approvals: boolean;
};

export type ProjectCheck = {
  id: string;
  contribution_id: string | null;
  name: string;
  status: "queued" | "running" | "passed" | "failed";
  summary: string;
  required: boolean;
  details: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProjectReview = {
  id: string;
  contribution_id: string;
  reviewer_user_id: string;
  reviewer_display_name: string;
  decision: "approve" | "request_changes" | "comment";
  body: string;
  revision_number: number;
  created_at: string;
  updated_at: string;
};

export type ProjectLabel = { id: string; name: string; color: string; description: string };
export type ProjectMilestone = { id: string; title: string; description: string; due_at: string | null; closed: boolean };
export type ProjectBoardColumn = { id: string; name: string; position: number };

export type ProjectWorkItem = {
  id: string;
  number: number;
  title: string;
  body: string;
  state: WorkItemState;
  author_user_id: string;
  assignee_user_id: string | null;
  milestone_id: string | null;
  label_ids: string[];
  board_column_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ProjectWebhook = {
  id: string;
  url: string;
  events: string[];
  status: "active" | "paused";
  last_delivery_status: string | null;
  created_at: string;
  updated_at: string;
};

export type ProjectWebhookDelivery = {
  id: string;
  webhook_id: string;
  event: string;
  status: string;
  attempts: number;
  next_attempt_at: string | null;
  response_status: number | null;
  response_body: string;
  created_at: string;
  updated_at: string;
};

export type IntegrationApp = {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  callback_url: string | null;
  installed: boolean;
  created_at: string;
  updated_at: string;
};

export type ProjectTeam = {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  member_count: number;
  project_role: ProjectRole | null;
};

export type ProjectAuditEvent = {
  id: string;
  actor_user_id: string;
  kind: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ProjectGovernanceSummary = {
  project_kind: ProjectKind;
  project_id: string;
  title: string;
  owner_user_id: string;
  viewer_role: ProjectRole;
  capabilities: ProjectCapabilities;
  member_count: number;
  pending_invitation_count: number;
  open_work_item_count: number;
};

export type ProjectGovernance = ProjectGovernanceSummary & {
  lesson_ids: string[];
  members: ProjectMember[];
  invitations: ProjectInvitation[];
  policy: ProjectPolicy;
  labels: ProjectLabel[];
  milestones: ProjectMilestone[];
  work_items: ProjectWorkItem[];
  board_columns: ProjectBoardColumn[];
  webhooks: ProjectWebhook[];
  integrations: IntegrationApp[];
  teams: ProjectTeam[];
  audit_events: ProjectAuditEvent[];
};

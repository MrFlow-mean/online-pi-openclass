import { request } from "@/lib/api";
import type { CoursePackage, DocumentSavePayload, Lesson } from "@/types";
import type {
  IncomingProjectInvitation,
  IntegrationApp,
  ProjectCheck,
  ProjectGovernance,
  ProjectGovernanceSummary,
  ProjectKind,
  ProjectPolicy,
  ProjectReview,
  ProjectRole,
  ProjectWebhookDelivery,
  WorkItemState,
} from "@/types/project-collaboration";

function projectPath(kind: ProjectKind, projectId: string) {
  return `/api/projects/${kind}/${encodeURIComponent(projectId)}`;
}

export const projectCollaborationApi = {
  listProjects() {
    return request<ProjectGovernanceSummary[]>("/api/project-collaboration/projects");
  },
  listInvitations() {
    return request<IncomingProjectInvitation[]>("/api/project-invitations");
  },
  acceptInvitation(invitationId: string) {
    return request<ProjectGovernance>(`/api/project-invitations/${encodeURIComponent(invitationId)}/accept`, {
      method: "POST",
    });
  },
  governance(kind: ProjectKind, projectId: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/governance`);
  },
  getLesson(kind: ProjectKind, projectId: string, lessonId: string) {
    return request<Lesson>(`${projectPath(kind, projectId)}/lessons/${encodeURIComponent(lessonId)}`);
  },
  saveLessonDocument(
    kind: ProjectKind,
    projectId: string,
    lessonId: string,
    payload: DocumentSavePayload
  ) {
    return request<CoursePackage>(
      `${projectPath(kind, projectId)}/lessons/${encodeURIComponent(lessonId)}/document/save`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  invite(kind: ProjectKind, projectId: string, email: string, role: ProjectRole) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/invitations`, {
      method: "POST",
      body: JSON.stringify({ email, role }),
    });
  },
  revokeInvitation(kind: ProjectKind, projectId: string, invitationId: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/invitations/${encodeURIComponent(invitationId)}`, {
      method: "DELETE",
    });
  },
  updateMember(kind: ProjectKind, projectId: string, userId: string, role: ProjectRole) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/members/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
  },
  removeMember(kind: ProjectKind, projectId: string, userId: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/members/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    });
  },
  updatePolicy(kind: ProjectKind, projectId: string, policy: ProjectPolicy) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/policy`, {
      method: "PUT",
      body: JSON.stringify(policy),
    });
  },
  runChecks(kind: ProjectKind, projectId: string) {
    return request<ProjectCheck[]>(`${projectPath(kind, projectId)}/checks/run`, { method: "POST" });
  },
  listChecks(kind: ProjectKind, projectId: string, contributionId?: string) {
    const query = contributionId ? `?contribution_id=${encodeURIComponent(contributionId)}` : "";
    return request<ProjectCheck[]>(`${projectPath(kind, projectId)}/checks${query}`);
  },
  listReviews(kind: ProjectKind, projectId: string, contributionId: string) {
    return request<ProjectReview[]>(
      `${projectPath(kind, projectId)}/contributions/${encodeURIComponent(contributionId)}/reviews`
    );
  },
  submitReview(
    kind: ProjectKind,
    projectId: string,
    contributionId: string,
    decision: ProjectReview["decision"],
    body: string,
    revisionNumber: number
  ) {
    return request<ProjectReview>(
      `${projectPath(kind, projectId)}/contributions/${encodeURIComponent(contributionId)}/reviews`,
      {
        method: "POST",
        body: JSON.stringify({ decision, body, revision_number: revisionNumber }),
      }
    );
  },
  createLabel(kind: ProjectKind, projectId: string, name: string, color: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/labels`, {
      method: "POST",
      body: JSON.stringify({ name, color }),
    });
  },
  createMilestone(kind: ProjectKind, projectId: string, title: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/milestones`, {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  },
  updateBoard(kind: ProjectKind, projectId: string, columns: string[]) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/board`, {
      method: "PUT",
      body: JSON.stringify({ columns }),
    });
  },
  createWorkItem(
    kind: ProjectKind,
    projectId: string,
    title: string,
    body: string,
    milestoneId?: string,
    labelIds: string[] = []
  ) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/work-items`, {
      method: "POST",
      body: JSON.stringify({ title, body, milestone_id: milestoneId || null, label_ids: labelIds }),
    });
  },
  updateWorkItem(kind: ProjectKind, projectId: string, itemId: string, state: WorkItemState, boardColumnId?: string | null) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/work-items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify({ state, board_column_id: boardColumnId }),
    });
  },
  createWebhook(kind: ProjectKind, projectId: string, url: string, events: string[], secret: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/webhooks`, {
      method: "POST",
      body: JSON.stringify({ url, events, secret }),
    });
  },
  listWebhookDeliveries(kind: ProjectKind, projectId: string) {
    return request<ProjectWebhookDelivery[]>(`${projectPath(kind, projectId)}/webhooks/deliveries`);
  },
  createApp(name: string, permissions: string[]) {
    return request<IntegrationApp>("/api/integration-apps", {
      method: "POST",
      body: JSON.stringify({ name, permissions }),
    });
  },
  installApp(kind: ProjectKind, projectId: string, appId: string) {
    return request<ProjectGovernance>(`${projectPath(kind, projectId)}/integrations`, {
      method: "POST",
      body: JSON.stringify({ app_id: appId }),
    });
  },
  createOrganization(name: string, slug: string) {
    return request<{ id: string; name: string; slug: string; viewer_role: "owner" | "member" }>("/api/organizations", {
      method: "POST",
      body: JSON.stringify({ name, slug }),
    });
  },
  createTeam(organizationId: string, name: string, slug: string) {
    return request<{ id: string; organization_id: string; name: string; slug: string; member_count: number }>(
      `/api/organizations/${encodeURIComponent(organizationId)}/teams`,
      { method: "POST", body: JSON.stringify({ name, slug }) }
    );
  },
  addTeamMember(organizationId: string, teamId: string, userId: string) {
    return request<{ ok: boolean }>(
      `/api/organizations/${encodeURIComponent(organizationId)}/teams/${encodeURIComponent(teamId)}/members`,
      { method: "POST", body: JSON.stringify({ user_id: userId }) }
    );
  },
  grantTeamProject(
    organizationId: string,
    teamId: string,
    kind: ProjectKind,
    projectId: string,
    role: ProjectRole
  ) {
    return request<{ ok: boolean }>(
      `/api/organizations/${encodeURIComponent(organizationId)}/teams/${encodeURIComponent(teamId)}/projects`,
      { method: "POST", body: JSON.stringify({ project_kind: kind, project_id: projectId, role }) }
    );
  },
};

"use client";

import clsx from "clsx";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AppWindow,
  CheckCircle2,
  ClipboardCheck,
  GitBranch,
  KanbanSquare,
  LoaderCircle,
  Network,
  Plus,
  PencilLine,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
  Webhook,
  XCircle,
} from "lucide-react";

import { projectCollaborationApi } from "@/lib/project-collaboration-api";
import type {
  ProjectCheck,
  ProjectGovernance,
  ProjectKind,
  ProjectRole,
  ProjectWebhookDelivery,
  WorkItemState,
} from "@/types/project-collaboration";

type GovernanceTab = "members" | "rules" | "work" | "integrations";

const ROLE_LABELS: Record<ProjectRole, string> = {
  owner: "owner",
  maintainer: "maintainer",
  editor: "Editor",
  reviewer: "reviewer",
  viewer: "Viewer",
};

const STATE_LABELS: Record<WorkItemState, string> = {
  open: "To-do",
  in_progress: "in progress",
  review: "Under review",
  closed: "Completed",
};

const CHECK_LABELS: Record<string, string> = {
  course_integrity: "Course historical integrity",
  source_access: "Data accessibility",
  publication_safety: "Post safe",
  export_readiness: "Course export preparation",
};

function slugify(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "") || "openclass-team";
}

function stateForColumn(position: number, total: number): WorkItemState {
  if (position === total - 1) return "closed";
  if (position === total - 2 && total > 2) return "review";
  if (position > 0) return "in_progress";
  return "open";
}

export function ProjectGovernancePanel({ projectKind, projectId }: { projectKind: ProjectKind; projectId: string }) {
  const [governance, setGovernance] = useState<ProjectGovernance | null>(null);
  const [checks, setChecks] = useState<ProjectCheck[]>([]);
  const [webhookDeliveries, setWebhookDeliveries] = useState<ProjectWebhookDelivery[]>([]);
  const [activeTab, setActiveTab] = useState<GovernanceTab>("members");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<ProjectRole>("editor");
  const [workTitle, setWorkTitle] = useState("");
  const [workBody, setWorkBody] = useState("");
  const [workMilestoneId, setWorkMilestoneId] = useState("");
  const [workLabelId, setWorkLabelId] = useState("");
  const [labelName, setLabelName] = useState("");
  const [milestoneTitle, setMilestoneTitle] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [appName, setAppName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [teamName, setTeamName] = useState("");
  const [teamMemberId, setTeamMemberId] = useState("");
  const [boardColumnNames, setBoardColumnNames] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextGovernance, nextChecks, nextDeliveries] = await Promise.all([
        projectCollaborationApi.governance(projectKind, projectId),
        projectCollaborationApi.listChecks(projectKind, projectId).catch(() => []),
        projectCollaborationApi.listWebhookDeliveries(projectKind, projectId).catch(() => []),
      ]);
      setGovernance(nextGovernance);
      setChecks(nextChecks);
      setWebhookDeliveries(nextDeliveries);
      setBoardColumnNames(nextGovernance.board_columns.map((column) => column.name).join("、"));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Project governance information failed to load");
    } finally {
      setLoading(false);
    }
  }, [projectId, projectKind]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  async function run(action: () => Promise<ProjectGovernance | void>, onSuccess?: () => void) {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      if (next) setGovernance(next);
      onSuccess?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Project collaboration operation failed");
    } finally {
      setBusy(false);
    }
  }

  const tabs = useMemo(
    () => [
      { id: "members" as const, label: "Members and Permissions", icon: Users },
      { id: "rules" as const, label: "review and inspection", icon: ShieldCheck },
      { id: "work" as const, label: "Tasks and Kanban", icon: KanbanSquare },
      { id: "integrations" as const, label: "Organization and integration", icon: Network },
    ],
    []
  );

  if (loading) {
    return <div className="flex items-center justify-center gap-2 border-t border-stone-200 py-12 text-sm text-stone-500"><LoaderCircle className="h-5 w-5 animate-spin" />Loading project governance capabilities...</div>;
  }
  if (!governance) {
    return <div className="border-t border-stone-200 py-8 text-sm text-rose-700">{error ?? "Unable to load project governance capabilities."}</div>;
  }

  return (
    <section className="mt-6 border-t border-stone-200 pt-5" aria-label={`Project governance for ${governance.title}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-stone-400">Repository governance</p>
          <h2 className="mt-1 text-lg font-semibold text-stone-950">Project governance</h2>
          <p className="mt-1 text-xs text-stone-500">Current role:{ROLE_LABELS[governance.viewer_role]} · {governance.member_count}  members</p>
        </div>
        <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border border-stone-200 px-3 py-2 text-xs font-semibold text-stone-600">
          <RefreshCw className={clsx("h-4 w-4", loading && "animate-spin")} />refresh
        </button>
      </div>
      {governance.lesson_ids.length ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {governance.lesson_ids.map((lessonId, index) => (
            <Link key={lessonId} href={`/profile/collaboration/${projectKind}/${encodeURIComponent(projectId)}/lesson/${encodeURIComponent(lessonId)}`} className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-3 py-2 text-xs font-semibold text-stone-700 hover:border-stone-400">
              <PencilLine className="h-3.5 w-3.5" />{governance.capabilities.edit_project ? "Collaborative editing" : "View courses"}{governance.lesson_ids.length > 1 ? ` ${index + 1}` : ""}
            </Link>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex gap-1 overflow-x-auto rounded-lg bg-stone-100 p-1">
        {tabs.map((tab) => (
          <button key={tab.id} type="button" onClick={() => setActiveTab(tab.id)} className={clsx("inline-flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-xs font-semibold", activeTab === tab.id ? "bg-white text-stone-950 shadow-sm" : "text-stone-500")}>
            <tab.icon className="h-4 w-4" />{tab.label}
          </button>
        ))}
      </div>
      {error ? <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

      {activeTab === "members" ? (
        <div className="mt-4 space-y-4">
          {governance.capabilities.manage_members ? (
            <form onSubmit={(event) => { event.preventDefault(); if (!inviteEmail.trim()) return; void run(() => projectCollaborationApi.invite(projectKind, projectId, inviteEmail, inviteRole), () => setInviteEmail("")); }} className="grid gap-2 rounded-lg border border-stone-200 bg-stone-50 p-3 sm:grid-cols-[1fr_150px_auto]">
              <input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="Collaborator's email" className="h-10 rounded-md border border-stone-200 bg-white px-3 text-sm outline-none focus:border-violet-400" />
              <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as ProjectRole)} className="h-10 rounded-md border border-stone-200 bg-white px-3 text-sm">
                {(["maintainer", "editor", "reviewer", "viewer"] as ProjectRole[]).map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
              </select>
              <button disabled={busy || !inviteEmail.trim()} className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-stone-950 px-4 text-sm font-semibold text-white disabled:opacity-40"><UserPlus className="h-4 w-4" />invite</button>
            </form>
          ) : null}
          <div className="divide-y divide-stone-200 rounded-lg border border-stone-200 bg-white">
            {governance.members.map((member) => (
              <div key={member.user_id} className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div><p className="text-sm font-semibold text-stone-900">{member.display_name || member.email || "Project members"}</p><p className="mt-1 text-xs text-stone-400">{member.email || member.user_id}</p></div>
                {governance.capabilities.manage_members && member.role !== "owner" ? (
                  <div className="flex items-center gap-2">
                    <select value={member.role} onChange={(event) => void run(() => projectCollaborationApi.updateMember(projectKind, projectId, member.user_id, event.target.value as ProjectRole))} className="h-9 rounded-md border border-stone-200 bg-white px-2 text-xs">
                      {(["maintainer", "editor", "reviewer", "viewer"] as ProjectRole[]).map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
                    </select>
                    <button type="button" disabled={busy} onClick={() => void run(() => projectCollaborationApi.removeMember(projectKind, projectId, member.user_id))} className="rounded-md p-2 text-stone-400 hover:bg-rose-50 hover:text-rose-700"><Trash2 className="h-4 w-4" /></button>
                  </div>
                ) : <span className="rounded-full bg-stone-100 px-3 py-1 text-xs font-semibold text-stone-600">{ROLE_LABELS[member.role]}</span>}
              </div>
            ))}
          </div>
          {governance.invitations.some((item) => item.status === "pending") ? <div className="rounded-lg border border-amber-200 bg-amber-50 p-3"><p className="text-xs font-semibold text-amber-900">Awaiting invitation</p>{governance.invitations.filter((item) => item.status === "pending").map((item) => <div key={item.id} className="mt-2 flex items-center justify-between gap-3 text-xs text-amber-800"><span>{item.email} · {ROLE_LABELS[item.role]}</span><button type="button" onClick={() => void run(() => projectCollaborationApi.revokeInvitation(projectKind, projectId, item.id))} className="font-semibold">Cancel</button></div>)}</div> : null}
          <div className="rounded-lg border border-stone-200 bg-white p-4">
            <p className="text-xs font-semibold">audit records</p>
            <div className="mt-3 space-y-2">
              {governance.audit_events.slice(0, 20).map((event) => <div key={event.id} className="flex flex-col gap-1 rounded-md bg-stone-50 px-3 py-2 text-xs sm:flex-row sm:items-center sm:justify-between"><span><span className="font-semibold">{event.actor_user_id}</span> · {event.kind}</span><time className="text-stone-400">{new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(event.created_at))}</time></div>)}
              {!governance.audit_events.length ? <p className="text-xs text-stone-400">There are no records of project operations yet.</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {activeTab === "rules" ? (
        <div className="mt-4 space-y-4">
          <div className="rounded-lg border border-stone-200 bg-white p-4">
            <div className="flex items-center gap-2"><GitBranch className="h-4 w-4 text-violet-600" /><h3 className="text-sm font-semibold">Default branching and merging rules</h3></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={governance.policy.protect_default_branch} disabled={!governance.capabilities.manage_rules} onChange={(event) => setGovernance({ ...governance, policy: { ...governance.policy, protect_default_branch: event.target.checked } })} />Protect default branch</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={governance.policy.require_passing_checks} disabled={!governance.capabilities.manage_rules} onChange={(event) => setGovernance({ ...governance, policy: { ...governance.policy, require_passing_checks: event.target.checked } })} />Pre-merge check must pass</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={governance.policy.dismiss_stale_approvals} disabled={!governance.capabilities.manage_rules} onChange={(event) => setGovernance({ ...governance, policy: { ...governance.policy, dismiss_stale_approvals: event.target.checked } })} />New revision invalidates old approval</label>
              <label className="flex items-center gap-2 text-sm">Number of approvals required<input type="number" min={0} max={20} value={governance.policy.required_approvals} disabled={!governance.capabilities.manage_rules} onChange={(event) => setGovernance({ ...governance, policy: { ...governance.policy, required_approvals: Number(event.target.value) } })} className="h-9 w-20 rounded-md border border-stone-200 px-2" /></label>
            </div>
            {governance.capabilities.manage_rules ? <button type="button" disabled={busy} onClick={() => void run(() => projectCollaborationApi.updatePolicy(projectKind, projectId, governance.policy))} className="mt-4 rounded-md bg-stone-950 px-4 py-2 text-xs font-semibold text-white">save rule</button> : null}
          </div>
          <div className="rounded-lg border border-stone-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><ClipboardCheck className="h-4 w-4 text-emerald-600" /><h3 className="text-sm font-semibold">course inspection</h3></div><button type="button" disabled={busy || !governance.capabilities.review_changes} onClick={() => void run(async () => { setChecks(await projectCollaborationApi.runChecks(projectKind, projectId)); })} className="rounded-md border border-stone-200 px-3 py-2 text-xs font-semibold">recheck</button></div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">{checks.map((check) => <div key={check.id} className={clsx("rounded-md border p-3", check.status === "passed" ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50")}><p className="flex items-center gap-2 text-xs font-semibold">{check.status === "passed" ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <XCircle className="h-4 w-4 text-rose-600" />}{CHECK_LABELS[check.name] ?? check.name}</p><p className="mt-1 text-xs text-stone-600">{check.summary}</p></div>)}</div>
          </div>
        </div>
      ) : null}

      {activeTab === "work" ? (
        <div className="mt-4 space-y-4">
          {governance.capabilities.manage_work_items ? <form onSubmit={(event) => { event.preventDefault(); if (!workTitle.trim()) return; void run(() => projectCollaborationApi.createWorkItem(projectKind, projectId, workTitle, workBody, workMilestoneId, workLabelId ? [workLabelId] : []), () => { setWorkTitle(""); setWorkBody(""); setWorkMilestoneId(""); setWorkLabelId(""); }); }} className="rounded-lg border border-stone-200 bg-stone-50 p-3"><input value={workTitle} onChange={(event) => setWorkTitle(event.target.value)} placeholder="New task title" className="h-10 w-full rounded-md border border-stone-200 bg-white px-3 text-sm" /><textarea value={workBody} onChange={(event) => setWorkBody(event.target.value)} placeholder="Mission statement" rows={2} className="mt-2 w-full rounded-md border border-stone-200 bg-white px-3 py-2 text-sm" /><div className="mt-2 grid gap-2 sm:grid-cols-2"><select value={workMilestoneId} onChange={(event) => setWorkMilestoneId(event.target.value)} className="h-9 rounded border border-stone-200 bg-white px-2 text-xs"><option value="">Not associated with milestones</option>{governance.milestones.filter((item) => !item.closed).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><select value={workLabelId} onChange={(event) => setWorkLabelId(event.target.value)} className="h-9 rounded border border-stone-200 bg-white px-2 text-xs"><option value="">No tags added</option>{governance.labels.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div><button disabled={busy || !workTitle.trim()} className="mt-2 inline-flex items-center gap-2 rounded-md bg-stone-950 px-4 py-2 text-xs font-semibold text-white"><Plus className="h-4 w-4" />Create task</button></form> : null}
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(1, governance.board_columns.length || 4)}, minmax(210px, 1fr))` }}>{(governance.board_columns.length ? governance.board_columns : (["open", "in_progress", "review", "closed"] as WorkItemState[]).map((state, position) => ({ id: state, name: STATE_LABELS[state], position }))).map((column, position, columns) => { const items = governance.work_items.filter((item) => governance.board_columns.length ? item.board_column_id === column.id || (!item.board_column_id && item.state === stateForColumn(position, columns.length)) : item.state === column.id); return <div key={column.id} className="rounded-lg bg-stone-100 p-2"><p className="px-1 py-2 text-xs font-semibold text-stone-600">{column.name} · {items.length}</p><div className="space-y-2">{items.map((item) => <article key={item.id} className="rounded-md border border-stone-200 bg-white p-3"><p className="text-xs text-stone-400">#{item.number}</p><p className="mt-1 text-sm font-semibold">{item.title}</p>{governance.capabilities.manage_work_items ? <select value={governance.board_columns.length ? item.board_column_id ?? column.id : item.state} onChange={(event) => { const targetIndex = governance.board_columns.findIndex((candidate) => candidate.id === event.target.value); void run(() => projectCollaborationApi.updateWorkItem(projectKind, projectId, item.id, governance.board_columns.length ? stateForColumn(targetIndex, governance.board_columns.length) : event.target.value as WorkItemState, governance.board_columns.length ? event.target.value : null)); }} className="mt-3 h-8 w-full rounded border border-stone-200 px-2 text-xs">{(governance.board_columns.length ? governance.board_columns : Object.entries(STATE_LABELS).map(([id, name], optionPosition) => ({ id, name, position: optionPosition }))).map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select> : null}</article>)}</div></div>; })}</div>
          {governance.capabilities.manage_work_items ? <div className="grid gap-3 sm:grid-cols-3"><form onSubmit={(event) => { event.preventDefault(); if (!labelName.trim()) return; void run(() => projectCollaborationApi.createLabel(projectKind, projectId, labelName, "#7c3aed"), () => setLabelName("")); }} className="rounded-lg border border-stone-200 p-3"><p className="text-xs font-semibold">Label</p><input value={labelName} onChange={(event) => setLabelName(event.target.value)} placeholder="Tag name" className="mt-2 h-9 w-full rounded border border-stone-200 px-2 text-xs" /></form><form onSubmit={(event) => { event.preventDefault(); if (!milestoneTitle.trim()) return; void run(() => projectCollaborationApi.createMilestone(projectKind, projectId, milestoneTitle), () => setMilestoneTitle("")); }} className="rounded-lg border border-stone-200 p-3"><p className="text-xs font-semibold">milestone</p><input value={milestoneTitle} onChange={(event) => setMilestoneTitle(event.target.value)} placeholder="milestone title" className="mt-2 h-9 w-full rounded border border-stone-200 px-2 text-xs" /></form><form onSubmit={(event) => { event.preventDefault(); const columns = boardColumnNames.split(/[,，、]/).map((name) => name.trim()).filter(Boolean); if (!columns.length) return; void run(() => projectCollaborationApi.updateBoard(projectKind, projectId, columns)); }} className="rounded-lg border border-stone-200 p-3"><p className="text-xs font-semibold">Kanban column</p><input value={boardColumnNames} onChange={(event) => setBoardColumnNames(event.target.value)} placeholder="To-do, in progress, completed" className="mt-2 h-9 w-full rounded border border-stone-200 px-2 text-xs" /><button type="submit" disabled={!boardColumnNames.trim()} className="mt-2 rounded border border-stone-200 px-3 py-2 text-xs">Save custom column</button></form></div> : null}
        </div>
      ) : null}

      {activeTab === "integrations" ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-stone-200 bg-white p-4"><div className="flex items-center gap-2"><Users className="h-4 w-4 text-blue-600" /><h3 className="text-sm font-semibold">Organization and Team</h3></div>{governance.teams.length ? <div className="mt-3 space-y-2">{governance.teams.map((team) => <div key={team.id} className="rounded-md bg-stone-50 p-3 text-xs"><p className="font-semibold">{team.name}</p><p className="mt-1 text-stone-500">{team.member_count}  members· {team.project_role ? ROLE_LABELS[team.project_role] : "This item is not authorized"}</p></div>)}</div> : <p className="mt-3 text-xs text-stone-500">There is no team associated with this project yet.</p>}{governance.capabilities.manage_members ? <form onSubmit={(event) => { event.preventDefault(); if (!organizationName.trim() || !teamName.trim()) return; void run(async () => { const organization = await projectCollaborationApi.createOrganization(organizationName, slugify(organizationName)); const team = await projectCollaborationApi.createTeam(organization.id, teamName, slugify(teamName)); if (teamMemberId.trim()) await projectCollaborationApi.addTeamMember(organization.id, team.id, teamMemberId.trim()); await projectCollaborationApi.grantTeamProject(organization.id, team.id, projectKind, projectId, "reviewer"); return projectCollaborationApi.governance(projectKind, projectId); }, () => { setOrganizationName(""); setTeamName(""); setTeamMemberId(""); }); }} className="mt-4 space-y-2 border-t border-stone-100 pt-4"><input value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} placeholder="New organization name" className="h-9 w-full rounded border border-stone-200 px-2 text-xs" /><input value={teamName} onChange={(event) => setTeamName(event.target.value)} placeholder="Review team name" className="h-9 w-full rounded border border-stone-200 px-2 text-xs" /><input value={teamMemberId} onChange={(event) => setTeamMemberId(event.target.value)} placeholder="Optional: member user ID" className="h-9 w-full rounded border border-stone-200 px-2 text-xs" /><button disabled={busy || !organizationName.trim() || !teamName.trim()} className="rounded bg-stone-950 px-3 py-2 text-xs font-semibold text-white">Create and empower review teams</button></form> : null}</div>
          <div className="rounded-lg border border-stone-200 bg-white p-4"><div className="flex items-center gap-2"><Webhook className="h-4 w-4 text-orange-600" /><h3 className="text-sm font-semibold">Webhook</h3></div>{governance.webhooks.map((item) => <div key={item.id} className="mt-3 rounded-md bg-stone-50 p-3 text-xs"><p className="break-all font-semibold">{item.url}</p><p className="mt-1 text-stone-500">{item.events.join("、")} · {item.last_delivery_status ?? "Not yet delivered"}</p></div>)}{webhookDeliveries.length ? <div className="mt-3 space-y-1 border-t border-stone-100 pt-3"><p className="text-xs font-semibold">latest delivery</p>{webhookDeliveries.slice(0, 8).map((delivery) => <div key={delivery.id} className="flex items-center justify-between gap-2 text-xs text-stone-500"><span className="truncate">{delivery.event}</span><span>{delivery.status} · {delivery.attempts}  Second-rate</span></div>)}</div> : null}{governance.capabilities.manage_integrations ? <form onSubmit={(event) => { event.preventDefault(); if (!webhookUrl.trim() || webhookSecret.length < 16) return; void run(() => projectCollaborationApi.createWebhook(projectKind, projectId, webhookUrl, ["project.updated", "review.submitted", "merge.completed"], webhookSecret), () => { setWebhookUrl(""); setWebhookSecret(""); void projectCollaborationApi.listWebhookDeliveries(projectKind, projectId).then(setWebhookDeliveries); }); }} className="mt-4 space-y-2 border-t border-stone-100 pt-4"><input value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://example.com/events" className="h-9 w-full rounded border border-stone-200 px-2 text-xs" /><input type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} placeholder="Signing key (at least 16 bits)" className="h-9 w-full rounded border border-stone-200 px-2 text-xs" /><button disabled={busy || !webhookUrl.trim() || webhookSecret.length < 16} className="rounded bg-stone-950 px-3 py-2 text-xs font-semibold text-white">Add Webhook</button></form> : null}</div>
          <div className="rounded-lg border border-stone-200 bg-white p-4 lg:col-span-2"><div className="flex items-center gap-2"><AppWindow className="h-4 w-4 text-violet-600" /><h3 className="text-sm font-semibold">Project application</h3></div><div className="mt-3 grid gap-2 sm:grid-cols-2">{governance.integrations.map((item) => <div key={item.id} className="rounded-md bg-stone-50 p-3 text-xs"><p className="font-semibold">{item.name}</p><p className="mt-1 text-stone-500">{item.permissions.join("、") || "No additional permissions"} · {item.installed ? "Installed" : "Not installed"}</p>{!item.installed && governance.capabilities.manage_integrations ? <button type="button" onClick={() => void run(() => projectCollaborationApi.installApp(projectKind, projectId, item.id))} className="mt-2 font-semibold text-violet-700">Install into project</button> : null}</div>)}</div>{governance.capabilities.manage_integrations ? <form onSubmit={(event) => { event.preventDefault(); if (!appName.trim()) return; void run(async () => { const app = await projectCollaborationApi.createApp(appName, ["checks:write", "issues:write"]); return projectCollaborationApi.installApp(projectKind, projectId, app.id); }, () => setAppName("")); }} className="mt-4 flex gap-2 border-t border-stone-100 pt-4"><input value={appName} onChange={(event) => setAppName(event.target.value)} placeholder="Application name" className="h-9 min-w-0 flex-1 rounded border border-stone-200 px-2 text-xs" /><button disabled={busy || !appName.trim()} className="rounded bg-stone-950 px-3 py-2 text-xs font-semibold text-white">Create and install</button></form> : null}</div>
        </div>
      ) : null}
      {busy ? <div className="mt-4 flex items-center gap-2 text-xs text-stone-400"><LoaderCircle className="h-4 w-4 animate-spin" />Saving project collaboration settings...</div> : null}
    </section>
  );
}

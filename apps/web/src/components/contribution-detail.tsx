"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  GitMerge,
  GitPullRequest,
  LoaderCircle,
  MessageCircle,
  Pencil,
  RotateCcw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { CommunityMarkdown } from "@/components/community/community-markdown";
import { api } from "@/lib/api";
import type { LessonContributionEvent, LessonContributionStatus, LessonContributionView, UserView } from "@/types";

const STATUS_LABELS: Record<LessonContributionStatus, string> = {
  open: "等待审查",
  merge_draft: "合并处理中",
  merged: "已合并",
  closed: "已关闭",
};

const EVENT_LABELS: Partial<Record<LessonContributionEvent["kind"], string>> = {
  opened: "提交了课程改进方案",
  revision_submitted: "更新了提交版本",
  merge_started: "开始合并审查",
  returned_for_changes: "退回继续修改",
  merged: "合并了课程改进",
  closed: "关闭了改进方案",
  reopened: "重新打开了改进方案",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function DocumentDiff({ contribution }: { contribution: LessonContributionView }) {
  const base = contribution.revision.base_document.content_text.split("\n");
  const proposed = contribution.revision.proposed_document.content_text.split("\n");
  return (
    <section className="rounded-[26px] border border-stone-200 bg-white p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">课程内容差异</h2>
        <span className="text-xs text-stone-400">基线 ↔ revision {contribution.current_revision}</span>
      </div>
      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        {[
          { label: "来源基线", lines: base, other: proposed, tone: "bg-rose-50/70" },
          { label: "贡献版本", lines: proposed, other: base, tone: "bg-emerald-50/70" },
        ].map((column) => (
          <div key={column.label} className="min-w-0 overflow-hidden rounded-2xl border border-stone-200">
            <div className="border-b border-stone-200 bg-stone-50 px-4 py-2 text-xs font-semibold text-stone-600">{column.label}</div>
            <pre className="max-h-[480px] overflow-auto bg-white p-3 text-xs leading-6 text-stone-700">
              {column.lines.map((line, index) => (
                <span key={`${index}-${line}`} className={`block min-h-6 whitespace-pre-wrap px-1 ${line !== column.other[index] ? column.tone : ""}`}>
                  <span className="mr-3 inline-block w-7 select-none text-right text-stone-300">{index + 1}</span>
                  {line || " "}
                </span>
              ))}
            </pre>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ContributionDetail({ contributionId }: { contributionId: string }) {
  const router = useRouter();
  const [contribution, setContribution] = useState<LessonContributionView | null>(null);
  const [user, setUser] = useState<UserView | null>(null);
  const [comment, setComment] = useState("");
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);
  const [editingComment, setEditingComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        let currentUser: UserView | null = null;
        try {
          currentUser = await api.getCurrentUser();
        } catch {
          currentUser = null;
        }
        const view = currentUser && currentUser.role !== "guest"
          ? await api.getLessonContribution(contributionId)
          : await api.getPublicLessonContribution(contributionId);
        if (active) {
          setUser(currentUser);
          setContribution(view);
        }
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "改进方案不存在或已停止公开");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [contributionId]);

  const comments = useMemo(
    () => contribution?.events.filter((event) => event.kind === "commented") ?? [],
    [contribution]
  );
  const systemEvents = useMemo(
    () => contribution?.events.filter((event) => event.kind !== "commented") ?? [],
    [contribution]
  );

  async function run(action: (current: LessonContributionView) => Promise<LessonContributionView>) {
    if (!contribution) return;
    setBusy(true);
    setError(null);
    try {
      setContribution(await action(contribution));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失败，请刷新后重试");
    } finally {
      setBusy(false);
    }
  }

  async function handleStartMerge(current: LessonContributionView) {
    const updated = await api.startLessonContributionMerge(current.id, current.version);
    router.push(`/studio?lesson=${encodeURIComponent(updated.source_lesson_id)}&contribution=${encodeURIComponent(updated.id)}`);
    return updated;
  }

  if (loading) {
    return <main className="flex min-h-screen items-center justify-center gap-2 bg-[#f7f5ef] text-sm text-stone-500"><LoaderCircle className="h-5 w-5 animate-spin" />正在载入改进方案…</main>;
  }

  if (!contribution) {
    return <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] px-5"><div className="max-w-lg rounded-[26px] border border-amber-200 bg-amber-50 p-8 text-center text-sm text-amber-900">{error ?? "改进方案不存在或已停止公开。"}</div></main>;
  }

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link href={user?.role !== "guest" ? "/contributions" : "/"} className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950"><ArrowLeft className="h-4 w-4" />返回</Link>
          <BrandMark alt="" className="h-8 w-8 rounded-lg bg-white" size={64} />
          <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-xs font-semibold text-stone-600">{STATUS_LABELS[contribution.status]}</span>
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-5 px-4 py-8 sm:px-6">
        <section className="rounded-[28px] border border-stone-200 bg-white p-6 shadow-[0_18px_50px_rgba(15,23,42,0.06)] sm:p-8">
          <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-stone-400"><GitPullRequest className="h-4 w-4" />Lesson contribution</p>
          <h1 className="mt-3 break-words text-3xl font-semibold tracking-tight">{contribution.title}</h1>
          <p className="mt-2 text-sm text-stone-500">{contribution.source_title} · {contribution.contributor.display_name} 提交</p>
          {contribution.description ? <div className="mt-5 max-w-3xl text-sm leading-7 text-stone-700"><CommunityMarkdown content={contribution.description} /></div> : null}
          {!contribution.source_is_public ? <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">来源课程已转为私有，此页面仅对参与者可见，当前不可开始新合并。</div> : null}
          {error ? <div className="mt-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</div> : null}
          <div className="mt-6 flex flex-wrap gap-2 border-t border-stone-100 pt-5">
            {contribution.viewer_permissions.can_update ? <button disabled={busy} onClick={() => void run((current) => api.updateLessonContribution(current.id, { expected_version: current.version }))} className="rounded-full bg-stone-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">提交当前个人课程新版本</button> : null}
            {contribution.viewer_permissions.can_start_merge ? <button disabled={busy} onClick={() => void run(handleStartMerge)} className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"><GitMerge className="h-4 w-4" />开始合并</button> : null}
            {contribution.viewer_permissions.can_return_for_changes ? <button disabled={busy} onClick={() => void run((current) => api.returnLessonContributionForChanges(current.id, current.version))} className="inline-flex items-center gap-2 rounded-full border border-stone-300 px-4 py-2 text-sm font-semibold"><RotateCcw className="h-4 w-4" />退回继续修改</button> : null}
            {contribution.viewer_permissions.can_close ? <button disabled={busy} onClick={() => void run((current) => api.closeLessonContribution(current.id, current.version))} className="inline-flex items-center gap-2 rounded-full border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-700"><XCircle className="h-4 w-4" />关闭</button> : null}
            {contribution.viewer_permissions.can_reopen ? <button disabled={busy} onClick={() => void run((current) => api.reopenLessonContribution(current.id, current.version))} className="inline-flex items-center gap-2 rounded-full border border-stone-300 px-4 py-2 text-sm font-semibold"><RotateCcw className="h-4 w-4" />重新打开</button> : null}
            {busy ? <LoaderCircle className="h-5 w-5 animate-spin self-center text-stone-400" /> : null}
          </div>
        </section>

        <DocumentDiff contribution={contribution} />

        <section className="rounded-[26px] border border-stone-200 bg-white p-5 sm:p-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><MessageCircle className="h-5 w-5" />讨论与时间线</h2>
          <div className="mt-5 space-y-3">
            {systemEvents.map((event) => <div key={event.id} className="flex gap-3 rounded-2xl bg-stone-50 px-4 py-3 text-sm"><span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-stone-400" /><div><p><span className="font-semibold">{event.actor.display_name}</span> {EVENT_LABELS[event.kind] ?? event.kind}</p><p className="mt-1 text-xs text-stone-400">{formatDate(event.created_at)}</p></div></div>)}
            {comments.map((event) => {
              const deleted = event.metadata.deleted === true;
              const own = user?.id === event.actor.user_id;
              return <article key={event.id} className="rounded-2xl border border-stone-200 p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold">{event.actor.display_name}</p><p className="mt-1 text-xs text-stone-400">{formatDate(event.created_at)}{event.metadata.edited ? " · 已编辑" : ""}</p></div>{own && !deleted ? <div className="flex gap-1"><button type="button" onClick={() => { setEditingCommentId(event.id); setEditingComment(event.body); }} className="rounded-lg p-2 text-stone-400 hover:bg-stone-100 hover:text-stone-700"><Pencil className="h-4 w-4" /></button><button type="button" disabled={busy} onClick={() => void run((current) => api.deleteLessonContributionComment(current.id, event.id, current.version))} className="rounded-lg p-2 text-stone-400 hover:bg-rose-50 hover:text-rose-700"><Trash2 className="h-4 w-4" /></button></div> : null}</div>{editingCommentId === event.id ? <div className="mt-3 flex gap-2"><input value={editingComment} onChange={(change) => setEditingComment(change.target.value)} className="min-w-0 flex-1 rounded-xl border border-stone-200 px-3 py-2 text-sm outline-none focus:border-stone-500" /><button type="button" disabled={!editingComment.trim() || busy} onClick={() => void run(async (current) => { const updated = await api.editLessonContributionComment(current.id, event.id, current.version, editingComment); setEditingCommentId(null); return updated; })} className="rounded-xl bg-stone-950 px-3 py-2 text-sm font-semibold text-white">保存</button></div> : <p className={`mt-3 whitespace-pre-wrap text-sm leading-6 ${deleted ? "italic text-stone-400" : "text-stone-700"}`}>{deleted ? "该评论已删除" : event.body}</p>}</article>;
            })}
          </div>
          {contribution.viewer_permissions.can_comment ? <div className="mt-5 flex gap-2 border-t border-stone-100 pt-5"><textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="参与这次课程改进讨论" rows={3} className="min-w-0 flex-1 resize-y rounded-2xl border border-stone-200 px-4 py-3 text-sm outline-none focus:border-stone-500" /><button type="button" disabled={!comment.trim() || busy} onClick={() => void run(async (current) => { const updated = await api.addLessonContributionComment(current.id, current.version, comment); setComment(""); return updated; })} className="inline-flex h-11 items-center gap-2 self-end rounded-full bg-stone-950 px-4 text-sm font-semibold text-white disabled:opacity-50"><Send className="h-4 w-4" />发送</button></div> : !user || user.role === "guest" ? <p className="mt-5 border-t border-stone-100 pt-5 text-sm text-stone-500"><Link href={`/login?next=${encodeURIComponent(`/contributions/${contribution.id}`)}`} className="font-semibold text-stone-950 underline">登录正式账号</Link> 后参与讨论。</p> : null}
        </section>
      </div>
    </main>
  );
}

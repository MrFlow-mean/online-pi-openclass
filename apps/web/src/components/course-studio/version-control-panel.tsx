"use client";

import clsx from "clsx";
import Link from "next/link";
import {
  Circle,
  Eye,
  FileText,
  GitBranch,
  GitCommitHorizontal,
  GitMerge,
  GitPullRequest,
  LoaderCircle,
  MessageCircle,
  Quote,
  RotateCcw,
  Share2,
} from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

import {
  buildLearningRequirementDisplay,
  learningRequirementStatusLabel,
  type LearningRequirementDisplayFactor,
} from "@/lib/learning-requirement-display";
import {
  buildHistoryGraphRows,
  historyNodeCommunityShareHref,
  historyNodeKindLabel,
  historyNodeSelection,
  type HistoryGraphLane,
  type HistoryGraphRow,
  type HistoryNodeKind,
} from "@/components/course-studio/history-graph-utils";
import { compactText, formatDate } from "@/components/course-studio/history-utils";
import { useAuthenticatedUser } from "@/contexts/auth-session-context";
import { api } from "@/lib/api";
import {
  LessonPackageControls,
  type LessonPackageControlsProps,
} from "@/components/course-studio/lesson-package-controls";
import type {
  BoardDecision,
  CommitRecord,
  Lesson,
  LessonContributionStatus,
  LessonContributionView,
  SelectionRef,
} from "@/types";

const CONTRIBUTION_STATUS_LABELS: Record<LessonContributionStatus, string> = {
  open: "等待审查",
  merge_draft: "合并处理中",
  merged: "已合并",
  closed: "已关闭",
};

function publicSourceLessonId(lesson: Lesson) {
  for (const commit of lesson.history_graph.commits) {
    const sourceId = commit.metadata?.forked_from_public_lesson_id;
    if (typeof sourceId === "string" && sourceId) {
      return sourceId;
    }
  }
  return null;
}

function LessonContributionSection({ activeLesson }: { activeLesson: Lesson }) {
  const sourceLessonId = useMemo(() => publicSourceLessonId(activeLesson), [activeLesson]);
  const [received, setReceived] = useState<LessonContributionView[]>([]);
  const [submitted, setSubmitted] = useState<LessonContributionView[]>([]);
  const [title, setTitle] = useState(`改进：${activeLesson.title}`);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.listLessonContributions("received"),
      api.listLessonContributions("submitted"),
    ])
      .then(([nextReceived, nextSubmitted]) => {
        if (!active) return;
        setReceived(nextReceived.filter((item) => item.source_lesson_id === activeLesson.id));
        setSubmitted(
          nextSubmitted.filter((item) => item.source_lesson_id === (sourceLessonId ?? activeLesson.id))
        );
        setError(null);
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : "协作记录载入失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [activeLesson.id, sourceLessonId]);

  const activeSubmission = submitted.find(
    (item) => item.status === "open" || item.status === "merge_draft"
  ) ?? null;
  const latestSubmission = submitted[0] ?? null;

  async function submitContribution() {
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createLessonContribution(activeLesson.id, title, description);
      setSubmitted((current) => [created, ...current.filter((item) => item.id !== created.id)]);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "提交改进方案失败");
    } finally {
      setBusy(false);
    }
  }

  async function updateContribution(item: LessonContributionView) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateLessonContribution(item.id, { expected_version: item.version });
      setSubmitted((current) => current.map((candidate) => candidate.id === updated.id ? updated : candidate));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "更新提交版本失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-violet-200 bg-violet-50/60 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-violet-700">
            <GitPullRequest className="h-3.5 w-3.5" />
            课程协作
          </p>
          <p className="mt-1 text-xs text-gray-600">
            {sourceLessonId ? "把你的个人学习版本提交给原作者" : `${received.length} 个改进方案提交到这节课`}
          </p>
        </div>
        <Link href="/contributions" className="text-[11px] font-semibold text-violet-700 hover:underline">协作中心</Link>
      </div>

      {loading ? <p className="mt-4 flex items-center gap-2 text-xs text-gray-500"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />载入中…</p> : null}
      {error ? <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700">{error}</p> : null}

      {!loading && sourceLessonId && activeSubmission ? (
        <div className="mt-4 rounded-lg border border-violet-100 bg-white p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-gray-900">{activeSubmission.title}</p>
              <p className="mt-1 text-[10px] text-gray-400">
                {CONTRIBUTION_STATUS_LABELS[activeSubmission.status]} · revision {activeSubmission.current_revision}
              </p>
            </div>
            <Link href={`/contributions/${activeSubmission.id}`} className="shrink-0 text-[11px] font-semibold text-violet-700 hover:underline">查看</Link>
          </div>
          {activeSubmission.viewer_permissions.can_update ? (
            <button type="button" disabled={busy} onClick={() => void updateContribution(activeSubmission)} className="mt-3 w-full rounded-lg bg-violet-700 px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-50">
              提交当前课程的新版本
            </button>
          ) : null}
        </div>
      ) : null}

      {!loading && sourceLessonId && !activeSubmission ? (
        <div className="mt-4 space-y-2">
          {latestSubmission ? (
            <Link href={`/contributions/${latestSubmission.id}`} className="block rounded-lg border border-violet-100 bg-white px-3 py-2 text-[11px] text-gray-600 hover:border-violet-300">
              上一次提交：{CONTRIBUTION_STATUS_LABELS[latestSubmission.status]} · revision {latestSubmission.current_revision}
            </Link>
          ) : null}
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="改进方案标题" className="w-full rounded-lg border border-violet-200 bg-white px-3 py-2 text-xs outline-none focus:border-violet-500" />
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="向原作者说明这次改进（可选）" rows={3} className="w-full resize-y rounded-lg border border-violet-200 bg-white px-3 py-2 text-xs leading-5 outline-none focus:border-violet-500" />
          <button type="button" disabled={busy || !title.trim()} onClick={() => void submitContribution()} className="w-full rounded-lg bg-violet-700 px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-50">
            {busy ? "正在提交…" : "提交课程改进方案"}
          </button>
        </div>
      ) : null}

      {!loading && !sourceLessonId && received.length ? (
        <div className="mt-4 space-y-2">
          {received.slice(0, 5).map((item) => (
            <Link key={item.id} href={`/contributions/${item.id}`} className="block rounded-lg border border-violet-100 bg-white p-3 hover:border-violet-300">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 truncate text-xs font-semibold text-gray-900">{item.title}</p>
                <span className="shrink-0 text-[10px] text-violet-700">{CONTRIBUTION_STATUS_LABELS[item.status]}</span>
              </div>
              <p className="mt-1 text-[10px] text-gray-400">{item.contributor.display_name} · revision {item.current_revision}</p>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}

type VersionControlPanelProps = {
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  activeBoardTask: Lesson["board_task_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  onCreateBranch: () => void | Promise<void>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onReferenceHistoryNode: (selection: SelectionRef) => void;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onMergeBranch: (branchName: string) => void | Promise<void>;
  lessonPackageControls: LessonPackageControlsProps;
};

function FactorList({ title, factors }: { title: string; factors: LearningRequirementDisplayFactor[] }) {
  if (!factors.length) {
    return null;
  }
  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
      <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{title}</p>
      <dl className="mt-3 space-y-2 text-[11px] leading-6">
        {factors.map((factor) => (
          <div key={factor.key} className="grid grid-cols-[72px_minmax(0,1fr)] gap-3">
            <dt className={clsx("font-semibold", factor.required ? "text-gray-700" : "text-gray-500")}>
              {factor.label}
            </dt>
            <dd className={clsx("min-w-0 break-words", factor.filled ? "text-gray-900" : "text-gray-400")}>
              {factor.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function HistoryNodeIcon({ kind }: { kind: HistoryNodeKind }) {
  if (kind === "chat") {
    return <MessageCircle className="h-3.5 w-3.5" />;
  }
  if (kind === "document") {
    return <FileText className="h-3.5 w-3.5" />;
  }
  if (kind === "restore") {
    return <RotateCcw className="h-3.5 w-3.5" />;
  }
  if (kind === "merge") {
    return <GitMerge className="h-3.5 w-3.5" />;
  }
  return <Circle className="h-3.5 w-3.5" />;
}

function nodeKindClasses(kind: HistoryNodeKind) {
  if (kind === "chat") {
    return "bg-sky-50 text-sky-700";
  }
  if (kind === "document") {
    return "bg-emerald-50 text-emerald-700";
  }
  if (kind === "restore") {
    return "bg-amber-50 text-amber-700";
  }
  if (kind === "merge") {
    return "bg-violet-50 text-violet-700";
  }
  return "bg-gray-100 text-gray-600";
}

function GraphCell({ row, lanes }: { row: HistoryGraphRow; lanes: HistoryGraphLane[] }) {
  const laneWidth = 16;
  const width = Math.max(lanes.length * laneWidth, laneWidth);
  const dotLeft = row.lane.index * laneWidth + laneWidth / 2;

  return (
    <div className="relative min-h-12 shrink-0" style={{ width }}>
      {row.continuationLaneIndexes.map((laneIndex) => {
        const lane = lanes[laneIndex];
        return (
          <span
            key={`${row.commit.id}:lane:${laneIndex}`}
            className="absolute inset-y-[-9px] w-px opacity-90"
            style={
              {
                left: laneIndex * laneWidth + laneWidth / 2,
                backgroundColor: lane?.color ?? "#6b7280",
              } satisfies CSSProperties
            }
          />
        );
      })}
      {row.connectors.map((connector) => {
        const fromLeft = connector.fromLane * laneWidth + laneWidth / 2;
        const toLeft = connector.toLane * laneWidth + laneWidth / 2;
        return (
          <span
            key={`${row.commit.id}:connector:${connector.fromLane}:${connector.toLane}`}
            className="absolute top-7 h-px"
            style={
              {
                left: Math.min(fromLeft, toLeft),
                width: Math.abs(toLeft - fromLeft),
                backgroundColor: connector.color,
              } satisfies CSSProperties
            }
          />
        );
      })}
      <span
        className={clsx(
          "absolute top-[17px] h-3 w-3 rounded-full border-2 bg-white",
          row.active && "ring-2 ring-blue-500/40 ring-offset-2 ring-offset-white",
          row.head && "shadow-[0_0_0_3px_rgba(37,99,235,0.14)]"
        )}
        style={
          {
            left: dotLeft - 6,
            borderColor: row.lane.color,
          } satisfies CSSProperties
        }
      />
    </div>
  );
}

function HistoryGraphRowItem({
  row,
  lanes,
  activeLesson,
  currentBranchName,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onReferenceHistoryNode,
  onSwitchBranch,
  canUseCommunity,
}: {
  row: HistoryGraphRow;
  lanes: HistoryGraphLane[];
  activeLesson: Lesson;
  currentBranchName: string;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onReferenceHistoryNode: (selection: SelectionRef) => void;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  canUseCommunity: boolean;
}) {
  return (
    <div
      data-history-node-id={row.commit.id}
      onClick={() => void onPreviewCommit(row.commit)}
      className={clsx(
        "group grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-md px-2 py-2 text-left font-mono transition",
        row.active ? "bg-blue-50" : "hover:bg-gray-50"
      )}
    >
      <GraphCell row={row} lanes={lanes} />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className={clsx("inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em]", nodeKindClasses(row.nodeKind))}>
            <HistoryNodeIcon kind={row.nodeKind} />
            {historyNodeKindLabel(row.nodeKind)}
          </span>
          <span
            className={clsx(
              "shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em]",
              row.lane.branchName === currentBranchName ? "bg-black text-white" : "bg-gray-100 text-gray-600"
            )}
          >
            {row.lane.branchName}
          </span>
          {row.head ? (
            <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em] text-gray-600">
              Head
            </span>
          ) : null}
          <p className="min-w-0 flex-1 truncate text-[12px] font-bold text-gray-900">{row.title}</p>
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-2 text-[11px] leading-5 text-gray-500">
          <span className="shrink-0 text-gray-500">{formatDate(row.commit.created_at)}</span>
          <span className="min-w-0 flex-1 truncate">{row.summary}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-label="Preview"
            title="Preview"
            onClick={(event) => {
              event.stopPropagation();
              void onPreviewCommit(row.commit);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <Eye className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="引用到输入框"
            title="引用到输入框"
            onClick={(event) => {
              event.stopPropagation();
              onReferenceHistoryNode(historyNodeSelection(activeLesson, row.commit));
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <Quote className="h-3.5 w-3.5" />
          </button>
          {canUseCommunity ? (
            <a
              href={historyNodeCommunityShareHref(activeLesson, row.commit)}
              target="_blank"
              rel="noreferrer"
              aria-label="分享到社区"
              title="分享到社区"
              onClick={(event) => event.stopPropagation()}
              className="inline-flex h-6 w-6 items-center justify-center rounded border border-sky-200 bg-sky-50 text-sky-700 hover:border-sky-300 hover:bg-sky-100"
            >
              <Share2 className="h-3.5 w-3.5" />
            </a>
          ) : null}
          <button
            type="button"
            aria-label="Restore"
            title="Restore"
            onClick={(event) => {
              event.stopPropagation();
              void onRestoreCommit(row.commit.id);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Branch"
            title="Branch"
            onClick={(event) => {
              event.stopPropagation();
              void onCreateBranchFromCommit(row.commit);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <GitBranch className="h-3.5 w-3.5" />
          </button>
          {row.lane.branchName !== currentBranchName ? (
            <button
              type="button"
              aria-label="Checkout"
              title="Checkout"
              onClick={(event) => {
                event.stopPropagation();
                void onSwitchBranch(row.lane.branchName);
              }}
              className="inline-flex h-6 w-6 items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-black"
            >
              <GitCommitHorizontal className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
        {row.nodeKind === "merge" && row.commit.parent_ids[1] ? (
          <details className="mt-2 rounded border border-violet-100 bg-violet-50/60 px-2 py-1.5 text-[10px] text-violet-700">
            <summary className="cursor-pointer font-semibold">展开来源分支谱系</summary>
            <p className="mt-1 font-mono">
              {String(row.commit.metadata?.merge_source_branch ?? "source")} · {row.commit.parent_ids[1].slice(0, 10)}
            </p>
          </details>
        ) : null}
      </div>
    </div>
  );
}

export function VersionControlPanel({
  activeLesson,
  previewCommit,
  previewCommitId,
  activeRequirements,
  activeBoardTask,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onReferenceHistoryNode,
  onSwitchBranch,
  onMergeBranch,
  lessonPackageControls,
}: VersionControlPanelProps) {
  const currentUser = useAuthenticatedUser();
  const canUseCommunity = currentUser.role !== "guest";
  const learningRequirementDisplay = activeRequirements
    ? buildLearningRequirementDisplay({ requirementSheet: activeRequirements })
    : null;
  const { lanes, rows } = buildHistoryGraphRows(activeLesson, previewCommitId);

  return (
    <div className="space-y-8">
      <LessonPackageControls {...lessonPackageControls} />

      {canUseCommunity ? <LessonContributionSection activeLesson={activeLesson} /> : null}

      <section className={clsx(lessonPackageControls.isPlaybackActive && "pointer-events-none opacity-60")}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">历史分支图</p>
            <p className="mt-1 text-xs font-semibold text-gray-900">
              {activeLesson.history_graph.commits.length} nodes · {lanes.length} branches
            </p>
          </div>
          <GitCommitHorizontal className="h-4 w-4 text-gray-400" />
        </div>

        <div className="mt-4 flex gap-2">
          <input
            value={newBranchName}
            onChange={(event) => onNewBranchNameChange(event.target.value)}
            placeholder="新分支名"
            className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-black"
          />
          <button
            type="button"
            onClick={() => void onCreateBranch()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#1a1a1a] px-3 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
          >
            <GitBranch className="h-3.5 w-3.5" />
            开分支
          </button>
        </div>

        <div className="mt-4 space-y-2">
          {lanes.map((lane) => (
            <div key={lane.branchName} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void onSwitchBranch(lane.branchName)}
                className={clsx(
                  "inline-flex min-w-0 flex-1 items-center gap-1.5 rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                  lane.isCurrent
                    ? "border-black bg-black text-white"
                    : "border-gray-200 bg-white text-gray-500 hover:text-black"
                )}
              >
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: lane.color }} />
                <span className="truncate">{lane.branchName}</span>
              </button>
              {!lane.isCurrent ? (
                <button
                  type="button"
                  onClick={() => void onMergeBranch(lane.branchName)}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-2.5 py-1 text-[10px] font-semibold text-violet-700 hover:bg-violet-100"
                >
                  <GitMerge className="h-3 w-3" />
                  合并到当前分支
                </button>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section className={clsx(lessonPackageControls.isPlaybackActive && "pointer-events-none opacity-60")}>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
        <div className="mt-4 rounded-lg border border-gray-200 bg-white p-2 shadow-sm">
          {rows.map((row) => (
            <HistoryGraphRowItem
              key={row.commit.id}
              row={row}
              lanes={lanes}
              activeLesson={activeLesson}
              currentBranchName={activeLesson.history_graph.current_branch}
              onPreviewCommit={onPreviewCommit}
              onRestoreCommit={onRestoreCommit}
              onCreateBranchFromCommit={onCreateBranchFromCommit}
              onReferenceHistoryNode={onReferenceHistoryNode}
              onSwitchBranch={onSwitchBranch}
              canUseCommunity={canUseCommunity}
            />
          ))}
        </div>
      </section>

      <section
        className={clsx(
          "border-t border-gray-200 pt-6",
          lessonPackageControls.isPlaybackActive && "pointer-events-none opacity-60"
        )}
      >
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-gray-400" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前上下文</p>
        </div>
        {previewCommit ? (
          <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs font-semibold text-gray-900">{previewCommit.label}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{compactText(previewCommit.message, 180)}</p>
          </div>
        ) : null}
        {activeBoardTask ? (
          <>
            <p className="mt-4 text-sm leading-7 text-gray-700">{activeBoardTask.question_or_topic}</p>
            <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
              <p className="text-xs font-semibold text-gray-900">{activeBoardTask.requested_action ?? "暂无待执行任务"}</p>
              <p className="mt-2 text-[11px] leading-6 text-gray-500">
                {activeBoardTask.target_hint || "执行完成后，当前清单会归档到历史并清空。"}
              </p>
            </div>
          </>
        ) : learningRequirementDisplay ? (
          <>
            <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">教学类型</p>
                  <p className="mt-1 text-sm font-semibold text-gray-900">{learningRequirementDisplay.teachingType}</p>
                </div>
                <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] font-semibold text-gray-600">
                  {learningRequirementStatusLabel(learningRequirementDisplay.status)}
                </span>
              </div>
              {learningRequirementDisplay.summary ? (
                <p className="mt-3 text-[11px] leading-6 text-gray-500">{learningRequirementDisplay.summary}</p>
              ) : null}
            </div>
            <FactorList title="核心因素" factors={learningRequirementDisplay.coreFactors} />
            <FactorList title="辅助因素" factors={learningRequirementDisplay.auxiliaryFactors} />
          </>
        ) : (
          <p className="mt-4 text-sm leading-7 text-gray-700">
            等待下一次任务需求：说明要操作的位置、动作类型，以及希望怎么讲解或怎么编写。
          </p>
        )}
        {latestBoardDecision ? (
          <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
            <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}

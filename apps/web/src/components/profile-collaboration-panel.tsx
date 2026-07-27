"use client";

import clsx from "clsx";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  FolderGit2,
  GitPullRequest,
  Inbox,
  LoaderCircle,
  RefreshCw,
  Send,
} from "lucide-react";

import { api } from "@/lib/api";
import type { LessonContributionStatus, LessonContributionView } from "@/types";

export type CollaborationProject = {
  key: string;
  kind: "lesson" | "package";
  id: string;
  title: string;
  visibility: "private" | "public";
  lessonIds: string[];
  lessonCount: number;
};

type ProfileCollaborationPanelProps = {
  isLoadingProjects: boolean;
  projects: CollaborationProject[];
  selectedProjectKey: string | null;
  onSelectProject: (projectKey: string | null) => void;
};

type ContributionRole = "received" | "submitted";

const STATUS_LABELS: Record<LessonContributionStatus, string> = {
  open: "等待审查",
  merge_draft: "合并处理中",
  merged: "已合并",
  closed: "已关闭",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function contributionProjectLessonId(item: LessonContributionView, role: ContributionRole) {
  return role === "submitted" ? item.viewer_project_lesson_id : item.source_lesson_id;
}

function belongsToProject(
  item: LessonContributionView,
  role: ContributionRole,
  project: CollaborationProject
) {
  const projectLessonId = contributionProjectLessonId(item, role);
  return Boolean(projectLessonId && project.lessonIds.includes(projectLessonId));
}

export function ProfileCollaborationPanel({
  isLoadingProjects,
  projects,
  selectedProjectKey,
  onSelectProject,
}: ProfileCollaborationPanelProps) {
  const [received, setReceived] = useState<LessonContributionView[]>([]);
  const [submitted, setSubmitted] = useState<LessonContributionView[]>([]);
  const [role, setRole] = useState<ContributionRole>("received");
  const [status, setStatus] = useState<LessonContributionStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextReceived, nextSubmitted] = await Promise.all([
        api.listLessonContributions("received"),
        api.listLessonContributions("submitted"),
      ]);
      setReceived(nextReceived);
      setSubmitted(nextSubmitted);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "暂时无法载入项目协作记录");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.listLessonContributions("received"),
      api.listLessonContributions("submitted"),
    ])
      .then(([nextReceived, nextSubmitted]) => {
        if (active) {
          setReceived(nextReceived);
          setSubmitted(nextSubmitted);
          setError(null);
        }
      })
      .catch((failure: unknown) => {
        if (active) {
          setError(failure instanceof Error ? failure.message : "暂时无法载入项目协作记录");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleFocus = () => void load();
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [load]);

  const selectedProject = projects.find((project) => project.key === selectedProjectKey) ?? null;
  const projectSummaries = useMemo(
    () =>
      projects.map((project) => {
        const projectReceived = received.filter((item) => belongsToProject(item, "received", project));
        const projectSubmitted = submitted.filter((item) => belongsToProject(item, "submitted", project));
        return {
          project,
          receivedCount: projectReceived.length,
          submittedCount: projectSubmitted.length,
          activeCount: [...projectReceived, ...projectSubmitted].filter(
            (item) => item.status === "open" || item.status === "merge_draft"
          ).length,
        };
      }),
    [projects, received, submitted]
  );
  const selectedItems = useMemo(() => {
    if (!selectedProject) {
      return [];
    }
    const source = role === "received" ? received : submitted;
    return source
      .filter((item) => belongsToProject(item, role, selectedProject))
      .filter((item) => status === "all" || item.status === status)
      .sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime());
  }, [received, role, selectedProject, status, submitted]);

  if (!selectedProject) {
    return (
      <div>
        <div className="mb-4 flex flex-col gap-4 border-b border-stone-200 pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-500">Project collaboration</p>
            <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight text-stone-950">
              <GitPullRequest className="h-6 w-6" />
              项目协作
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
              从个人项目仓库进入协作管理，按课程或课程包审查改进方案、讨论修改并完成合并。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-400"
          >
            <RefreshCw className={clsx("h-4 w-4", loading && "animate-spin")} />
            刷新
          </button>
        </div>

        {error ? (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700" role="alert">
            {error}
          </div>
        ) : null}

        <div className="space-y-0">
          {isLoadingProjects ? (
            Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="border-b border-stone-200 bg-white p-5">
                <div className="h-4 w-1/3 animate-pulse rounded bg-stone-200" />
                <div className="mt-3 h-3 w-1/2 animate-pulse rounded bg-stone-100" />
              </div>
            ))
          ) : projectSummaries.length ? (
            projectSummaries.map(({ project, receivedCount, submittedCount, activeCount }) => (
              <article key={project.key} className="border-b border-stone-200 bg-white p-5">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="break-words text-base font-semibold text-blue-600">{project.title}</h2>
                      <span className="rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5 text-[11px] font-semibold text-stone-600">
                        {project.kind === "lesson" ? "单独课程" : `课程包 · ${project.lessonCount} 节`}
                      </span>
                      <span className="rounded-full border border-stone-200 bg-white px-2 py-0.5 text-[11px] font-semibold text-stone-500">
                        {project.visibility === "public" ? "Public" : "Private"}
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-stone-500">
                      <span>{receivedCount} 个收到的改进</span>
                      <span>{submittedCount} 个已提交改进</span>
                      <span className={activeCount ? "font-semibold text-violet-700" : undefined}>
                        {activeCount} 个待处理
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onSelectProject(project.key)}
                    className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm font-semibold text-violet-700 transition hover:border-violet-300 hover:bg-violet-100"
                  >
                    管理协作
                    <ArrowUpRight className="h-4 w-4" />
                  </button>
                </div>
              </article>
            ))
          ) : (
            <div className="rounded-lg border border-dashed border-stone-300 bg-white/80 px-5 py-10 text-center text-sm text-stone-500">
              还没有可以管理协作的课程项目。
            </div>
          )}
        </div>
      </div>
    );
  }

  const selectedSummary = projectSummaries.find(({ project }) => project.key === selectedProject.key);

  return (
    <div>
      <div className="mb-4 border-b border-stone-200 pb-5">
        <button
          type="button"
          onClick={() => onSelectProject(null)}
          className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 transition hover:text-stone-950"
        >
          <ArrowLeft className="h-4 w-4" />
          所有项目
        </button>
        <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-500">Collaboration workspace</p>
            <h1 className="mt-2 break-words text-2xl font-semibold tracking-tight text-stone-950">
              {selectedProject.title}
            </h1>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-stone-100 px-2.5 py-1 font-semibold">
                {selectedProject.kind === "lesson" ? (
                  <BookOpen className="h-3.5 w-3.5" />
                ) : (
                  <FolderGit2 className="h-3.5 w-3.5" />
                )}
                {selectedProject.kind === "lesson" ? "单独课程" : `${selectedProject.lessonCount} 节课程`}
              </span>
              <span>{selectedSummary?.activeCount ?? 0} 个待处理</span>
            </div>
          </div>
          <Link
            href={selectedProject.kind === "lesson" ? `/studio?lesson=${encodeURIComponent(selectedProject.id)}` : `/?package=${encodeURIComponent(selectedProject.id)}`}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-400"
          >
            打开项目
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-lg border border-stone-200 bg-white p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex rounded-md bg-stone-100 p-1">
          {(["received", "submitted"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setRole(value)}
              className={clsx(
                "inline-flex items-center gap-2 rounded px-3 py-2 text-sm font-semibold transition",
                role === value ? "bg-white text-stone-950 shadow-sm" : "text-stone-500"
              )}
            >
              {value === "received" ? <Inbox className="h-4 w-4" /> : <Send className="h-4 w-4" />}
              {value === "received" ? "收到的" : "我提交的"}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {(["all", "open", "merge_draft", "merged", "closed"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setStatus(value)}
              className={clsx(
                "rounded-full border px-3 py-1.5 text-xs font-semibold transition",
                status === value
                  ? "border-stone-950 bg-stone-950 text-white"
                  : "border-stone-200 bg-white text-stone-500 hover:border-stone-400"
              )}
            >
              {value === "all" ? "全部" : STATUS_LABELS[value]}
            </button>
          ))}
        </div>
      </div>

      <section className="mt-4 space-y-3" aria-label={`${selectedProject.title} 的协作记录`}>
        {loading ? (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-stone-200 bg-white py-12 text-sm text-stone-500">
            <LoaderCircle className="h-5 w-5 animate-spin" />
            正在载入项目协作记录…
          </div>
        ) : null}
        {error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-5 py-8 text-sm text-rose-800">{error}</div>
        ) : null}
        {!loading && !error && !selectedItems.length ? (
          <div className="rounded-lg border border-dashed border-stone-300 bg-white/70 px-5 py-12 text-center text-sm text-stone-500">
            当前项目和筛选条件下还没有协作记录。
          </div>
        ) : null}
        {!loading && !error
          ? selectedItems.map((item) => (
              <Link
                key={item.id}
                href={`/contributions/${encodeURIComponent(item.id)}`}
                className="block rounded-lg border border-stone-200 bg-white p-5 transition hover:border-violet-300 hover:shadow-md"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600">
                        {STATUS_LABELS[item.status]}
                      </span>
                      <span className="text-xs text-stone-400">revision {item.current_revision}</span>
                    </div>
                    <h2 className="mt-3 break-words text-base font-semibold text-stone-950">{item.title}</h2>
                    <p className="mt-1 text-sm text-stone-500">{item.source_title}</p>
                    {item.description ? (
                      <p className="mt-3 line-clamp-2 text-sm leading-6 text-stone-600">{item.description}</p>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-left text-xs leading-5 text-stone-400 sm:text-right">
                    <p>{role === "received" ? item.contributor.display_name : item.source_author.display_name}</p>
                    <p>{formatDate(item.updated_at)}</p>
                  </div>
                </div>
              </Link>
            ))
          : null}
      </section>
    </div>
  );
}

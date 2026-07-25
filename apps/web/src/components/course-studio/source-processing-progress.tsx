import clsx from "clsx";
import { ChevronRight } from "lucide-react";

import { publicAgentActivityLabel } from "@/lib/agent-activity";
import type { AgentActivityEvent, SourceIngestionRecord } from "@/types";

type SourceProcessingProgressProps = {
  label: string;
  detail?: string;
  value?: number;
  className?: string;
  activity?: AgentActivityEvent[];
};

type SourceProcessingState = {
  label: string;
  detail?: string;
  value: number;
  activity?: AgentActivityEvent[];
};

const LEGACY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "等待开始解析", value: 12 },
  fetching: { label: "正在获取资料", value: 32 },
  parsing: { label: "正在解析正文", value: 58 },
  indexing: { label: "正在建立检索索引", value: 78 },
};

const DIRECTORY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "等待建立目录", value: 12 },
  fetching: { label: "正在获取资料", value: 32 },
  parsing: { label: "正在读取文件结构", value: 58 },
  indexing: { label: "正在建立目录", value: 78 },
};

const LEGACY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "文件已接收，正在准备解析",
  parsing: "正在解析正文",
  reading_pages: "正在逐页读取正文",
  mapping_structure: "正在识别目录与正文结构",
  building_chunks: "正在建立检索片段",
  extracting_visuals: "正在提取图表与图片",
  persisting: "正在保存资料索引",
  transforming: "正在生成导入产物",
};

const DIRECTORY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "文件已接收，正在准备建立目录",
  parsing: "正在读取文件结构",
  reading_directory_metadata: "正在读取目录元数据",
  locating_toc_pages: "正在定位目录页",
  mapping_directory_to_pages: "正在绑定目录与文件范围",
  scanning_heading_regions: "正在检查页面标题区域",
  normalizing_directory: "文件资料 Agent：读取原文件并生成目录",
  source_codex_investigation: "文件资料 Agent：调查目录结构",
  source_codex_scanning_pages: "文件资料 Agent：扫描文件页面",
  source_codex_mapping_nodes: "文件资料 Agent：映射目录节点",
  source_codex_verifying_ranges: "文件资料 Agent：验证目录坐标",
  source_codex_writing_catalog: "文件资料 Agent：写入目录结果",
  source_codex_ranges_authored: "文件资料 Agent 已提交目录结果",
  reusing_directory_catalog: "后端任务：复用已完成目录",
  calibrating_pdf_pages: "文件资料 Agent：核对印刷页码与 PDF 页码",
  validating_directory: "后端任务：验证目录结构",
  validating_directory_ranges: "后端任务：验证目录范围",
  publishing_catalog: "后端任务：保存目录",
  catalog_ready: "目录已经保存",
};

export function isDirectoryCatalogSource(source: SourceIngestionRecord) {
  return (
    source.structure_strategy === "codex_directory_v1" ||
    source.ingestion_job?.adapter === "codex_directory_v1" ||
    source.metadata.catalog_pipeline === "codex_directory_v1"
  );
}

function activityDetail(event: AgentActivityEvent): string {
  for (const key of ["detail", "command", "query"] as const) {
    const value = event.metadata[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  const result = event.metadata.result;
  if (result !== undefined && result !== null) {
    return typeof result === "string" ? result : JSON.stringify(result, null, 2);
  }
  return "";
}

export function SourceCodexActivity({
  events,
  className,
  title = "后端实时 OpenClass 输出",
  expandedByDefault = false,
}: {
  events: AgentActivityEvent[];
  className?: string;
  title?: string;
  expandedByDefault?: boolean;
}) {
  if (!events.length) {
    return null;
  }
  const visibleEvents = events.slice(-4);
  const latestEvent = visibleEvents.at(-1);
  return (
    <details
      open={expandedByDefault || undefined}
      className={clsx("group mt-2 rounded-md border border-gray-200 bg-gray-50/80 px-2.5 py-2", className)}
      aria-live="polite"
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[10px] text-gray-500 marker:content-none [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />
        <span className="shrink-0 font-semibold tracking-wide">{title}</span>
        {latestEvent ? (
          <span className="truncate text-gray-400">
            · {publicAgentActivityLabel(latestEvent.label)}
          </span>
        ) : null}
      </summary>
      <div className="mt-1.5 space-y-1.5">
        {visibleEvents.map((event) => {
          const detail = activityDetail(event);
          const isActive = event.status === "pending" || event.status === "running";
          return (
            <div key={event.id} className="min-w-0 text-[11px] leading-4 text-gray-600">
              <div className="flex items-center gap-1.5">
                <span
                  className={clsx(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    isActive ? "animate-pulse bg-emerald-500" : "bg-gray-300"
                  )}
                />
                <span className="truncate font-medium text-gray-700">
                  {publicAgentActivityLabel(event.label)}
                </span>
              </div>
              {detail ? (
                <pre className="custom-scrollbar mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded bg-white px-2 py-1.5 font-sans text-[10px] leading-4 text-gray-600">
                  {detail}
                </pre>
              ) : null}
            </div>
          );
        })}
      </div>
    </details>
  );
}

export function SourceProcessingProgress({ label, detail, value, className, activity = [] }: SourceProcessingProgressProps) {
  const isDeterminate = typeof value === "number";
  const normalizedValue = isDeterminate ? Math.max(0, Math.min(100, Math.round(value))) : undefined;

  return (
    <div className={clsx("min-w-0", className)} aria-live="polite">
      <div className="mb-1.5 flex items-center justify-between gap-3 text-[11px] font-medium text-gray-500">
        <span className="truncate">{label}</span>
        {isDeterminate ? <span className="shrink-0 tabular-nums text-emerald-700">{normalizedValue}%</span> : null}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={isDeterminate ? 0 : undefined}
        aria-valuemax={isDeterminate ? 100 : undefined}
        aria-valuenow={normalizedValue}
        aria-valuetext={isDeterminate ? undefined : "处理中"}
        className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100"
      >
        <div
          className={clsx(
            "h-full rounded-full bg-emerald-500 transition-[width] duration-500 ease-out",
            !isDeterminate && "source-processing-progress__indeterminate"
          )}
          style={isDeterminate ? { width: `${normalizedValue}%` } : undefined}
        />
      </div>
      {detail ? <p className="mt-1.5 text-[10px] leading-4 text-gray-500">{detail}</p> : null}
      <SourceCodexActivity events={activity} />
    </div>
  );
}

export function getSourceProcessingState(source: SourceIngestionRecord): SourceProcessingState | null {
  const isDirectoryCatalog = isDirectoryCatalogSource(source);
  const statusProgress = isDirectoryCatalog
    ? DIRECTORY_SOURCE_STATUS_PROGRESS
    : LEGACY_SOURCE_STATUS_PROGRESS;
  const phaseLabels = isDirectoryCatalog
    ? DIRECTORY_JOB_PHASE_LABELS
    : LEGACY_JOB_PHASE_LABELS;
  const job = source.ingestion_job;
  if (job && ACTIVE_JOB_STATUSES.has(job.status)) {
    const phase = job.phase_history.at(-1) ?? "";
    const sourceProgress = latestSourceProgress(job.agent_activity ?? []);
    return {
      label:
        sourceProgress?.label ??
        phaseLabels[phase] ??
        statusProgress[job.status]?.label ??
        (isDirectoryCatalog ? "正在建立目录" : "正在处理资料"),
      detail: sourceProgress?.detail,
      value: job.progress,
      activity: job.agent_activity ?? [],
    };
  }
  const ingestionState = statusProgress[source.status];
  if (ingestionState) {
    return { ...ingestionState, activity: [] };
  }
  if (source.status !== "ready") {
    return null;
  }
  if (source.structure_status === "pending") {
    return {
      label: isDirectoryCatalog ? "正在准备建立目录" : "正在准备结构索引",
      value: 88,
      activity: [],
    };
  }
  if (source.structure_status === "building") {
    return {
      label: isDirectoryCatalog ? "正在验证目录范围" : "正在绑定目录与正文",
      value: 94,
      activity: [],
    };
  }
  return null;
}

function latestSourceProgress(events: AgentActivityEvent[]): { label: string; detail?: string } | null {
  for (const event of events.toReversed()) {
    const value = event.metadata.source_progress;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      continue;
    }
    const progress = value as Record<string, unknown>;
    const label = typeof progress.label === "string" ? progress.label.trim() : "";
    const detail = typeof progress.detail === "string" ? progress.detail.trim() : "";
    if (label) {
      return { label, detail: detail || undefined };
    }
  }
  return null;
}

const ACTIVE_JOB_STATUSES = new Set<SourceIngestionRecord["status"]>([
  "queued",
  "fetching",
  "parsing",
  "indexing",
]);

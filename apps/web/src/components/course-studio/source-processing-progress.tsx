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
  value?: number;
  activity?: AgentActivityEvent[];
};

const LEGACY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "Waiting for parsing to begin", value: 12 },
  fetching: { label: "Retrieving information", value: 32 },
  parsing: { label: "Parsing text", value: 58 },
  indexing: { label: "Building search index", value: 78 },
};

const DIRECTORY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "Waiting for directory creation", value: 12 },
  fetching: { label: "Retrieving information", value: 32 },
  parsing: { label: "Reading file structure", value: 58 },
  indexing: { label: "Creating directory", value: 78 },
};

const LEGACY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "The file has been received and is being prepared for parsing",
  parsing: "Parsing text",
  reading_pages: "Reading text page by page",
  mapping_structure: "Recognizing table of contents and text structure",
  building_chunks: "Creating search fragment",
  extracting_visuals: "Retrieving charts and pictures",
  persisting: "Saving data index",
  transforming: "Generating import product",
};

const DIRECTORY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "The file has been received and the directory is being created.",
  parsing: "Reading file structure",
  reading_directory_metadata: "Reading directory metadata",
  locating_toc_pages: "Locating directory page",
  mapping_directory_to_pages: "Binding directory and file range",
  scanning_heading_regions: "Checking page title area",
  normalizing_directory: "Document Agent: Read the original file and generate a directory",
  source_codex_investigation: "Documentation Agent: Investigate the directory structure",
  source_codex_scanning_pages: "Document Agent: Scan file pages",
  source_codex_mapping_nodes: "Document Agent: Mapping directory node",
  source_codex_verifying_ranges: "File Agent: Verify directory coordinates",
  source_codex_writing_catalog: "File information Agent: write directory results",
  source_codex_ranges_authored: "Document Agent has submitted the directory results",
  reusing_directory_catalog: "Backend tasks: reuse completed directories",
  calibrating_pdf_pages: "Document Agent: Check printed page numbers and PDF page numbers",
  validating_directory: "Backend task: verify directory structure",
  validating_directory_ranges: "Backend task: verify directory scope",
  publishing_catalog: "Backend tasks: Save directory",
  catalog_ready: "Directory has been saved",
  directory_discovery: "确认目录边界",
  page_calibration: "标定 PDF 与印刷页码",
  range_mapping: "生成正文范围",
  validation: "验证目录索引",
  source_agent_working: "文件 Agent 正在确认目录边界",
  catalog_snapshot_available: "目录快照已保存",
  background_catalog_refine: "目录已可用，正在后台补齐正文范围",
};

export function isDirectoryCatalogSource(source: SourceIngestionRecord) {
  return (
    source.structure_strategy === "codex_directory_v1" ||
    source.ingestion_job?.adapter === "codex_directory_v1" ||
    source.ingestion_job?.adapter === "agent_catalog_v2" ||
    source.ingestion_job?.adapter === "agent_catalog_v3" ||
    source.metadata.catalog_pipeline === "codex_directory_v1" ||
    source.metadata.catalog_pipeline === "agent_catalog_v2" ||
    source.metadata.catalog_pipeline === "agent_catalog_v3"
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

function sourceActivityLabel(event: AgentActivityEvent): string {
  const label = publicAgentActivityLabel(event.label);
  if (!/(继续核对|持续完善)/.test(label)) {
    return label;
  }
  return event.status === "pending" || event.status === "running"
    ? "正在确认目录边界"
    : "确认目录边界";
}

export function SourceCodexActivity({
  events,
  className,
  title = "Backend real-time OpenClass output",
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
  const visibleEvents = [...events]
    .sort((left, right) => left.created_at.localeCompare(right.created_at))
    .slice(-4);
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
            · {sourceActivityLabel(latestEvent)}
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
                  {sourceActivityLabel(event)}
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
        {isDeterminate ? (
          <span className="shrink-0 tabular-nums text-emerald-700">{normalizedValue}%</span>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1 text-emerald-700">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" aria-hidden="true" />
            进行中
          </span>
        )}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={isDeterminate ? 0 : undefined}
        aria-valuemax={isDeterminate ? 100 : undefined}
        aria-valuenow={normalizedValue}
        aria-valuetext={isDeterminate ? undefined : `${label}，进行中`}
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
        (isDirectoryCatalog ? "Creating directory" : "Processing data"),
      detail: sourceProgress?.stale
        ? [sourceProgress.detail, "暂无新进度，可能停滞"].filter(Boolean).join(" · ")
        : sourceProgress?.detail,
      value: isDirectoryCatalog
        ? sourceProgress?.determinate
          ? sourceProgress.value
          : undefined
        : job.progress,
      activity: job.agent_activity ?? [],
    };
  }
  const ingestionState = statusProgress[source.status];
  if (ingestionState) {
    return {
      ...ingestionState,
      value: isDirectoryCatalog ? undefined : ingestionState.value,
      activity: [],
    };
  }
  if (source.status !== "ready") {
    return null;
  }
  if (source.structure_status === "pending") {
    return {
      label: isDirectoryCatalog ? "Preparing to create directory" : "Preparing structure index",
      value: 88,
      activity: [],
    };
  }
  if (source.structure_status === "building") {
    return {
      label: isDirectoryCatalog ? "Verifying directory range" : "Binding table of contents and text",
      value: 94,
      activity: [],
    };
  }
  return null;
}

function latestSourceProgress(events: AgentActivityEvent[]): {
  label: string;
  detail?: string;
  value?: number;
  determinate: boolean;
  stale: boolean;
} | null {
  const newestFirst = [...events].sort((left, right) => right.created_at.localeCompare(left.created_at));
  for (const event of newestFirst) {
    const value = event.metadata.source_progress;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      continue;
    }
    const progress = value as Record<string, unknown>;
    const label = typeof progress.label === "string" ? progress.label.trim() : "";
    const detail = typeof progress.detail === "string" ? progress.detail.trim() : "";
    const determinate = progress.determinate === true;
    const rawValue = typeof progress.progress === "number" ? progress.progress : undefined;
    const heartbeatAt = typeof progress.heartbeat_at === "string" ? Date.parse(progress.heartbeat_at) : NaN;
    const stale = Number.isFinite(heartbeatAt) && Date.now() - heartbeatAt > 60_000;
    if (label) {
      return {
        label: stale ? "暂无新进度，可能停滞" : label,
        detail: detail || undefined,
        value: rawValue,
        determinate: determinate && rawValue !== undefined,
        stale,
      };
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

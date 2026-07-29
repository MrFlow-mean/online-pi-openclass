"use client";

import clsx from "clsx";
import { Activity, LoaderCircle, RefreshCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type ModelRunLogEvent } from "@/lib/api";

const EVENT_LABELS: Record<string, string> = {
  started: "模型开始处理",
  provider_started: "已连接模型",
  reasoning_progress: "正在推理",
  output_delta: "正在生成输出",
  provider_completed: "模型已返回",
  completed: "运行完成",
  failed: "运行失败",
  queued: "任务已排队",
  transcript_delta: "正在转写",
  transcript_completed: "转写完成",
  provider_error: "模型连接错误",
  provider_event: "模型事件",
  ready: "实时会话已就绪",
  closed: "实时会话已关闭",
};

type RunGroup = {
  id: string;
  events: ModelRunLogEvent[];
  firstAt: string;
  lastAt: string;
  provider: string;
  model: string;
  status: string;
  title: string;
};

function text(value: unknown) {
  return typeof value === "string" ? value : "";
}

function compact(value: string, limit = 110) {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1)}…`;
}

function inputTitle(payload: Record<string, unknown>) {
  const input = payload.input;
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "模型运行";
  }
  const values = input as Record<string, unknown>;
  return compact(text(values.text) || text(values.user_prompt) || "模型运行");
}

function eventName(event: ModelRunLogEvent) {
  const normalized = text(event.payload.event);
  return EVENT_LABELS[normalized] || normalized || event.event_type;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function runDuration(firstAt: string, lastAt: string) {
  const duration = new Date(lastAt).getTime() - new Date(firstAt).getTime();
  if (!Number.isFinite(duration) || duration < 0) {
    return "";
  }
  if (duration < 1000) {
    return `${duration}ms`;
  }
  return `${(duration / 1000).toFixed(duration < 10_000 ? 1 : 0)}s`;
}

function groupEvents(events: ModelRunLogEvent[]): RunGroup[] {
  const groups = new Map<string, ModelRunLogEvent[]>();
  for (const event of events) {
    const runId = text(event.payload.run_id) || `legacy:${event.id}`;
    const current = groups.get(runId) ?? [];
    current.push(event);
    groups.set(runId, current);
  }
  return [...groups.entries()]
    .map(([id, runEvents]) => {
      const sorted = [...runEvents].sort((left, right) =>
        left.occurred_at.localeCompare(right.occurred_at)
      );
      const first = sorted[0];
      const last = sorted.at(-1) ?? first;
      const firstWithInput = sorted.find((event) => event.payload.input);
      return {
        id,
        events: sorted,
        firstAt: first.occurred_at,
        lastAt: last.occurred_at,
        provider: text(last.payload.provider) || text(first.payload.provider) || "OpenClass",
        model: text(last.payload.model) || text(first.payload.model),
        status: text(last.payload.status) || "completed",
        title: firstWithInput ? inputTitle(firstWithInput.payload) : eventName(last),
      };
    })
    .sort((left, right) => right.lastAt.localeCompare(left.lastAt));
}

function statusClasses(status: string) {
  if (status === "failed") {
    return "bg-red-50 text-red-700";
  }
  if (status === "running" || status === "queued") {
    return "bg-sky-50 text-sky-700";
  }
  return "bg-emerald-50 text-emerald-700";
}

export function ModelRunHistoryPanel({ lessonId }: { lessonId: string }) {
  const [events, setEvents] = useState<ModelRunLogEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef<string | null>(null);
  const requestActiveRef = useRef(false);

  const load = useCallback(
    async (reset: boolean) => {
      if (requestActiveRef.current) {
        return;
      }
      requestActiveRef.current = true;
      if (reset) {
        setRefreshing(true);
      }
      try {
        const response = await api.getModelRunHistory(lessonId, {
          limit: reset ? 600 : 300,
          after: reset ? null : cursorRef.current,
        });
        if (!reset && response.cursor_found === false) {
          cursorRef.current = null;
          setEvents([]);
          setTruncated(false);
          return;
        }
        setEvents((current) => {
          const combined = reset ? response.events : [...current, ...response.events];
          return [...new Map(combined.map((event) => [event.id, event])).values()];
        });
        cursorRef.current = response.next_cursor;
        if (reset) {
          setTruncated(response.truncated);
        }
        setError(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "模型运行历史加载失败");
      } finally {
        requestActiveRef.current = false;
        setLoading(false);
        setRefreshing(false);
      }
    },
    [lessonId]
  );

  useEffect(() => {
    cursorRef.current = null;
    const initial = window.setTimeout(() => void load(true), 0);
    const timer = window.setInterval(() => void load(false), 2500);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [load]);

  const runs = useMemo(() => groupEvents(events), [events]);
  const runningCount = runs.filter((run) => run.status === "running" || run.status === "queued").length;

  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">模型运行历史</p>
          <p className="mt-1 text-xs font-semibold text-gray-900">
            {runs.length} 次运行 · {events.length} 个事件
          </p>
        </div>
        <div className="flex items-center gap-2">
          {runningCount ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-1 text-[10px] font-semibold text-sky-700">
              <LoaderCircle className="h-3 w-3 animate-spin" />
              {runningCount} 个运行中
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:text-black disabled:opacity-50"
            aria-label="刷新模型运行历史"
          >
            <RefreshCcw className={clsx("h-3.5 w-3.5", refreshing && "animate-spin")} />
          </button>
        </div>
      </div>

      {truncated ? (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
          当前先显示最近 600 个事件；更早记录仍保留在服务器日志中。
        </p>
      ) : null}
      {error ? <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p> : null}
      {loading ? (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-4 text-xs text-gray-500">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          正在读取模型事件
        </div>
      ) : runs.length ? (
        <div className="mt-4 space-y-2">
          {runs.map((run) => {
            const active = run.status === "running" || run.status === "queued";
            return (
              <details key={run.id} className="group rounded-lg border border-gray-200 bg-white shadow-sm">
                <summary className="flex cursor-pointer list-none items-start gap-3 p-3 marker:hidden">
                  <span className={clsx("mt-0.5 rounded-lg p-1.5", statusClasses(run.status))}>
                    {active ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-gray-900">{run.title}</span>
                    <span className="mt-1 block text-[10px] leading-4 text-gray-500">
                      {[run.provider, run.model, `${run.events.length} 个事件`, runDuration(run.firstAt, run.lastAt)]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                  <span className="shrink-0 text-[10px] text-gray-400">{formatTime(run.lastAt)}</span>
                </summary>
                <div className="border-t border-gray-100 px-3 py-3">
                  <p className="mb-3 break-all font-mono text-[10px] text-gray-400">run_id: {run.id}</p>
                  <div className="space-y-2">
                    {run.events.map((event) => (
                      <div key={event.id} className="rounded-lg bg-gray-50 p-3">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-[11px] font-semibold text-gray-800">{eventName(event)}</p>
                          <time className="shrink-0 text-[10px] text-gray-400">{formatTime(event.occurred_at)}</time>
                        </div>
                        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-5 text-gray-600">
                          {JSON.stringify(event.payload, null, 2)}
                        </pre>
                      </div>
                    ))}
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      ) : (
        <p className="mt-4 rounded-lg border border-dashed border-gray-200 bg-white p-4 text-xs leading-6 text-gray-500">
          这个课节还没有保存到新的模型运行事件。后续 GPT 和 Codex Live 调用会自动出现在这里。
        </p>
      )}
    </section>
  );
}

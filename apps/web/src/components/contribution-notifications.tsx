"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, GitPullRequest } from "lucide-react";

import { api } from "@/lib/api";
import type { LessonContributionEvent, LessonContributionView } from "@/types";

const EVENT_LABELS: Partial<Record<LessonContributionEvent["kind"], string>> = {
  opened: "提交了新的课程改进",
  revision_submitted: "更新了课程版本",
  commented: "发表了评论",
  merge_started: "开始合并审查",
  returned_for_changes: "退回继续修改",
  merged: "完成了合并",
  closed: "关闭了改进方案",
  reopened: "重新打开了改进方案",
};

function eventSummary(item: LessonContributionView) {
  const event = item.events[item.events.length - 1];
  if (!event) return "课程协作状态已更新";
  const action = EVENT_LABELS[event.kind] ?? "更新了协作状态";
  return `${event.actor.display_name} ${action}`;
}

export function ContributionNotifications() {
  const [items, setItems] = useState<LessonContributionView[]>([]);

  const load = useCallback(async () => {
    const received = await api.listLessonContributions("received");
    setItems(received.slice(0, 3));
  }, []);

  useEffect(() => {
    let active = true;
    api.listLessonContributions("received")
      .then((received) => {
        if (active) setItems(received.slice(0, 3));
      })
      .catch(() => {
        if (active) setItems([]);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleFocus = () => void load().catch(() => undefined);
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [load]);

  const activeCount = items.filter((item) => item.status === "open" || item.status === "merge_draft").length;

  return (
    <section className="mb-4 rounded-2xl border border-violet-200 bg-violet-50/70 p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-xs font-semibold text-violet-900">
          <GitPullRequest className="h-4 w-4" />
          课程协作
        </p>
        <span className="rounded-full bg-violet-700 px-2 py-0.5 text-[10px] font-semibold text-white">
          {activeCount} 待处理
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {items.length ? items.map((item) => (
          <Link key={item.id} href={`/contributions/${item.id}`} className="block rounded-xl border border-violet-100 bg-white px-3 py-2.5 transition hover:border-violet-300">
            <p className="truncate text-xs font-semibold text-stone-950">{item.title}</p>
            <p className="mt-1 line-clamp-1 text-[11px] text-stone-500">{eventSummary(item)}</p>
          </Link>
        )) : (
          <p className="rounded-xl border border-dashed border-violet-200 bg-white/70 px-3 py-4 text-xs text-stone-500">暂时没有收到课程改进方案。</p>
        )}
      </div>
      <Link href="/contributions" className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-full border border-violet-200 bg-white px-3 py-2 text-xs font-semibold text-violet-800 hover:border-violet-400">
        查看全部协作
        <ArrowUpRight className="h-3.5 w-3.5" />
      </Link>
    </section>
  );
}

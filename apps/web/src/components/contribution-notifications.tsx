"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, GitPullRequest } from "lucide-react";

import { api } from "@/lib/api";
import type { LessonContributionEvent, LessonContributionView } from "@/types";

const EVENT_LABELS: Partial<Record<LessonContributionEvent["kind"], string>> = {
  opened: "Submitted new course improvements",
  revision_submitted: "Updated course version",
  commented: "Posted a comment",
  merge_started: "Start merge review",
  returned_for_changes: "Return to continue modification",
  merged: "Merge completed",
  closed: "Improvement plan closed",
  reopened: "Improvement plan reopened",
};

function eventSummary(item: LessonContributionView) {
  const event = item.events[item.events.length - 1];
  if (!event) return "Course collaboration status updated";
  const action = EVENT_LABELS[event.kind] ?? "Collaboration status updated";
  return `${event.actor.display_name} ${action}`;
}

export function ContributionNotifications() {
  const [items, setItems] = useState<LessonContributionView[]>([]);

  const load = useCallback(async () => {
    const received = await api.listLessonContributions("received");
    setItems(received);
  }, []);

  useEffect(() => {
    let active = true;
    api.listLessonContributions("received")
      .then((received) => {
        if (active) setItems(received);
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

          Course Collaboration
        </p>
        <span className="rounded-full bg-violet-700 px-2 py-0.5 text-[10px] font-semibold text-white">
          {activeCount}  Pending
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {items.length ? items.slice(0, 3).map((item) => (
          <Link key={item.id} href={`/contributions/${item.id}`} className="block rounded-xl border border-violet-100 bg-white px-3 py-2.5 transition hover:border-violet-300">
            <p className="truncate text-xs font-semibold text-stone-950">{item.title}</p>
            <p className="mt-1 line-clamp-1 text-[11px] text-stone-500">{eventSummary(item)}</p>
          </Link>
        )) : (
          <p className="rounded-xl border border-dashed border-violet-200 bg-white/70 px-3 py-4 text-xs text-stone-500">No course improvement plan has been received yet.</p>
        )}
      </div>
      <Link href="/contributions" className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-full border border-violet-200 bg-white px-3 py-2 text-xs font-semibold text-violet-800 hover:border-violet-400">

        View all collaborations
        <ArrowUpRight className="h-3.5 w-3.5" />
      </Link>
    </section>
  );
}

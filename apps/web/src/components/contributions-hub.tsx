"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, GitPullRequest, Inbox, LoaderCircle, RefreshCw, Send } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { api } from "@/lib/api";
import type { LessonContributionStatus, LessonContributionView } from "@/types";

const STATUS_LABELS: Record<LessonContributionStatus, string> = {
  open: "Awaiting review",
  merge_draft: "Merging in progress",
  merged: "Merged",
  closed: "Closed",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function ContributionsHub() {
  const [role, setRole] = useState<"received" | "submitted">("received");
  const [status, setStatus] = useState<LessonContributionStatus | "all">("all");
  const [items, setItems] = useState<LessonContributionView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    await Promise.resolve();
    setLoading(true);
    setError(null);
    try {
      setItems(await api.listLessonContributions(role, status === "all" ? null : status));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Unable to load course collaboration records at the moment");
    } finally {
      setLoading(false);
    }
  }, [role, status]);

  useEffect(() => {
    let active = true;
    api.listLessonContributions(role, status === "all" ? null : status)
      .then((nextItems) => {
        if (active) {
          setItems(nextItems);
          setError(null);
        }
      })
      .catch((failure: unknown) => {
        if (active) {
          setError(failure instanceof Error ? failure.message : "Unable to load course collaboration records at the moment");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [role, status]);

  useEffect(() => {
    const handleFocus = () => void load();
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [load]);

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950">
            <ArrowLeft className="h-4 w-4" />

            Return to homepage
          </Link>
          <Link href="/" aria-label="OpenClass Home Page">
            <BrandMark alt="" className="h-8 w-8 rounded-lg bg-white" size={64} />
          </Link>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-3 py-2 text-xs font-semibold text-stone-600 hover:border-stone-400"
          >
            <RefreshCw className="h-3.5 w-3.5" />

            refresh
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <section className="rounded-[30px] border border-stone-200 bg-white p-6 shadow-[0_18px_50px_rgba(15,23,42,0.06)] sm:p-8">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-stone-400">Course collaboration</p>
              <h1 className="mt-2 flex items-center gap-3 text-3xl font-semibold tracking-tight">
                <GitPullRequest className="h-7 w-7" />

                Course Collaboration
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-stone-500">

                Review course improvements submitted to you by others, or follow up on individual learning versions you submit.
              </p>
            </div>
            <div className="flex rounded-full bg-stone-100 p-1">
              {(["received", "submitted"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setLoading(true);
                    setRole(value);
                  }}
                  className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition ${
                    role === value ? "bg-white text-stone-950 shadow-sm" : "text-stone-500"
                  }`}
                >
                  {value === "received" ? <Inbox className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                  {value === "received" ? "received" : "I submitted"}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-7 flex flex-wrap gap-2 border-t border-stone-100 pt-5">
            {(["all", "open", "merge_draft", "merged", "closed"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setLoading(true);
                  setStatus(value);
                }}
                className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                  status === value
                    ? "border-stone-950 bg-stone-950 text-white"
                    : "border-stone-200 bg-white text-stone-500 hover:border-stone-400"
                }`}
              >
                {value === "all" ? "all" : STATUS_LABELS[value]}
              </button>
            ))}
          </div>
        </section>

        <section className="mt-5 space-y-3">
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-[24px] border border-stone-200 bg-white py-16 text-sm text-stone-500">
              <LoaderCircle className="h-5 w-5 animate-spin" />

              Loading collaboration records...
            </div>
          ) : null}
          {error ? (
            <div className="rounded-[24px] border border-rose-200 bg-rose-50 px-5 py-8 text-sm text-rose-800">{error}</div>
          ) : null}
          {!loading && !error && !items.length ? (
            <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-14 text-center text-sm text-stone-500">

              There are no course improvement plans under the current filter.
            </div>
          ) : null}
          {!loading && !error
            ? items.map((item) => (
                <Link
                  key={item.id}
                  href={`/contributions/${encodeURIComponent(item.id)}`}
                  className="block rounded-[24px] border border-stone-200 bg-white p-5 transition hover:-translate-y-0.5 hover:border-stone-400 hover:shadow-lg"
                >
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600">
                          {STATUS_LABELS[item.status]}
                        </span>
                        <span className="text-xs text-stone-400">revision {item.current_revision}</span>
                      </div>
                      <h2 className="mt-3 break-words text-lg font-semibold">{item.title}</h2>
                      <p className="mt-1 text-sm text-stone-500">{item.source_title}</p>
                      {item.description ? <p className="mt-3 line-clamp-2 text-sm leading-6 text-stone-600">{item.description}</p> : null}
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
    </main>
  );
}

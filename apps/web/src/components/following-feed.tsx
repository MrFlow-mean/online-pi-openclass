"use client";

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, LoaderCircle, Search, UserRound } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { PublicCourseDiscoveryCard } from "@/components/public-course-discovery-card";
import {
  listPublicCourses,
  type PublicCourseSearchResult,
} from "@/lib/project-visibility";

type CreatorFilter = "all" | string;

function matchesSearch(course: PublicCourseSearchResult, query: string) {
  if (!query) {
    return true;
  }
  return [
    course.owner_display_name,
    course.title,
    course.summary,
    course.tags.join(" "),
  ]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

export function FollowingFeedContent() {
  const [courses, setCourses] = useState<PublicCourseSearchResult[]>([]);
  const [selectedCreator, setSelectedCreator] = useState<CreatorFilter>("all");
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listPublicCourses("recent", 100, controller.signal)
      .then(setCourses)
      .catch((loadError: unknown) => {
        if (!controller.signal.aborted) {
          setError(loadError instanceof Error ? loadError.message : "暂时无法载入课程动态。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  const creators = useMemo(() => {
    const counts = new Map<string, number>();
    courses.forEach((course) => {
      counts.set(course.owner_display_name, (counts.get(course.owner_display_name) ?? 0) + 1);
    });
    return Array.from(counts, ([name, count]) => ({ name, count })).sort(
      (left, right) => right.count - left.count || left.name.localeCompare(right.name, "zh-CN"),
    );
  }, [courses]);
  const normalizedQuery = query.trim().toLowerCase();
  const visibleCourses = courses.filter(
    (course) =>
      (selectedCreator === "all" || course.owner_display_name === selectedCreator) &&
      matchesSearch(course, normalizedQuery),
  );

  return (
    <div className="mx-auto grid max-w-7xl gap-5 lg:grid-cols-[250px_minmax(0,1fr)] lg:items-start">
      <aside className="min-w-0 rounded-2xl border border-stone-200 bg-[#eef0f3] p-2 lg:sticky lg:top-24">
        <button
          type="button"
          onClick={() => setSelectedCreator("all")}
          className={clsx(
            "flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition",
            selectedCreator === "all" ? "bg-white text-stone-950 shadow-sm" : "text-stone-600 hover:bg-white/70",
          )}
        >
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[#ff6699] text-white">
            <Activity className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">全部动态</span>
            <span className="text-xs text-stone-400">{courses.length} 个真实项目</span>
          </span>
        </button>
        <div className="mt-1 space-y-1">
          {creators.map((creator) => (
            <button
              key={creator.name}
              type="button"
              onClick={() => setSelectedCreator(creator.name)}
              className={clsx(
                "flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition",
                selectedCreator === creator.name
                  ? "bg-white text-stone-950 shadow-sm"
                  : "text-stone-600 hover:bg-white/70",
              )}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-stone-200 text-stone-600">
                <UserRound className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{creator.name}</span>
              <span className="text-xs text-stone-400">{creator.count}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="min-w-0 rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_18px_50px_rgba(15,23,42,0.06)]">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
              <Activity className="h-5 w-5" />
              {selectedCreator === "all" ? "课程动态" : selectedCreator}
            </h1>
            <p className="mt-2 text-sm leading-6 text-stone-500">
              按真实公开课程的最近更新时间排列；下载后可在个人项目中编辑、保留历史并提交 PR 协作。
            </p>
          </div>
          <div className="relative min-w-0 md:w-80">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索作者、课程或主题"
              className="w-full rounded-full border border-stone-200 bg-white py-2.5 pl-9 pr-4 text-sm outline-none focus:border-stone-950"
            />
          </div>
        </div>

        {isLoading ? (
          <div className="mt-6 flex items-center justify-center gap-2 rounded-xl border border-stone-200 py-16 text-sm text-stone-500">
            <LoaderCircle className="h-5 w-5 animate-spin" />
            正在载入真实课程动态…
          </div>
        ) : error ? (
          <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">
            {error}
          </div>
        ) : visibleCourses.length ? (
          <div className="mt-6 space-y-4">
            {visibleCourses.map((course) => (
              <PublicCourseDiscoveryCard
                key={`${course.kind}:${course.id}`}
                course={course}
                badge="最近更新"
              />
            ))}
          </div>
        ) : (
          <div className="mt-6 rounded-xl border border-dashed border-stone-300 px-5 py-12 text-center text-sm text-stone-500">
            没有找到匹配的公开课程动态。
          </div>
        )}
      </section>
    </div>
  );
}

export function FollowingFeed() {
  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-40 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100"
          >
            <ArrowLeft className="h-4 w-4" />
            开放课堂
          </Link>
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700"
          >
            <BrandMark alt="" className="h-5 w-5 rounded bg-white" size={40} />
            开放课堂
          </Link>
        </div>
      </header>
      <div className="px-4 py-6 sm:px-6">
        <FollowingFeedContent />
      </div>
    </main>
  );
}

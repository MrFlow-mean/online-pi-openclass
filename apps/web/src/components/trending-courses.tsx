"use client";

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  BookOpen,
  Flame,
  LoaderCircle,
  Search,
  Star,
  UsersRound,
} from "lucide-react";

import { PublicCourseDiscoveryCard } from "@/components/public-course-discovery-card";
import {
  listPublicCourses,
  type PublicCourseSearchResult,
} from "@/lib/project-visibility";

type TrendWindow = "today" | "week" | "month";

const trendWindows: Array<{ id: TrendWindow; label: string; days: number }> = [
  { id: "today", label: "今日", days: 1 },
  { id: "week", label: "本周", days: 7 },
  { id: "month", label: "本月", days: 31 },
];

function isWithinDays(value: string | null | undefined, days: number) {
  if (!value) {
    return false;
  }
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp <= days * 86_400_000;
}

function matchesQuery(course: PublicCourseSearchResult, query: string) {
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

export function TrendingCourses() {
  const [courses, setCourses] = useState<PublicCourseSearchResult[]>([]);
  const [trendWindow, setTrendWindow] = useState<TrendWindow>("month");
  const [query, setQuery] = useState("");
  const [selectedTag, setSelectedTag] = useState("all");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listPublicCourses("popular", 100, controller.signal)
      .then(setCourses)
      .catch((loadError: unknown) => {
        if (!controller.signal.aborted) {
          setError(loadError instanceof Error ? loadError.message : "暂时无法载入热门课程。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  const tags = useMemo(() => {
    const counts = new Map<string, number>();
    courses.forEach((course) => {
      course.tags.forEach((tag) => counts.set(tag, (counts.get(tag) ?? 0) + 1));
    });
    return Array.from(counts, ([tag, count]) => ({ tag, count }))
      .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag, "zh-CN"))
      .slice(0, 12);
  }, [courses]);
  const selectedWindow = trendWindows.find((item) => item.id === trendWindow) ?? trendWindows[2];
  const visibleCourses = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return courses.filter(
      (course) =>
        isWithinDays(course.updated_at, selectedWindow.days) &&
        (selectedTag === "all" || course.tags.includes(selectedTag)) &&
        matchesQuery(course, normalizedQuery),
    );
  }, [courses, query, selectedTag, selectedWindow.days]);
  const totalStars = visibleCourses.reduce((sum, course) => sum + course.star_count, 0);
  const authorCount = new Set(visibleCourses.map((course) => course.owner_display_name)).size;

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            返回开放课堂
          </Link>
          <Link
            href="/profile?tab=stars"
            className="inline-flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-700"
          >
            <Star className="h-4 w-4" />
            我的 Stars
          </Link>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="h-fit rounded-xl border border-stone-200 bg-white p-4 lg:sticky lg:top-24">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Flame className="h-4 w-4 text-orange-600" />
            热门范围
          </div>
          <div className="mt-4 grid grid-cols-3 gap-1 rounded-lg bg-stone-100 p-1">
            {trendWindows.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTrendWindow(item.id)}
                aria-pressed={trendWindow === item.id}
                className={clsx(
                  "rounded-md px-2 py-2 text-xs font-semibold transition",
                  trendWindow === item.id ? "bg-white text-stone-950 shadow-sm" : "text-stone-500",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="mt-5 border-t border-stone-100 pt-4">
            <button
              type="button"
              onClick={() => setSelectedTag("all")}
              className={clsx(
                "flex w-full items-center justify-between rounded-md px-2 py-2 text-sm",
                selectedTag === "all" ? "bg-stone-950 text-white" : "text-stone-600 hover:bg-stone-50",
              )}
            >
              <span>全部主题</span>
              <span>{courses.length}</span>
            </button>
            <div className="mt-1 space-y-1">
              {tags.map(({ tag, count }) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setSelectedTag(tag)}
                  className={clsx(
                    "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm",
                    selectedTag === tag ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50",
                  )}
                >
                  <span className="truncate">{tag}</span>
                  <span className="text-xs text-stone-400">{count}</span>
                </button>
              ))}
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          <div className="rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_16px_36px_rgba(15,23,42,0.05)]">
            <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold text-orange-600">
                  <Flame className="h-4 w-4" />
                  真实公开课程
                </p>
                <h1 className="mt-2 text-3xl font-semibold tracking-tight">热门项目</h1>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-stone-600">
                  按真实 Stars 收藏数量与最近更新排序。详情进入公开项目页，下载会建立私有可编辑副本。
                </p>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <Metric label="项目" value={visibleCourses.length} Icon={BookOpen} />
                <Metric label="Stars" value={totalStars} Icon={Star} />
                <Metric label="作者" value={authorCount} Icon={UsersRound} />
              </div>
            </div>
            <div className="relative mt-6">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索课程、作者或主题"
                className="w-full rounded-full border border-stone-200 bg-white py-2.5 pl-9 pr-4 text-sm outline-none focus:border-stone-950"
              />
            </div>
          </div>

          {isLoading ? (
            <div className="mt-5 flex items-center justify-center gap-2 rounded-xl border border-stone-200 bg-white py-16 text-sm text-stone-500">
              <LoaderCircle className="h-5 w-5 animate-spin" />
              正在载入真实热门课程…
            </div>
          ) : error ? (
            <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">
              {error}
            </div>
          ) : visibleCourses.length ? (
            <div className="mt-5 space-y-3">
              {visibleCourses.map((course, index) => (
                <PublicCourseDiscoveryCard
                  key={`${course.kind}:${course.id}`}
                  course={course}
                  rank={index + 1}
                  badge={selectedWindow.label}
                />
              ))}
            </div>
          ) : (
            <div className="mt-5 rounded-xl border border-dashed border-stone-300 bg-white/70 px-5 py-12 text-center text-sm text-stone-500">
              当前范围内没有匹配的公开课程。
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function Metric({
  label,
  value,
  Icon,
}: {
  label: string;
  value: number;
  Icon: typeof BookOpen;
}) {
  return (
    <div className="min-w-20 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2">
      <p className="flex items-center gap-1 text-[11px] text-stone-500">
        <Icon className="h-3 w-3" />
        {label}
      </p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

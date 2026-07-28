"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  FolderClosed,
  Globe2,
  GraduationCap,
  LoaderCircle,
  LockKeyhole,
  Search,
  UserRound,
} from "lucide-react";

import {
  publicProjectHref,
  searchCourses,
  type CourseSearchResponse,
  type PublicCourseSearchResult,
} from "@/lib/project-visibility";

type CourseSearchResultsProps = {
  query: string;
  language: "en" | "zh-CN";
  onOpenOwnedCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
};

const EMPTY_RESULTS: CourseSearchResponse = {
  owned_courses: [],
  public_courses: [],
};

function formatUpdatedAt(value: string, language: CourseSearchResultsProps["language"]) {
  return new Intl.DateTimeFormat(language === "en" ? "en" : "zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

export function CourseSearchResults({
  query,
  language,
  onOpenOwnedCourse,
}: CourseSearchResultsProps) {
  const normalizedQuery = query.trim();

  if (!normalizedQuery) {
    return (
      <section className="rounded-[28px] border border-dashed border-stone-300 bg-white/75 px-6 py-16 text-center">
        <Search className="mx-auto h-7 w-7 text-stone-400" />
        <h2 className="mt-4 text-lg font-semibold text-stone-950">
          {language === "en" ? "Search courses" : "搜索课程"}
        </h2>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-stone-500">
          {language === "en"
            ? "Search all of your courses and public courses shared by other users."
            : "搜索你的全部课程，以及其他用户分享的公开课程。"}
        </p>
      </section>
    );
  }

  return (
    <CourseSearchMatches
      key={normalizedQuery}
      query={normalizedQuery}
      language={language}
      onOpenOwnedCourse={onOpenOwnedCourse}
    />
  );
}

function CourseSearchMatches({
  query,
  language,
  onOpenOwnedCourse,
}: CourseSearchResultsProps) {
  const normalizedQuery = query.trim();
  const [results, setResults] = useState<CourseSearchResponse>(EMPTY_RESULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [openingKey, setOpeningKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      setIsLoading(true);
      setError(null);
      void searchCourses(normalizedQuery, controller.signal)
        .then((payload) => setResults(payload))
        .catch((searchError: unknown) => {
          if (controller.signal.aborted) {
            return;
          }
          setResults(EMPTY_RESULTS);
          setError(
            searchError instanceof Error
              ? searchError.message
              : language === "en"
                ? "Course search is temporarily unavailable."
                : "暂时无法搜索课程。",
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setIsLoading(false);
          }
        });
    }, 220);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [language, normalizedQuery]);

  const totalResults = results.owned_courses.length + results.public_courses.length;

  async function openOwnedCourse(course: PublicCourseSearchResult) {
    const key = `${course.kind}:${course.id}`;
    setOpeningKey(key);
    try {
      await onOpenOwnedCourse(course);
    } finally {
      setOpeningKey(null);
    }
  }

  return (
    <section className="pb-20" aria-live="polite">
      <div className="mb-6 overflow-hidden rounded-[28px] border border-white/80 bg-[linear-gradient(135deg,rgba(255,255,255,0.98),rgba(245,250,255,0.92))] p-6 shadow-[0_20px_50px_rgba(15,23,42,0.06)]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">
              {language === "en" ? "Course search" : "课程搜索"}
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-stone-950">
              {isLoading
                ? language === "en"
                  ? "Searching…"
                  : "正在搜索…"
                : language === "en"
                  ? `${totalResults} result${totalResults === 1 ? "" : "s"}`
                  : `找到 ${totalResults} 个结果`}
            </h2>
            <p className="mt-2 text-sm text-stone-500">
              {language === "en" ? `For “${normalizedQuery}”` : `“${normalizedQuery}”的匹配结果`}
            </p>
          </div>
          {!isLoading && !error ? (
            <div className="flex flex-wrap gap-2 text-xs font-semibold">
              <span className="rounded-full bg-stone-950 px-3 py-1.5 text-white">
                {language === "en" ? "Mine" : "我的课程"} {results.owned_courses.length}
              </span>
              <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1.5 text-sky-700">
                {language === "en" ? "Public" : "公开课程"} {results.public_courses.length}
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="rounded-[22px] border border-rose-200 bg-rose-50 px-5 py-5 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      {isLoading ? (
        <div className="flex items-center justify-center gap-3 rounded-[22px] border border-stone-200 bg-white/80 py-14 text-sm text-stone-500">
          <LoaderCircle className="h-5 w-5 animate-spin" />
          {language === "en" ? "Searching your courses and public courses…" : "正在搜索你的课程和公开课程…"}
        </div>
      ) : null}

      {!isLoading && !error ? (
        <div className="space-y-8">
          <SearchResultGroup
            icon={UserRound}
            title={language === "en" ? "My courses" : "我的课程"}
            description={
              language === "en"
                ? "Only you can see your private courses in these results."
                : "这里包含你的私有与公开课程；私有内容仍然只有你能看到。"
            }
            emptyText={
              language === "en"
                ? "No matching course was found in your workspace."
                : "你的工作区中没有匹配的课程。"
            }
            courses={results.owned_courses}
            language={language}
            scope="owned"
            openingKey={openingKey}
            onOpenOwnedCourse={openOwnedCourse}
          />

          <SearchResultGroup
            icon={Globe2}
            title={language === "en" ? "Public courses" : "其他用户的公开课程"}
            description={
              language === "en"
                ? "Public course packages and standalone courses shared by other users."
                : "来自其他用户公开分享的课程包与单独课程。"
            }
            emptyText={
              language === "en"
                ? "No matching public course was found."
                : "暂时没有匹配的其他用户公开课程。"
            }
            courses={results.public_courses}
            language={language}
            scope="public"
            openingKey={openingKey}
            onOpenOwnedCourse={openOwnedCourse}
          />
        </div>
      ) : null}
    </section>
  );
}

type SearchResultGroupProps = {
  icon: typeof UserRound;
  title: string;
  description: string;
  emptyText: string;
  courses: PublicCourseSearchResult[];
  language: CourseSearchResultsProps["language"];
  scope: "owned" | "public";
  openingKey: string | null;
  onOpenOwnedCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
};

function SearchResultGroup({
  icon: GroupIcon,
  title,
  description,
  emptyText,
  courses,
  language,
  scope,
  openingKey,
  onOpenOwnedCourse,
}: SearchResultGroupProps) {
  return (
    <section>
      <div className="mb-3 flex items-start gap-3 px-1">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border border-stone-200 bg-white text-stone-600 shadow-sm">
          <GroupIcon className="h-4 w-4" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold text-stone-950">{title}</h3>
            <span className="rounded-full bg-stone-200/70 px-2 py-0.5 text-[11px] font-semibold text-stone-600">
              {courses.length}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-stone-500">{description}</p>
        </div>
      </div>

      {courses.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {courses.map((course) => (
            <CourseResultCard
              key={`${scope}:${course.kind}:${course.id}`}
              course={course}
              language={language}
              scope={scope}
              isOpening={openingKey === `${course.kind}:${course.id}`}
              onOpenOwnedCourse={onOpenOwnedCourse}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-[22px] border border-dashed border-stone-300 bg-white/65 px-5 py-8 text-center text-sm text-stone-500">
          {emptyText}
        </div>
      )}
    </section>
  );
}

type CourseResultCardProps = {
  course: PublicCourseSearchResult;
  language: CourseSearchResultsProps["language"];
  scope: "owned" | "public";
  isOpening: boolean;
  onOpenOwnedCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
};

function CourseResultCard({
  course,
  language,
  scope,
  isOpening,
  onOpenOwnedCourse,
}: CourseResultCardProps) {
  const ProjectIcon = course.kind === "package" ? FolderClosed : BookOpen;
  const kindLabel =
    language === "en"
      ? course.kind === "package"
        ? "Course package"
        : "Standalone course"
      : course.kind === "package"
        ? "课程包"
        : "单独课程";
  const visibilityLabel =
    course.visibility === "public"
      ? language === "en"
        ? "Public"
        : "公开"
      : language === "en"
        ? "Private"
        : "私有";
  const VisibilityIcon = course.visibility === "public" ? Globe2 : LockKeyhole;
  const titleClassName =
    "block break-words text-left text-lg font-semibold text-stone-950 transition hover:text-blue-600 hover:underline";

  return (
    <article className="group flex min-h-56 flex-col rounded-[24px] border border-stone-200 bg-white/92 p-5 shadow-[0_12px_28px_rgba(15,23,42,0.04)] transition hover:-translate-y-0.5 hover:border-stone-300 hover:shadow-[0_18px_38px_rgba(15,23,42,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-stone-200 bg-stone-100 text-stone-600">
            <ProjectIcon className="h-5 w-5" />
          </div>
          <div className="min-w-0 text-xs text-stone-500">
            <p className="truncate font-semibold text-stone-700">
              {scope === "owned" ? (language === "en" ? "My course" : "我的课程") : course.owner_display_name}
            </p>
            <p className="mt-0.5">{kindLabel}</p>
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[11px] font-semibold text-stone-600">
          <VisibilityIcon className="h-3 w-3" />
          {visibilityLabel}
        </span>
      </div>

      <div className="mt-4">
        {scope === "owned" ? (
          <button type="button" onClick={() => void onOpenOwnedCourse(course)} className={titleClassName}>
            {course.title}
          </button>
        ) : (
          <Link href={publicProjectHref(course.kind, course.id)} className={titleClassName}>
            {course.title}
          </Link>
        )}
        {course.summary ? (
          <p className="mt-2 line-clamp-3 text-sm leading-6 text-stone-600">{course.summary}</p>
        ) : null}
        {course.tags.length ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {course.tags.slice(0, 4).map((tag) => (
              <span
                key={`${course.kind}:${course.id}:${tag}`}
                className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700"
              >
                {tag}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="mt-auto flex items-end justify-between gap-3 pt-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500">
          <span className="inline-flex items-center gap-1.5">
            <GraduationCap className="h-3.5 w-3.5" />
            {language === "en"
              ? `${course.lesson_count} lesson${course.lesson_count === 1 ? "" : "s"}`
              : `${course.lesson_count} 节课程`}
          </span>
          {course.updated_at ? <span>{formatUpdatedAt(course.updated_at, language)}</span> : null}
        </div>
        {scope === "owned" ? (
          <button
            type="button"
            onClick={() => void onOpenOwnedCourse(course)}
            disabled={isOpening}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full bg-stone-950 px-4 py-2 text-xs font-semibold text-white transition hover:bg-stone-800 disabled:cursor-wait disabled:opacity-60"
          >
            {isOpening ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
            {language === "en" ? "Open" : "打开"}
            {!isOpening ? <ArrowUpRight className="h-3.5 w-3.5" /> : null}
          </button>
        ) : (
          <Link
            href={publicProjectHref(course.kind, course.id)}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full border border-stone-200 bg-stone-50 px-4 py-2 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            {language === "en" ? "View" : "查看"}
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        )}
      </div>
    </article>
  );
}

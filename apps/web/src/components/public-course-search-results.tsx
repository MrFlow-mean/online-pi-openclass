"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  FolderClosed,
  GraduationCap,
  LoaderCircle,
  Search,
} from "lucide-react";

import {
  publicProjectHref,
  searchPublicCourses,
  type PublicCourseSearchResult,
} from "@/lib/project-visibility";

type PublicCourseSearchResultsProps = {
  query: string;
  language: "en" | "zh-CN";
};

function formatUpdatedAt(value: string, language: PublicCourseSearchResultsProps["language"]) {
  return new Intl.DateTimeFormat(language === "en" ? "en" : "zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

export function PublicCourseSearchResults({
  query,
  language,
}: PublicCourseSearchResultsProps) {
  const normalizedQuery = query.trim();

  if (!normalizedQuery) {
    return (
      <section className="rounded-[28px] border border-dashed border-stone-300 bg-white/75 px-6 py-16 text-center">
        <Search className="mx-auto h-7 w-7 text-stone-400" />
        <h2 className="mt-4 text-lg font-semibold text-stone-950">
          {language === "en" ? "Search public courses" : "搜索其他用户的公开课程"}
        </h2>
        <p className="mt-2 text-sm text-stone-500">
          {language === "en"
            ? "Enter a title, author, topic, tag, or phrase from the course."
            : "输入课程标题、作者、主题、标签，或课程正文中的内容。"}
        </p>
      </section>
    );
  }

  return <PublicCourseSearchMatches key={normalizedQuery} query={normalizedQuery} language={language} />;
}

function PublicCourseSearchMatches({
  query,
  language,
}: PublicCourseSearchResultsProps) {
  const normalizedQuery = query.trim();
  const [results, setResults] = useState<PublicCourseSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      setIsLoading(true);
      setError(null);
      void searchPublicCourses(normalizedQuery, controller.signal)
        .then((payload) => setResults(payload))
        .catch((searchError: unknown) => {
          if (controller.signal.aborted) {
            return;
          }
          setResults([]);
          setError(
            searchError instanceof Error
              ? searchError.message
              : language === "en"
                ? "Public course search is temporarily unavailable."
                : "暂时无法搜索公开课程。",
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

  return (
    <section className="pb-20" aria-live="polite">
      <div className="mb-4 rounded-[22px] border border-stone-200 bg-white/88 px-5 py-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)] backdrop-blur">
        <h2 className="text-base font-semibold text-stone-950">
          {isLoading
            ? language === "en"
              ? "Searching public courses…"
              : "正在搜索公开课程…"
            : language === "en"
              ? `${results.length} public course results`
              : `找到 ${results.length} 个其他用户公开的课程`}
        </h2>
        <p className="mt-1 text-xs text-stone-500">
          {language === "en" ? `Search: “${normalizedQuery}”` : `搜索：“${normalizedQuery}”`}
        </p>
      </div>

      {error ? (
        <div className="rounded-[22px] border border-rose-200 bg-rose-50 px-5 py-5 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      {isLoading ? (
        <div className="flex items-center justify-center gap-3 rounded-[22px] border border-stone-200 bg-white/80 py-14 text-sm text-stone-500">
          <LoaderCircle className="h-5 w-5 animate-spin" />
          {language === "en" ? "Searching real public projects…" : "正在查询真实公开项目…"}
        </div>
      ) : null}

      {!isLoading && !error && results.length ? (
        <div className="space-y-3">
          {results.map((course) => {
            const ProjectIcon = course.kind === "package" ? FolderClosed : BookOpen;
            const kindLabel =
              language === "en"
                ? course.kind === "package"
                  ? "Course package"
                  : "Standalone course"
                : course.kind === "package"
                  ? "课程包"
                  : "单独课程";
            return (
              <article
                key={`${course.kind}:${course.id}`}
                className="rounded-[22px] border border-stone-200 bg-white/92 p-5 shadow-[0_12px_28px_rgba(15,23,42,0.04)] transition hover:border-stone-300 hover:bg-white"
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 gap-3">
                    <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-stone-200 bg-stone-100 text-stone-600">
                      <ProjectIcon className="h-5 w-5" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
                        <span className="font-semibold text-stone-700">{course.owner_display_name}</span>
                        <span>·</span>
                        <span>{kindLabel}</span>
                      </div>
                      <Link
                        href={publicProjectHref(course.kind, course.id)}
                        className="mt-1 block break-words text-lg font-semibold text-blue-600 hover:underline"
                      >
                        {course.title}
                      </Link>
                      {course.summary ? (
                        <p className="mt-2 line-clamp-3 text-sm leading-6 text-stone-700">{course.summary}</p>
                      ) : null}
                      {course.tags.length ? (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {course.tags.map((tag) => (
                            <span
                              key={`${course.kind}:${course.id}:${tag}`}
                              className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
                        <span className="inline-flex items-center gap-1.5">
                          <GraduationCap className="h-3.5 w-3.5" />
                          {language === "en"
                            ? `${course.lesson_count} lesson${course.lesson_count === 1 ? "" : "s"}`
                            : `${course.lesson_count} 节课程`}
                        </span>
                        {course.updated_at ? (
                          <span>
                            {language === "en" ? "Updated " : "更新于 "}
                            {formatUpdatedAt(course.updated_at, language)}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <Link
                    href={publicProjectHref(course.kind, course.id)}
                    className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full border border-stone-200 bg-stone-50 px-4 py-2 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
                  >
                    {language === "en" ? "Open" : "打开"}
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}

      {!isLoading && !error && !results.length ? (
        <div className="rounded-[22px] border border-dashed border-stone-300 bg-white/80 px-6 py-14 text-center text-sm text-stone-500">
          {language === "en"
            ? "No matching public courses were found. Try another title, author, topic, or phrase."
            : "没有找到匹配的公开课程。可以换一个标题、作者、主题或正文关键词。"}
        </div>
      ) : null}
    </section>
  );
}

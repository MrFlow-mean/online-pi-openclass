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
  Star,
  UserRound,
} from "lucide-react";

import { PublicCourseActions } from "@/components/public-course-actions";
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
  onDownloadPublicCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
  onTogglePublicCourseStar: (course: PublicCourseSearchResult, isStarred: boolean) => void | Promise<void>;
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
  onDownloadPublicCourse,
  onTogglePublicCourseStar,
}: CourseSearchResultsProps) {
  const normalizedQuery = query.trim();

  if (!normalizedQuery) {
    return (
      <section className="rounded-[28px] border border-dashed border-stone-300 bg-white/75 px-6 py-16 text-center">
        <Search className="mx-auto h-7 w-7 text-stone-400" />
        <h2 className="mt-4 text-lg font-semibold text-stone-950">
          {language === "en" ? "Search courses" : "Search courses"}
        </h2>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-stone-500">
          {language === "en"
            ? "Search all of your courses and public courses shared by other users."
            : "Search all your courses, as well as public courses shared by other users."}
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
      onDownloadPublicCourse={onDownloadPublicCourse}
      onTogglePublicCourseStar={onTogglePublicCourseStar}
    />
  );
}

function CourseSearchMatches({
  query,
  language,
  onOpenOwnedCourse,
  onDownloadPublicCourse,
  onTogglePublicCourseStar,
}: CourseSearchResultsProps) {
  const normalizedQuery = query.trim();
  const [results, setResults] = useState<CourseSearchResponse>(EMPTY_RESULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [openingKey, setOpeningKey] = useState<string | null>(null);
  const [starringKey, setStarringKey] = useState<string | null>(null);
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
                : "Unable to search for courses at the moment.",
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

  async function downloadPublicCourse(course: PublicCourseSearchResult) {
    const key = `${course.kind}:${course.id}`;
    setOpeningKey(key);
    setError(null);
    try {
      await onDownloadPublicCourse(course);
    } catch (downloadError) {
      setError(
        downloadError instanceof Error
          ? downloadError.message
          : language === "en"
            ? "The course could not be downloaded."
            : "This course is currently unavailable for download.",
      );
    } finally {
      setOpeningKey(null);
    }
  }

  async function togglePublicCourseStar(course: PublicCourseSearchResult) {
    const key = `${course.kind}:${course.id}`;
    const nextIsStarred = !course.is_starred;
    setStarringKey(key);
    setError(null);
    try {
      await onTogglePublicCourseStar(course, nextIsStarred);
      setResults((current) => ({
        ...current,
        public_courses: current.public_courses.map((item) =>
          item.kind === course.kind && item.id === course.id
            ? { ...item, is_starred: nextIsStarred }
            : item,
        ),
      }));
    } catch (starError) {
      setError(
        starError instanceof Error
          ? starError.message
          : language === "en"
            ? "The course could not be starred."
            : "This course cannot be saved at the moment.",
      );
    } finally {
      setStarringKey(null);
    }
  }

  return (
    <section className="pb-20" aria-live="polite">
      <div className="mb-6 overflow-hidden rounded-[28px] border border-white/80 bg-[linear-gradient(135deg,rgba(255,255,255,0.98),rgba(245,250,255,0.92))] p-6 shadow-[0_20px_50px_rgba(15,23,42,0.06)]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">
              {language === "en" ? "Course search" : "Course search"}
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-stone-950">
              {isLoading
                ? language === "en"
                  ? "Searching…"
                  : "Searching…"
                : language === "en"
                  ? `${totalResults} result${totalResults === 1 ? "" : "s"}`
                  : `${totalResults} results`}
            </h2>
            <p className="mt-2 text-sm text-stone-500">
              {language === "en" ? `For “${normalizedQuery}”` : `Matches for “${normalizedQuery}”`}
            </p>
          </div>
          {!isLoading && !error ? (
            <div className="flex flex-wrap gap-2 text-xs font-semibold">
              <span className="rounded-full bg-stone-950 px-3 py-1.5 text-white">
                {language === "en" ? "Mine" : "my courses"} {results.owned_courses.length}
              </span>
              <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1.5 text-sky-700">
                {language === "en" ? "Public" : "Open courses"} {results.public_courses.length}
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
          {language === "en" ? "Searching your courses and public courses…" : "Searching for your courses and public courses…"}
        </div>
      ) : null}

      {!isLoading && !error ? (
        <div className="space-y-8">
          <SearchResultGroup
            icon={UserRound}
            title={language === "en" ? "My courses" : "my courses"}
            description={
              language === "en"
                ? "Only you can see your private courses in these results."
                : "This contains your private and public courses; private content is still only visible to you."
            }
            emptyText={
              language === "en"
                ? "No matching course was found in your workspace."
                : "There is no matching class in your workspace."
            }
            courses={results.owned_courses}
            language={language}
            scope="owned"
            openingKey={openingKey}
            starringKey={starringKey}
            onOpenOwnedCourse={openOwnedCourse}
            onDownloadPublicCourse={downloadPublicCourse}
            onTogglePublicCourseStar={togglePublicCourseStar}
          />

          <SearchResultGroup
            icon={Globe2}
            title={language === "en" ? "Public courses" : "Other users’ public courses"}
            description={
              language === "en"
                ? "Public course packages and standalone courses shared by other users."
                : "Course packages and individual courses shared publicly from other users."
            }
            emptyText={
              language === "en"
                ? "No matching public course was found."
                : "There are currently no matching public courses for other users."
            }
            courses={results.public_courses}
            language={language}
            scope="public"
            openingKey={openingKey}
            starringKey={starringKey}
            onOpenOwnedCourse={openOwnedCourse}
            onDownloadPublicCourse={downloadPublicCourse}
            onTogglePublicCourseStar={togglePublicCourseStar}
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
  starringKey: string | null;
  onOpenOwnedCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
  onDownloadPublicCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
  onTogglePublicCourseStar: (course: PublicCourseSearchResult) => void | Promise<void>;
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
  starringKey,
  onOpenOwnedCourse,
  onDownloadPublicCourse,
  onTogglePublicCourseStar,
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
              isStarring={starringKey === `${course.kind}:${course.id}`}
              onOpenOwnedCourse={onOpenOwnedCourse}
              onDownloadPublicCourse={onDownloadPublicCourse}
              onTogglePublicCourseStar={onTogglePublicCourseStar}
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
  isStarring: boolean;
  onOpenOwnedCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
  onDownloadPublicCourse: (course: PublicCourseSearchResult) => void | Promise<void>;
  onTogglePublicCourseStar: (course: PublicCourseSearchResult) => void | Promise<void>;
};

function CourseResultCard({
  course,
  language,
  scope,
  isOpening,
  isStarring,
  onOpenOwnedCourse,
  onDownloadPublicCourse,
  onTogglePublicCourseStar,
}: CourseResultCardProps) {
  const ProjectIcon = course.kind === "package" ? FolderClosed : BookOpen;
  const kindLabel =
    language === "en"
      ? course.kind === "package"
        ? "Course package"
        : "Standalone course"
      : course.kind === "package"
        ? "course package"
        : "individual courses";
  const visibilityLabel =
    course.visibility === "public"
      ? language === "en"
        ? "Public"
        : "public"
      : language === "en"
        ? "Private"
        : "private";
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
              {scope === "owned" ? (language === "en" ? "My course" : "my courses") : course.owner_display_name}
            </p>
            <p className="mt-0.5">{kindLabel}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[11px] font-semibold text-stone-600">
            <VisibilityIcon className="h-3 w-3" />
            {visibilityLabel}
          </span>
          {scope === "public" ? (
            <button
              type="button"
              onClick={() => void onTogglePublicCourseStar(course)}
              disabled={isOpening || isStarring}
              aria-label={
                language === "en"
                  ? `${course.is_starred ? "Unstar" : "Star"} ${course.title}`
                  : `${course.is_starred ? "Cancel favorites" : "collect"} ${course.title}`
              }
              aria-pressed={course.is_starred}
              className={`inline-flex h-8 w-8 items-center justify-center rounded-full border transition disabled:cursor-wait disabled:opacity-60 ${
                course.is_starred
                  ? "border-amber-200 bg-amber-50 text-amber-700"
                  : "border-stone-200 bg-white text-stone-500 hover:text-stone-950"
              }`}
            >
              {isStarring ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Star className={`h-3.5 w-3.5 ${course.is_starred ? "fill-current" : ""}`} />
              )}
            </button>
          ) : null}
        </div>
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
              : `${course.lesson_count} courses`}
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
            {language === "en" ? "Open" : "Open"}
            {!isOpening ? <ArrowUpRight className="h-3.5 w-3.5" /> : null}
          </button>
        ) : (
          <PublicCourseActions
            course={course}
            language={language}
            disabled={isOpening || isStarring}
            onDownload={() => onDownloadPublicCourse(course)}
          />
        )}
      </div>
    </article>
  );
}

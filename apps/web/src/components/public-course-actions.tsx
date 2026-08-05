"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Download, Info, LoaderCircle } from "lucide-react";

import {
  forkPublicLesson,
  forkPublicPackage,
  ProjectVisibilityRequestError,
  publicProjectHref,
  type PublicCourseSearchResult,
} from "@/lib/project-visibility";

type PublicCourseActionsProps = {
  course: PublicCourseSearchResult;
  language?: "en" | "zh-CN";
  compact?: boolean;
  disabled?: boolean;
  onDownload?: () => void | Promise<void>;
};

export function PublicCourseActions({
  course,
  language = "en",
  compact = false,
  disabled = false,
  onDownload,
}: PublicCourseActionsProps) {
  const router = useRouter();
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detailHref = publicProjectHref(course.kind, course.id);
  const detailsLabel = language === "en" ? "Details" : "Details";
  const downloadLabel = language === "en" ? "Download" : "download";

  async function downloadCourse() {
    setIsDownloading(true);
    setError(null);
    try {
      if (onDownload) {
        await onDownload();
        return;
      }
      const personalProject =
        course.kind === "lesson"
          ? await forkPublicLesson(course.id)
          : await forkPublicPackage(course.id);
      const lessonId = personalProject.active_lesson_id;
      if (!lessonId) {
        throw new Error(
          language === "en"
            ? "The downloaded project did not contain an editable course."
            : "There are no editable courses in the downloaded project.",
        );
      }
      router.push(`/studio?lesson=${encodeURIComponent(lessonId)}`);
    } catch (downloadError) {
      if (downloadError instanceof ProjectVisibilityRequestError && downloadError.status === 401) {
        const next =
          typeof window === "undefined"
            ? detailHref
            : `${window.location.pathname}${window.location.search}`;
        router.push(`/login?next=${encodeURIComponent(next)}`);
        return;
      }
      setError(
        downloadError instanceof Error
          ? downloadError.message
          : language === "en"
            ? "The course could not be downloaded."
            : "This course is currently unavailable for download.",
      );
    } finally {
      setIsDownloading(false);
    }
  }

  const buttonClassName = compact
    ? "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition"
    : "inline-flex items-center justify-center gap-1.5 rounded-full px-4 py-2 text-xs font-semibold transition";

  return (
    <div className="shrink-0">
      <div className="flex items-center justify-end gap-2">
        <Link
          href={detailHref}
          aria-label={`${detailsLabel} ${course.title}`}
          className={`${buttonClassName} border border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:text-stone-950`}
        >
          <Info className="h-3.5 w-3.5" />
          {detailsLabel}
        </Link>
        <button
          type="button"
          onClick={() => void downloadCourse()}
          disabled={disabled || isDownloading}
          aria-label={`${downloadLabel} ${course.title}`}
          className={`${buttonClassName} bg-blue-600 text-white hover:bg-blue-500 disabled:cursor-wait disabled:opacity-60`}
        >
          {isDownloading ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          {isDownloading
            ? language === "en"
              ? "Downloading…"
              : "Downloading…"
            : downloadLabel}
        </button>
      </div>
      {error ? (
        <p className="mt-2 max-w-64 text-right text-xs leading-5 text-rose-600" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

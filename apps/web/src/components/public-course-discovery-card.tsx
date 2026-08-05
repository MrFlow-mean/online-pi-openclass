"use client";

import Image from "next/image";
import { BookOpen, FolderClosed, GraduationCap, Star } from "lucide-react";

import { PublicCourseActions } from "@/components/public-course-actions";
import type { PublicCourseSearchResult } from "@/lib/project-visibility";

type PublicCourseDiscoveryCardProps = {
  course: PublicCourseSearchResult;
  rank?: number;
  badge?: string;
};

function formatUpdatedAt(value?: string | null) {
  if (!value) {
    return "Just updated";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "numeric",
    day: "numeric",
  }).format(new Date(value));
}

export function PublicCourseDiscoveryCard({
  course,
  rank,
  badge,
}: PublicCourseDiscoveryCardProps) {
  const ProjectIcon = course.kind === "package" ? FolderClosed : BookOpen;
  const kindLabel = course.kind === "package" ? "course package" : "individual courses";

  return (
    <article className="rounded-xl border border-stone-200 bg-white p-5 shadow-[0_12px_30px_rgba(15,23,42,0.04)] transition hover:border-stone-300 hover:shadow-[0_18px_36px_rgba(15,23,42,0.07)]">
      <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 gap-3">
          {typeof rank === "number" ? (
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-sm font-semibold text-orange-700">
              #{rank}
            </span>
          ) : course.owner_avatar_url ? (
            <Image
              src={course.owner_avatar_url}
              alt=""
              width={40}
              height={40}
              unoptimized
              className="h-10 w-10 shrink-0 rounded-lg border border-stone-200 bg-stone-100 object-cover"
            />
          ) : (
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-stone-200 bg-stone-100 text-stone-600">
              <ProjectIcon className="h-5 w-5" />
            </span>
          )}

          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
              <span className="font-semibold text-stone-700">{course.owner_display_name}</span>
              <span>·</span>
              <span>{kindLabel}</span>
              {badge ? (
                <span className="rounded-full bg-sky-50 px-2 py-0.5 font-semibold text-sky-700">
                  {badge}
                </span>
              ) : null}
            </div>
            <h2 className="mt-2 break-words text-lg font-semibold text-stone-950">{course.title}</h2>
            {course.summary ? (
              <p className="mt-2 line-clamp-3 text-sm leading-6 text-stone-600">{course.summary}</p>
            ) : null}
            {course.tags.length ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {course.tags.slice(0, 5).map((tag) => (
                  <span
                    key={`${course.kind}:${course.id}:${tag}`}
                    className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1.5">
                <GraduationCap className="h-3.5 w-3.5" />
                {course.lesson_count}  lessons
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Star className="h-3.5 w-3.5" />
                {course.star_count} Stars
              </span>
              <span>updated on {formatUpdatedAt(course.updated_at)}</span>
            </div>
          </div>
        </div>

        <PublicCourseActions course={course} compact />
      </div>
    </article>
  );
}

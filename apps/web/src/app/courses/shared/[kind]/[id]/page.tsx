"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, Globe2, LoaderCircle } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { CommunityMarkdown } from "@/components/community/community-markdown";
import {
  getPublicLesson,
  getPublicPackage,
  type PublicCoursePackage,
  type PublicLesson,
} from "@/lib/project-visibility";

type PublicProject =
  | { kind: "lesson"; lesson: PublicLesson }
  | { kind: "package"; coursePackage: PublicCoursePackage };

function PublicLessonArticle({ lesson }: { lesson: PublicLesson }) {
  return (
    <article className="rounded-[28px] border border-stone-200 bg-white p-6 shadow-[0_18px_50px_rgba(15,23,42,0.06)] sm:p-8">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-stone-100 text-stone-600">
          <BookOpen className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h2 className="break-words text-2xl font-semibold tracking-tight text-stone-950">{lesson.title}</h2>
          {lesson.summary ? <p className="mt-2 text-sm leading-6 text-stone-500">{lesson.summary}</p> : null}
        </div>
      </div>
      <div className="mt-7 border-t border-stone-100 pt-2">
        {lesson.board_document.content_text.trim() ? (
          <CommunityMarkdown content={lesson.board_document.content_text} />
        ) : (
          <p className="py-8 text-sm text-stone-400">这节课程暂时还没有公开内容。</p>
        )}
      </div>
    </article>
  );
}

export default function SharedCoursePage() {
  const params = useParams<{ kind: string; id: string }>();
  const [project, setProject] = useState<PublicProject | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        if (params.kind === "lesson") {
          const lesson = await getPublicLesson(params.id);
          if (active) setProject({ kind: "lesson", lesson });
          return;
        }
        if (params.kind === "package") {
          const coursePackage = await getPublicPackage(params.id);
          if (active) setProject({ kind: "package", coursePackage });
          return;
        }
        throw new Error("Unknown public project type");
      } catch {
        if (active) setError("这个项目不存在，或者所有者已将它设为 private。");
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [params.id, params.kind]);

  const title =
    project?.kind === "lesson"
      ? project.lesson.title
      : project?.kind === "package"
        ? project.coursePackage.title
        : "公开课程";
  const summary = project?.kind === "package" ? project.coursePackage.summary : "";

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950">
            <ArrowLeft className="h-4 w-4" />
            返回开放课堂
          </Link>
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700">
            <Globe2 className="h-3.5 w-3.5" />
            Public · 只读
          </div>
        </header>

        <section className="mb-8 flex items-start gap-4">
          <BrandMark alt="" className="h-14 w-14 rounded-2xl border border-stone-200 bg-white shadow-sm" size={112} priority />
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-stone-400">OpenClass</p>
            <h1 className="mt-2 break-words text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>
            {summary ? <p className="mt-3 max-w-3xl text-base leading-7 text-stone-600">{summary}</p> : null}
          </div>
        </section>

        {!project && !error ? (
          <div className="flex items-center justify-center gap-3 rounded-[28px] border border-stone-200 bg-white py-16 text-sm text-stone-500">
            <LoaderCircle className="h-5 w-5 animate-spin" />
            正在载入公开课程…
          </div>
        ) : null}

        {error ? (
          <div className="rounded-[28px] border border-amber-200 bg-amber-50 px-6 py-12 text-center text-sm text-amber-800">
            {error}
          </div>
        ) : null}

        {project?.kind === "lesson" ? <PublicLessonArticle lesson={project.lesson} /> : null}
        {project?.kind === "package" ? (
          <div className="space-y-5">
            {project.coursePackage.lessons.length ? (
              project.coursePackage.lessons.map((lesson) => <PublicLessonArticle key={lesson.id} lesson={lesson} />)
            ) : (
              <div className="rounded-[28px] border border-dashed border-stone-300 bg-white/70 px-6 py-14 text-center text-sm text-stone-500">
                这个公开课程包目前还没有课程。
              </div>
            )}
          </div>
        ) : null}
      </div>
    </main>
  );
}

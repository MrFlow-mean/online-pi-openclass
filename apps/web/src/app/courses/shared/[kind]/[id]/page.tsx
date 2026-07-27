"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, GitFork, Globe2, LoaderCircle } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { CommunityMarkdown } from "@/components/community/community-markdown";
import { readEffectiveAuthToken } from "@/lib/api";
import {
  forkPublicLesson,
  getPublicLesson,
  getPublicPackage,
  ProjectVisibilityRequestError,
  type PublicCoursePackage,
  type PublicLesson,
} from "@/lib/project-visibility";

type PublicProject =
  | { kind: "lesson"; lesson: PublicLesson }
  | { kind: "package"; coursePackage: PublicCoursePackage };

function PublicLessonArticle({
  lesson,
  isRetaining,
  onRetain,
}: {
  lesson: PublicLesson;
  isRetaining: boolean;
  onRetain: (lesson: PublicLesson) => void;
}) {
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
      <div className="mt-8 flex flex-col gap-4 border-t border-stone-100 pt-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-2xl text-sm leading-6 text-stone-500">
          完成阅读后，可保留为你的单独课程。后续提问和修改只进入个人版本，并保留完整历史，可随时回滚。
        </p>
        <button
          type="button"
          onClick={() => onRetain(lesson)}
          disabled={isRetaining}
          className="inline-flex shrink-0 items-center justify-center gap-2 rounded-full bg-stone-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-stone-800 disabled:cursor-wait disabled:opacity-60"
        >
          {isRetaining ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <GitFork className="h-4 w-4" />}
          {isRetaining ? "正在保留…" : "保留到单独课程并继续"}
        </button>
      </div>
    </article>
  );
}

export default function SharedCoursePage() {
  const params = useParams<{ kind: string; id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const historyNodeId = searchParams.get("history_node")?.trim() ?? "";
  const [project, setProject] = useState<PublicProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retainError, setRetainError] = useState<string | null>(null);
  const [retainingLessonId, setRetainingLessonId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        if (params.kind === "lesson") {
          const lesson = await getPublicLesson(params.id, historyNodeId);
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
  }, [historyNodeId, params.id, params.kind]);

  async function handleRetainLesson(lesson: PublicLesson) {
    if (!readEffectiveAuthToken()) {
      const next = `${window.location.pathname}${window.location.search}`;
      router.push(`/login?next=${encodeURIComponent(next)}`);
      return;
    }

    setRetainError(null);
    setRetainingLessonId(lesson.id);
    try {
      const retained = await forkPublicLesson(
        lesson.id,
        params.kind === "lesson" ? historyNodeId : undefined,
      );
      const retainedLessonId = retained.active_lesson_id ?? lesson.id;
      router.push(`/studio?lesson=${encodeURIComponent(retainedLessonId)}`);
    } catch (retainFailure) {
      if (retainFailure instanceof ProjectVisibilityRequestError && retainFailure.status === 401) {
        const next = `${window.location.pathname}${window.location.search}`;
        router.push(`/login?next=${encodeURIComponent(next)}`);
        return;
      }
      setRetainError(retainFailure instanceof Error ? retainFailure.message : "暂时无法保留这节课程");
      setRetainingLessonId(null);
    }
  }

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

        {project?.kind === "lesson" && historyNodeId ? (
          <div className="mb-5 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm font-semibold text-sky-800">
            当前展示的是该课程被引用的历史节点。
          </div>
        ) : null}

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

        {retainError ? (
          <div className="mb-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-800">
            {retainError}
          </div>
        ) : null}

        {project?.kind === "lesson" ? (
          <PublicLessonArticle
            lesson={project.lesson}
            isRetaining={retainingLessonId === project.lesson.id}
            onRetain={(lesson) => void handleRetainLesson(lesson)}
          />
        ) : null}
        {project?.kind === "package" ? (
          <div className="space-y-5">
            {project.coursePackage.lessons.length ? (
              project.coursePackage.lessons.map((lesson) => (
                <PublicLessonArticle
                  key={lesson.id}
                  lesson={lesson}
                  isRetaining={retainingLessonId === lesson.id}
                  onRetain={(selectedLesson) => void handleRetainLesson(selectedLesson)}
                />
              ))
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

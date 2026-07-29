"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, LoaderCircle, Save, ShieldCheck } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { WordBoardEditor } from "@/components/course-studio/word-board-editor";
import { projectCollaborationApi } from "@/lib/project-collaboration-api";
import type { BoardDocument, Lesson } from "@/types";
import type { ProjectGovernance, ProjectKind } from "@/types/project-collaboration";

function currentHeadCommitId(lesson: Lesson) {
  const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
  return branch?.head_commit_id ?? null;
}

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function CollaborativeLessonEditor({
  projectKind,
  projectId,
  lessonId,
}: {
  projectKind: ProjectKind;
  projectId: string;
  lessonId: string;
}) {
  const [governance, setGovernance] = useState<ProjectGovernance | null>(null);
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [document, setDocument] = useState<BoardDocument | null>(null);
  const [structureRemovalIntent, setStructureRemovalIntent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextGovernance, nextLesson] = await Promise.all([
        projectCollaborationApi.governance(projectKind, projectId),
        projectCollaborationApi.getLesson(projectKind, projectId, lessonId),
      ]);
      setGovernance(nextGovernance);
      setLesson(nextLesson);
      setDocument(nextLesson.board_document);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "协作课程载入失败");
    } finally {
      setLoading(false);
    }
  }, [lessonId, projectId, projectKind]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const protectedForEditor = Boolean(
    governance?.policy.protect_default_branch && governance.viewer_role === "editor"
  );
  const readOnly = !governance?.capabilities.edit_project || protectedForEditor;
  const dirty = useMemo(
    () => Boolean(lesson && document && JSON.stringify(lesson.board_document) !== JSON.stringify(document)),
    [document, lesson]
  );

  async function save() {
    if (!lesson || !document || readOnly) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const coursePackage = await projectCollaborationApi.saveLessonDocument(
        projectKind,
        projectId,
        lessonId,
        {
          document,
          label: "Collaborative edit",
          message: "Saved changes from the project collaboration editor",
          base_commit_id: currentHeadCommitId(lesson),
          metadata: {
            source: "project_collaboration_editor",
            ...(structureRemovalIntent ? { structure_removal_intent: true } : {}),
          },
        }
      );
      const nextLesson = coursePackage.lessons.find((item) => item.id === lessonId);
      if (!nextLesson) throw new Error("保存后未找到协作课程。");
      setLesson(nextLesson);
      setDocument(nextLesson.board_document);
      setStructureRemovalIntent(false);
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "协作内容保存失败");
    } finally {
      setSaving(false);
    }
  }

  const backHref = "/profile?tab=repositories";

  if (loading) {
    return <main className="flex min-h-screen items-center justify-center gap-2 bg-[#f7f5ef] text-sm text-stone-500"><LoaderCircle className="h-5 w-5 animate-spin" />正在载入协作编辑器…</main>;
  }
  if (!lesson || !document || !governance) {
    return <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] p-6"><div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-800">{error ?? "无法打开协作课程。"}</div></main>;
  }

  return (
    <main className="flex h-screen min-h-0 flex-col bg-[#f7f5ef] text-stone-950">
      <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-stone-200 bg-white px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link href={backHref} className="rounded-full p-2 text-stone-500 hover:bg-stone-100"><ArrowLeft className="h-4 w-4" /></Link>
          <BrandMark alt="" className="h-8 w-8 rounded-lg" size={64} />
          <div className="min-w-0"><h1 className="truncate text-sm font-semibold">{lesson.title}</h1><p className="text-xs text-stone-400">{governance.title} · {governance.viewer_role}</p></div>
        </div>
        <button type="button" disabled={saving || readOnly || !dirty} onClick={() => void save()} className="inline-flex items-center gap-2 rounded-full bg-stone-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : saved && !dirty ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}{saving ? "保存中" : saved && !dirty ? "已保存" : "保存到项目"}</button>
      </header>
      {protectedForEditor ? <div className="flex shrink-0 items-center gap-2 border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-800"><ShieldCheck className="h-4 w-4" />默认分支受保护；编辑者需要通过改进方案和审查合并。</div> : null}
      {error ? <div className="shrink-0 border-b border-rose-200 bg-rose-50 px-5 py-3 text-sm text-rose-800">{error}</div> : null}
      <div className="min-h-0 flex-1 overflow-hidden bg-white">
        <WordBoardEditor
          document={document}
          readOnly={readOnly}
          toolbarCollapsed={false}
          onDocumentChange={(nextDocument) => { setDocument(nextDocument); setSaved(false); }}
          onStructureRemovalIntent={() => setStructureRemovalIntent(true)}
          onSelectionChange={() => undefined}
          onImportDocx={() => setError("协作页暂不支持导入 DOCX，请在项目课程内继续编辑。")}
          onExportDocx={() => setError("协作页暂不支持导出 DOCX。")}
          onExportHtml={() => download(`${lesson.slug || lesson.id}.html`, document.content_html, "text/html;charset=utf-8")}
          onImportRidoc={() => setError("协作页暂不支持导入 RIDOC。")}
          onExportRidoc={() => download(`${lesson.slug || lesson.id}.ridoc`, JSON.stringify(document, null, 2), "application/json")}
        />
      </div>
    </main>
  );
}

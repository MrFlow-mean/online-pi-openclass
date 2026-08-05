"use client";

import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";

import { api } from "@/lib/api";
import {
  AUTO_SAVE_DELAY_MS,
  currentHeadCommitId,
  documentsEqual,
} from "@/components/course-studio/history-utils";
import type { AppliedCoursePackage, CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type { BoardDocument, CoursePackage, DocumentSaveDelta, Lesson } from "@/types";

export type AutoSaveStatus = "idle" | "pending" | "saving" | "saved" | "error";
export type AutoSaveReason =
  | "debounce"
  | "queued"
  | "manual"
  | "return-home"
  | "select-lesson"
  | "open-lesson"
  | "close-lesson"
  | "create-lesson"
  | "chat"
  | "branch"
  | "preview"
  | "switch-branch"
  | "restore"
  | "import"
  | "export"
  | "voice"
  | "pagehide";

type AutoSavedPackageResult = {
  coursePackage: CoursePackage;
  savedLesson: Lesson | null;
};

type UseBoardDraftOptions = {
  activeLesson: Lesson | null;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  applyCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => AppliedCoursePackage;
  applyAutoSavedCoursePackage: (
    delta: DocumentSaveDelta,
    lessonId: string,
    currentActiveLessonId: string | null
  ) => AutoSavedPackageResult;
  onPackageApplied?: () => void;
};

function buildDocumentSavePayload(
  document: BoardDocument,
  reason: AutoSaveReason,
  baseCommitId: string | null,
  allowStructureRemoval: boolean
) {
  if (reason === "manual") {
    return {
      document,
      label: "Manual document edit",
      message: "Saved Word-like rich document changes from the editor",
      base_commit_id: baseCommitId,
      metadata: {
        kind: "manual_document_save",
        ...(allowStructureRemoval ? { structure_removal_intent: true } : {}),
      },
    };
  }
  return {
    document,
    label: "Auto Save",
    message: "Auto-saved Word-like rich document changes from the editor",
    base_commit_id: baseCommitId,
    metadata: {
      kind: "auto_document_save",
      autosave: true,
      autosave_reason: reason,
      source: "word_board_editor",
      ...(allowStructureRemoval ? { structure_removal_intent: true } : {}),
    },
  };
}

function plainTextFromHtml(value: string) {
  return value
    .replace(/<\/(h[1-6]|p|li|blockquote|tr)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function visibleDocumentText(document: BoardDocument) {
  return plainTextFromHtml(document.content_html) || document.content_text.replace(/\s+/g, " ").trim();
}

function looksLikeSameRenderedDocument(left: BoardDocument, right: BoardDocument) {
  return left.id === right.id && visibleDocumentText(left) === visibleDocumentText(right);
}

function richStructureCounts(document: BoardDocument) {
  const counts = {
    heading: 0,
    bold: 0,
    italic: 0,
    bulletList: 0,
    orderedList: 0,
    listItem: 0,
    table: 0,
    resourceVisualBlock: 0,
    blockquote: 0,
    paragraph: 0,
  };

  function walk(value: unknown) {
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (!value || typeof value !== "object") {
      return;
    }
    const node = value as { type?: unknown; marks?: unknown };
    if (typeof node.type === "string" && node.type in counts) {
      counts[node.type as keyof typeof counts] += 1;
    }
    if (Array.isArray(node.marks)) {
      for (const mark of node.marks) {
        if (!mark || typeof mark !== "object") {
          continue;
        }
        const markType = (mark as { type?: unknown }).type;
        if (typeof markType === "string" && markType in counts) {
          counts[markType as keyof typeof counts] += 1;
        }
      }
    }
    Object.values(value).forEach(walk);
  }

  walk(document.content_json);
  return counts;
}

function richStructureScore(counts: ReturnType<typeof richStructureCounts>) {
  return (
    counts.heading * 3 +
    counts.table * 4 +
    counts.bulletList * 2 +
    counts.orderedList * 2 +
    counts.listItem +
    counts.blockquote * 2 +
    counts.bold +
    counts.italic
  );
}

function isStructuredDocument(document: BoardDocument) {
  return richStructureScore(richStructureCounts(document)) >= 8;
}

function wouldFlattenRenderedDocument(
  currentDocument: BoardDocument,
  nextDocument: BoardDocument,
  allowStructureRemoval = false
) {
  const currentCounts = richStructureCounts(currentDocument);
  const nextCounts = richStructureCounts(nextDocument);
  const currentScore = richStructureScore(currentCounts);
  const headingHierarchyLost = currentCounts.heading >= 2 && nextCounts.heading === 0;
  const tableStructureLost = !allowStructureRemoval && currentCounts.table > 0 && nextCounts.table === 0;
  const resourceVisualLost =
    !allowStructureRemoval && currentCounts.resourceVisualBlock > nextCounts.resourceVisualBlock;
  if (resourceVisualLost) {
    return true;
  }
  if (currentScore < 8 && !headingHierarchyLost && !tableStructureLost) {
    return false;
  }
  if (currentCounts.heading + currentCounts.table <= 0) {
    return false;
  }
  if (headingHierarchyLost || tableStructureLost) {
    return true;
  }
  if (nextCounts.heading || nextCounts.table) {
    return false;
  }
  const nextScore = richStructureScore(nextCounts);
  return (
    nextScore <= Math.max(2, Math.floor(currentScore * 0.5)) &&
    nextCounts.paragraph >= Math.max(8, Math.floor(currentCounts.paragraph / 2))
  );
}

export function useBoardDraft({
  activeLesson,
  setError,
  setBusyAction,
  applyCoursePackage,
  applyAutoSavedCoursePackage,
  onPackageApplied,
}: UseBoardDraftOptions) {
  const autoSaveTimerRef = useRef<number | null>(null);
  const autoSaveInFlightRef = useRef<Promise<boolean> | null>(null);
  const autoSaveQueuedRef = useRef(false);
  const scheduleAutoSaveRef = useRef<(reason?: AutoSaveReason) => void>(() => undefined);
  const flushAutoSaveRef = useRef<(reason: AutoSaveReason) => Promise<boolean>>(async () => true);
  const documentDraftVersionRef = useRef(0);
  const activeLessonRef = useRef<Lesson | null>(null);
  const draftDocumentRef = useRef<BoardDocument | null>(null);
  const isDocumentDirtyRef = useRef(false);
  const isPreviewingRef = useRef(false);
  const ignoredStreamingPreviewRef = useRef<BoardDocument | null>(null);
  const lastStructuredDocumentRef = useRef<BoardDocument | null>(null);
  const structureRemovalIntentRef = useRef(false);

  const [draftDocument, setDraftDocument] = useState<BoardDocument | null>(null);
  const [isDocumentDirty, setIsDocumentDirty] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [, setAutoSaveStatus] = useState<AutoSaveStatus>("idle");

  const displayedDocument = useMemo(
    () => draftDocument ?? activeLesson?.board_document ?? null,
    [activeLesson?.board_document, draftDocument]
  );

  const clearAutoSaveTimer = useCallback(() => {
    if (autoSaveTimerRef.current === null) {
      return;
    }
    window.clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = null;
  }, []);

  const resetToLesson = useCallback(
    (lesson: Lesson | null) => {
      const nextDocument = lesson?.board_document ?? null;
      clearAutoSaveTimer();
      autoSaveQueuedRef.current = false;
      documentDraftVersionRef.current += 1;
      activeLessonRef.current = lesson;
      draftDocumentRef.current = nextDocument;
      lastStructuredDocumentRef.current = nextDocument && isStructuredDocument(nextDocument) ? nextDocument : null;
      structureRemovalIntentRef.current = false;
      isDocumentDirtyRef.current = false;
      isPreviewingRef.current = false;
      if (!nextDocument || ignoredStreamingPreviewRef.current?.id !== nextDocument.id) {
        ignoredStreamingPreviewRef.current = null;
      }
      setDraftDocument(nextDocument);
      setIsDocumentDirty(false);
      setIsPreviewing(false);
      setAutoSaveStatus("idle");
    },
    [clearAutoSaveTimer]
  );

  const setPreviewDocument = useCallback(
    (document: BoardDocument) => {
      clearAutoSaveTimer();
      documentDraftVersionRef.current += 1;
      draftDocumentRef.current = document;
      isDocumentDirtyRef.current = false;
      isPreviewingRef.current = true;
      ignoredStreamingPreviewRef.current = null;
      structureRemovalIntentRef.current = false;
      setDraftDocument(document);
      setIsDocumentDirty(false);
      setIsPreviewing(true);
      setAutoSaveStatus("idle");
    },
    [clearAutoSaveTimer]
  );

  const setStreamingDocumentPreview = useCallback(
    (lessonId: string, document: BoardDocument) => {
      if (
        activeLessonRef.current?.id !== lessonId ||
        activeLessonRef.current.board_document.id !== document.id
      ) {
        return false;
      }
      const currentDocument = draftDocumentRef.current;
      const sameVisiblePreview =
        isPreviewingRef.current &&
        currentDocument?.id === document.id &&
        visibleDocumentText(currentDocument) === visibleDocumentText(document);
      if (sameVisiblePreview) {
        return true;
      }
      clearAutoSaveTimer();
      documentDraftVersionRef.current += 1;
      draftDocumentRef.current = document;
      isDocumentDirtyRef.current = false;
      isPreviewingRef.current = true;
      ignoredStreamingPreviewRef.current = document;
      setDraftDocument((current) => {
        if (
          current?.id === document.id &&
          visibleDocumentText(current) === visibleDocumentText(document)
        ) {
          return current;
        }
        return document;
      });
      setIsDocumentDirty(false);
      setIsPreviewing(true);
      setAutoSaveStatus("idle");
      return true;
    },
    [clearAutoSaveTimer]
  );

  const applyAutoSavedPackage = useCallback(
    (delta: DocumentSaveDelta, lessonId: string, savedVersion: number) => {
      const currentActiveLessonId = activeLessonRef.current?.id ?? null;
      const { savedLesson } = applyAutoSavedCoursePackage(delta, lessonId, currentActiveLessonId);

      if (currentActiveLessonId !== lessonId || !savedLesson) {
        setError(null);
        return;
      }

      if (documentDraftVersionRef.current === savedVersion) {
        setDraftDocument(savedLesson.board_document);
        draftDocumentRef.current = savedLesson.board_document;
        if (isStructuredDocument(savedLesson.board_document)) {
          lastStructuredDocumentRef.current = savedLesson.board_document;
        }
        setIsDocumentDirty(false);
        isDocumentDirtyRef.current = false;
        structureRemovalIntentRef.current = false;
        setAutoSaveStatus("saved");
        setError(null);
        return;
      }

      const latestDraft = draftDocumentRef.current;
      if (isStructuredDocument(savedLesson.board_document)) {
        lastStructuredDocumentRef.current = savedLesson.board_document;
      }
      const stillDirty = Boolean(latestDraft && !documentsEqual(latestDraft, savedLesson.board_document));
      setIsDocumentDirty(stillDirty);
      isDocumentDirtyRef.current = stillDirty;
      setAutoSaveStatus(stillDirty ? "pending" : "saved");
      setError(null);
    },
    [applyAutoSavedCoursePackage, setError]
  );

  const flushAutoSave = useCallback(
    async (reason: AutoSaveReason): Promise<boolean> => {
      clearAutoSaveTimer();
      if (autoSaveInFlightRef.current) {
        autoSaveQueuedRef.current = true;
        await autoSaveInFlightRef.current;
        if (!isDocumentDirtyRef.current) {
          return true;
        }
        return flushAutoSaveRef.current(reason);
      }

      const lesson = activeLessonRef.current;
      const document = draftDocumentRef.current;
      if (!lesson || !document || !isDocumentDirtyRef.current || isPreviewingRef.current) {
        return true;
      }
      if (document.id !== lesson.board_document.id) {
        draftDocumentRef.current = lesson.board_document;
        isDocumentDirtyRef.current = false;
        setDraftDocument(lesson.board_document);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return true;
      }
      const structuredGuard = lastStructuredDocumentRef.current;
      const allowStructureRemoval = structureRemovalIntentRef.current;
      if (
        structuredGuard &&
        structuredGuard.id === document.id &&
        wouldFlattenRenderedDocument(structuredGuard, document, allowStructureRemoval)
      ) {
        draftDocumentRef.current = structuredGuard;
        isDocumentDirtyRef.current = false;
        setDraftDocument(structuredGuard);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return true;
      }
      if (documentsEqual(document, lesson.board_document)) {
        setIsDocumentDirty(false);
        isDocumentDirtyRef.current = false;
        ignoredStreamingPreviewRef.current = null;
        setAutoSaveStatus("idle");
        return true;
      }

      const savedVersion = documentDraftVersionRef.current;
      const isManualSave = reason === "manual";
      const baseCommitId = currentHeadCommitId(lesson);
      const payload = buildDocumentSavePayload(document, reason, baseCommitId, allowStructureRemoval);
      if (isManualSave) {
        setBusyAction("save");
      }
      setAutoSaveStatus("saving");

      const request = (async () => {
        try {
          const delta = await api.saveDocument(lesson.id, payload);
          applyAutoSavedPackage(delta, lesson.id, savedVersion);
          return true;
        } catch (saveError) {
          setAutoSaveStatus("error");
          setError(saveError instanceof Error ? saveError.message : "Autosave failed");
          return false;
        } finally {
          if (isManualSave) {
            setBusyAction((current) => (current === "save" ? null : current));
          }
        }
      })();

      autoSaveInFlightRef.current = request;
      try {
        return await request;
      } finally {
        autoSaveInFlightRef.current = null;
        if (autoSaveQueuedRef.current) {
          autoSaveQueuedRef.current = false;
          if (isDocumentDirtyRef.current) {
            scheduleAutoSaveRef.current("queued");
          }
        }
      }
    },
    [applyAutoSavedPackage, clearAutoSaveTimer, setBusyAction, setError]
  );

  const scheduleAutoSave = useCallback(
    (reason: AutoSaveReason = "debounce") => {
      clearAutoSaveTimer();
      if (!isDocumentDirtyRef.current || isPreviewingRef.current) {
        return;
      }
      if (autoSaveInFlightRef.current) {
        autoSaveQueuedRef.current = true;
        return;
      }
      setAutoSaveStatus("pending");
      autoSaveTimerRef.current = window.setTimeout(() => {
        autoSaveTimerRef.current = null;
        void flushAutoSave(reason);
      }, AUTO_SAVE_DELAY_MS);
    },
    [clearAutoSaveTimer, flushAutoSave]
  );

  useEffect(() => {
    flushAutoSaveRef.current = flushAutoSave;
    scheduleAutoSaveRef.current = scheduleAutoSave;
  }, [flushAutoSave, scheduleAutoSave]);

  const flushAutoSaveWithBeacon = useCallback(
    (reason: AutoSaveReason = "pagehide") => {
      clearAutoSaveTimer();
      const lesson = activeLessonRef.current;
      const document = draftDocumentRef.current;
      if (!lesson || !document || !isDocumentDirtyRef.current || isPreviewingRef.current) {
        return;
      }
      if (document.id !== lesson.board_document.id) {
        draftDocumentRef.current = lesson.board_document;
        isDocumentDirtyRef.current = false;
        setDraftDocument(lesson.board_document);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return;
      }
      const structuredGuard = lastStructuredDocumentRef.current;
      const allowStructureRemoval = structureRemovalIntentRef.current;
      if (
        structuredGuard &&
        structuredGuard.id === document.id &&
        wouldFlattenRenderedDocument(structuredGuard, document, allowStructureRemoval)
      ) {
        draftDocumentRef.current = structuredGuard;
        isDocumentDirtyRef.current = false;
        setDraftDocument(structuredGuard);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return;
      }
      if (documentsEqual(document, lesson.board_document)) {
        ignoredStreamingPreviewRef.current = null;
        return;
      }
      const baseCommitId = currentHeadCommitId(lesson);
      const payload = buildDocumentSavePayload(document, reason, baseCommitId, allowStructureRemoval);
      const sent = api.saveDocumentBeacon(lesson.id, payload);
      if (!sent) {
        void api.saveDocumentKeepalive(lesson.id, payload).catch(() => undefined);
      }
    },
    [clearAutoSaveTimer]
  );

  const handleLocalDocumentChange = useCallback(
    (nextDocument: BoardDocument) => {
      const lesson = activeLessonRef.current;
      if (isPreviewingRef.current || !lesson) {
        return;
      }
      if (nextDocument.id !== lesson.board_document.id) {
        return;
      }
      const ignoredStreamingPreview = ignoredStreamingPreviewRef.current;
      const structuredGuard = lastStructuredDocumentRef.current;
      const allowStructureRemoval = structureRemovalIntentRef.current;
      if (
        ignoredStreamingPreview &&
        looksLikeSameRenderedDocument(nextDocument, ignoredStreamingPreview) &&
        !documentsEqual(nextDocument, lesson.board_document)
      ) {
        ignoredStreamingPreviewRef.current = null;
        draftDocumentRef.current = lesson.board_document;
        isDocumentDirtyRef.current = false;
        setDraftDocument(lesson.board_document);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return;
      }
      if (
        nextDocument.id === lesson.board_document.id &&
        wouldFlattenRenderedDocument(lesson.board_document, nextDocument, allowStructureRemoval)
      ) {
        ignoredStreamingPreviewRef.current = null;
        draftDocumentRef.current = lesson.board_document;
        isDocumentDirtyRef.current = false;
        setDraftDocument(lesson.board_document);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return;
      }
      if (
        structuredGuard &&
        structuredGuard.id === nextDocument.id &&
        wouldFlattenRenderedDocument(structuredGuard, nextDocument, allowStructureRemoval)
      ) {
        ignoredStreamingPreviewRef.current = null;
        draftDocumentRef.current = structuredGuard;
        isDocumentDirtyRef.current = false;
        setDraftDocument(structuredGuard);
        setIsDocumentDirty(false);
        setAutoSaveStatus("idle");
        return;
      }
      const hasChanged = !documentsEqual(draftDocumentRef.current, nextDocument);
      const dirty = !documentsEqual(nextDocument, lesson.board_document);
      if (!dirty) {
        ignoredStreamingPreviewRef.current = null;
      } else if (
        ignoredStreamingPreview &&
        !looksLikeSameRenderedDocument(nextDocument, ignoredStreamingPreview)
      ) {
        ignoredStreamingPreviewRef.current = null;
      }
      if (hasChanged) {
        documentDraftVersionRef.current += 1;
      }
      draftDocumentRef.current = nextDocument;
      if (isStructuredDocument(nextDocument)) {
        lastStructuredDocumentRef.current = nextDocument;
      }
      isDocumentDirtyRef.current = dirty;
      setDraftDocument((current) => {
        if (current && current.id === nextDocument.id && documentsEqual(current, nextDocument)) {
          return current;
        }
        return nextDocument;
      });
      setIsDocumentDirty(dirty);
      setAutoSaveStatus(dirty ? "pending" : "idle");
    },
    []
  );

  const markStructureRemovalIntent = useCallback(() => {
    structureRemovalIntentRef.current = true;
  }, []);

  const handleImportDocx = useCallback(
    async (file: File) => {
      const lesson = activeLessonRef.current;
      if (!lesson) {
        return;
      }
      if (!(await flushAutoSave("import"))) {
        return;
      }
      setBusyAction("import-docx");
      try {
        const nextPackage = await api.importDocx(lesson.id, file);
        const result = applyCoursePackage(nextPackage, { activeLessonId: lesson.id });
        resetToLesson(result.activeLesson);
        onPackageApplied?.();
      } catch (importError) {
        setError(importError instanceof Error ? importError.message : "Import DOCX failed");
      } finally {
        setBusyAction(null);
      }
    },
    [applyCoursePackage, flushAutoSave, onPackageApplied, resetToLesson, setBusyAction, setError]
  );

  const handleExportDocx = useCallback(async () => {
    const lesson = activeLessonRef.current;
    if (!lesson) {
      return;
    }
    if (!(await flushAutoSave("export"))) {
      return;
    }
    setBusyAction("export-docx");
    try {
      const blob = await api.exportDocx(lesson.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${lesson.slug || lesson.id}.docx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "Exporting DOCX failed");
    } finally {
      setBusyAction(null);
    }
  }, [flushAutoSave, setBusyAction, setError]);

  const handleExportHtml = useCallback(async () => {
    const lesson = activeLessonRef.current;
    if (!lesson) {
      return;
    }
    if (!(await flushAutoSave("export"))) {
      return;
    }
    setBusyAction("export-html");
    try {
      const blob = await api.exportHtml(lesson.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${lesson.slug || lesson.id}.html`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "Export HTML failed");
    } finally {
      setBusyAction(null);
    }
  }, [flushAutoSave, setBusyAction, setError]);

  const scheduleAutoSaveEffectEvent = useEffectEvent(() => {
    scheduleAutoSave("debounce");
  });

  const clearAutoSaveTimerEffectEvent = useEffectEvent(() => {
    clearAutoSaveTimer();
  });

  useEffect(() => {
    activeLessonRef.current = activeLesson;
  }, [activeLesson]);

  useEffect(() => {
    if (!isDocumentDirty || isPreviewing) {
      clearAutoSaveTimerEffectEvent();
      return;
    }
    scheduleAutoSaveEffectEvent();
    return () => {
      clearAutoSaveTimerEffectEvent();
    };
  }, [activeLesson?.id, draftDocument, isDocumentDirty, isPreviewing]);

  return {
    draftDocument,
    displayedDocument,
    isDocumentDirty,
    isPreviewing,
    resetToLesson,
    setPreviewDocument,
    setStreamingDocumentPreview,
    flushAutoSave,
    flushAutoSaveWithBeacon,
    handleLocalDocumentChange,
    markStructureRemovalIntent,
    handleImportDocx,
    handleExportDocx,
    handleExportHtml,
  };
}

"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";

import type { AppliedCoursePackage, CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import { api } from "@/lib/api";
import type {
  AIModelSelection,
  BoardDocument,
  CoursePackage,
  Lesson,
  LessonMergeResolution,
  LessonMergeSessionView,
} from "@/types";

const MERGE_AUTO_SAVE_DELAY_MS = 900;

type UseLessonMergeOptions = {
  activeLesson: Lesson | null;
  selectedTextModel: AIModelSelection;
  flushBoardAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  applyCoursePackage: (
    nextPackage: CoursePackage,
    options?: CoursePackageApplyOptions
  ) => AppliedCoursePackage;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
};

export function useLessonMerge({
  activeLesson,
  selectedTextModel,
  flushBoardAutoSave,
  applyCoursePackage,
  setError,
  setBusyAction,
}: UseLessonMergeOptions) {
  const [session, setSession] = useState<LessonMergeSessionView | null>(null);
  const [draftDocument, setDraftDocument] = useState<BoardDocument | null>(null);
  const [isDraftDirty, setIsDraftDirty] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isAIProposing, setIsAIProposing] = useState(false);
  const sessionRef = useRef<LessonMergeSessionView | null>(null);
  const draftDocumentRef = useRef<BoardDocument | null>(null);
  const dirtyRef = useRef(false);
  const draftRevisionRef = useRef(0);
  const saveTimerRef = useRef<number | null>(null);
  const saveInFlightRef = useRef<Promise<boolean> | null>(null);
  const flushDraftRef = useRef<() => Promise<boolean>>(async () => true);
  const aiAbortRef = useRef<AbortController | null>(null);

  const replaceSession = useCallback((next: LessonMergeSessionView | null) => {
    sessionRef.current = next;
    draftDocumentRef.current = next?.draft_document ?? null;
    dirtyRef.current = false;
    setSession(next);
    setDraftDocument(next?.draft_document ?? null);
    setIsDraftDirty(false);
  }, []);

  const clearSaveTimer = useCallback(() => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  }, []);

  const flushDraft = useCallback(async (): Promise<boolean> => {
    clearSaveTimer();
    if (saveInFlightRef.current) {
      await saveInFlightRef.current;
      if (!dirtyRef.current) {
        return true;
      }
    }
    const currentSession = sessionRef.current;
    const document = draftDocumentRef.current;
    if (!currentSession || !document || !dirtyRef.current) {
      return true;
    }
    const savedRevision = draftRevisionRef.current;
    dirtyRef.current = false;
    setIsDraftDirty(false);
    const request = (async () => {
      try {
        const next = await api.updateMergeSession(currentSession.lesson_id, currentSession.id, {
          expected_version: currentSession.version,
          draft_document: document,
        });
        if (draftRevisionRef.current === savedRevision) {
          replaceSession(next);
        } else {
          const localDocument = draftDocumentRef.current ?? next.draft_document;
          const withLocalDraft = { ...next, draft_document: localDocument };
          sessionRef.current = withLocalDraft;
          setSession(withLocalDraft);
        }
        return true;
      } catch (error) {
        dirtyRef.current = true;
        setIsDraftDirty(true);
        setError(error instanceof Error ? error.message : "Failed to save merge draft");
        return false;
      }
    })();
    saveInFlightRef.current = request;
    try {
      return await request;
    } finally {
      saveInFlightRef.current = null;
      if (dirtyRef.current) {
        saveTimerRef.current = window.setTimeout(() => {
          saveTimerRef.current = null;
          void flushDraftRef.current();
        }, MERGE_AUTO_SAVE_DELAY_MS);
      }
    }
  }, [clearSaveTimer, replaceSession, setError]);

  useEffect(() => {
    flushDraftRef.current = flushDraft;
  }, [flushDraft]);

  const handleDocumentChange = useCallback(
    (document: BoardDocument) => {
      if (!sessionRef.current) {
        return;
      }
      draftRevisionRef.current += 1;
      draftDocumentRef.current = document;
      dirtyRef.current = true;
      setDraftDocument(document);
      setIsDraftDirty(true);
      clearSaveTimer();
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        void flushDraft();
      }, MERGE_AUTO_SAVE_DELAY_MS);
    },
    [clearSaveTimer, flushDraft]
  );

  useEffect(() => {
    const lessonId = activeLesson?.id;
    let cancelled = false;
    clearSaveTimer();
    aiAbortRef.current?.abort();
    const timer = window.setTimeout(() => {
      if (cancelled) {
        return;
      }
      replaceSession(null);
      if (!lessonId) {
        return;
      }
      setIsLoading(true);
      void api
        .getActiveMergeSession(lessonId)
        .then((next) => {
          if (!cancelled) {
            replaceSession(next);
          }
        })
        .catch((error) => {
          if (!cancelled) {
            setError(error instanceof Error ? error.message : "Failed to read merge draft");
          }
        })
        .finally(() => {
          if (!cancelled) {
            setIsLoading(false);
          }
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeLesson?.id, clearSaveTimer, replaceSession, setError]);

  const startMerge = useCallback(
    async (sourceBranchName: string) => {
      if (!activeLesson || sessionRef.current) {
        return;
      }
      if (!(await flushBoardAutoSave("branch"))) {
        return;
      }
      setBusyAction("merge-create");
      try {
        const next = await api.createMergeSession(
          activeLesson.id,
          sourceBranchName,
          "manual",
          selectedTextModel
        );
        replaceSession(next);
        setError(null);
      } catch (error) {
        setError(error instanceof Error ? error.message : "Failed to create merge draft");
      } finally {
        setBusyAction(null);
      }
    },
    [activeLesson, flushBoardAutoSave, replaceSession, selectedTextModel, setBusyAction, setError]
  );

  const resolveConflict = useCallback(
    async (conflictId: string, resolution: LessonMergeResolution, customValue?: unknown) => {
      if (!(await flushDraft())) {
        return;
      }
      const current = sessionRef.current;
      if (!current) {
        return;
      }
      setBusyAction("merge-resolve");
      try {
        const next = await api.updateMergeSession(current.lesson_id, current.id, {
          expected_version: current.version,
          resolutions: [{ conflict_id: conflictId, resolution, custom_value: customValue }],
        });
        replaceSession(next);
        setError(null);
      } catch (error) {
        setError(error instanceof Error ? error.message : "Saving conflict resolution failed");
      } finally {
        setBusyAction(null);
      }
    },
    [flushDraft, replaceSession, setBusyAction, setError]
  );

  const proposeWithAI = useCallback(async () => {
    if (!(await flushDraft())) {
      return;
    }
    const current = sessionRef.current;
    if (!current || isAIProposing) {
      return;
    }
    const controller = new AbortController();
    aiAbortRef.current = controller;
    setIsAIProposing(true);
    setBusyAction("merge-ai");
    try {
      const next = await api.streamMergeProposal(
        current.lesson_id,
        current.id,
        current.version,
        {
          onAgentActivity: (activity) => {
            setSession((value) =>
              value ? { ...value, agent_activity: [...value.agent_activity, activity] } : value
            );
          },
          onFinal: replaceSession,
        },
        { signal: controller.signal }
      );
      replaceSession(next);
      setError(null);
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error instanceof Error ? error.message : "AI merge failed");
        try {
          replaceSession(await api.getMergeSession(current.lesson_id, current.id));
        } catch {
          // Keep the last durable view if the refresh also fails.
        }
      }
    } finally {
      aiAbortRef.current = null;
      setIsAIProposing(false);
      setBusyAction(null);
    }
  }, [flushDraft, isAIProposing, replaceSession, setBusyAction, setError]);

  const cancelAI = useCallback(() => {
    aiAbortRef.current?.abort();
  }, []);

  const recompute = useCallback(async () => {
    const current = sessionRef.current;
    if (!current) {
      return;
    }
    setBusyAction("merge-recompute");
    try {
      const next = await api.recomputeMergeSession(
        current.lesson_id,
        current.id,
        current.version,
        selectedTextModel
      );
      replaceSession(next);
      setError(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Recalculation of merge draft failed");
    } finally {
      setBusyAction(null);
    }
  }, [replaceSession, selectedTextModel, setBusyAction, setError]);

  const abandon = useCallback(async () => {
    const current = sessionRef.current;
    if (!current) {
      return;
    }
    clearSaveTimer();
    aiAbortRef.current?.abort();
    setBusyAction("merge-abandon");
    try {
      const contributionId = current.audit.lesson_contribution_id;
      if (typeof contributionId === "string" && contributionId) {
        const contribution = await api.getLessonContribution(contributionId);
        await api.returnLessonContributionForChanges(contributionId, contribution.version);
      } else {
        await api.abandonMergeSession(current.lesson_id, current.id, current.version);
      }
      replaceSession(null);
      setError(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Abandoning merge draft failed");
    } finally {
      setBusyAction(null);
    }
  }, [clearSaveTimer, replaceSession, setBusyAction, setError]);

  const submit = useCallback(async () => {
    if (!(await flushDraft())) {
      return;
    }
    const current = sessionRef.current;
    if (!current || current.conflicts.some((conflict) => !conflict.resolved)) {
      return;
    }
    setBusyAction("merge-submit");
    try {
      const nextPackage = await api.submitMergeSession(
        current.lesson_id,
        current.id,
        current.version
      );
      applyCoursePackage(nextPackage, {
        activeLessonId: current.lesson_id,
        rebuildMessageLessonIds: [current.lesson_id],
      });
      replaceSession(null);
      setError(null);
      const contributionId = current.audit.lesson_contribution_id;
      if (typeof contributionId === "string" && contributionId) {
        window.location.assign(`/contributions/${encodeURIComponent(contributionId)}`);
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "Commit merge failed");
      try {
        replaceSession(await api.getMergeSession(current.lesson_id, current.id));
      } catch {
        // The original submit error remains the actionable message.
      }
    } finally {
      setBusyAction(null);
    }
  }, [applyCoursePackage, flushDraft, replaceSession, setBusyAction, setError]);

  return {
    session,
    draftDocument,
    isActive: Boolean(session),
    isDraftDirty,
    isLoading,
    isAIProposing,
    startMerge,
    handleDocumentChange,
    flushDraft,
    resolveConflict,
    proposeWithAI,
    cancelAI,
    recompute,
    abandon,
    submit,
  };
}

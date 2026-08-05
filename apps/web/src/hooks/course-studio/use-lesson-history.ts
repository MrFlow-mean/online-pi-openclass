"use client";

import { useMemo, useState, type Dispatch, type SetStateAction } from "react";

import { api } from "@/lib/api";
import {
  currentHeadCommitId,
  getLessonCommit,
  nextBranchName,
} from "@/components/course-studio/history-utils";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { AppliedCoursePackage, CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type { BoardDocument, CommitRecord, CoursePackage, Lesson } from "@/types";

type UseLessonHistoryOptions = {
  activeLesson: Lesson | null;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  resetDraftToLesson: (lesson: Lesson | null) => void;
  setPreviewDocument: (document: BoardDocument) => void;
  applyCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => AppliedCoursePackage;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
};

export function useLessonHistory({
  activeLesson,
  flushAutoSave,
  resetDraftToLesson,
  setPreviewDocument,
  applyCoursePackage,
  setError,
  setBusyAction,
}: UseLessonHistoryOptions) {
  const [previewCommitId, setPreviewCommitId] = useState<string | null>(null);
  const [newBranchName, setNewBranchName] = useState("");

  const previewCommit = useMemo(
    () =>
      previewCommitId && activeLesson
        ? activeLesson.history_graph.commits.find((commit) => commit.id === previewCommitId) ?? null
        : null,
    [activeLesson, previewCommitId]
  );
  const activeHeadCommit = useMemo(
    () => (activeLesson ? getLessonCommit(activeLesson, currentHeadCommitId(activeLesson)) : null),
    [activeLesson]
  );
  const isPreviewMode = Boolean(previewCommit);

  async function handleCreateBranch(fromCommitId = previewCommitId, branchNameOverride?: string) {
    if (!activeLesson) {
      return;
    }
    if (!fromCommitId && !(await flushAutoSave("branch"))) {
      return;
    }
    const branchName = (branchNameOverride ?? newBranchName.trim()).trim();
    const finalBranchName = branchName || nextBranchName(activeLesson);
    setBusyAction("branch");
    try {
      const nextPackage = await api.createBranch(activeLesson.id, finalBranchName, fromCommitId);
      applyCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: fromCommitId ? [activeLesson.id] : undefined,
      });
      setNewBranchName("");
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "Failed to create branch");
    } finally {
      setBusyAction(null);
    }
  }

  async function handlePreviewCommit(commit: CommitRecord) {
    if (!(await flushAutoSave("preview"))) {
      return;
    }
    setPreviewCommitId(commit.id);
    setPreviewDocument(commit.snapshot);
  }

  function exitPreviewMode() {
    if (!activeLesson || !previewCommitId) {
      return;
    }
    setPreviewCommitId(null);
    resetDraftToLesson(activeLesson);
  }

  async function handleCreateBranchFromCommit(commit: CommitRecord) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("branch"))) {
      return;
    }
    setPreviewCommitId(commit.id);
    setPreviewDocument(commit.snapshot);
    await handleCreateBranch(commit.id, newBranchName.trim() || nextBranchName(activeLesson));
  }

  async function handleSwitchBranch(branchName: string) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("switch-branch"))) {
      return;
    }
    setBusyAction("switch-branch");
    try {
      const nextPackage = await api.switchBranch(activeLesson.id, branchName);
      applyCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: [activeLesson.id],
      });
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "Failed to switch branches");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRestoreCommit(commitId: string) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("restore"))) {
      return;
    }
    setBusyAction("restore");
    try {
      const nextPackage = await api.restoreCommit(activeLesson.id, commitId);
      applyCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: [activeLesson.id],
      });
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Restore version failed");
    } finally {
      setBusyAction(null);
    }
  }

  return {
    previewCommitId,
    previewCommit,
    activeHeadCommit,
    isPreviewMode,
    newBranchName,
    setPreviewCommitId,
    setNewBranchName,
    handleCreateBranch,
    handlePreviewCommit,
    exitPreviewMode,
    handleCreateBranchFromCommit,
    handleSwitchBranch,
    handleRestoreCommit,
  };
}

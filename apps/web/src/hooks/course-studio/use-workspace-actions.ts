"use client";

import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";

import { api } from "@/lib/api";
import { applyLessonWorkspaceDeltaToPackage } from "@/lib/workspace-delta";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type { CoursePackage, Lesson } from "@/types";

type UseWorkspaceActionsOptions = {
  coursePackage: CoursePackage | null;
  activeLesson: Lesson | null;
  lessonMap: Map<string, Lesson>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  updateCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => void;
  selectLocalLesson: (lessonId: string) => void;
  resetDraftToLesson: (lesson: Lesson | null) => void;
  resetTransientUi: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
};

export function useWorkspaceActions({
  coursePackage,
  activeLesson,
  lessonMap,
  flushAutoSave,
  updateCoursePackage,
  selectLocalLesson,
  resetDraftToLesson,
  resetTransientUi,
  setError,
  setBusyAction,
}: UseWorkspaceActionsOptions) {
  const activeLessonIdRef = useRef<string | null>(activeLesson?.id ?? null);
  const coursePackageRef = useRef<CoursePackage | null>(coursePackage);
  const workspaceMutationInFlightRef = useRef(false);

  useEffect(() => {
    activeLessonIdRef.current = activeLesson?.id ?? null;
  }, [activeLesson?.id]);

  useEffect(() => {
    coursePackageRef.current = coursePackage;
  }, [coursePackage]);

  async function saveGeneratedLesson(): Promise<boolean> {
    const initialActiveLessonId = activeLesson?.id ?? null;
    setBusyAction("generate");
    try {
      const delta = await api.generateLesson({
        branchFromLessonId: coursePackage?.is_standalone ? null : activeLesson?.id,
        startBlank: true,
        targetPackageId: coursePackage?.id,
      });
      const currentPackage = coursePackageRef.current;
      if (!currentPackage) {
        throw new Error("Course workspace is not loaded");
      }
      const nextPackage = applyLessonWorkspaceDeltaToPackage(currentPackage, delta);
      const generatedLessonId = delta.created_lesson?.id ?? delta.active_lesson_id ?? null;
      const currentActiveLessonId = activeLessonIdRef.current;
      const shouldPreserveCurrentLesson =
        currentActiveLessonId !== null &&
        currentActiveLessonId !== initialActiveLessonId &&
        nextPackage.workspace_tab_order.includes(currentActiveLessonId);
      updateCoursePackage(nextPackage, {
        activeLessonId: shouldPreserveCurrentLesson ? currentActiveLessonId : generatedLessonId,
        blankLessonIds: generatedLessonId ? [generatedLessonId] : [],
      });
      return true;
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "Failed to generate lesson");
      return false;
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateLesson() {
    if (workspaceMutationInFlightRef.current) {
      return false;
    }
    workspaceMutationInFlightRef.current = true;
    try {
      if (!(await flushAutoSave("create-lesson"))) {
        return false;
      }
      return await saveGeneratedLesson();
    } finally {
      workspaceMutationInFlightRef.current = false;
    }
  }

  async function handleOpenLesson(lessonId: string) {
    if (!(await flushAutoSave("open-lesson"))) {
      return;
    }
    setBusyAction("open-lesson");
    try {
      const nextPackage = await api.openLesson(lessonId);
      updateCoursePackage(nextPackage);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Failed to open course");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCloseLesson(lessonId: string) {
    if (workspaceMutationInFlightRef.current) {
      return;
    }
    workspaceMutationInFlightRef.current = true;
    if (activeLesson?.id === lessonId && !(await flushAutoSave("close-lesson"))) {
      workspaceMutationInFlightRef.current = false;
      return;
    }
    setBusyAction("close-lesson");
    try {
      const currentPackage = coursePackageRef.current;
      if (!currentPackage) {
        return;
      }
      const delta = await api.closeLesson(lessonId);
      const nextPackage = applyLessonWorkspaceDeltaToPackage(currentPackage, delta);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson && activeLesson.id !== lessonId ? activeLesson.id : undefined,
      });
    } catch (closeError) {
      setError(closeError instanceof Error ? closeError.message : "Failed to close course");
    } finally {
      setBusyAction(null);
      workspaceMutationInFlightRef.current = false;
    }
  }

  async function handleSelectLesson(lessonId: string) {
    if (activeLesson?.id !== lessonId && !(await flushAutoSave("select-lesson"))) {
      return;
    }
    resetTransientUi();
    selectLocalLesson(lessonId);
    resetDraftToLesson(lessonMap.get(lessonId) ?? null);
  }

  return {
    handleCreateLesson,
    handleOpenLesson,
    handleCloseLesson,
    handleSelectLesson,
  };
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { applyDocumentSaveDeltaToPackage } from "@/lib/workspace-delta";
import {
  DEFAULT_LESSON_COMPOSER_STATE,
  buildLessonMessagesFromHistory,
  createLessonComposerState,
  type ChatMessage,
  type LessonComposerState,
  type LessonComposerStateMap,
  type LessonMessageMap,
} from "@/components/course-studio/history-utils";
import { mergeCoursePackageForLesson } from "@/hooks/course-studio/chat-turn-ui-state";
import type { CoursePackage, DocumentSaveDelta, Lesson } from "@/types";

export type CoursePackageApplyOptions = {
  blankLessonIds?: string[];
  activeLessonId?: string | null;
  rebuildMessageLessonIds?: string[];
  mergeLessonId?: string;
};

export type AppliedCoursePackage = {
  coursePackage: CoursePackage;
  activeLesson: Lesson | null;
};

type AutoSavedPackageResult = {
  coursePackage: CoursePackage;
  savedLesson: Lesson | null;
};

function createMessageMap(nextPackage: CoursePackage, current: LessonMessageMap): LessonMessageMap {
  const next: LessonMessageMap = {};
  nextPackage.lessons.forEach((lesson) => {
    next[lesson.id] = lessonMessagesFromCacheOrHistory(lesson, current);
  });
  return next;
}

function lessonMessagesFromCacheOrHistory(lesson: Lesson, current: LessonMessageMap): ChatMessage[] {
  const cachedMessages = current[lesson.id];
  const historyMessages = buildLessonMessagesFromHistory(lesson);
  if (!cachedMessages || (cachedMessages.length === 0 && historyMessages.length > 0)) {
    return historyMessages;
  }
  return cachedMessages;
}

function createComposerStateMap(nextPackage: CoursePackage, current: LessonComposerStateMap): LessonComposerStateMap {
  const next: LessonComposerStateMap = {};
  nextPackage.lessons.forEach((lesson) => {
    next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
  });
  return next;
}

function newestLessonsFirst(lessons: Lesson[]): Lesson[] {
  return [...lessons].sort(
    (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  );
}

export function useCourseWorkspace() {
  const [coursePackage, setCoursePackage] = useState<CoursePackage | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lessonMessages, setLessonMessages] = useState<LessonMessageMap>({});
  const [lessonComposerStates, setLessonComposerStates] = useState<LessonComposerStateMap>({});
  const coursePackageRef = useRef<CoursePackage | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const payload = await api.getCoursePackage();
        coursePackageRef.current = payload;
        setCoursePackage(payload);
        setLessonMessages((current) => createMessageMap(payload, current));
        setLessonComposerStates((current) => createComposerStateMap(payload, current));
        setError(null);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Loading failed");
      } finally {
        setIsLoading(false);
      }
    }
    void load();
  }, []);

  const lessonMap = useMemo(() => {
    const next = new Map<string, Lesson>();
    coursePackage?.lessons.forEach((lesson) => next.set(lesson.id, lesson));
    return next;
  }, [coursePackage]);

  const activeLesson = useMemo(() => {
    if (!coursePackage) {
      return null;
    }
    return coursePackage.active_lesson_id
      ? lessonMap.get(coursePackage.active_lesson_id) ?? null
      : null;
  }, [coursePackage, lessonMap]);

  const openLessons = useMemo(
    () =>
      newestLessonsFirst(
        (coursePackage?.workspace_tab_order
          .map((lessonId) => lessonMap.get(lessonId))
          .filter(Boolean) as Lesson[]) ?? []
      ),
    [coursePackage?.workspace_tab_order, lessonMap]
  );

  const activeMessages = activeLesson
    ? lessonMessagesFromCacheOrHistory(activeLesson, lessonMessages)
    : [];

  const activeComposerState = activeLesson
    ? lessonComposerStates[activeLesson.id] ?? DEFAULT_LESSON_COMPOSER_STATE
    : DEFAULT_LESSON_COMPOSER_STATE;

  const syncLessonMessages = useCallback(
    (nextPackage: CoursePackage, options?: { blankLessonIds?: string[]; rebuildLessonIds?: string[] }) => {
      const blankLessonIds = new Set(options?.blankLessonIds ?? []);
      const rebuildLessonIds = new Set(options?.rebuildLessonIds ?? []);
      setLessonMessages((current) => {
        const next: LessonMessageMap = {};
        nextPackage.lessons.forEach((lesson) => {
          next[lesson.id] = blankLessonIds.has(lesson.id)
            ? []
            : rebuildLessonIds.has(lesson.id)
              ? buildLessonMessagesFromHistory(lesson)
              : lessonMessagesFromCacheOrHistory(lesson, current);
        });
        return next;
      });
    },
    []
  );

  const syncLessonComposerStates = useCallback((lessons: Lesson[]) => {
    setLessonComposerStates((current) => {
      const next: LessonComposerStateMap = {};
      lessons.forEach((lesson) => {
        next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
      });
      return next;
    });
  }, []);

  const updateLessonMessages = useCallback(
    (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => {
      setLessonMessages((current) => ({
        ...current,
        [lessonId]: updater(current[lessonId] ?? []),
      }));
    },
    []
  );

  const updateLessonComposerState = useCallback(
    (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => {
      setLessonComposerStates((current) => ({
        ...current,
        [lessonId]: updater(current[lessonId] ?? createLessonComposerState()),
      }));
    },
    []
  );

  const updateActiveLessonComposerState = useCallback(
    (updater: (current: LessonComposerState) => LessonComposerState) => {
      if (!activeLesson) {
        return;
      }
      updateLessonComposerState(activeLesson.id, updater);
    },
    [activeLesson, updateLessonComposerState]
  );

  const applyCoursePackage = useCallback(
    (nextPackage: CoursePackage, options?: CoursePackageApplyOptions): AppliedCoursePackage => {
      const packageToApply =
        options?.mergeLessonId && coursePackageRef.current
          ? mergeCoursePackageForLesson(coursePackageRef.current, nextPackage, options.mergeLessonId)
          : nextPackage;
      const requestedActiveLessonId = options?.activeLessonId;
      const effectiveActiveLessonId =
        requestedActiveLessonId && packageToApply.workspace_tab_order.includes(requestedActiveLessonId)
          ? requestedActiveLessonId
          : packageToApply.active_lesson_id;
      const mergedPackage =
        effectiveActiveLessonId === packageToApply.active_lesson_id
          ? packageToApply
          : { ...packageToApply, active_lesson_id: effectiveActiveLessonId };
      const nextActiveLesson =
        mergedPackage.lessons.find((lesson) => lesson.id === mergedPackage.active_lesson_id) ??
        mergedPackage.lessons[0] ??
        null;

      coursePackageRef.current = mergedPackage;
      setCoursePackage(mergedPackage);
      syncLessonMessages(mergedPackage, {
        blankLessonIds: options?.blankLessonIds,
        rebuildLessonIds: options?.rebuildMessageLessonIds,
      });
      syncLessonComposerStates(mergedPackage.lessons);
      if (!options?.mergeLessonId || mergedPackage.active_lesson_id === options.mergeLessonId) {
        setError(null);
      }

      return { coursePackage: mergedPackage, activeLesson: nextActiveLesson };
    },
    [syncLessonComposerStates, syncLessonMessages]
  );

  const applyAutoSavedCoursePackage = useCallback(
    (
      delta: DocumentSaveDelta,
      lessonId: string,
      currentActiveLessonId: string | null
    ): AutoSavedPackageResult => {
      const currentPackage = coursePackageRef.current;
      if (!currentPackage) {
        throw new Error("Course workspace is not loaded");
      }
      const packageToApply = applyDocumentSaveDeltaToPackage(currentPackage, delta);
      const effectiveActiveLessonId =
        currentActiveLessonId && packageToApply.workspace_tab_order.includes(currentActiveLessonId)
          ? currentActiveLessonId
          : packageToApply.active_lesson_id;
      const mergedPackage =
        effectiveActiveLessonId === packageToApply.active_lesson_id
          ? packageToApply
          : { ...packageToApply, active_lesson_id: effectiveActiveLessonId };
      const savedLesson = mergedPackage.lessons.find((lesson) => lesson.id === lessonId) ?? null;

      coursePackageRef.current = mergedPackage;
      setCoursePackage(mergedPackage);
      syncLessonMessages(mergedPackage);
      syncLessonComposerStates(mergedPackage.lessons);
      setError(null);

      return { coursePackage: mergedPackage, savedLesson };
    },
    [syncLessonComposerStates, syncLessonMessages]
  );

  const selectLocalLesson = useCallback((lessonId: string) => {
    setCoursePackage((current) => {
      const nextPackage = current ? { ...current, active_lesson_id: lessonId } : current;
      coursePackageRef.current = nextPackage;
      return nextPackage;
    });
  }, []);

  return {
    coursePackage,
    isLoading,
    error,
    setError,
    lessonMap,
    activeLesson,
    openLessons,
    activeMessages,
    activeComposerState,
    lessonMessages,
    lessonComposerStates,
    syncLessonMessages,
    syncLessonComposerStates,
    updateLessonMessages,
    updateLessonComposerState,
    updateActiveLessonComposerState,
    applyCoursePackage,
    applyAutoSavedCoursePackage,
    selectLocalLesson,
  };
}

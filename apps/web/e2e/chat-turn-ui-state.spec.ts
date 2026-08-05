import { expect, test } from "@playwright/test";

import {
  activeLessonIdForAsyncPackage,
  editorUpdateBelongsToDocument,
  mergeCoursePackageForLesson,
  resolvedBoardFocusForTurn,
} from "../src/hooks/course-studio/chat-turn-ui-state";
import type { BoardFocusRef, BoardTaskRequirementSheet, CoursePackage, Lesson } from "../src/types";

const focus = {
  source: "board",
  lesson_id: "lesson-source",
  document_id: "document-source",
  segment_id: "segment-source",
  excerpt: "Approved target",
  heading_path: ["Section"],
} as BoardFocusRef;

const task = {
  location_status: "resolved",
  target_location: focus,
} as BoardTaskRequirementSheet;

const coursePackage = {
  active_lesson_id: "lesson-created",
  workspace_tab_order: ["lesson-source", "lesson-created", "lesson-user-opened"],
} as CoursePackage;

test("ordinary chat never restores a prior transient board focus", () => {
  expect(resolvedBoardFocusForTurn("ordinary_chat", task)).toBeNull();
  expect(resolvedBoardFocusForTurn("unclear", task)).toBeNull();
  expect(resolvedBoardFocusForTurn("learning_need", task)).toEqual(focus);
});

test("a confirmed new lesson becomes active unless the learner switched tabs", () => {
  expect(
    activeLessonIdForAsyncPackage(
      coursePackage,
      "lesson-source",
      "lesson-source",
      coursePackage.active_lesson_id
    )
  ).toBe("lesson-created");
  expect(
    activeLessonIdForAsyncPackage(
      coursePackage,
      "lesson-source",
      "lesson-user-opened",
      coursePackage.active_lesson_id
    )
  ).toBe("lesson-user-opened");
});

test("an async lesson response only replaces the lesson that started the turn", () => {
  const currentSource = { id: "lesson-source", title: "source before" } as Lesson;
  const currentOpened = { id: "lesson-user-opened", title: "opened current" } as Lesson;
  const incomingSource = { id: "lesson-source", title: "source generated" } as Lesson;
  const staleOpened = { id: "lesson-user-opened", title: "opened stale" } as Lesson;
  const incomingNew = { id: "lesson-new", title: "new lesson" } as Lesson;
  const currentPackage = {
    ...coursePackage,
    active_lesson_id: "lesson-user-opened",
    workspace_tab_order: ["lesson-source", "lesson-user-opened"],
    lessons: [currentSource, currentOpened],
  } as CoursePackage;
  const incomingPackage = {
    ...coursePackage,
    active_lesson_id: "lesson-source",
    workspace_tab_order: ["lesson-source", "lesson-user-opened", "lesson-new"],
    lessons: [incomingSource, staleOpened, incomingNew],
  } as CoursePackage;

  const merged = mergeCoursePackageForLesson(currentPackage, incomingPackage, "lesson-source");

  expect(merged.active_lesson_id).toBe("lesson-user-opened");
  expect(merged.workspace_tab_order).toEqual(["lesson-source", "lesson-user-opened", "lesson-new"]);
  expect(merged.lessons.find((lesson) => lesson.id === "lesson-source")?.title).toBe("source generated");
  expect(merged.lessons.find((lesson) => lesson.id === "lesson-user-opened")?.title).toBe("opened current");
  expect(merged.lessons.find((lesson) => lesson.id === "lesson-new")?.title).toBe("new lesson");
});

test("a stale editor callback cannot write into the newly selected document", () => {
  expect(editorUpdateBelongsToDocument("document-source", "document-source")).toBe(true);
  expect(editorUpdateBelongsToDocument("document-source", "document-user-opened")).toBe(false);
  expect(editorUpdateBelongsToDocument(null, "document-user-opened")).toBe(false);
});

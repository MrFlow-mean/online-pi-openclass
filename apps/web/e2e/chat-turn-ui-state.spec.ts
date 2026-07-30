import { expect, test } from "@playwright/test";

import {
  activeLessonIdForAsyncPackage,
  resolvedBoardFocusForTurn,
} from "../src/hooks/course-studio/chat-turn-ui-state";
import type { BoardFocusRef, BoardTaskRequirementSheet, CoursePackage } from "../src/types";

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

import { expect, test } from "@playwright/test";

import { boardTaskConfirmationPayload } from "../src/components/course-studio/chat-sidebar";

test("builds explicit board-task confirmation payloads independently of visible wording", () => {
  expect(boardTaskConfirmationPayload("confirm")).toEqual({
    message: "确认执行当前板书任务",
    interaction_mode: "ask",
    board_task_confirmation: "confirm",
  });
  expect(boardTaskConfirmationPayload("decline")).toEqual({
    message: "取消当前板书任务",
    interaction_mode: "ask",
    board_task_confirmation: "decline",
  });
});

import { expect, test } from "@playwright/test";

import { boardTaskConfirmationPayload } from "../src/components/course-studio/chat-sidebar";
import { latestGenerationFailureSnapshot } from "../src/components/course-studio/current-need-snapshot";
import { recoveredLearningRequirementFailureReason } from "../src/hooks/course-studio/use-lesson-chat-agent";
import type { CommitRecord, Lesson } from "../src/types";

test("builds explicit board-task confirmation payloads independently of visible wording", () => {
  expect(boardTaskConfirmationPayload("confirm")).toEqual({
    message: "Confirm execution of current board content task",
    interaction_mode: "ask",
    board_task_confirmation: "confirm",
  });
  expect(boardTaskConfirmationPayload("decline")).toEqual({
    message: "Cancel the current board content task",
    interaction_mode: "ask",
    board_task_confirmation: "decline",
  });
});

function lessonWithCommits(commits: CommitRecord[]): Lesson {
  const head = commits.at(-1)?.id ?? "";
  return {
    history_graph: {
      current_branch: "main",
      branches: {
        main: {
          name: "main",
          head_commit_id: head,
          created_at: "2026-08-03T00:00:00Z",
          created_from_commit_id: null,
        },
      },
      commits,
    },
  } as unknown as Lesson;
}

function commit(
  id: string,
  parentIds: string[],
  metadata: Record<string, unknown>
): CommitRecord {
  return {
    id,
    label: id,
    message: id,
    branch_name: "main",
    created_at: "2026-08-03T00:00:00Z",
    parent_ids: parentIds,
    operations: [],
    snapshot: {} as CommitRecord["snapshot"],
    metadata,
  };
}

test("keeps a failed frozen generation visible until a later board generation succeeds", () => {
  const failure = commit("failure", [], {
    kind: "learning_requirement_generation_failed",
    generation_failure_reason: "Provider stream ended early",
  });
  const ordinaryChat = commit("ordinary", [failure.id], { kind: "basic_chat" });
  const failedLesson = lessonWithCommits([failure, ordinaryChat]);

  expect(latestGenerationFailureSnapshot(failedLesson, ordinaryChat.id)).toEqual({
    commit: failure,
    reason: "Provider stream ended early",
  });
  expect(recoveredLearningRequirementFailureReason(failure)).toBe(
    "Provider stream ended early"
  );

  const success = commit("success", [ordinaryChat.id], {
    kind: "board_document_generation",
    requirement_cleared: true,
  });
  expect(latestGenerationFailureSnapshot(lessonWithCommits([failure, ordinaryChat, success]), success.id)).toBeNull();
});

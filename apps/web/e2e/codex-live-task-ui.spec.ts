import { expect, test } from "@playwright/test";

import {
  applyCodexLiveTaskEvent,
  codexLiveTaskHasVisibleActivity,
  codexLiveTaskSummary,
  createCodexLiveTaskState,
  handleCodexLiveTaskEvent,
  type CodexLiveBridgeEvent,
  type CodexLiveTaskState,
  type RealtimeToolStatusUpdate,
} from "../src/hooks/course-studio/codex-live-task-ui";

function workflowEvent(
  type: string,
  workflowRunId: string,
  turnId: string,
  extra: Partial<CodexLiveBridgeEvent> = {}
): CodexLiveBridgeEvent {
  return {
    type,
    delegation_id: `delegation-${workflowRunId}`,
    workflow_run_id: workflowRunId,
    turn_id: turnId,
    ...extra,
  };
}

const successfulResult = {
  status: "ok",
  model_output: { status: "ok" },
} as NonNullable<CodexLiveBridgeEvent["result"]>;

test("Codex Live tracks concurrent workflow runs independently under out-of-order events", () => {
  let state = createCodexLiveTaskState();
  state = applyCodexLiveTaskEvent(state, workflowEvent("codex_live.workflow.started", "run-a", "turn-a"));
  state = applyCodexLiveTaskEvent(state, workflowEvent("codex_live.workflow.queued", "run-b", "turn-b"));
  state = applyCodexLiveTaskEvent(
    state,
    workflowEvent("codex_live.workflow.result", "run-b", "turn-b", { result: successfulResult })
  );
  state = applyCodexLiveTaskEvent(state, workflowEvent("codex_live.workflow.started", "run-b", "turn-b"));
  state = applyCodexLiveTaskEvent(state, workflowEvent("codex_live.workflow.error", "run-a", "turn-a"));
  state = applyCodexLiveTaskEvent(state, workflowEvent("codex_live.workflow.cancelled", "run-c", "turn-c"));

  expect(state.runsByWorkflowId["run-a"].status).toBe("error");
  expect(state.runsByWorkflowId["run-b"].status).toBe("completed");
  expect(state.runsByWorkflowId["run-c"].status).toBe("cancelled");
  expect(codexLiveTaskSummary(state)).toEqual({ runningCount: 0, queuedCount: 0, pendingCount: 0 });
});

test("a speech-channel error cannot replace a successful document workflow result", () => {
  let state: CodexLiveTaskState = createCodexLiveTaskState();
  const statuses: RealtimeToolStatusUpdate[] = [];
  const errors: string[] = [];
  const applyTaskEvent = (payload: CodexLiveBridgeEvent) => {
    state = applyCodexLiveTaskEvent(state, payload);
    return state;
  };
  const context = {
    lessonId: "lesson-1",
    applyTaskEvent,
    delegationTurnId: () => "fallback-turn",
    onToolStatusUpdate: (update: RealtimeToolStatusUpdate) => statuses.push(update),
    onToolResult: () => undefined,
    setVoiceStatusText: () => undefined,
    setError: (message: string) => errors.push(message),
    logWorkflowStarted: () => undefined,
  };

  handleCodexLiveTaskEvent(
    workflowEvent("codex_live.workflow.result", "run-success", "turn-success", {
      route: "learning_need",
      result: successfulResult,
    }),
    context
  );
  handleCodexLiveTaskEvent(
    workflowEvent("codex_live.workflow.error", "run-success", "turn-success", {
      message: "speech channel failed",
    }),
    context
  );

  expect(state.runsByWorkflowId["run-success"]).toMatchObject({
    status: "completed",
    documentResultSucceeded: true,
    speechStatus: "failed",
  });
  expect(statuses.map((update) => update.status)).toEqual(["completed", "completed"]);
  expect(errors).toEqual([]);
});

test("ordinary chat results never become visible document task activity", () => {
  let state: CodexLiveTaskState = createCodexLiveTaskState();
  const statuses: RealtimeToolStatusUpdate[] = [];
  const event = workflowEvent("codex_live.workflow.result", "run-chat", "turn-chat", {
    route: "ordinary_chat",
    result: successfulResult,
  });

  handleCodexLiveTaskEvent(event, {
    lessonId: "lesson-1",
    applyTaskEvent: (payload) => {
      state = applyCodexLiveTaskEvent(state, payload);
      return state;
    },
    delegationTurnId: () => "fallback-turn",
    onToolStatusUpdate: (update) => statuses.push(update),
    onToolResult: () => undefined,
    setVoiceStatusText: () => undefined,
    setError: () => undefined,
    logWorkflowStarted: () => undefined,
  });

  expect(state.runsByWorkflowId["run-chat"].visibleAsTask).toBe(false);
  expect(codexLiveTaskHasVisibleActivity(state)).toBe(false);
  expect(statuses).toEqual([]);
});

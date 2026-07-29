import type { Dispatch, SetStateAction } from "react";

import type { AgentActivityEvent, RealtimeToolCallResponse } from "@/types";

export type RealtimeToolStatusUpdate = {
  lessonId: string;
  turnId: string;
  delegationId?: string;
  label: string;
  status: "pending" | "waiting" | "queued" | "running" | "completed" | "cancelled" | "error";
  activity?: AgentActivityEvent;
};

export type CodexLiveTaskAction = "supplement" | "replace" | "queue" | "chat" | "dismiss";

export type CodexLivePendingTask = {
  delegationId: string;
  text: string;
};

export type CodexLiveTaskState = {
  runningCount: number;
  queuedCount: number;
  pendingCount: number;
  pendingTasks: CodexLivePendingTask[];
};

export type CodexLiveBridgeEvent = {
  type?: string;
  role?: "user" | "assistant";
  text?: string;
  message?: string;
  delegation_id?: string;
  duplicate_of?: string;
  delta?: string;
  activity?: AgentActivityEvent;
  running_count?: number;
  queued_count?: number;
  pending_count?: number;
  position?: number;
  reason?: string;
  result?: RealtimeToolCallResponse;
};

type CodexLiveTaskEventContext = {
  lessonId: string;
  setTaskState: Dispatch<SetStateAction<CodexLiveTaskState>>;
  updateQueueState: (payload: CodexLiveBridgeEvent) => void;
  removePendingTask: (delegationId: string) => void;
  delegationTurnId: (delegationId: string) => string;
  onToolStatusUpdate: (update: RealtimeToolStatusUpdate) => void;
  onToolResult: (lessonId: string, result: RealtimeToolCallResponse) => void;
  setVoiceStatusText: (value: string) => void;
  setError: (value: string) => void;
  logWorkflowStarted: (delegationId: string, turnId: string) => void;
};

export function handleCodexLiveTaskEvent(
  payload: CodexLiveBridgeEvent,
  context: CodexLiveTaskEventContext
) {
  const {
    lessonId,
    setTaskState,
    updateQueueState,
    removePendingTask,
    delegationTurnId,
    onToolStatusUpdate,
    onToolResult,
    setVoiceStatusText,
    setError,
    logWorkflowStarted,
  } = context;
  if (payload.type === "codex_live.queue.updated") {
    updateQueueState(payload);
    return true;
  }
  if (payload.type === "codex_live.workflow.input_pending" && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    setTaskState((current) => ({
      ...current,
      pendingTasks: [
        ...current.pendingTasks.filter((task) => task.delegationId !== delegationId),
        { delegationId, text: payload.text ?? "" },
      ],
    }));
    const label = "检测到运行中的新话语，请选择如何处理";
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label,
      status: "waiting",
    });
    setVoiceStatusText(label);
    return true;
  }
  if (payload.type === "codex_live.workflow.duplicate" && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label: "重复任务已忽略，原任务仍在执行",
      status: "cancelled",
    });
    setVoiceStatusText("重复指令已忽略");
    return true;
  }
  if (payload.type === "codex_live.workflow.queued" && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    removePendingTask(delegationId);
    const label = `任务已排队${payload.position ? ` · 第 ${payload.position} 项` : ""}`;
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label,
      status: "queued",
    });
    setVoiceStatusText(label);
    return true;
  }
  if (payload.type === "codex_live.workflow.started" && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    removePendingTask(delegationId);
    const turnId = delegationTurnId(delegationId);
    const label = "正在理解任务并准备工作流";
    setVoiceStatusText(label);
    onToolStatusUpdate({ lessonId, turnId, delegationId, label, status: "running" });
    logWorkflowStarted(delegationId, turnId);
    return true;
  }
  if (payload.type === "codex_live.workflow.progress" && payload.delegation_id && payload.activity) {
    const delegationId = payload.delegation_id;
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label: payload.activity.label,
      status: payload.activity.status === "failed" ? "error" : "running",
      activity: payload.activity,
    });
    setVoiceStatusText(payload.activity.label);
    return true;
  }
  if (payload.type === "codex_live.workflow.output_delta" && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label: "正在生成工作流结果",
      status: "running",
    });
    return true;
  }
  if (payload.type === "codex_live.workflow.result" && payload.result && payload.delegation_id) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    onToolResult(lessonId, payload.result);
    const succeeded = payload.result.status === "ok" && payload.result.model_output.status === "ok";
    const label = succeeded ? "Chatbot 工作流已完成" : "Chatbot 工作流执行失败";
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label,
      status: succeeded ? "completed" : "error",
    });
    setVoiceStatusText(succeeded ? `${label}，Codex Live 正在回答` : label);
    return true;
  }
  if (
    ["codex_live.workflow.cancelled", "codex_live.workflow.dismissed"].includes(payload.type ?? "") &&
    payload.delegation_id
  ) {
    const delegationId = payload.delegation_id;
    updateQueueState(payload);
    removePendingTask(delegationId);
    onToolStatusUpdate({
      lessonId,
      turnId: delegationTurnId(delegationId),
      delegationId,
      label: payload.reason === "ordinary_chat" ? "已作为普通对话处理" : "任务已取消",
      status: "cancelled",
    });
    return true;
  }
  if (payload.type === "codex_live.workflow.error" || payload.type === "codex_live.error") {
    const message = payload.message || "Codex Live Chatbot 工作流通道发生错误";
    const delegationId = payload.delegation_id;
    onToolStatusUpdate({
      lessonId,
      turnId: delegationId ? delegationTurnId(delegationId) : `realtime-error-${crypto.randomUUID()}`,
      delegationId,
      label: message,
      status: "error",
    });
    setError(message);
    return true;
  }
  return false;
}

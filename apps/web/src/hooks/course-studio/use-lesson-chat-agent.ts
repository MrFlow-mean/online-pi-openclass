"use client";

import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api, isMissingChatStreamFinalError } from "@/lib/api";
import { publicAgentActivityLabel } from "@/lib/agent-activity";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  createChatMessage,
  isBoardDocumentEmpty,
  learningClarityFromCommit,
  nextEditBranchName,
  type ChatMessage,
  type LessonComposerState,
} from "@/components/course-studio/history-utils";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type {
  AgentActivityEvent,
  AIModelSelection,
  BoardDocument,
  BoardDecision,
  BoardTaskRequirementSheet,
  ChatAttachmentRef,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  SelectionRef,
} from "@/types";

type UseLessonChatAgentOptions = {
  activeLesson: Lesson | null;
  activeMessages: ChatMessage[];
  activeComposerState: LessonComposerState;
  composerSelections: SelectionRef[];
  currentBoardDocument: BoardDocument | null;
  selectedTextModel: AIModelSelection;
  textModelReady: boolean;
  isPreviewMode: boolean;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  exitPreviewMode: () => void;
  updateCoursePackage: (
    nextPackage: CoursePackage,
    options?: CoursePackageApplyOptions
  ) => { activeLesson: Lesson | null } | void;
  updateLessonMessages: (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  updateLessonComposerState: (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => void;
  setStreamingDocumentPreview: (lessonId: string, document: BoardDocument) => boolean;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
};

const DEFAULT_LEARNING_REQUIREMENT_FAILURE_REASON = "本轮学习需求没有成功更新，请重试刚才的输入。";

function upsertAgentActivity(
  events: AgentActivityEvent[],
  nextEvent: AgentActivityEvent
): AgentActivityEvent[] {
  const existingIndex = events.findIndex((event) => event.id === nextEvent.id);
  if (existingIndex < 0) {
    return [...events, nextEvent];
  }
  return events.map((event, index) => (index === existingIndex ? nextEvent : event));
}

export function recoveredLearningRequirementFailureReason(commit: CommitRecord | null): string | null {
  const metadata = commit?.metadata;
  if (
    metadata?.learning_requirement_operation_status !== "failed" &&
    metadata?.refinement_route !== "refinement_failed"
  ) {
    return null;
  }
  const failureReason = metadata.learning_requirement_operation_failure_reason;
  return typeof failureReason === "string" && failureReason.trim()
    ? failureReason.trim()
    : DEFAULT_LEARNING_REQUIREMENT_FAILURE_REASON;
}

export function useLessonChatAgent({
  activeLesson,
  activeMessages,
  activeComposerState,
  composerSelections,
  currentBoardDocument,
  selectedTextModel,
  textModelReady,
  isPreviewMode,
  chatRequestInFlightRef,
  flushAutoSave,
  exitPreviewMode,
  updateCoursePackage,
  updateLessonMessages,
  updateLessonComposerState,
  setStreamingDocumentPreview,
  clearSelection,
  setError,
  setBusyAction,
  busyAction,
}: UseLessonChatAgentOptions) {
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [streamedRequirementSheet, setStreamedRequirementSheet] = useState<LearningRequirementSheet | null>(null);
  const [streamedBoardTaskSheet, setStreamedBoardTaskSheet] = useState<BoardTaskRequirementSheet | null>(null);
  const [currentNeedPending, setCurrentNeedPending] = useState(false);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const activeLessonIdRef = useRef<string | null>(activeLesson?.id ?? null);
  const chatAbortControllerRef = useRef<AbortController | null>(null);
  const chatAbortRequestedRef = useRef(false);

  useEffect(() => {
    activeLessonIdRef.current = activeLesson?.id ?? null;
  }, [activeLesson?.id]);

  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const composerAttachments = activeComposerState.composerAttachments;
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit" || busyAction === "chat-edit";

  type ChatTurnBusyAction = "chat" | "agent-edit" | "chat-edit";
  type ChatTurnBeforeRequestResult = {
    lesson?: Lesson | null;
    document?: BoardDocument | null;
  };
  type ChatTurnBeforeRequestContext = {
    lessonId: string;
    pendingMessageId: string;
  };
  type RunChatTurnOptions = {
    lesson: Lesson;
    payload: ChatRequestPayload;
    conversationMessages: ChatMessage[];
    userMessageContent: string;
    submittedSelection: SelectionRef | null;
    busyActionName: ChatTurnBusyAction;
    flushReason: AutoSaveReason;
    clearComposerInput?: boolean;
    restoreComposerInput?: string;
    restoreComposerAttachments?: ChatAttachmentRef[];
    rollbackMessages?: ChatMessage[];
    beforeRequest?: (context: ChatTurnBeforeRequestContext) => Promise<ChatTurnBeforeRequestResult | void>;
    messageListUpdater?: (current: ChatMessage[], userMessage: ChatMessage, pendingAssistant: ChatMessage) => ChatMessage[];
  };

  function updatePendingAssistant(
    lessonId: string,
    messageId: string,
    patch: Partial<Pick<ChatMessage, "agentActivity" | "content" | "statusLabel">>
  ) {
    updateLessonMessages(lessonId, (current) =>
      current.map((message) => (message.id === messageId ? { ...message, ...patch } : message))
    );
  }

  function restoreComposerInputIfUntouched(lessonId: string, value: string) {
    updateLessonComposerState(lessonId, (current) =>
      current.chatInput.length > 0
        ? current
        : {
            ...current,
            chatInput: value,
          }
    );
  }

  function restoreComposerAttachmentsIfUntouched(lessonId: string, value: ChatAttachmentRef[]) {
    updateLessonComposerState(lessonId, (current) =>
      current.composerAttachments.length
        ? current
        : {
            ...current,
            composerAttachments: value,
          }
    );
  }

  function conversationFromMessages(messages: ChatMessage[]) {
    return messages.slice(-8).map(({ role, content }) => ({ role, content }));
  }

  function displayContentForPayload(payload: ChatRequestPayload) {
    if (payload.board_generation_action === "start") {
      return "开始生成板书";
    }
    if (payload.teaching_action === "continue") {
      return "继续讲下一个标题";
    }
    if (payload.teaching_action === "restart") {
      return "从第一个标题重新讲";
    }
    const content = payload.interaction_mode === "direct_edit" ? `直接编辑讲义：${payload.message}` : payload.message;
    if (!payload.attachments?.length) {
      return content;
    }
    return `${content}\n\n附件：${payload.attachments.map((attachment) => attachment.name).join("、")}`;
  }

  function latestCommitFromPackage(coursePackage: CoursePackage, lessonId: string): CommitRecord | null {
    const lesson = coursePackage.lessons.find((item) => item.id === lessonId);
    if (!lesson) {
      return null;
    }
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = branch?.head_commit_id ?? lesson.history_graph.commits[lesson.history_graph.commits.length - 1]?.id;
    return lesson.history_graph.commits.find((commit) => commit.id === commitId) ?? null;
  }

  function lessonFromPackage(coursePackage: CoursePackage, lessonId: string) {
    return coursePackage.lessons.find((item) => item.id === lessonId) ?? null;
  }

  function activeLessonIdForAsyncPackage(
    coursePackage: CoursePackage,
    requestLessonId: string,
    fallbackActiveLessonId?: string | null
  ) {
    const currentActiveLessonId = activeLessonIdRef.current;
    if (
      currentActiveLessonId &&
      currentActiveLessonId !== requestLessonId &&
      coursePackage.workspace_tab_order.includes(currentActiveLessonId)
    ) {
      return currentActiveLessonId;
    }
    return fallbackActiveLessonId;
  }

  function recoveredCommitForTurn(lesson: Lesson, submittedMessage: string, requestStartedAtMs: number) {
    const earliestCommitMs = requestStartedAtMs - 5000;
    const normalizedMessage = submittedMessage.trim();
    return (
      [...lesson.history_graph.commits]
        .reverse()
        .find((commit) => {
          const userMessage = commit.metadata?.user_message;
          if (typeof userMessage !== "string" || userMessage.trim() !== normalizedMessage) {
            return false;
          }
          return new Date(commit.created_at).getTime() >= earliestCommitMs;
        }) ?? null
    );
  }

  function shouldStreamDocumentPreview(payload: ChatRequestPayload, document: BoardDocument | null) {
    if (payload.interaction_mode === "direct_edit") {
      return false;
    }
    return (
      payload.board_generation_action === "start" ||
      isBoardDocumentEmpty(document)
    );
  }

  function resetAgentState(options?: { clearComposerSelection?: boolean }) {
    setClarificationQuestions([]);
    setLearningClarity(null);
    setStreamedRequirementSheet(null);
    setStreamedBoardTaskSheet(null);
    setCurrentNeedPending(false);
    setLatestBoardDecision(null);
    if (options?.clearComposerSelection !== false) {
      clearSelection();
    }
  }

  async function runChatTurn({
    lesson,
    payload,
    conversationMessages,
    userMessageContent,
    submittedSelection,
    busyActionName,
    flushReason,
    clearComposerInput = false,
    restoreComposerInput,
    restoreComposerAttachments,
    rollbackMessages,
    beforeRequest,
    messageListUpdater,
  }: RunChatTurnOptions) {
    if (!textModelReady) {
      return;
    }
    const lessonId = lesson.id;
    const payloadWithConversation: ChatRequestPayload = {
      ...payload,
      post_generation_action: payload.post_generation_action ?? "auto_explain",
      text_model: payload.text_model ?? selectedTextModel,
      conversation: payload.conversation ?? conversationFromMessages(conversationMessages),
    };

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    const userMessage = createChatMessage("user", userMessageContent, "ready", undefined, submittedSelection);
    const pendingAssistantMessage: ChatMessage = {
      ...createChatMessage("assistant", "", "pending"),
      statusLabel: submittedSelection?.kind === "source" ? "正在解析资料范围" : "正在保存当前文档",
    };
    let requestStarted = false;
    let streamedChatContent = "";
    let streamedDocumentText = "";
    let streamedDocumentPreviewFrame: number | null = null;
    let streamedAgentActivity: AgentActivityEvent[] = [];
    let sawReadyForBoardRequirementUpdate = false;
    let requestLesson = lesson;
    let baseStreamingDocument = currentBoardDocument ?? lesson.board_document;
    let requestStartedAtMs = Date.now();
    const abortController = new AbortController();
    let canStreamDocumentPreview = false;

    function flushStreamingDocumentPreview() {
      if (!canStreamDocumentPreview || !streamedDocumentText) {
        return;
      }
      setStreamingDocumentPreview(requestLesson.id, {
        ...baseStreamingDocument,
        content_json: {},
        content_html: streamingMarkdownToHtml(streamedDocumentText),
        content_text: streamedDocumentText,
      });
    }

    function scheduleStreamingDocumentPreview() {
      if (!canStreamDocumentPreview) {
        return;
      }
      if (streamedDocumentPreviewFrame !== null) {
        return;
      }
      streamedDocumentPreviewFrame = window.requestAnimationFrame(() => {
        streamedDocumentPreviewFrame = null;
        flushStreamingDocumentPreview();
      });
    }

    function clearStreamingDocumentPreviewFrame() {
      if (streamedDocumentPreviewFrame === null) {
        return;
      }
      window.cancelAnimationFrame(streamedDocumentPreviewFrame);
      streamedDocumentPreviewFrame = null;
    }

    function finishCancelledTurn() {
      clearStreamingDocumentPreviewFrame();
      const stoppedContent = streamedChatContent.trim();
      updateLessonMessages(lessonId, (current) =>
        current
          .map((message) =>
            message.id === pendingAssistantMessage.id
              ? {
                  ...message,
                  content: streamedChatContent,
                  agentActivity: streamedAgentActivity,
                  status: "ready" as const,
                  statusLabel: undefined,
                }
              : message
          )
          .filter((message) => message.id !== pendingAssistantMessage.id || Boolean(stoppedContent))
      );
      setCurrentNeedPending(false);
      setError(null);
    }

    chatAbortRequestedRef.current = false;
    chatAbortControllerRef.current = abortController;
    chatRequestInFlightRef.current = true;
    setBusyAction(busyActionName);
    setError(null);
    if (!isBoardDocumentEmpty(currentBoardDocument ?? lesson.board_document)) {
      setLearningClarity(null);
      setStreamedRequirementSheet(null);
      setStreamedBoardTaskSheet(null);
      setCurrentNeedPending(true);
    }
    if (clearComposerInput) {
      updateLessonComposerState(lessonId, (current) => ({
        ...current,
        chatInput: "",
        composerAttachments: [],
      }));
    }
    updateLessonMessages(lessonId, (current) =>
      messageListUpdater
        ? messageListUpdater(current, userMessage, pendingAssistantMessage)
        : [...current, userMessage, pendingAssistantMessage]
    );

    try {
      if (!(await flushAutoSave(flushReason))) {
        if (rollbackMessages) {
          updateLessonMessages(lessonId, () => rollbackMessages);
        } else {
          updateLessonMessages(lessonId, (current) =>
            current.filter((message) => message.id !== pendingAssistantMessage.id && message.id !== userMessage.id)
          );
        }
        if (restoreComposerInput !== undefined) {
          restoreComposerInputIfUntouched(lessonId, restoreComposerInput);
        }
        setCurrentNeedPending(false);
        return;
      }
      const beforeRequestResult = await beforeRequest?.({
        lessonId,
        pendingMessageId: pendingAssistantMessage.id,
      });
      if (beforeRequestResult?.lesson) {
        requestLesson = beforeRequestResult.lesson;
      }
      if (beforeRequestResult?.document !== undefined) {
        baseStreamingDocument = beforeRequestResult.document ?? baseStreamingDocument;
      } else if (beforeRequestResult?.lesson) {
        baseStreamingDocument = beforeRequestResult.lesson.board_document;
      }
      canStreamDocumentPreview = shouldStreamDocumentPreview(payloadWithConversation, baseStreamingDocument);
      if (abortController.signal.aborted) {
        finishCancelledTurn();
        return;
      }
      requestStarted = true;
      requestStartedAtMs = Date.now();
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "正在回复" });
      const response = await api.streamChatOnLesson(
        requestLesson.id,
        payloadWithConversation,
        {
          onPhase(label) {
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: label });
          },
          onAgentActivity(event) {
            streamedAgentActivity = upsertAgentActivity(streamedAgentActivity, event);
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
              agentActivity: streamedAgentActivity,
              statusLabel: publicAgentActivityLabel(event.label),
            });
          },
          onChatDelta(delta) {
            streamedChatContent += delta;
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
              content: streamedChatContent,
              agentActivity: streamedAgentActivity,
              statusLabel:
                submittedSelection?.kind === "source" && payloadWithConversation.post_generation_action === "auto_explain"
                  ? "正在从第一个标题开始讲解"
                  : "正在回复",
            });
          },
          onDocumentDelta(delta) {
            if (!canStreamDocumentPreview) {
              return;
            }
            streamedDocumentText += delta;
            if (submittedSelection?.kind === "source") {
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "正在生成板书" });
            }
            scheduleStreamingDocumentPreview();
          },
          onRequirementUpdate(payload) {
            if (payload.learning_clarification?.ready_for_board) {
              sawReadyForBoardRequirementUpdate = true;
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
                statusLabel:
                  payload.requirement_phase === "frozen" ? "正在生成板书" : "学习需求已确认",
              });
            }
            setCurrentNeedPending(false);
            setClarificationQuestions(payload.clarification_questions);
            setLearningClarity(payload.learning_clarification);
            setStreamedRequirementSheet(payload.active_requirement_sheet ?? payload.learning_requirement_sheet);
            if (submittedSelection?.kind === "source") {
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "资料范围已定位" });
            }
          },
          onBoardTaskUpdate(payload) {
            setCurrentNeedPending(false);
            setStreamedRequirementSheet(null);
            setLearningClarity(null);
            setClarificationQuestions([]);
            setStreamedBoardTaskSheet(payload.active_board_task_sheet ?? payload.board_task_sheet);
          },
        },
        { signal: abortController.signal }
      );
      clearStreamingDocumentPreviewFrame();
      flushStreamingDocumentPreview();
      const failedStreamingDocumentPreview =
        canStreamDocumentPreview &&
        streamedDocumentText.trim() &&
        response.board_document_operation_status === "failed"
          ? {
              ...baseStreamingDocument,
              content_json: {},
              content_html: streamingMarkdownToHtml(streamedDocumentText),
              content_text: streamedDocumentText,
            }
          : null;
      const responseCommit = latestCommitFromPackage(response.course_package, requestLesson.id);
      const committedUserMessage: ChatMessage = responseCommit
        ? {
            ...userMessage,
            id: `${responseCommit.id}:user`,
            commitId: responseCommit.id,
            parentCommitIds: responseCommit.parent_ids,
            editableContent: payloadWithConversation.message,
            interactionMode: payloadWithConversation.interaction_mode ?? "ask",
            editedFromCommitId: payloadWithConversation.chat_edit_source_commit_id ?? null,
          }
        : userMessage;
      updateCoursePackage(response.course_package, {
        activeLessonId: activeLessonIdForAsyncPackage(
          response.course_package,
          requestLesson.id,
          requestLesson.id
        ),
      });
      if (failedStreamingDocumentPreview) {
        setStreamingDocumentPreview(requestLesson.id, failedStreamingDocumentPreview);
      }
      if (response.board_document_operation_status === "failed") {
        setError(response.board_document_operation_failure_reason ?? "右侧文档生成失败，请重试。");
      }
      if (response.learning_requirement_operation_status === "failed") {
        setError(
          response.learning_requirement_operation_failure_reason ??
            "本轮学习需求没有成功更新，请重试。"
        );
      }
      if (response.auto_teaching_operation_status === "failed") {
        setError("板书已生成，但自动讲解未完成；可以发送“从第一个标题重新讲”重试。\n" +
          (response.auto_teaching_operation_failure_reason ?? ""));
      }
      setLatestBoardDecision(response.board_decision);
      setCurrentNeedPending(false);
      setClarificationQuestions(response.clarification_questions);
      setLearningClarity(response.learning_clarification);
      const nextBoardTaskSheet = response.active_board_task_sheet ?? response.board_task_sheet ?? null;
      setStreamedRequirementSheet(
        response.requirement_cleared || nextBoardTaskSheet
          ? null
          : response.active_requirement_sheet ?? response.learning_requirement_sheet
      );
      setStreamedBoardTaskSheet(nextBoardTaskSheet);
      const chatbotMessage = response.chatbot_message.trim();
      const streamedFallbackMessage = streamedChatContent.trim();
      const finalAgentActivity = response.agent_activity?.length ? response.agent_activity : streamedAgentActivity;
      const assistantMessages: ChatMessage[] = [];
      if (chatbotMessage) {
        assistantMessages.push(
          createChatMessage(
            "assistant",
            chatbotMessage,
            "ready",
            responseCommit ? `${responseCommit.id}:assistant` : undefined,
            null,
            response.teaching_progress ?? null,
            responseCommit
              ? {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                }
          )
        );
      } else if (streamedFallbackMessage) {
        assistantMessages.push(
          createChatMessage(
            "assistant",
            streamedFallbackMessage,
            "ready",
            responseCommit ? `${responseCommit.id}:assistant` : undefined,
            null,
            response.teaching_progress ?? null,
            responseCommit
              ? {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                }
          )
        );
      }
      updateLessonMessages(lessonId, (current) => [
        ...current
          .map((message) => (message.id === userMessage.id ? committedUserMessage : message))
          .filter((message) => message.id !== pendingAssistantMessage.id),
        ...assistantMessages,
      ]);
      clearSelection();
    } catch (chatError) {
      if (abortController.signal.aborted && chatAbortRequestedRef.current) {
        finishCancelledTurn();
        return;
      }
      const rawErrorMessage = chatError instanceof Error ? chatError.message : "聊天失败";
      const isTransientNetworkError =
        rawErrorMessage.toLowerCase().includes("network error") ||
        rawErrorMessage.toLowerCase().includes("failed to fetch");
      const userFacingError =
        payloadWithConversation.board_generation_action === "start" && isTransientNetworkError
          ? "板书生成连接中断，可以再次点击“开始生成板书”重试；已确认的学习需求会保留。"
          : sawReadyForBoardRequirementUpdate && isTransientNetworkError
            ? "学习需求已确认，但板书生成连接中断；可以点击“开始生成板书”继续。"
            : rawErrorMessage;
      if (isMissingChatStreamFinalError(chatError)) {
        try {
          const refreshedPackage = await api.getCoursePackage();
          const refreshedLesson = lessonFromPackage(refreshedPackage, requestLesson.id);
          const recoveredCommit =
            refreshedLesson !== null
              ? recoveredCommitForTurn(refreshedLesson, payloadWithConversation.message, requestStartedAtMs)
              : null;
          updateCoursePackage(refreshedPackage, {
            activeLessonId: activeLessonIdForAsyncPackage(refreshedPackage, requestLesson.id, requestLesson.id),
            rebuildMessageLessonIds: recoveredCommit ? [requestLesson.id] : undefined,
          });
          if (refreshedLesson) {
            setStreamedRequirementSheet(
              recoveredCommit || refreshedLesson.board_task_requirements
                ? null
                : refreshedLesson.learning_requirements ?? null
            );
            setStreamedBoardTaskSheet(refreshedLesson.board_task_requirements ?? null);
            setLearningClarity(recoveredCommit ? learningClarityFromCommit(recoveredCommit) : null);
            setClarificationQuestions([]);
          }
          setCurrentNeedPending(false);
          if (recoveredCommit) {
            setError(recoveredLearningRequirementFailureReason(recoveredCommit));
            return;
          }
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          setError("聊天连接在最终结果返回前中断，本轮没有写入历史；可以重试。");
          return;
        } catch (refreshError) {
          const refreshMessage = refreshError instanceof Error ? refreshError.message : "刷新失败";
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          setError(`${rawErrorMessage}；刷新最新历史失败：${refreshMessage}`);
          setCurrentNeedPending(false);
          return;
        }
      }
      if (restoreComposerInput !== undefined && !sawReadyForBoardRequirementUpdate) {
        restoreComposerInputIfUntouched(lessonId, restoreComposerInput);
      }
      if (restoreComposerAttachments?.length && !sawReadyForBoardRequirementUpdate) {
        restoreComposerAttachmentsIfUntouched(lessonId, restoreComposerAttachments);
      }
      if (!requestStarted && rollbackMessages) {
        updateLessonMessages(lessonId, () => rollbackMessages);
      } else {
        updateLessonMessages(lessonId, (current) =>
          current.filter(
            (message) =>
              message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
          )
        );
      }
      setError(userFacingError);
      setCurrentNeedPending(false);
    } finally {
      clearStreamingDocumentPreviewFrame();
      if (chatAbortControllerRef.current === abortController) {
        chatAbortControllerRef.current = null;
      }
      chatAbortRequestedRef.current = false;
      chatRequestInFlightRef.current = false;
      setBusyAction(null);
    }
  }

  function handleStopChat() {
    if (!chatRequestInFlightRef.current || !chatAbortControllerRef.current) {
      return;
    }
    chatAbortRequestedRef.current = true;
    chatAbortControllerRef.current.abort();
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload) {
    if (!textModelReady || !activeLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    if (isPreviewMode) {
      exitPreviewMode();
    }
    const submittedInput = chatInput;
    const submittedAttachments = composerAttachments;
    const includedSelections = includeSelectionInPrompt ? composerSelections : [];
    const payload =
      payloadOverride ??
      ({
        message:
          chatInput.trim() ||
          (composerAttachments.length
            ? "请查看我添加的附件。"
            : includedSelections.length
              ? "请结合我引用的内容回答。"
              : ""),
        selection: includedSelections[includedSelections.length - 1] ?? null,
        selections: includedSelections,
        attachments: composerAttachments,
        interaction_mode: composerMode,
      } satisfies ChatRequestPayload);
    const submittedSelection = payload.selection ?? payload.selections?.at(-1) ?? null;
    const payloadMessage = payload.message.trim();
    if (!payloadMessage) {
      return;
    }
    const payloadForTurn = { ...payload, message: payloadMessage };
    const isBoardGenerationControl = payloadForTurn.board_generation_action === "start";

    await runChatTurn({
      lesson: activeLesson,
      payload: payloadForTurn,
      conversationMessages: activeMessages,
      userMessageContent: displayContentForPayload(payloadForTurn),
      submittedSelection,
      busyActionName: payloadForTurn.interaction_mode === "direct_edit" ? "agent-edit" : "chat",
      flushReason: "chat",
      clearComposerInput: !payloadOverride || isBoardGenerationControl,
      restoreComposerInput: payloadOverride || isBoardGenerationControl ? undefined : submittedInput,
      restoreComposerAttachments: payloadOverride || isBoardGenerationControl ? undefined : submittedAttachments,
    });
  }

  async function handleEditMessage(sourceMessage: ChatMessage, nextContent: string) {
    if (!textModelReady || !activeLesson || chatRequestInFlightRef.current || isChatBusy || isPreviewMode) {
      return;
    }
    const editedMessage = nextContent.trim();
    const sourceCommitId = sourceMessage.commitId;
    const baseCommitId = sourceMessage.parentCommitIds?.[0];
    if (!sourceCommitId || !baseCommitId || !editedMessage) {
      setError("这条消息缺少可分叉的历史版本");
      return;
    }
    const sourceIndex = activeMessages.findIndex((message) => message.id === sourceMessage.id);
    if (sourceIndex < 0) {
      setError("没有找到要编辑的历史消息");
      return;
    }
    const originalMessage = sourceMessage.editableContent ?? sourceMessage.content;
    if (editedMessage === originalMessage.trim()) {
      return;
    }
    const prefixMessages = activeMessages.slice(0, sourceIndex);
    const rollbackMessages = activeMessages;
    const payload: ChatRequestPayload = {
      message: editedMessage,
      selection: sourceMessage.selection ?? null,
      interaction_mode: sourceMessage.interactionMode ?? "ask",
      chat_edit_source_commit_id: sourceCommitId,
      chat_edit_base_commit_id: baseCommitId,
      chat_edit_original_message: originalMessage,
    };

    await runChatTurn({
      lesson: activeLesson,
      payload,
      conversationMessages: prefixMessages,
      userMessageContent: editedMessage,
      submittedSelection: sourceMessage.selection ?? null,
      busyActionName: "chat-edit",
      flushReason: "chat",
      rollbackMessages,
      messageListUpdater: (_current, userMessage, pendingAssistant) => [
        ...prefixMessages,
        userMessage,
        pendingAssistant,
      ],
      beforeRequest: async ({ lessonId, pendingMessageId }) => {
        updatePendingAssistant(lessonId, pendingMessageId, { statusLabel: "正在创建新链路" });
        const branchName = nextEditBranchName(activeLesson);
        const branchedPackage = await api.createBranch(activeLesson.id, branchName, baseCommitId);
        const applied = updateCoursePackage(branchedPackage, {
          activeLessonId: activeLesson.id,
        });
        const branchedLesson =
          applied?.activeLesson ?? branchedPackage.lessons.find((lesson) => lesson.id === activeLesson.id) ?? null;
        return {
          lesson: branchedLesson,
          document: branchedLesson?.board_document ?? null,
        };
      },
    });
  }

  async function handleContinueTeaching() {
    if (!textModelReady || !activeLesson) {
      return;
    }
    await handleSubmitChat({
      message: "继续下一项",
      interaction_mode: "ask",
      teaching_action: "continue",
    });
  }

  return {
    chatInput,
    composerMode,
    includeSelectionInPrompt,
    isChatBusy,
    clarificationQuestions,
    learningClarity,
    streamedRequirementSheet,
    streamedBoardTaskSheet,
    currentNeedPending,
    latestBoardDecision,
    resetAgentState,
    handleSubmitChat,
    handleStopChat,
    handleEditMessage,
    handleContinueTeaching,
  };
}

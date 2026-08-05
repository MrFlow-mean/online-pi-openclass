"use client";

import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api, isMissingChatStreamFinalError } from "@/lib/api";
import { publicAgentActivityLabel } from "@/lib/agent-activity";
import { createTextChatSessionId, freezeTextChatTurnIdentity } from "@/lib/chat-turn-identity";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  activeLessonIdForAsyncPackage,
  resolvedBoardFocusForTurn,
} from "@/hooks/course-studio/chat-turn-ui-state";
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
  BoardFocusRef,
  BoardTaskRequirementSheet,
  ChatAttachmentRef,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  SelectionRef,
  SourceQueryScope,
} from "@/types";

type UseLessonChatAgentOptions = {
  activeLesson: Lesson | null;
  activeMessages: ChatMessage[];
  activeComposerState: LessonComposerState;
  composerSelections: SelectionRef[];
  sourceQueryScope: SourceQueryScope | null;
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
  onTransientBoardFocusChange: (lessonId: string, focus: BoardFocusRef | null) => void;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
};

const DEFAULT_LEARNING_REQUIREMENT_FAILURE_REASON = "This round of learning requirements was not updated successfully, please try again the input just now.";
const DEFAULT_BOARD_GENERATION_FAILURE_REASON =
  "Board generation did not finish. The confirmed learning requirements were retained and can be retried.";
const INTERRUPTED_CHAT_RECOVERY_DELAYS_MS = [
  0,
  500,
  1000,
  2000,
  4000,
  8000,
  15000,
  30000,
  30000,
] as const;

function waitForInterruptedChatRecovery(delayMs: number) {
  if (delayMs <= 0) {
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => window.setTimeout(resolve, delayMs));
}

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
  if (metadata?.kind === "learning_requirement_generation_failed") {
    const generationFailureReason = metadata.generation_failure_reason;
    return typeof generationFailureReason === "string" && generationFailureReason.trim()
      ? generationFailureReason.trim()
      : DEFAULT_BOARD_GENERATION_FAILURE_REASON;
  }
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
  sourceQueryScope,
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
  onTransientBoardFocusChange,
  clearSelection,
  setError,
  setBusyAction,
  busyAction,
}: UseLessonChatAgentOptions) {
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [streamedRequirementSheet, setStreamedRequirementSheet] = useState<LearningRequirementSheet | null>(null);
  const [streamedBoardTaskSheet, setStreamedBoardTaskSheet] = useState<BoardTaskRequirementSheet | null>(null);
  const currentNeedPending = false;
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const activeLessonIdRef = useRef<string | null>(activeLesson?.id ?? null);
  const chatAbortControllerRef = useRef<AbortController | null>(null);
  const chatAbortRequestedRef = useRef(false);
  const activeChatCancellationRef = useRef<{
    lessonId: string;
    sessionId: string;
    inputEventId: string;
  } | null>(null);
  const textChatSessionIdsRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    activeLessonIdRef.current = activeLesson?.id ?? null;
  }, [activeLesson?.id]);

  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const composerAttachments = activeComposerState.composerAttachments;
  const sourceQuerySelections = activeComposerState.sourceQuerySelections ?? [];
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
    submittedSourceSelections?: SelectionRef[];
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

  function clearSelectionForLesson(lessonId: string) {
    updateLessonComposerState(lessonId, (current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: null,
      composerSelections: [],
      sourceQuerySelections: [],
      sourceQueryAllReady: false,
    }));
    if (activeLessonIdRef.current === lessonId) {
      clearSelection();
    }
  }

  function conversationFromMessages(messages: ChatMessage[]) {
    return messages.slice(-8).map(({ role, content }) => ({ role, content }));
  }

  function displayContentForPayload(payload: ChatRequestPayload) {
    if (payload.board_generation_action === "start") {
      return "Start generating board content";
    }
    if (payload.teaching_action === "continue") {
      return "Continue to the next title";
    }
    if (payload.teaching_action === "restart") {
      return "Retelling from the first title";
    }
    const content = payload.interaction_mode === "direct_edit" ? `Direct lesson edit: ${payload.message}` : payload.message;
    if (!payload.attachments?.length) {
      return content;
    }
    return `${content}\n\nAttachments: ${payload.attachments.map((attachment) => attachment.name).join(", ")}`;
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
    submittedSourceSelections = [],
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
    let textChatSessionId = textChatSessionIdsRef.current.get(lessonId);
    if (!textChatSessionId) {
      textChatSessionId = createTextChatSessionId();
      textChatSessionIdsRef.current.set(lessonId, textChatSessionId);
    }
    const identifiedPayload = freezeTextChatTurnIdentity(payload, textChatSessionId);
    const payloadWithConversation: ChatRequestPayload = {
      ...identifiedPayload,
      post_generation_action: identifiedPayload.post_generation_action ?? "stop_after_generation",
      text_model: identifiedPayload.text_model ?? selectedTextModel,
      conversation: identifiedPayload.conversation ?? conversationFromMessages(conversationMessages),
    };
    const hasSubmittedSourceScope = Boolean(payloadWithConversation.source_query_scope);

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    const userMessage = createChatMessage(
      "user",
      userMessageContent,
      "ready",
      undefined,
      submittedSelection,
      null,
      { sourceSelections: submittedSourceSelections }
    );
    const pendingAssistantMessage: ChatMessage = {
      ...createChatMessage("assistant", "", "pending"),
      statusLabel: payloadWithConversation.source_query_scope ? "Retrieving original information" : "Saving current document",
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
    const isRequestLessonActive = () => activeLessonIdRef.current === lessonId;

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
      if (isRequestLessonActive()) {
        setError(
          payloadWithConversation.board_generation_action === "start" || sawReadyForBoardRequirementUpdate
            ? "Board generation was stopped. Confirmed learning requirements were retained and can be retried."
            : null
        );
      }
    }

    chatAbortRequestedRef.current = false;
    chatAbortControllerRef.current = abortController;
    chatRequestInFlightRef.current = true;
    setBusyAction(busyActionName);
    setError(null);
    onTransientBoardFocusChange(lessonId, null);
    if (!isBoardDocumentEmpty(currentBoardDocument ?? lesson.board_document)) {
      setLearningClarity(null);
      setStreamedRequirementSheet(null);
      setStreamedBoardTaskSheet(null);
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
        return;
      }
      if (payloadWithConversation.source_query_scope) {
        clearSelectionForLesson(lessonId);
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
      activeChatCancellationRef.current = {
        lessonId: requestLesson.id,
        sessionId: identifiedPayload.session_id,
        inputEventId: identifiedPayload.input_event_id,
      };
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "Replying" });
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
                hasSubmittedSourceScope &&
                sawReadyForBoardRequirementUpdate &&
                Boolean(streamedDocumentText.trim())
                  ? "Starting from the first title"
                  : "Replying",
            });
          },
          onDocumentDelta(delta) {
            if (!canStreamDocumentPreview) {
              return;
            }
            streamedDocumentText += delta;
            if (hasSubmittedSourceScope) {
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "Generating board content" });
            }
            scheduleStreamingDocumentPreview();
          },
          onRequirementUpdate(payload) {
            if (payload.learning_clarification?.ready_for_board) {
              sawReadyForBoardRequirementUpdate = true;
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
                statusLabel:
                  payload.requirement_phase === "frozen" ? "Generating board content" : "Learning needs confirmed",
              });
            }
            if (isRequestLessonActive()) {
              setClarificationQuestions(payload.clarification_questions);
              setLearningClarity(payload.learning_clarification);
              setStreamedRequirementSheet(payload.active_requirement_sheet ?? payload.learning_requirement_sheet);
            }
            if (hasSubmittedSourceScope) {
              updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "Data range has been located" });
            }
          },
          onBoardTaskUpdate(payload) {
            const nextTask = payload.active_board_task_sheet ?? payload.board_task_sheet;
            if (isRequestLessonActive()) {
              setStreamedRequirementSheet(null);
              setLearningClarity(null);
              setClarificationQuestions([]);
              setStreamedBoardTaskSheet(nextTask);
              onTransientBoardFocusChange(
                lessonId,
                resolvedBoardFocusForTurn("learning_need", nextTask)
              );
            }
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
        mergeLessonId: requestLesson.id,
        activeLessonId: activeLessonIdForAsyncPackage(
          response.course_package,
          requestLesson.id,
          activeLessonIdRef.current,
          response.course_package.active_lesson_id ?? requestLesson.id
        ),
      });
      if (failedStreamingDocumentPreview) {
        setStreamingDocumentPreview(requestLesson.id, failedStreamingDocumentPreview);
      }
      if (isRequestLessonActive() && response.board_document_operation_status === "failed") {
        setError(response.board_document_operation_failure_reason ?? "The document generation on the right failed, please try again.");
      }
      if (isRequestLessonActive() && response.learning_requirement_operation_status === "failed") {
        setError(
          response.learning_requirement_operation_failure_reason ??
            "This round of learning requirements was not updated successfully, please try again."
        );
      }
      if (isRequestLessonActive() && response.auto_teaching_operation_status === "failed") {
        setError("The board content has been generated, but the automatic explanation has not been completed; you can send \"Restart from the first title\" to try again." +
          (response.auto_teaching_operation_failure_reason ?? ""));
      }
      const nextBoardTaskSheet = response.active_board_task_sheet ?? response.board_task_sheet ?? null;
      if (isRequestLessonActive()) {
        setLatestBoardDecision(response.board_decision);
        setClarificationQuestions(response.clarification_questions);
        setLearningClarity(response.learning_clarification);
        setStreamedRequirementSheet(
          response.requirement_cleared || nextBoardTaskSheet
            ? null
            : response.active_requirement_sheet ?? response.learning_requirement_sheet
        );
        setStreamedBoardTaskSheet(nextBoardTaskSheet);
        onTransientBoardFocusChange(
          lessonId,
          resolvedBoardFocusForTurn(response.turn_decision?.intent, nextBoardTaskSheet)
        );
      }
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
                  sourceCitations: response.source_citations ?? [],
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                  sourceCitations: response.source_citations ?? [],
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
                  sourceCitations: response.source_citations ?? [],
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  guidedRequirementDiscovery: response.guided_requirement_discovery ?? null,
                  followUpSuggestions: response.follow_up_suggestions ?? [],
                  sourceCitations: response.source_citations ?? [],
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
      clearSelectionForLesson(lessonId);
    } catch (chatError) {
      if (abortController.signal.aborted && chatAbortRequestedRef.current) {
        finishCancelledTurn();
        return;
      }
      const rawErrorMessage = chatError instanceof Error ? chatError.message : "Chat failed";
      const isTransientNetworkError =
        rawErrorMessage.toLowerCase().includes("network error") ||
        rawErrorMessage.toLowerCase().includes("failed to fetch");
      const userFacingError =
        payloadWithConversation.board_generation_action === "start" && isTransientNetworkError
          ? "The board generation connection was interrupted. Select \"Start generating board\" to retry; your confirmed learning needs will be retained."
          : sawReadyForBoardRequirementUpdate && isTransientNetworkError
            ? "Your learning needs are confirmed, but board generation was interrupted. Select \"Start generating board\" to continue."
            : rawErrorMessage;
      if (isMissingChatStreamFinalError(chatError) || isTransientNetworkError) {
        try {
          updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
            statusLabel: "Restoring saved result",
          });
          let refreshedPackage: CoursePackage | null = null;
          let refreshedLesson: Lesson | null = null;
          let recoveredCommit: CommitRecord | null = null;
          let turnStatus: "running" | "finished" | null = null;
          let lastRefreshError: unknown = null;
          for (const delayMs of INTERRUPTED_CHAT_RECOVERY_DELAYS_MS) {
            await waitForInterruptedChatRecovery(delayMs);
            if (abortController.signal.aborted && chatAbortRequestedRef.current) {
              finishCancelledTurn();
              return;
            }
            try {
              const status = await api.getChatTurnStatus(requestLesson.id, {
                session_id: identifiedPayload.session_id,
                input_event_id: identifiedPayload.input_event_id,
              });
              turnStatus = status.status;
            } catch (statusError) {
              lastRefreshError = statusError;
            }
            try {
              const candidatePackage = await api.getCoursePackage();
              const candidateLesson = lessonFromPackage(candidatePackage, requestLesson.id);
              const candidateCommit =
                candidateLesson !== null
                  ? recoveredCommitForTurn(
                      candidateLesson,
                      payloadWithConversation.message,
                      requestStartedAtMs
                    )
                  : null;
              refreshedPackage = candidatePackage;
              refreshedLesson = candidateLesson;
              recoveredCommit = candidateCommit;
            } catch (refreshAttemptError) {
              lastRefreshError = refreshAttemptError;
            }
            if (recoveredCommit || (turnStatus === "finished" && refreshedPackage)) {
              break;
            }
          }
          if (!refreshedPackage) {
            throw lastRefreshError ?? new Error("Refresh failed");
          }
          updateCoursePackage(refreshedPackage, {
            mergeLessonId: requestLesson.id,
            activeLessonId: activeLessonIdForAsyncPackage(
              refreshedPackage,
              requestLesson.id,
              activeLessonIdRef.current,
              refreshedPackage.active_lesson_id ?? requestLesson.id
            ),
            rebuildMessageLessonIds: recoveredCommit ? [requestLesson.id] : undefined,
          });
          if (refreshedLesson && isRequestLessonActive()) {
            setStreamedRequirementSheet(
              recoveredCommit || refreshedLesson.board_task_requirements
                ? null
                : refreshedLesson.learning_requirements ?? null
            );
            setStreamedBoardTaskSheet(refreshedLesson.board_task_requirements ?? null);
            setLearningClarity(recoveredCommit ? learningClarityFromCommit(recoveredCommit) : null);
            setClarificationQuestions([]);
          }
          if (recoveredCommit) {
            if (isRequestLessonActive()) {
              setError(recoveredLearningRequirementFailureReason(recoveredCommit));
            }
            return;
          }
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          if (isRequestLessonActive()) {
            setError(
              turnStatus === "running"
                ? "The chat connection was interrupted, but generation is still running in the background. Refresh later to load the saved result."
                : "The chat connection was interrupted before the final result was returned, and no history was written this round; you can try again."
            );
          }
          return;
        } catch (refreshError) {
          const refreshMessage = refreshError instanceof Error ? refreshError.message : "Refresh failed";
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          if (isRequestLessonActive()) {
            setError(`${rawErrorMessage}; could not refresh the latest history: ${refreshMessage}`);
          }
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
      if (isRequestLessonActive()) {
        setError(userFacingError);
      }
    } finally {
      clearStreamingDocumentPreviewFrame();
      if (chatAbortControllerRef.current === abortController) {
        chatAbortControllerRef.current = null;
      }
      if (activeChatCancellationRef.current?.inputEventId === identifiedPayload.input_event_id) {
        activeChatCancellationRef.current = null;
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
    const activeCancellation = activeChatCancellationRef.current;
    if (activeCancellation) {
      void api
        .cancelChatOnLesson(activeCancellation.lessonId, {
          session_id: activeCancellation.sessionId,
          input_event_id: activeCancellation.inputEventId,
        })
        .catch(() => undefined);
    }
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
    const basePayload =
      payloadOverride ??
      ({
        message:
          chatInput.trim() ||
          (composerAttachments.length
            ? "Please see the attachment I added."
            : includedSelections.length
              ? "Please answer based on what I quoted."
              : ""),
        selection: includedSelections[includedSelections.length - 1] ?? null,
        selections: includedSelections,
        attachments: composerAttachments,
        interaction_mode: composerMode,
      } satisfies ChatRequestPayload);
    const payload: ChatRequestPayload =
      sourceQueryScope &&
      basePayload.source_query_scope === undefined &&
      basePayload.interaction_mode !== "direct_edit" &&
      !basePayload.board_generation_action
        ? { ...basePayload, source_query_scope: sourceQueryScope }
        : basePayload;
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
      submittedSourceSelections:
        payloadForTurn.source_query_scope?.mode === "all_ready_sources"
          ? [{ kind: "source", excerpt: "当前课程全部可用资料", source_scope_kind: "source" }]
          : payloadForTurn.source_query_scope
            ? sourceQuerySelections
            : [],
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
      setError("This message lacks a forkable history");
      return;
    }
    const sourceIndex = activeMessages.findIndex((message) => message.id === sourceMessage.id);
    if (sourceIndex < 0) {
      setError("No historical message found for editing");
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
        updatePendingAssistant(lessonId, pendingMessageId, { statusLabel: "Creating new link" });
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
      message: "Continue to next item",
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

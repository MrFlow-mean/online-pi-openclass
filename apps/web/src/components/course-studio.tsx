"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { BookOpen, Plus } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BoardEditorPanel } from "@/components/course-studio/board-editor-panel";
import { CourseStudioChatSidebar } from "@/components/course-studio/chat-sidebar";
import { CourseStudioPageShell } from "@/components/course-studio/course-studio-page-shell";
import type { FormulaInkEditorSubmitPayload } from "@/components/course-studio/word-board-editor";
import {
  appendComposerSelection,
  buildLessonMessagesFromHistory,
  learningClarityFromCommit,
} from "@/components/course-studio/history-utils";
import {
  samePopoverPosition,
  sameSelection,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import { LessonTabs } from "@/components/course-studio/lesson-tabs";
import { SelectionPopover } from "@/components/course-studio/selection-popover";
import {
  appendSourceQuerySelection,
  sourceQueryScopeFromComposer,
} from "@/components/course-studio/source-query-scope";
import { CourseStudioSidePanel, type CourseStudioSidebarTab } from "@/components/course-studio/studio-side-panel";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { useBoardDraft } from "@/hooks/course-studio/use-board-draft";
import { useChatSpeech } from "@/hooks/course-studio/use-chat-speech";
import { useCourseWorkspace, type CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import { useLessonChatAgent } from "@/hooks/course-studio/use-lesson-chat-agent";
import { useLessonHistory } from "@/hooks/course-studio/use-lesson-history";
import { useLessonMerge } from "@/hooks/course-studio/use-lesson-merge";
import { useLessonPackage } from "@/hooks/course-studio/use-lesson-package";
import { useModelCatalog } from "@/hooks/course-studio/use-model-catalog";
import {
  applyRealtimeToolStatusUpdate,
  applyRealtimeTranscriptUpdate,
  type RealtimeTranscriptUpdate,
} from "@/components/course-studio/realtime-message-state";
import type { RealtimeToolStatusUpdate } from "@/hooks/course-studio/codex-live-task-ui";
import { useRealtimeVoice } from "@/hooks/course-studio/use-realtime-voice";
import { useWorkspaceActions } from "@/hooks/course-studio/use-workspace-actions";
import { useResizablePanelWidth } from "@/hooks/use-resizable-panel-width";
import type {
  AIModelOption,
  BoardFocusRef,
  ChatInteractionMode,
  ChatRequestPayload,
  CoursePackage,
  LearningClarificationStatus,
  RealtimeToolCallResponse,
  SelectionRef,
} from "@/types";

const CHAT_PANEL_WIDTH_STORAGE_KEY = "openclass:studio:chat-panel-width";
const CHAT_PANEL_DEFAULT_WIDTH = 380;
const CHAT_PANEL_MIN_WIDTH = 300;
const CHAT_PANEL_MAX_WIDTH = 640;
const RIGHT_SIDEBAR_WIDTH_STORAGE_KEY = "openclass:studio:right-sidebar-width";
const RIGHT_SIDEBAR_DEFAULT_WIDTH = 360;
const RIGHT_SIDEBAR_MIN_WIDTH = 280;
const RIGHT_SIDEBAR_MAX_WIDTH = 640;

export function CourseStudio() {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const studioTexts = txt.studio;
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const chatScrollEndRef = useRef<HTMLDivElement | null>(null);
  const chatRequestInFlightRef = useRef(false);

  const workspace = useCourseWorkspace();
  const {
    coursePackage,
    isLoading,
    error,
    setError,
    lessonMap,
    activeLesson,
    openLessons,
    activeMessages,
    activeComposerState,
    updateLessonMessages,
    updateLessonComposerState,
    updateActiveLessonComposerState,
    applyCoursePackage: applyWorkspaceCoursePackage,
  } = workspace;
  const modelSelection = useModelCatalog();
  const {
    modelCatalog,
    selectedTextModel,
    selectedRealtimeModel,
    selectedTextOption,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    openModelMenu,
    setOpenModelMenu,
    selectTextModel,
    selectRealtimeModel,
  } = modelSelection;
  const textModelReady = modelCatalog.text.some((option) => option.enabled);
  const [selection, setSelection] = useState<SelectionRef | null>(null);
  const [geometryReference, setGeometryReference] = useState<SelectionRef | null>(null);
  const [selectionPopover, setSelectionPopover] = useState<SelectionPopoverPosition | null>(null);
  const [realtimeTeachingFocusState, setRealtimeTeachingFocusState] = useState<{
    lessonId: string;
    focus: BoardFocusRef;
  } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [topCollapsed, setTopCollapsed] = useState(true);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const {
    width: chatPanelWidth,
    isResizing: isChatPanelResizing,
    dragHandleProps: chatPanelResizeHandleProps,
  } = useResizablePanelWidth({
    storageKey: CHAT_PANEL_WIDTH_STORAGE_KEY,
    defaultWidth: CHAT_PANEL_DEFAULT_WIDTH,
    minWidth: CHAT_PANEL_MIN_WIDTH,
    maxWidth: CHAT_PANEL_MAX_WIDTH,
    label: "调整 Chatbot 宽度",
  });
  const {
    width: rightSidebarWidth,
    isResizing: isRightSidebarResizing,
    dragHandleProps: rightSidebarResizeHandleProps,
  } = useResizablePanelWidth({
    storageKey: RIGHT_SIDEBAR_WIDTH_STORAGE_KEY,
    defaultWidth: RIGHT_SIDEBAR_DEFAULT_WIDTH,
    minWidth: RIGHT_SIDEBAR_MIN_WIDTH,
    maxWidth: RIGHT_SIDEBAR_MAX_WIDTH,
    dragDirection: "grow-left",
    label: "调整课程工作台辅助宽度",
  });
  const [sidebarTab, setSidebarTab] = useState<CourseStudioSidebarTab>("history");
  const boardDraft = useBoardDraft({
    activeLesson,
    setError,
    setBusyAction,
    applyCoursePackage: applyWorkspaceCoursePackage,
    applyAutoSavedCoursePackage: workspace.applyAutoSavedCoursePackage,
    onPackageApplied: resetTransientUi,
  });

  function updateCoursePackage(nextPackage: CoursePackage, options?: CoursePackageApplyOptions) {
    const result = applyWorkspaceCoursePackage(nextPackage, options);
    boardDraft.resetToLesson(result.activeLesson);
    resetTransientUi();
    return result;
  }

  const lessonMerge = useLessonMerge({
    activeLesson,
    selectedTextModel,
    flushBoardAutoSave: boardDraft.flushAutoSave,
    applyCoursePackage: updateCoursePackage,
    setError,
    setBusyAction,
  });

  const history = useLessonHistory({
    activeLesson,
    flushAutoSave: boardDraft.flushAutoSave,
    resetDraftToLesson: boardDraft.resetToLesson,
    setPreviewDocument: boardDraft.setPreviewDocument,
    applyCoursePackage: updateCoursePackage,
    setError,
    setBusyAction,
  });
  const {
    flushAutoSave,
    flushAutoSaveWithBeacon,
    handleLocalDocumentChange,
    markStructureRemovalIntent,
    handleImportDocx,
    handleExportDocx,
    handleExportHtml,
  } = boardDraft;
  const {
    previewCommitId,
    newBranchName,
    setNewBranchName,
    handleCreateBranch,
    handlePreviewCommit,
    exitPreviewMode,
    handleCreateBranchFromCommit,
    handleSwitchBranch,
    handleRestoreCommit,
  } = history;

  const lessonPackage = useLessonPackage({
    activeLesson,
    mergeActive: lessonMerge.isActive,
    flushAutoSave: boardDraft.flushAutoSave,
    setPreviewCommitId: history.setPreviewCommitId,
    setPreviewDocument: boardDraft.setPreviewDocument,
    resetPreview: history.exitPreviewMode,
    createBranchFromCommit: history.handleCreateBranchFromCommit,
    applyCoursePackage: updateCoursePackage,
    setError,
    setBusyAction,
  });

  const previewCommit = history.previewCommit;
  const activeHeadCommit = history.activeHeadCommit;
  const isPreviewMode = history.isPreviewMode;
  const isDraftPreviewMode = !isPreviewMode && boardDraft.isPreviewing;
  const displayedDocument = lessonMerge.draftDocument ?? boardDraft.displayedDocument;
  const displayedMessageCommitId = lessonPackage.playbackMessageCommitId ?? previewCommit?.id ?? null;
  const displayedMessages = activeLesson && displayedMessageCommitId
    ? buildLessonMessagesFromHistory(activeLesson, displayedMessageCommitId)
    : activeMessages;
  const persistedRequirements = lessonMerge.session?.draft_runtime.learning_requirements ?? activeLesson?.learning_requirements ?? null;
  const persistedBoardTask = lessonMerge.session?.draft_runtime.board_task_requirements ?? activeLesson?.board_task_requirements ?? null;
  const previewLearningClarity = learningClarityFromCommit(previewCommit);
  const persistedLearningClarity = learningClarityFromCommit(activeHeadCommit);
  const currentRequirementCleared =
    !isPreviewMode && !persistedRequirements && activeHeadCommit?.metadata?.requirement_cleared === true;
  const latestAssistantMessage = [...activeMessages].reverse().find((message) => message.role === "assistant");
  const composerSelections = activeComposerState.composerSelections?.length
    ? activeComposerState.composerSelections
    : activeComposerState.composerSelection
      ? [activeComposerState.composerSelection]
      : [];
  const composerSelection = composerSelections[composerSelections.length - 1] ?? null;
  const composerAttachments = activeComposerState.composerAttachments;
  const sourceQuerySelections = activeComposerState.sourceQuerySelections ?? [];
  const sourceQueryAllReady = activeComposerState.sourceQueryAllReady ?? false;
  const sourceQueryScope = sourceQueryScopeFromComposer(sourceQuerySelections, sourceQueryAllReady);
  function exitAnyPreviewMode() {
    if (lessonPackage.isPlaybackActive) {
      lessonPackage.exitPlayback();
      return;
    }
    if (isDraftPreviewMode) {
      boardDraft.resetToLesson(activeLesson);
      return;
    }
    exitPreviewMode();
  }
  const chatAgent = useLessonChatAgent({
    activeLesson,
    activeMessages,
    activeComposerState,
    composerSelections,
    currentBoardDocument: displayedDocument,
    selectedTextModel,
    textModelReady,
    isPreviewMode: isPreviewMode || isDraftPreviewMode || lessonMerge.isActive,
    chatRequestInFlightRef,
    flushAutoSave,
    exitPreviewMode: exitAnyPreviewMode,
    updateCoursePackage,
    updateLessonMessages,
    updateLessonComposerState,
    sourceQueryScope,
    clearSelection: clearTurnSelection,
    setStreamingDocumentPreview: boardDraft.setStreamingDocumentPreview,
    onTransientBoardFocusChange: (lessonId, focus) => {
      setRealtimeTeachingFocusState(focus ? { lessonId, focus } : null);
    },
    setError,
    setBusyAction,
    busyAction,
  });
  const {
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
    handleSubmitChat,
    handleStopChat,
    handleEditMessage,
    handleContinueTeaching,
  } = chatAgent;
  const activeRequirements = streamedRequirementSheet ?? persistedRequirements;
  const activeBoardTask = streamedBoardTaskSheet ?? persistedBoardTask;
  const voice = useRealtimeVoice({
    activeLesson,
    latestAssistantMessageContent: latestAssistantMessage?.content ?? null,
    selectedTextModel,
    selectedRealtimeModel,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    busyAction,
    setBusyAction,
    setError,
    flushAutoSave,
    chatRequestInFlightRef,
    onSubmitTranscript: (message) => {
      void handleSubmitChat({
        message,
        interaction_mode: "ask",
      });
    },
    currentSelection: includeSelectionInPrompt ? composerSelection : null,
    currentSelections: includeSelectionInPrompt ? composerSelections : [],
    onTranscriptUpdate: handleRealtimeTranscriptUpdate,
    onToolStatusUpdate: handleRealtimeToolStatusUpdate,
    onToolResult: handleRealtimeToolResult,
  });
  const {
    remoteAudioRef,
    voiceActive,
    voiceStatusText,
    handleVoiceToggle,
    stopRealtimeSession,
    sendRealtimeText,
    codexLiveTaskState,
    resolveCodexLiveTask,
  } = voice;
  const chatSpeech = useChatSpeech({
    lessonId: activeLesson?.id ?? null,
    messages: activeMessages,
  });

  const latestDisplayedMessage = displayedMessages[displayedMessages.length - 1];
  useEffect(() => {
    chatScrollEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeLesson?.id, displayedMessages.length, latestDisplayedMessage?.content, isChatBusy]);

  const realtimeTeachingFocus =
    realtimeTeachingFocusState && realtimeTeachingFocusState.lessonId === activeLesson?.id
      ? realtimeTeachingFocusState.focus
      : null;

  function handleRealtimeTranscriptUpdate(update: RealtimeTranscriptUpdate) {
    updateLessonMessages(update.lessonId, (current) => applyRealtimeTranscriptUpdate(current, update));
  }

  function handleRealtimeToolStatusUpdate(update: RealtimeToolStatusUpdate) {
    updateLessonMessages(update.lessonId, (current) => applyRealtimeToolStatusUpdate(current, update));
  }

  function handleRealtimeToolResult(lessonId: string, result: RealtimeToolCallResponse) {
    if (result.course_package) {
      updateCoursePackage(result.course_package, { activeLessonId: lessonId });
    }
    if (result.resolved_focus?.source === "board") {
      setRealtimeTeachingFocusState({ lessonId, focus: result.resolved_focus });
    } else {
      setRealtimeTeachingFocusState(null);
    }
  }

  async function handleStudioSubmitChat(payloadOverride?: ChatRequestPayload) {
    const realtimePayload = payloadOverride ?? {
      message: chatInput.trim(),
      interaction_mode: composerMode,
      selection: includeSelectionInPrompt ? composerSelection : null,
      selections: includeSelectionInPrompt ? composerSelections : [],
      attachments: composerAttachments,
      source_query_scope: sourceQueryScope ?? undefined,
    };
    const canUseRealtimeText =
      voiceActive &&
      realtimePayload.interaction_mode !== "direct_edit" &&
      !realtimePayload.attachments?.length &&
      !realtimePayload.formula_ink &&
      !realtimePayload.board_generation_action &&
      !realtimePayload.teaching_action;
    const canUseRealtimeSourceFreeText = canUseRealtimeText && !realtimePayload.source_query_scope;
    if (canUseRealtimeSourceFreeText && sendRealtimeText(realtimePayload.message)) {
      updateActiveLessonComposerState((current) => ({
        ...current,
        chatInput: "",
        composerAttachments: [],
      }));
      return;
    }
    await handleSubmitChat(payloadOverride);
  }

  const clarityStatus: LearningClarificationStatus =
    previewLearningClarity ??
    learningClarity ??
    (currentRequirementCleared ? null : persistedLearningClarity) ?? {
      progress: 0,
      label: "",
      reason: "",
      missing_items: [],
      can_start: false,
      forced_start: false,
      summary: "",
      key_facts: [],
      checklist: [],
      next_question: "",
      ready_for_board: false,
    };
  const clarityBarTone =
    clarityStatus.progress >= 90
      ? "bg-emerald-500"
      : clarityStatus.can_start
      ? "bg-blue-500"
      : "bg-amber-500";

  function applySelection(nextSelection: SelectionRef, popoverPosition?: SelectionPopoverPosition | null) {
    setSelection((current) => (sameSelection(current, nextSelection) ? current : nextSelection));
    setSelectionPopover((current) => {
      const nextPosition = popoverPosition ?? null;
      return samePopoverPosition(current, nextPosition) ? current : nextPosition;
    });
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      composerSelection: null,
    }));
  }

  function clearTransientSelection() {
    setSelection((current) => (current ? null : current));
    setSelectionPopover((current) => (current ? null : current));
  }

  function clearSelection() {
    clearTransientSelection();
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: null,
      composerSelections: [],
      sourceQuerySelections: [],
      sourceQueryAllReady: false,
    }));
  }

  function clearTurnSelection() {
    clearTransientSelection();
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: null,
      composerSelections: [],
    }));
  }

  function focusComposerWithSelection(nextMode: ChatInteractionMode, explicitSelection?: SelectionRef) {
    const selectionToFocus = explicitSelection ?? selection;
    if (!selectionToFocus) {
      return;
    }
    const normalizedSelection: SelectionRef =
      selectionToFocus.kind === "board"
        ? {
            ...selectionToFocus,
            location_kind: selectionToFocus.location_kind === "insertion_anchor" ? "insertion_anchor" : "target_range",
          }
        : selectionToFocus;
    setSelection(normalizedSelection);
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: nextMode,
      includeSelectionInPrompt: true,
      composerSelection: normalizedSelection,
      composerSelections:
        nextMode === "direct_edit"
          ? [normalizedSelection]
          : appendComposerSelection(current.composerSelections ?? [], normalizedSelection),
    }));
    setSelectionPopover(null);
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus();
    });
  }

  function openGeometryWithSelection(explicitSelection?: SelectionRef) {
    const selectionToOpen = explicitSelection ?? selection;
    if (!selectionToOpen || selectionToOpen.kind !== "board") {
      return;
    }
    const normalizedSelection: SelectionRef = {
      ...selectionToOpen,
      location_kind: selectionToOpen.location_kind === "insertion_anchor" ? "insertion_anchor" : "target_range",
    };
    setSelection(normalizedSelection);
    setSelectionPopover(null);
    setGeometryReference({ ...normalizedSelection });
    setSidebarTab("geometry");
    setRightSidebarOpen(true);
  }

  function applySourceReference(sourceReference: SelectionRef) {
    if (
      sourceReference.kind !== "source" ||
      !sourceReference.source_ingestion_id ||
      !sourceReference.source_content_hash
    ) {
      return;
    }
    setSelection(sourceReference);
    setSelectionPopover(null);
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: null,
      composerSelections: [],
      sourceQuerySelections: appendSourceQuerySelection(
        current.sourceQueryAllReady ? [] : current.sourceQuerySelections,
        sourceReference
      ),
      sourceQueryAllReady: false,
    }));
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus();
    });
  }

  function applyAllReadySourcesReference() {
    setSelection(null);
    setSelectionPopover(null);
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      sourceQuerySelections: [],
      sourceQueryAllReady: true,
    }));
    window.requestAnimationFrame(() => chatInputRef.current?.focus());
  }

  function handleFormulaInkSubmit(payload: FormulaInkEditorSubmitPayload) {
    if (!activeLesson || lessonMerge.isActive || !textModelReady || isChatBusy || chatRequestInFlightRef.current) {
      return false;
    }
    const formulaSelection: SelectionRef = {
      kind: "board",
      location_kind: payload.selection.locationKind,
      lesson_id: activeLesson.id,
      document_id: payload.selection.documentId,
      excerpt: payload.selection.excerpt,
      before_text: payload.selection.beforeText,
      after_text: payload.selection.afterText,
    };
    setSelection(formulaSelection);
    setSelectionPopover(null);
    const isReplaceAction = payload.action === "replace";
    void handleSubmitChat({
      message: isReplaceAction
        ? "请识别我手写的公式，并把当前选中的公式更改为识别结果。"
        : "请识别我手写的公式，并结合当前选中的公式回答。",
      selection: formulaSelection,
      interaction_mode: isReplaceAction ? "direct_edit" : "ask",
      formula_ink: {
        action: payload.action,
        image_data_url: payload.imageDataUrl,
        source_latex: payload.sourceLatex,
      },
    });
    return true;
  }

  function adjustComposerHeight() {
    const input = chatInputRef.current;
    if (!input) {
      return;
    }
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }

  const adjustComposerHeightEffectEvent = useEffectEvent(() => {
    adjustComposerHeight();
  });

  useEffect(() => {
    adjustComposerHeightEffectEvent();
  }, [chatInput, composerSelection?.excerpt]);

  useEffect(() => {
    if (!lessonMerge.isActive) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      setSidebarTab("history");
      setRightSidebarOpen(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [lessonMerge.isActive]);

  function resetTransientUi() {
    history.setPreviewCommitId(null);
    chatAgent.resetAgentState();
  }

  function resetTransientUiForLessonSwitch() {
    history.setPreviewCommitId(null);
    chatAgent.resetAgentState({ clearComposerSelection: false });
    clearTransientSelection();
  }

  const workspaceActions = useWorkspaceActions({
    coursePackage,
    activeLesson,
    lessonMap,
    flushAutoSave,
    updateCoursePackage,
    selectLocalLesson: workspace.selectLocalLesson,
    resetDraftToLesson: boardDraft.resetToLesson,
    resetTransientUi: resetTransientUiForLessonSwitch,
    setError,
    setBusyAction,
  });
  const {
    handleCreateLesson,
    handleCloseLesson,
    handleSelectLesson,
  } = workspaceActions;

  function handleSelectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("已切换实时语音模型，当前会话已断开");
    }
    selectRealtimeModel(option);
  }

  const flushAutoSaveWithBeaconEffectEvent = useEffectEvent(() => {
    flushAutoSaveWithBeacon("pagehide");
  });

  useEffect(() => {
    function handlePageHide() {
      flushAutoSaveWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      flushAutoSaveWithBeaconEffectEvent();
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
    };
  }, []);

  async function handleReturnHome() {
    if (lessonMerge.isActive && !(await lessonMerge.flushDraft())) {
      return;
    }
    if (!(await flushAutoSave("return-home"))) {
      return;
    }
    router.push("/home");
  }

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{studioTexts.loading}</div>;
  }

  if (!coursePackage) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{studioTexts.packageMissing}</div>;
  }

  const workspaceTitle = coursePackage.title;
  const lessonTabs = (
    <LessonTabs
      texts={studioTexts}
      lessons={openLessons}
      activeLessonId={activeLesson?.id ?? null}
      isBusyCreating={busyAction === "generate"}
      onSelectLesson={(lessonId) => {
        if (lessonMerge.isActive) {
          setError("合并期间不能切换课程，请先提交或放弃合并。");
          return;
        }
        void handleSelectLesson(lessonId);
      }}
      onCloseLesson={(lessonId) => {
        if (lessonMerge.isActive) {
          setError("合并期间不能关闭课程。");
          return;
        }
        void handleCloseLesson(lessonId);
      }}
      onCreateLesson={() => {
        if (lessonMerge.isActive) {
          setError("合并期间不能创建新课程。");
          return;
        }
        void handleCreateLesson();
      }}
    />
  );
  const selectionPopoverNode = (
    <SelectionPopover
      selection={selection}
      position={selectionPopover}
      onFocusComposerWithSelection={() => focusComposerWithSelection("ask")}
      onOpenGeometryWithSelection={() => openGeometryWithSelection()}
    />
  );

  if (!activeLesson || !displayedDocument) {
    return (
      <CourseStudioPageShell
        texts={studioTexts}
        workspaceTitle={workspaceTitle}
        topCollapsed={topCollapsed}
        rightSidebarOpen={rightSidebarOpen}
        error={error}
        tabs={lessonTabs}
        accountMenu={<AccountMenu compact ariaLabel="开放课堂用户头像" />}
        selectionPopover={selectionPopoverNode}
        onReturnHome={() => void handleReturnHome()}
        onTopCollapsedChange={setTopCollapsed}
        onRightSidebarOpenChange={setRightSidebarOpen}
        onClearError={() => setError(null)}
      >
        <section className="flex flex-1 items-center justify-center px-6">
          <div className="w-full max-w-xl rounded-[32px] border border-stone-200 bg-white/90 p-10 text-center shadow-[0_24px_70px_rgba(15,23,42,0.08)]">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[20px] bg-stone-950 text-white">
              <BookOpen className="h-7 w-7" />
            </div>
            <h2 className="mt-6 text-2xl font-semibold tracking-tight text-stone-950">{studioTexts.emptyPackageTitle}</h2>
            <p className="mt-3 text-sm leading-7 text-stone-500">
              {studioTexts.emptyPackageBody}
            </p>
            <div className="mt-8 flex justify-center">
              <button
                type="button"
                onClick={() => {
                  setTopCollapsed(false);
                  void handleCreateLesson();
                }}
                disabled={busyAction === "generate"}
                className="inline-flex items-center gap-2 rounded-full bg-stone-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-stone-800 disabled:cursor-wait disabled:bg-stone-500"
              >
                <Plus className="h-4 w-4" />
                {studioTexts.createFirstPage}
              </button>
            </div>
          </div>
        </section>
      </CourseStudioPageShell>
    );
  }

  return (
    <CourseStudioPageShell
      texts={studioTexts}
      workspaceTitle={workspaceTitle}
      topCollapsed={topCollapsed}
      rightSidebarOpen={rightSidebarOpen}
      error={error}
      tabs={lessonTabs}
      accountMenu={<AccountMenu compact ariaLabel="开放课堂用户头像" />}
      selectionPopover={selectionPopoverNode}
      onReturnHome={() => void handleReturnHome()}
      onTopCollapsedChange={setTopCollapsed}
      onRightSidebarOpenChange={setRightSidebarOpen}
      onClearError={() => setError(null)}
    >
      <div
        ref={mainContainerRef}
        style={
          {
            "--chat-panel-width": `${chatPanelWidth}px`,
            "--right-sidebar-width": `${rightSidebarWidth}px`,
          } as CSSProperties
        }
        className={clsx(
          "grid min-h-0 flex-1 grid-cols-[var(--chat-panel-width)_minmax(0,1fr)] overflow-hidden transition-[grid-template-columns]",
          isChatPanelResizing || isRightSidebarResizing ? "duration-0" : "duration-300",
          rightSidebarOpen && "xl:grid-cols-[var(--chat-panel-width)_minmax(0,1fr)_var(--right-sidebar-width)]"
        )}
      >
        <CourseStudioChatSidebar
          packageId={coursePackage.id}
          resizeHandleProps={chatPanelResizeHandleProps}
          isResizing={isChatPanelResizing}
          clarityBarTone={clarityBarTone}
          clarityStatus={clarityStatus}
          activeLesson={activeLesson}
          targetCommitId={currentRequirementCleared ? null : previewCommit?.id ?? activeHeadCommit?.id ?? null}
          previewCommit={previewCommit}
          displayedMessages={displayedMessages}
          isPreviewMode={isPreviewMode}
          interactionLocked={lessonMerge.isActive}
          isChatBusy={isChatBusy}
          clarificationQuestions={clarificationQuestions}
          activeBoardTask={activeBoardTask}
          activeRequirementSheet={activeRequirements}
          currentNeedPending={currentNeedPending}
          latestBoardDecision={latestBoardDecision}
          chatScrollEndRef={chatScrollEndRef}
          chatInputRef={chatInputRef}
          remoteAudioRef={remoteAudioRef}
          modelCatalog={modelCatalog}
          selectedTextModel={selectedTextModel}
          selectedRealtimeModel={selectedRealtimeModel}
          selectedTextOption={selectedTextOption}
          selectedRealtimeOption={selectedRealtimeOption}
          openModelMenu={openModelMenu}
          setOpenModelMenu={setOpenModelMenu}
          voiceActive={voiceActive}
          voiceStatusText={voiceStatusText}
          codexLiveTaskState={codexLiveTaskState}
          chatInput={chatInput}
          composerAttachments={composerAttachments}
          composerMode={composerMode}
          composerSelection={composerSelection}
          composerSelections={composerSelections}
          sourceQuerySelections={sourceQuerySelections}
          sourceQueryAllReady={sourceQueryAllReady}
          includeSelectionInPrompt={includeSelectionInPrompt}
          onApplySelection={applySelection}
          onContinueTeaching={() => void handleContinueTeaching()}
          onSubmitChat={(payload) => handleStudioSubmitChat(payload)}
          onStopChat={handleStopChat}
          onEditMessage={(message, nextContent) => handleEditMessage(message, nextContent)}
          onSelectTextModel={selectTextModel}
          onSelectRealtimeModel={handleSelectRealtimeModel}
          onVoiceToggle={handleVoiceToggle}
          onResolveCodexLiveTask={resolveCodexLiveTask}
          onSpeakMessage={chatSpeech.speakMessage}
          onExitPreviewMode={exitAnyPreviewMode}
          onClearSelection={clearSelection}
          onUpdateComposerState={updateActiveLessonComposerState}
          onAdjustComposerHeight={adjustComposerHeight}
          onError={setError}
        />

        <BoardEditorPanel
          activeLesson={activeLesson}
          document={displayedDocument}
          isPreviewMode={isPreviewMode}
          isDraftPreviewMode={isDraftPreviewMode}
          isMergeMode={lessonMerge.isActive}
          previewCommit={previewCommit}
          toolbarCollapsed={topCollapsed}
          transientTeachingFocus={realtimeTeachingFocus}
          onExitPreviewMode={exitAnyPreviewMode}
          onDocumentChange={lessonMerge.isActive ? lessonMerge.handleDocumentChange : handleLocalDocumentChange}
          onStructureRemovalIntent={markStructureRemovalIntent}
          onApplySelection={applySelection}
          onClearTransientSelection={clearTransientSelection}
          onImportDocx={(file) => {
            if (lessonMerge.isActive) {
              setError("合并期间不能导入 DOCX，请先提交或放弃合并。");
              return;
            }
            void handleImportDocx(file);
          }}
          onExportDocx={() => void handleExportDocx()}
          onExportHtml={() => void handleExportHtml()}
          onImportRidoc={(file) => void lessonPackage.importRidoc(file)}
          onExportRidoc={() => void lessonPackage.exportRidoc()}
          onReferenceFormula={(formulaSelection) => focusComposerWithSelection("ask", formulaSelection)}
          onReferenceFormulaToGeometry={(formulaSelection) => openGeometryWithSelection(formulaSelection)}
          onFormulaInkSubmit={handleFormulaInkSubmit}
        />

        <CourseStudioSidePanel
          open={rightSidebarOpen}
          resizeHandleProps={rightSidebarResizeHandleProps}
          isResizing={isRightSidebarResizing}
          sidebarTab={sidebarTab}
          onSidebarTabChange={setSidebarTab}
          onClose={() => setRightSidebarOpen(false)}
          activeLesson={activeLesson}
          packageId={coursePackage.id}
          previewCommit={previewCommit}
          previewCommitId={previewCommitId}
          activeRequirements={activeRequirements}
          activeBoardTask={activeBoardTask}
          latestBoardDecision={latestBoardDecision}
          newBranchName={newBranchName}
          onNewBranchNameChange={setNewBranchName}
          onCreateBranch={() => handleCreateBranch()}
          onPreviewCommit={(commit) => handlePreviewCommit(commit)}
          onRestoreCommit={(commitId) => handleRestoreCommit(commitId)}
          onCreateBranchFromCommit={(commit) => handleCreateBranchFromCommit(commit)}
          onReferenceHistoryNode={(historySelection) => focusComposerWithSelection("ask", historySelection)}
          onSwitchBranch={(branchName) => handleSwitchBranch(branchName)}
          onMergeBranch={(branchName) => lessonMerge.startMerge(branchName)}
          lessonPackageControls={{
            currentStep: lessonPackage.currentStep,
            stepIndex: lessonPackage.stepIndex,
            stepCount: lessonPackage.steps.length,
            isPlaying: lessonPackage.isPlaying,
            isPlaybackActive: lessonPackage.isPlaybackActive,
            speed: lessonPackage.speed,
            operation: lessonPackage.operation,
            onSpeedChange: lessonPackage.setSpeed,
            onPlayToggle: lessonPackage.startOrTogglePlayback,
            onPrevious: () => lessonPackage.movePlayback(-1),
            onNext: () => lessonPackage.movePlayback(1),
            onExit: lessonPackage.exitPlayback,
            onFork: lessonPackage.forkFromCurrentStep,
            onExport: lessonPackage.exportRidoc,
            onImport: lessonPackage.importRidoc,
          }}
          mergeSession={lessonMerge.session}
          mergeDraftDirty={lessonMerge.isDraftDirty}
          mergeAIProposing={lessonMerge.isAIProposing}
          onResolveMergeConflict={lessonMerge.resolveConflict}
          onProposeMergeWithAI={lessonMerge.proposeWithAI}
          onCancelMergeAI={lessonMerge.cancelAI}
          onRecomputeMerge={lessonMerge.recompute}
          onAbandonMerge={lessonMerge.abandon}
          onSubmitMerge={lessonMerge.submit}
          onError={setError}
          onSourceReference={applySourceReference}
          onAllReadySourcesReference={applyAllReadySourcesReference}
          geometryReference={geometryReference}
          onGeometryReferenceClear={() => setGeometryReference(null)}
          textModel={selectedTextModel}
          catalogModelOptions={modelCatalog.text}
          defaultCatalogModel={modelCatalog.defaults.text}
          selectedTextModel={selectedTextModel}
          selectedTextOption={selectedTextOption}
          textModelOptions={modelCatalog.text}
          onSelectTextModel={selectTextModel}
          speechAutoEnabled={chatSpeech.autoSpeakEnabled}
          speechIsLoading={chatSpeech.isSpeechLoading}
          speechIsPlaying={chatSpeech.isSpeechPlaying}
          speechIsPaused={chatSpeech.isSpeechPaused}
          speechStatusText={chatSpeech.speechStatusText}
          speechOptions={chatSpeech.speechOptions}
          speechSelectedVoice={chatSpeech.selectedVoice}
          speechRate={chatSpeech.speechRate}
          speechCurrentModel={chatSpeech.currentModel}
          speechCurrentText={chatSpeech.currentSpeechText}
          speechCurrentTime={chatSpeech.currentTime}
          speechDuration={chatSpeech.duration}
          speechCanSeek={chatSpeech.canSeekSpeech}
          speechCanReplay={chatSpeech.canReplaySpeech}
          onSpeechAutoToggle={chatSpeech.toggleAutoSpeak}
          onSpeechCancel={chatSpeech.stopSpeech}
          onSpeechPause={chatSpeech.pauseSpeech}
          onSpeechResume={chatSpeech.resumeSpeech}
          onSpeechReplay={chatSpeech.replayCurrentSpeech}
          onSpeechSeek={chatSpeech.seekSpeech}
          onSpeechVoiceChange={chatSpeech.selectVoice}
          onSpeechRateChange={chatSpeech.selectSpeechRate}
        />
      </div>
    </CourseStudioPageShell>
  );
}

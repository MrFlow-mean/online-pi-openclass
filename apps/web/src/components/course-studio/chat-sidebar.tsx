import clsx from "clsx";
import {
  AudioLines,
  BrainCircuit,
  Check,
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Send,
  Square,
  TextQuote,
  Volume2,
  X,
} from "lucide-react";
import { useRef, useState, type Dispatch, type HTMLAttributes, type ReactNode, type RefObject, type SetStateAction } from "react";

import { CourseChatMessage } from "@/components/chatbot";
import { ChatAttachmentChips, ChatAttachmentMenu } from "@/components/course-studio/chat-attachment-menu";
import { CodexModelSettingsPicker } from "@/components/course-studio/codex-model-settings-picker";
import {
  modelButtonLabel,
  modelOptionKey,
  modelSelectionKey,
  PROVIDER_LABELS,
} from "@/components/course-studio/model-catalog";
import { latestExecutedNeedSnapshot } from "@/components/course-studio/current-need-snapshot";
import { popoverPositionFromDomSelection } from "@/components/course-studio/selection-utils";
import { LearningClarityCard } from "@/components/learning-clarity-card";
import { boardWorkflowLabel } from "@/lib/learning-requirement-display";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardDecision,
  BoardTaskRequirementSheet,
  ChatAttachmentRef,
  ChatInteractionMode,
  ChatRequestPayload,
  CommitRecord,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  SelectionRef,
} from "@/types";
import type { ChatMessage, LessonComposerState } from "@/components/course-studio/history-utils";

type ModelMenu = "text" | "realtime" | null;

const BOARD_TASK_ACTION_LABELS: Partial<Record<NonNullable<BoardTaskRequirementSheet["requested_action"]>, string>> = {
  write: "写入",
  edit: "修改",
  explain: "讲解",
  chat: "互动",
};

function boardTaskActionLabel(action: BoardTaskRequirementSheet["requested_action"]) {
  return action ? BOARD_TASK_ACTION_LABELS[action] ?? action : "待确认";
}

function boardTaskLocationLabel(task: BoardTaskRequirementSheet) {
  const kindLabels: Record<NonNullable<BoardTaskRequirementSheet["location_kind"]>, string> = {
    target_range: "目标范围",
    insertion_anchor: "插入位置",
    unspecified: "待确认",
  };
  const kind = kindLabels[task.location_kind ?? "unspecified"];
  const hint = task.target_hint || task.target_location?.display_label || task.location_status;
  return `${kind} · ${hint}`;
}

function composerSelectionLabel(selection: SelectionRef) {
  if (selection.kind === "source") {
    return selection.source_scope_kind === "source" ? "整份资料" : "资料章节";
  }
  if (selection.kind === "board" && selection.location_kind === "target_range") {
    return "TargetRange";
  }
  if (selection.kind === "board" && selection.location_kind === "insertion_anchor") {
    return "InsertionAnchor";
  }
  return selection.kind === "board" ? "板书选区" : "对话引用";
}

function composerSelectionToggleLabel(selection: SelectionRef, included: boolean) {
  if (selection.kind === "source") {
    return included ? "包含资料" : "忽略资料";
  }
  return included ? "包含选区" : "忽略选区";
}

function hasVisibleLearningClarity(
  clarityStatus: LearningClarificationStatus,
  activeRequirementSheet: LearningRequirementSheet | null
) {
  return (
    clarityStatus.progress > 0 ||
    clarityStatus.ready_for_board ||
    Boolean(clarityStatus.summary.trim()) ||
    clarityStatus.key_facts.length > 0 ||
    clarityStatus.checklist.length > 0 ||
    Boolean(activeRequirementSheet?.work_mode && activeRequirementSheet.work_mode !== "unknown")
  );
}

function CurrentNeedCard({
  activeBoardTask,
  activeRequirementSheet,
  barTone,
  clarityStatus,
  currentNeedPending,
  isChatBusy,
  lesson,
  targetCommitId,
}: {
  activeBoardTask: BoardTaskRequirementSheet | null;
  activeRequirementSheet: LearningRequirementSheet | null;
  barTone: string;
  clarityStatus: LearningClarificationStatus;
  currentNeedPending: boolean;
  isChatBusy: boolean;
  lesson: Lesson;
  targetCommitId: string | null;
}) {
  if (currentNeedPending) {
    return (
      <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">当前任务</p>
          <span className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-sky-700">
            <LoaderCircle className="h-3 w-3 animate-spin" />
            识别中
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-1/3 rounded-full bg-sky-500 transition-all" />
        </div>
        <p className="mt-3 text-xs leading-6 text-sky-950">正在把你的新问题整理成位置、动作和怎么做。</p>
      </div>
    );
  }

  const executedNeed =
    !activeBoardTask && !activeRequirementSheet && !currentNeedPending
      ? latestExecutedNeedSnapshot(lesson, targetCommitId)
      : null;

  if (executedNeed?.kind === "board_task") {
    const task = executedNeed.boardTask;
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
            已被执行
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-full rounded-full bg-emerald-500 transition-all" />
        </div>
        <div className="mt-3 grid gap-2 text-xs leading-5 text-emerald-950">
          <p>链路：{boardWorkflowLabel(task.board_workflow ?? "act_on_existing_board")}</p>
          <p>位置：{boardTaskLocationLabel(task)}</p>
          <p>动作：{boardTaskActionLabel(task.requested_action)}</p>
          <p>内容：{task.question_or_topic || "已执行的板书任务"}</p>
        </div>
      </div>
    );
  }

  if (executedNeed?.kind === "learning_requirement") {
    return (
      <LearningClarityCard
        activeRequirementSheet={executedNeed.requirementSheet}
        barTone="bg-emerald-500"
        clarityStatus={executedNeed.clarityStatus}
        lesson={lesson}
        statusLabelOverride="已被执行"
        targetCommitId={executedNeed.commit.id}
      />
    );
  }

  if (activeBoardTask) {
    const progress = Math.max(0, Math.min(100, activeBoardTask.progress));
    const statusLabel = isChatBusy ? "执行中" : progress >= 100 ? "已完成" : "收集中";
    return (
      <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-sky-700">
            {statusLabel} · {progress}%
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-3 grid gap-2 text-xs leading-5 text-sky-950">
          <p>链路：{boardWorkflowLabel(activeBoardTask.board_workflow ?? "act_on_existing_board")}</p>
          <p>位置：{boardTaskLocationLabel(activeBoardTask)}</p>
          <p>动作：{boardTaskActionLabel(activeBoardTask.requested_action)}</p>
          <p>内容：{activeBoardTask.question_or_topic || "待确认"}</p>
        </div>
        {activeBoardTask.confirmation_status === "awaiting" ? (
          <p className="mt-3 text-xs leading-6 text-sky-900">等待你确认是否先扩写板书。</p>
        ) : null}
        {activeBoardTask.missing_items.length ? (
          <p className="mt-3 text-xs leading-6 text-sky-900">待补充：{activeBoardTask.missing_items.join("、")}</p>
        ) : null}
      </div>
    );
  }

  if (hasVisibleLearningClarity(clarityStatus, activeRequirementSheet)) {
    return (
      <LearningClarityCard
        activeRequirementSheet={activeRequirementSheet}
        barTone={barTone}
        clarityStatus={clarityStatus}
        lesson={lesson}
        targetCommitId={targetCommitId}
      />
    );
  }

  if (lesson.board_document.content_text.trim()) {
    return (
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-gray-600">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-gray-600">待输入</span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-0 rounded-full bg-gray-400" />
        </div>
        <p className="mt-3 text-xs leading-6 text-gray-700">等待新的板书任务。</p>
      </div>
    );
  }

  return (
    <LearningClarityCard
      activeRequirementSheet={activeRequirementSheet}
      barTone={barTone}
      clarityStatus={clarityStatus}
      lesson={lesson}
      targetCommitId={targetCommitId}
    />
  );
}

type CourseStudioChatSidebarProps = {
  packageId: string;
  resizeHandleProps: HTMLAttributes<HTMLDivElement>;
  isResizing: boolean;
  clarityBarTone: string;
  clarityStatus: LearningClarificationStatus;
  activeLesson: Lesson;
  targetCommitId: string | null;
  previewCommit: CommitRecord | null;
  displayedMessages: ChatMessage[];
  isPreviewMode: boolean;
  interactionLocked?: boolean;
  isChatBusy: boolean;
  clarificationQuestions: string[];
  activeBoardTask: BoardTaskRequirementSheet | null;
  activeRequirementSheet: LearningRequirementSheet | null;
  currentNeedPending: boolean;
  latestBoardDecision: BoardDecision | null;
  chatScrollEndRef: RefObject<HTMLDivElement | null>;
  chatInputRef: RefObject<HTMLTextAreaElement | null>;
  remoteAudioRef: RefObject<HTMLAudioElement | null>;
  modelCatalog: AIModelCatalog;
  selectedTextModel: AIModelSelection;
  selectedRealtimeModel: AIModelSelection;
  selectedTextOption: AIModelOption | null;
  selectedRealtimeOption: AIModelOption | null;
  openModelMenu: ModelMenu;
  setOpenModelMenu: Dispatch<SetStateAction<ModelMenu>>;
  voiceActive: boolean;
  voiceStatusText: string;
  chatInput: string;
  composerAttachments: ChatAttachmentRef[];
  composerMode: ChatInteractionMode;
  composerSelection: SelectionRef | null;
  composerSelections: SelectionRef[];
  includeSelectionInPrompt: boolean;
  onApplySelection: (selection: SelectionRef, popoverPosition: ReturnType<typeof popoverPositionFromDomSelection>) => void;
  onContinueTeaching: () => void;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
  onStopChat: () => void;
  onEditMessage: (message: ChatMessage, nextContent: string) => void | Promise<void>;
  onSelectTextModel: (selection: AIModelSelection) => void;
  onSelectRealtimeModel: (option: AIModelOption) => void;
  onVoiceToggle: () => void | Promise<void>;
  onSpeakMessage: (content: string) => void | Promise<void>;
  onExitPreviewMode: () => void;
  onClearSelection: () => void;
  onUpdateComposerState: (updater: (current: LessonComposerState) => LessonComposerState) => void;
  onAdjustComposerHeight: () => void;
  onError: (message: string) => void;
};

export function CourseStudioChatSidebar({
  packageId,
  resizeHandleProps,
  isResizing,
  clarityBarTone,
  clarityStatus,
  activeLesson,
  targetCommitId,
  previewCommit,
  displayedMessages,
  isPreviewMode,
  interactionLocked = false,
  isChatBusy,
  clarificationQuestions,
  activeBoardTask,
  activeRequirementSheet,
  currentNeedPending,
  latestBoardDecision,
  chatScrollEndRef,
  chatInputRef,
  remoteAudioRef,
  modelCatalog,
  selectedTextModel,
  selectedRealtimeModel,
  selectedTextOption,
  selectedRealtimeOption,
  openModelMenu,
  setOpenModelMenu,
  voiceActive,
  voiceStatusText,
  chatInput,
  composerAttachments,
  composerMode,
  composerSelection,
  composerSelections,
  includeSelectionInPrompt,
  onApplySelection,
  onContinueTeaching,
  onSubmitChat,
  onStopChat,
  onEditMessage,
  onSelectTextModel,
  onSelectRealtimeModel,
  onVoiceToggle,
  onSpeakMessage,
  onExitPreviewMode,
  onClearSelection,
  onUpdateComposerState,
  onAdjustComposerHeight,
  onError,
}: CourseStudioChatSidebarProps) {
  const modelControlsRef = useRef<HTMLDivElement | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");
  const textModelReady = Boolean(selectedTextOption?.enabled);
  const attachmentsReady = composerAttachments.every(
    (attachment) => attachment.kind === "image" || attachment.status === "ready"
  );

  function startEditingMessage(message: ChatMessage) {
    setEditingMessageId(message.id);
    setEditingMessageContent(message.editableContent ?? message.content);
  }

  async function submitEditedMessage(message: ChatMessage) {
    const nextContent = editingMessageContent.trim();
    if (!nextContent || isChatBusy || !textModelReady) {
      return;
    }
    await onEditMessage(message, nextContent);
    setEditingMessageId(null);
    setEditingMessageContent("");
  }

  return (
    <aside className="relative flex h-full min-h-0 flex-col border-r border-gray-200 bg-white">
      <div
        {...resizeHandleProps}
        className={clsx(
          "group absolute inset-y-0 right-[-6px] z-30 flex w-3 cursor-col-resize items-center justify-center outline-none",
          isResizing && "bg-gray-100/60"
        )}
      >
        <span
          className={clsx(
            "h-14 w-1 rounded-full bg-gray-200 opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100",
            isResizing && "bg-gray-400 opacity-100"
          )}
        />
      </div>
      <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
        <div className="space-y-6">
          <CurrentNeedCard
            activeBoardTask={!isPreviewMode ? activeBoardTask : null}
            activeRequirementSheet={!isPreviewMode ? activeRequirementSheet : null}
            barTone={clarityBarTone}
            clarityStatus={clarityStatus}
            currentNeedPending={!isPreviewMode && currentNeedPending}
            isChatBusy={isChatBusy}
            lesson={activeLesson}
            targetCommitId={targetCommitId}
          />
          <div className="space-y-5">
            {previewCommit ? (
              <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-xs leading-6 text-violet-800">
                正在查看 {previewCommit.label} 时的交流记录。
              </div>
            ) : null}

            {displayedMessages.map((message, index) => (
              <div
                key={message.id}
                onMouseUp={() => {
                  const excerpt = window.getSelection()?.toString().trim();
                  if (excerpt) {
                    onApplySelection(
                      {
                        kind: "chat",
                        lesson_id: activeLesson.id,
                        excerpt,
                      },
                      popoverPositionFromDomSelection()
                    );
                  }
                }}
              >
                <CourseChatMessage
                  message={message}
                  onStartEdit={
                    !isPreviewMode &&
                    !interactionLocked &&
                    !isChatBusy &&
                    message.role === "user" &&
                    Boolean(message.commitId && message.parentCommitIds?.[0])
                      ? () => startEditingMessage(message)
                      : undefined
                  }
                  isEditing={editingMessageId === message.id}
                  editingContent={editingMessageContent}
                  onEditingContentChange={setEditingMessageContent}
                  onCancelEdit={() => {
                    setEditingMessageId(null);
                    setEditingMessageContent("");
                  }}
                  onSubmitEdit={() => void submitEditedMessage(message)}
                  isEditDisabled={!editingMessageContent.trim() || isChatBusy}
                  onContinueTeaching={
                    !isPreviewMode &&
                    !interactionLocked &&
                    index === displayedMessages.length - 1 &&
                    message.role === "assistant" &&
                    message.teachingProgress?.has_next_section
                      ? onContinueTeaching
                      : undefined
                  }
                  onFollowUpSuggestion={
                    !isPreviewMode &&
                    !interactionLocked &&
                    !isChatBusy &&
                    textModelReady &&
                    index === displayedMessages.length - 1 &&
                    message.role === "assistant"
                      ? (suggestion) => void onSubmitChat({
                          message: suggestion,
                          interaction_mode: "ask",
                        })
                      : undefined
                  }
                />
                {message.role === "assistant" && message.status === "ready" && message.content.trim() ? (
                  <button
                    type="button"
                    onClick={() => void onSpeakMessage(message.content)}
                    className="ml-11 mt-1 inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
                    aria-label="播报这条回复"
                  >
                    <Volume2 className="h-3 w-3" />
                    播报
                  </button>
                ) : null}
              </div>
            ))}
            <div ref={chatScrollEndRef} aria-hidden="true" />
          </div>

          {!isPreviewMode && !interactionLocked && clarificationQuestions.length ? (
            <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
              <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">需求澄清</p>
              <p className="mt-2 text-xs leading-6 text-sky-900">
                {latestBoardDecision?.reason ?? "AI 还需要再确认一点学习目标，才能决定后面的讲义策略。"}
              </p>
              <div className="mt-3 space-y-2">
                {clarificationQuestions.map((question, index) => (
                  <div key={`${question}-${index}`} className="rounded-lg bg-white px-3 py-2 text-xs leading-6 text-gray-700">
                    {index + 1}. {question}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

        </div>
      </div>

      <div className="shrink-0 border-t border-gray-100 bg-white px-3 py-3">
        <div
          ref={modelControlsRef}
          className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] items-center gap-2"
        >
          <CodexModelSettingsPicker
            open={openModelMenu === "text"}
            onOpenChange={(open) => setOpenModelMenu(open ? "text" : null)}
            selectedModel={selectedTextModel}
            selectedOption={selectedTextOption}
            defaultSelection={modelCatalog.defaults.text}
            options={modelCatalog.text}
            onChange={onSelectTextModel}
          />
          <ModelPicker
            kind="realtime"
            label="语音模型"
            icon={<Volume2 className="h-4 w-4 shrink-0 text-gray-600" />}
            openModelMenu={openModelMenu}
            setOpenModelMenu={setOpenModelMenu}
            selectedModel={selectedRealtimeModel}
            selectedOption={selectedRealtimeOption}
            options={modelCatalog.realtime}
            onSelect={onSelectRealtimeModel}
          />

          <button
            type="button"
            onClick={() => void onVoiceToggle()}
            disabled={interactionLocked}
            aria-label={voiceStatusText}
            title={voiceStatusText}
            className={clsx(
              "flex h-10 w-10 items-center justify-center rounded-full text-white shadow-sm transition-all hover:scale-105 hover:shadow-md",
              voiceActive ? "bg-gray-800 ring-2 ring-gray-200" : "bg-[#1a1a1a]",
              interactionLocked && "cursor-not-allowed opacity-40"
            )}
          >
            <AudioLines className="h-4.5 w-4.5" />
          </button>
        </div>
        <p className="mb-2 truncate px-1 text-center text-[10px] leading-4 text-gray-500">{voiceStatusText}</p>
        <audio ref={remoteAudioRef} autoPlay className="hidden" />

        <div
          className={clsx(
            "overflow-hidden rounded-2xl border bg-white shadow-sm transition-colors focus-within:ring-1",
            composerMode === "direct_edit"
              ? "border-amber-200 focus-within:border-amber-500 focus-within:ring-amber-500"
              : "border-gray-200 focus-within:border-black focus-within:ring-black"
          )}
        >
          {composerSelections.length ? (
            <div className="mx-2.5 mt-2.5 space-y-1.5">
              {composerSelections.length > 1 ? (
                <div className="flex items-center justify-between px-1 text-[10px] text-gray-500">
                  <span>已保留 {composerSelections.length} 个引用</span>
                  <button type="button" onClick={onClearSelection} className="hover:text-black">
                    清空全部
                  </button>
                </div>
              ) : null}
              {composerSelections.map((selection, index) => (
                <div
                  key={`${selection.document_id ?? selection.source_ingestion_id ?? selection.kind}:${selection.segment_id ?? selection.text_hash ?? selection.excerpt}:${index}`}
                  className="flex items-center justify-between gap-2 rounded-xl bg-gray-50 px-2.5 py-1.5"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {composerMode === "direct_edit" ? (
                      <PencilLine className="h-4 w-4 shrink-0 text-amber-600" />
                    ) : (
                      <TextQuote className="h-4 w-4 shrink-0 text-gray-500" />
                    )}
                    <span className="shrink-0 rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-bold text-sky-700">
                      引用 {index + 1} · {composerSelectionLabel(selection)}
                    </span>
                    <p className="min-w-0 truncate text-xs leading-5 text-gray-700">
                      “{selection.excerpt.replace(/\s+/g, " ").slice(0, 160)}”
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      onUpdateComposerState((current) => {
                        const nextSelections = (current.composerSelections ?? []).filter(
                          (_item, itemIndex) => itemIndex !== index
                        );
                        return {
                          ...current,
                          composerSelections: nextSelections,
                          composerSelection: nextSelections[nextSelections.length - 1] ?? null,
                          includeSelectionInPrompt: nextSelections.length ? current.includeSelectionInPrompt : true,
                        };
                      })
                    }
                    aria-label={`移除引用 ${index + 1}`}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-white hover:text-black"
                    title="移除这个引用"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <ChatAttachmentChips
            attachments={composerAttachments}
            disabled={isChatBusy || interactionLocked}
            onRemove={(sourceId) =>
              onUpdateComposerState((current) => ({
                ...current,
                composerAttachments: current.composerAttachments.filter(
                  (attachment) => attachment.source_ingestion_id !== sourceId
                ),
              }))
            }
          />

          <textarea
            ref={chatInputRef}
            value={chatInput}
            rows={1}
            onFocus={() => {
              if (isPreviewMode && !interactionLocked) {
                onExitPreviewMode();
              }
            }}
            readOnly={interactionLocked}
            onChange={(event) =>
              onUpdateComposerState((current) => ({
                ...current,
                chatInput: event.target.value,
              }))
            }
            onInput={() => onAdjustComposerHeight()}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                if (
                  interactionLocked ||
                  isChatBusy ||
                  !textModelReady ||
                  !attachmentsReady ||
                  (!chatInput.trim() && !composerAttachments.length && !composerSelections.length)
                ) {
                  return;
                }
                event.preventDefault();
                void onSubmitChat();
              }
            }}
            placeholder={
              interactionLocked
                ? "合并期间对话已暂停，提交或放弃合并后可继续"
                : isPreviewMode
                  ? "点击输入会回到当前版本并继续对话"
                  : composerMode === "direct_edit"
                    ? "描述要怎么改这段板书，或直接说“重写整篇”..."
                    : composerSelection
                      ? composerSelection.kind === "source"
                        ? "基于引用章节继续提问"
                        : "基于选中内容继续追问"
                      : "给 OpenClass 发消息..."
            }
            className="custom-scrollbar block w-full resize-none border-0 bg-transparent px-3.5 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-gray-400"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <ChatAttachmentMenu
                packageId={packageId}
                attachments={composerAttachments}
                disabled={isChatBusy || isPreviewMode || interactionLocked}
                menuAboveRef={modelControlsRef}
                showInkBoard
                onChange={(attachments) =>
                  onUpdateComposerState((current) => ({
                    ...current,
                    composerAttachments: attachments,
                  }))
                }
                onError={onError}
              />
              <div className="flex shrink-0 items-center gap-1 rounded-md border border-gray-200 bg-gray-50 p-0.5">
                <button
                  type="button"
                  aria-label="Ask Mode"
                  disabled={interactionLocked}
                  onClick={() =>
                    onUpdateComposerState((current) => ({
                      ...current,
                      composerMode: "ask",
                    }))
                  }
                  className={clsx(
                    "flex h-7 w-7 items-center justify-center rounded text-gray-500 transition-colors hover:bg-white hover:text-black",
                    composerMode === "ask" && "bg-white text-black shadow-sm"
                  )}
                  title="Ask Mode"
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  aria-label="Agent Edit Mode"
                  disabled={interactionLocked}
                  onClick={() => {
                    onUpdateComposerState((current) => {
                      const editSelection = (current.composerSelections ?? []).at(-1) ?? current.composerSelection;
                      return {
                        ...current,
                        composerMode: "direct_edit",
                        includeSelectionInPrompt: true,
                        composerSelection: editSelection ?? null,
                        composerSelections: editSelection ? [editSelection] : [],
                      };
                    });
                  }}
                  className={clsx(
                    "flex h-7 w-7 items-center justify-center rounded text-gray-500 transition-colors hover:bg-white hover:text-black",
                    composerMode === "direct_edit" && "bg-white text-amber-700 shadow-sm"
                  )}
                  title="Agent Edit Mode"
                >
                  <BrainCircuit className="h-3.5 w-3.5" />
                </button>
              </div>
              {composerSelection && composerSelections.length ? (
                <button
                  type="button"
                  onClick={() =>
                    onUpdateComposerState((current) => ({
                      ...current,
                      includeSelectionInPrompt: !current.includeSelectionInPrompt,
                    }))
                  }
                  className={clsx(
                    "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition-colors",
                    includeSelectionInPrompt
                      ? "border-gray-200 bg-gray-50 text-gray-600"
                      : "border-gray-200 bg-white text-gray-400"
                  )}
                >
                  <TextQuote className="h-3.5 w-3.5" />
                  {composerSelections.length > 1
                    ? includeSelectionInPrompt
                      ? `包含 ${composerSelections.length} 个引用`
                      : `忽略 ${composerSelections.length} 个引用`
                    : composerSelectionToggleLabel(composerSelection, includeSelectionInPrompt)}
                </button>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => {
                if (isChatBusy) {
                  onStopChat();
                  return;
                }
                if (!interactionLocked) {
                  void onSubmitChat();
                }
              }}
              aria-label={isChatBusy ? "停止回复" : "发送消息"}
              title={isChatBusy ? "停止回复" : "发送消息"}
              disabled={
                !isChatBusy &&
                (interactionLocked ||
                  (!chatInput.trim() && !composerAttachments.length && !composerSelections.length) ||
                  !textModelReady ||
                  !attachmentsReady)
              }
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1a1a1a] text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isChatBusy ? (
                <Square className="h-3.5 w-3.5 fill-current" />
              ) : (
                <Send className="h-4 w-4 -translate-x-[1px]" />
              )}
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}

function ModelPicker({
  kind,
  label,
  icon,
  openModelMenu,
  setOpenModelMenu,
  selectedModel,
  selectedOption,
  options,
  onSelect,
}: {
  kind: "text" | "realtime";
  label: string;
  icon: ReactNode;
  openModelMenu: ModelMenu;
  setOpenModelMenu: Dispatch<SetStateAction<ModelMenu>>;
  selectedModel: AIModelSelection;
  selectedOption: AIModelOption | null;
  options: AIModelOption[];
  onSelect: (option: AIModelOption) => void;
}) {
  return (
    <div className="relative min-w-0">
      <button
        type="button"
        data-testid={`${kind}-model-picker-button`}
        aria-expanded={openModelMenu === kind}
        aria-label={`${label}，当前模型 ${modelButtonLabel(selectedOption, selectedModel)}`}
        onClick={() => setOpenModelMenu((current) => (current === kind ? null : kind))}
        className="flex h-10 w-full items-center justify-center gap-1.5 rounded-full bg-gray-100 px-3 text-sm text-gray-900 transition-colors hover:bg-gray-200"
      >
        <span className="flex min-w-0 items-center gap-2">
          {icon}
          <span className="truncate font-medium">{label}</span>
        </span>
        <ChevronDown
          className={clsx(
            "ml-auto h-4 w-4 shrink-0 text-gray-400 transition-transform",
            openModelMenu === kind && "rotate-180"
          )}
        />
      </button>

      {openModelMenu === kind ? (
        <div
          data-testid={`${kind}-model-picker-menu`}
          className="absolute bottom-full left-0 z-40 mb-2 max-h-[420px] w-56 overflow-y-auto rounded-xl border border-gray-200 bg-white p-1.5 shadow-[0_18px_50px_rgba(0,0,0,0.16)]"
        >
          <p className="px-2.5 pb-1 pt-1.5 text-sm text-gray-400">{label}</p>
          <div>
            {options.map((option) => {
              const selected = modelOptionKey(option) === modelSelectionKey(selectedModel);
              return (
                <button
                  key={`${kind}-${modelOptionKey(option)}`}
                  type="button"
                  onClick={() => onSelect(option)}
                  disabled={!option.enabled}
                  className={clsx(
                    "flex min-h-10 w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm text-gray-900 transition-colors hover:bg-gray-50",
                    !option.enabled && "cursor-not-allowed opacity-45 hover:bg-transparent"
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{option.label}</span>
                    <span className="mt-0.5 block truncate text-xs leading-4 text-gray-400">
                      {PROVIDER_LABELS[option.provider]} / {option.model}
                      {option.configured ? "" : " / 未配置"}
                    </span>
                  </span>
                  {selected ? <Check className="h-4 w-4 shrink-0 text-gray-900" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

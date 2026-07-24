"use client";

import clsx from "clsx";
import { FilePlus2, FileText, ImagePlus, LoaderCircle, PenLine, Plus, X } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState, type ChangeEvent, type RefObject } from "react";
import { createPortal } from "react-dom";

import { ChatInkBoard } from "@/components/course-studio/chat-ink-board";
import { api } from "@/lib/api";
import type { ChatAttachmentRef, SourceIngestionRecord } from "@/types";

const MAX_CHAT_ATTACHMENTS = 10;
const FILE_ACCEPT =
  ".pdf,.epub,.docx,.pptx,.xlsx,.csv,.txt,.md,.markdown,.html,.htm,.json,.xml,.png,.jpg,.jpeg,.webp,.gif,.mp3,.m4a,.wav,.ogg,.mp4,.mov,.webm,.mpeg,application/pdf,application/epub+zip,text/*,image/*,audio/*,video/*,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function attachmentFromSource(source: SourceIngestionRecord): ChatAttachmentRef {
  return {
    source_ingestion_id: source.id,
    name: source.file_name || source.title,
    mime_type: source.mime_type || "application/octet-stream",
    size_bytes: source.size_bytes,
    kind: source.mime_type.startsWith("image/") ? "image" : "file",
    status: source.status,
  };
}

function readableSize(sizeBytes: number) {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${Math.round(sizeBytes / 1024)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ChatAttachmentChips({
  attachments,
  disabled,
  onRemove,
}: {
  attachments: ChatAttachmentRef[];
  disabled: boolean;
  onRemove: (sourceId: string) => void;
}) {
  if (!attachments.length) {
    return null;
  }
  return (
    <div className="mx-2.5 mt-2.5 flex flex-wrap gap-2" aria-label="已添加附件">
      {attachments.map((attachment) => (
        <div
          key={attachment.source_ingestion_id}
          className="flex min-w-0 max-w-full items-center gap-2 rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-gray-600 shadow-sm">
            {attachment.kind === "image" ? <ImagePlus className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
          </span>
          <span className="min-w-0">
            <span className="block max-w-48 truncate text-xs font-medium text-gray-800">{attachment.name}</span>
            <span className="block text-[10px] text-gray-500">
              {attachment.kind === "file" && attachment.status !== "ready"
                ? attachment.status === "failed"
                  ? "解析失败"
                  : "正在解析"
                : readableSize(attachment.size_bytes)}
            </span>
          </span>
          <button
            type="button"
            onClick={() => onRemove(attachment.source_ingestion_id)}
            disabled={disabled}
            aria-label={`移除附件 ${attachment.name}`}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition hover:bg-white hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

export function ChatAttachmentMenu({
  packageId,
  attachments,
  disabled,
  menuAboveRef,
  limitLabel = "每条消息",
  testIdPrefix = "chat",
  triggerText = "",
  triggerHint = "",
  showInkBoard = false,
  onChange,
  onError,
}: {
  packageId: string;
  attachments: ChatAttachmentRef[];
  disabled: boolean;
  menuAboveRef: RefObject<HTMLElement | null>;
  limitLabel?: string;
  testIdPrefix?: string;
  triggerText?: string;
  triggerHint?: string;
  showInkBoard?: boolean;
  onChange: (attachments: ChatAttachmentRef[]) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [inkBoardOpen, setInkBoardOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number } | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const inkBoardRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pendingFileIds = attachments
    .filter((attachment) => attachment.kind === "file" && attachment.status !== "ready" && attachment.status !== "failed")
    .map((attachment) => attachment.source_ingestion_id)
    .sort()
    .join(",");

  const applySourceStatuses = useEffectEvent((sources: SourceIngestionRecord[]) => {
    const sourceById = new Map(sources.map((source) => [source.id, source]));
    const next = attachments.map((attachment) => {
      const source = sourceById.get(attachment.source_ingestion_id);
      return source ? attachmentFromSource(source) : attachment;
    });
    if (next.some((attachment, index) => attachment.status !== attachments[index]?.status)) {
      onChange(next);
    }
  });

  useEffect(() => {
    if (!pendingFileIds) {
      return;
    }
    let disposed = false;
    async function refreshStatuses() {
      try {
        const sources = await api.listPackageSources(packageId);
        if (!disposed) {
          applySourceStatuses(sources);
        }
      } catch {
        // The normal upload/error surface remains authoritative; retry on the next interval.
      }
    }
    void refreshStatuses();
    const intervalId = window.setInterval(() => void refreshStatuses(), 2000);
    return () => {
      disposed = true;
      window.clearInterval(intervalId);
    };
  }, [packageId, pendingFileIds]);

  useEffect(() => {
    if (!open && !inkBoardOpen) {
      return;
    }
    function closeMenu(event: MouseEvent) {
      const target = event.target as Node;
      if (
        !rootRef.current?.contains(target) &&
        !menuRef.current?.contains(target) &&
        !inkBoardRef.current?.contains(target)
      ) {
        setOpen(false);
        setInkBoardOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setInkBoardOpen(false);
      }
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [inkBoardOpen, open]);

  useEffect(() => {
    if (!open && !inkBoardOpen) {
      return;
    }
    function updateMenuPosition() {
      const trigger = rootRef.current;
      const menu = inkBoardOpen ? inkBoardRef.current : menuRef.current;
      const boundary = menuAboveRef.current;
      if (!trigger || !menu || !boundary) {
        return;
      }
      const triggerRect = trigger.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const boundaryRect = boundary.getBoundingClientRect();
      setMenuPosition({
        left: Math.max(8, Math.min(triggerRect.left, window.innerWidth - menuRect.width - 8)),
        top: Math.max(8, boundaryRect.top - menuRect.height - 8),
      });
    }
    const frameId = window.requestAnimationFrame(updateMenuPosition);
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [inkBoardOpen, menuAboveRef, open]);

  async function uploadSelectedFiles(files: File[]) {
    if (!files.length || disabled || isUploading) {
      return 0;
    }
    const availableSlots = Math.max(0, MAX_CHAT_ATTACHMENTS - attachments.length);
    if (!availableSlots) {
      onError(`${limitLabel}最多添加 ${MAX_CHAT_ATTACHMENTS} 个附件。`);
      return 0;
    }
    const selectedFiles = files.slice(0, availableSlots);
    if (selectedFiles.length < files.length) {
      onError(`${limitLabel}最多添加 ${MAX_CHAT_ATTACHMENTS} 个附件，已保留前 ${selectedFiles.length} 个。`);
    }
    setOpen(false);
    setIsUploading(true);
    setUploadProgress(0);
    const imported: ChatAttachmentRef[] = [];
    const failures: string[] = [];
    try {
      for (const [index, file] of selectedFiles.entries()) {
        try {
          const source = await api.importPackageSource(
            packageId,
            { file },
            {
              onUploadProgress: (progress) => {
                setUploadProgress(Math.round(((index + progress / 100) / selectedFiles.length) * 100));
              },
            }
          );
          imported.push(attachmentFromSource(source));
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "上传失败"}`);
        }
      }
      if (imported.length) {
        const seen = new Set(attachments.map((item) => item.source_ingestion_id));
        onChange([...attachments, ...imported.filter((item) => !seen.has(item.source_ingestion_id))]);
      }
      if (failures.length) {
        onError(failures.join("；"));
      }
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
    }
    return imported.length;
  }

  async function uploadFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    await uploadSelectedFiles(files);
  }

  async function uploadInkImage(imageDataUrl: string) {
    try {
      const response = await fetch(imageDataUrl);
      const blob = await response.blob();
      const file = new File([blob], `handwriting-${Date.now()}.png`, { type: blob.type || "image/png" });
      const importedCount = await uploadSelectedFiles([file]);
      if (importedCount) {
        setInkBoardOpen(false);
      }
      return importedCount > 0;
    } catch (error) {
      onError(error instanceof Error ? error.message : "手写内容添加失败");
      return false;
    }
  }

  const menu =
    open && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label="添加内容"
            style={menuPosition ?? { left: 0, top: 0, visibility: "hidden" }}
            className="fixed z-[100] w-48 rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl"
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => imageInputRef.current?.click()}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 hover:text-black"
            >
              <ImagePlus className="h-4 w-4 text-gray-500" />
              添加图片
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 hover:text-black"
            >
              <FilePlus2 className="h-4 w-4 text-gray-500" />
              添加文件
            </button>
            {showInkBoard ? (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuPosition(null);
                  setOpen(false);
                  setInkBoardOpen(true);
                }}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 hover:text-black"
              >
                <PenLine className="h-4 w-4 text-gray-500" />
                展开手写板
              </button>
            ) : null}
          </div>,
          document.body
        )
      : null;

  const inkBoard =
    inkBoardOpen && typeof document !== "undefined"
      ? createPortal(
          <ChatInkBoard
            panelRef={inkBoardRef}
            position={menuPosition ?? { left: 0, top: 0, visibility: "hidden" }}
            disabled={disabled || isUploading}
            onClose={() => setInkBoardOpen(false)}
            onSubmit={uploadInkImage}
          />,
          document.body
        )
      : null;

  return (
    <>
      <div ref={rootRef} className="relative shrink-0">
        <input
          ref={imageInputRef}
          type="file"
          multiple
          accept="image/*"
          onChange={(event) => void uploadFiles(event)}
          className="hidden"
          disabled={disabled || isUploading}
          data-testid={`${testIdPrefix}-image-input`}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={FILE_ACCEPT}
          onChange={(event) => void uploadFiles(event)}
          className="hidden"
          disabled={disabled || isUploading}
          data-testid={`${testIdPrefix}-file-input`}
        />
        <button
          type="button"
          onClick={() => {
            if (!open) {
              setMenuPosition(null);
            }
            setInkBoardOpen(false);
            setOpen(!open);
          }}
          disabled={disabled || isUploading}
          aria-label={isUploading ? "正在添加附件" : "添加附件"}
          aria-expanded={open || inkBoardOpen}
          aria-haspopup="menu"
          className={clsx(
            "flex h-8 items-center justify-center text-gray-600 transition hover:bg-gray-100 hover:text-black disabled:cursor-not-allowed disabled:opacity-50",
            triggerText ? "w-auto gap-1.5 rounded-lg px-1.5" : "w-8 rounded-full",
            (open || inkBoardOpen) && "bg-gray-100 text-black"
          )}
          title={isUploading ? `上传中 ${uploadProgress ?? 0}%` : "添加附件"}
        >
          {isUploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          {triggerText ? <span className="text-[11px] font-medium">{triggerText}</span> : null}
          {triggerHint ? <span className="text-[10px] text-gray-400">{triggerHint}</span> : null}
        </button>
      </div>
      {menu}
      {inkBoard}
    </>
  );
}

"use client";

import clsx from "clsx";
import { BookOpen, Check, Download, FileText, Globe2, Pencil, RefreshCw, RotateCcw, TextQuote, Trash2, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import {
  SourceBatchControls,
  type SourceSortOption,
} from "@/components/course-studio/source-batch-controls";
import { SourceCatalogModelPicker } from "@/components/course-studio/source-catalog-model-picker";
import { SourceChapterTree } from "@/components/course-studio/source-chapter-tree";
import {
  findModelOption,
  persistModelSelection,
  readStoredModelSelection,
  resolveModelSelection,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import {
  createOpenNotebookSourceSelection,
} from "@/components/course-studio/source-reference";
import {
  getSourceProcessingState,
  isDirectoryCatalogSource,
  SourceCodexActivity,
  SourceProcessingProgress,
} from "@/components/course-studio/source-processing-progress";
import {
  SourceStructureQualitySummary,
  sourceStructureBadgeClass,
  sourceStructureBadgeLabel,
  sourceStructureQualityLevel,
  sourceStructureQualityNote,
} from "@/components/course-studio/source-structure-quality";
import { api } from "@/lib/api";
import { useSourceBatchManagement } from "@/hooks/course-studio/use-source-batch-management";
import type { SourceCatalogCacheController } from "@/hooks/course-studio/use-source-catalog-cache";
import type {
  AIModelOption,
  AIModelSelection,
  SelectionRef,
  SourceCatalogView,
  SourceIngestionRecord,
} from "@/types";

type SourceImportPanelProps = {
  packageId: string;
  catalogCache: SourceCatalogCacheController;
  catalogModelOptions: AIModelOption[];
  defaultCatalogModel: AIModelSelection;
  disabled?: boolean;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
};

const STATUS_LABELS: Record<SourceIngestionRecord["status"], string> = {
  queued: "等待",
  fetching: "获取",
  parsing: "解析",
  indexing: "索引",
  ready: "就绪",
  failed: "失败",
};

const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>(["queued", "fetching", "parsing", "indexing"]);
const ACTIVE_STRUCTURE_STATUSES = new Set<SourceIngestionRecord["structure_status"]>(["pending", "building"]);
const CATALOG_MODEL_STORAGE_KEY = "blackboard-ai:selected-catalog-model";

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function SourceImportPanel({
  packageId,
  catalogCache,
  catalogModelOptions,
  defaultCatalogModel,
  disabled = false,
  onError,
  onSourceReference,
}: SourceImportPanelProps) {
  const [sources, setSources] = useState<SourceIngestionRecord[]>([]);
  const [sourceUri, setSourceUri] = useState("");
  const [title, setTitle] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [removingSourceId, setRemovingSourceId] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [sortOption, setSortOption] = useState<SourceSortOption>("uploaded_desc");
  const [catalogModel, setCatalogModel] = useState<AIModelSelection>(defaultCatalogModel);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const didRestoreCatalogModelRef = useRef(false);
  const {
    ensureCurrentSource,
    invalidateSource,
    invalidateSources,
    prefetchPackage,
    putCatalog,
  } = catalogCache;
  const batchManagement = useSourceBatchManagement({
    packageId,
    sourceIds: sources.map((source) => source.id),
    disabled: disabled || isImporting || Boolean(removingSourceId),
    onRemoved: (sourceIds) => {
      const removedIds = new Set(sourceIds);
      setSources((current) => current.filter((source) => !removedIds.has(source.id)));
      invalidateSources(sourceIds);
    },
    onError,
  });
  const selectedCatalogModelOption = findModelOption(catalogModelOptions, catalogModel);
  const activeCatalogModel = selectedCatalogModelOption?.enabled
    ? selectionForModelOption(selectedCatalogModelOption, catalogModel)
    : null;
  const sortedSources = sortSources(sources, sortOption);

  useEffect(() => {
    if (
      didRestoreCatalogModelRef.current ||
      !catalogModelOptions.some((option) => option.enabled)
    ) {
      return;
    }
    didRestoreCatalogModelRef.current = true;
    setCatalogModel(
      resolveModelSelection(
        catalogModelOptions,
        readStoredModelSelection(CATALOG_MODEL_STORAGE_KEY),
        defaultCatalogModel
      )
    );
  }, [catalogModelOptions, defaultCatalogModel]);

  function updateCatalogModel(selection: AIModelSelection) {
    setCatalogModel(selection);
    persistModelSelection(CATALOG_MODEL_STORAGE_KEY, selection);
  }

  useEffect(() => {
    let active = true;
    void prefetchPackage(packageId).catch((error) => {
      if (active) {
        onError(error instanceof Error ? error.message : "资料目录读取失败");
      }
    });
    return () => {
      active = false;
    };
  }, [onError, packageId, prefetchPackage, sources]);

  const refreshSources = useCallback(async () => {
    if (!packageId) {
      return;
    }
    setIsLoading(true);
    try {
      setSources(await api.listPackageSources(packageId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料列表读取失败");
    } finally {
      setIsLoading(false);
    }
  }, [onError, packageId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshSources();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshSources]);

  useEffect(() => {
    if (disabled || !sources.some(sourceNeedsRefresh)) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void refreshSources();
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [disabled, refreshSources, sources]);

  useEffect(() => {
    if (!catalogCache.prefetchedPackageIds.has(packageId)) {
      return;
    }
    let active = true;
    void Promise.all(sources.map((source) => ensureCurrentSource(packageId, source))).catch(
      (error) => {
        if (active) {
          onError(error instanceof Error ? error.message : "资料目录更新失败");
        }
      }
    );
    return () => {
      active = false;
    };
  }, [catalogCache.prefetchedPackageIds, ensureCurrentSource, onError, packageId, sources]);

  async function submitUrl() {
    const uri = sourceUri.trim();
    if (!uri || disabled || isImporting) {
      return;
    }
    setIsImporting(true);
    try {
      const record = await api.importPackageSource(packageId, { sourceUri: uri, title: title.trim() });
      setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
      setSourceUri("");
      setTitle("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "URL 导入失败");
    } finally {
      setIsImporting(false);
    }
  }

  async function submitFiles(files: FileList | File[] | null) {
    const fileList = Array.from(files ?? []);
    if (!fileList.length || disabled || isImporting) {
      return;
    }
    setIsImporting(true);
    setUploadProgress(0);
    const imported: SourceIngestionRecord[] = [];
    const failures: string[] = [];
    try {
      for (const [fileIndex, file] of fileList.entries()) {
        try {
          const record = await api.importPackageSource(
            packageId,
            {
              file,
              title: fileList.length === 1 ? title.trim() : "",
              catalogModel: activeCatalogModel,
            },
            {
              onUploadProgress: (fileProgress) => {
                const overallProgress = ((fileIndex + fileProgress / 100) / fileList.length) * 100;
                setUploadProgress(Math.round(overallProgress));
              },
            }
          );
          imported.push(record);
          setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "导入失败"}`);
        }
      }
      setTitle("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } finally {
      setIsImporting(false);
      setUploadProgress(null);
    }
    if (failures.length) {
      onError(`${imported.length} 个文件导入成功，${failures.length} 个失败：${failures.join("；")}`);
    }
  }

  async function removeSource(sourceId: string) {
    if (!sourceId || disabled || removingSourceId) {
      return;
    }
    setRemovingSourceId(sourceId);
    try {
      await api.deletePackageSource(packageId, sourceId);
      setSources((current) => current.filter((source) => source.id !== sourceId));
      invalidateSource(sourceId);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料移除失败");
    } finally {
      setRemovingSourceId(null);
    }
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    if (!disabled && !isImporting) {
      event.dataTransfer.dropEffect = "copy";
      setIsDragActive(true);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = disabled || isImporting ? "none" : "copy";
    if (!disabled && !isImporting) {
      setIsDragActive(true);
    }
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragActive(false);
    if (disabled || isImporting) {
      return;
    }
    void submitFiles(event.dataTransfer.files);
  }

  const uploadButton = (
    <button
      type="button"
      onClick={() => fileInputRef.current?.click()}
      disabled={disabled || isImporting}
      className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <UploadCloud className="h-3.5 w-3.5" />
      {isImporting ? "处理中" : isDragActive ? "松开上传" : "上传资料"}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <label className="text-[11px] font-bold uppercase tracking-widest text-gray-500">标题</label>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="可选"
          className="mt-2 h-9 w-full rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
          disabled={disabled || isImporting}
        />
        <SourceCatalogModelPicker
          options={catalogModelOptions}
          selection={catalogModel}
          defaultSelection={defaultCatalogModel}
          disabled={disabled || isImporting}
          onChange={updateCatalogModel}
        />
        <p className="mt-1 text-[11px] leading-5 text-gray-400">
          仅用于上传后解析文件结构并建立目录；后续按章阅读使用聊天框当前模型。
        </p>
        <label className="mt-3 block text-[11px] font-bold uppercase tracking-widest text-gray-500">URL</label>
        <div className="mt-2 flex gap-2">
          <input
            value={sourceUri}
            onChange={(event) => setSourceUri(event.target.value)}
            placeholder="https://"
            className="h-9 min-w-0 flex-1 rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
            disabled={disabled || isImporting}
          />
          <button
            type="button"
            onClick={() => void submitUrl()}
            disabled={!sourceUri.trim() || disabled || isImporting}
            className="flex h-9 w-9 items-center justify-center rounded-md bg-black text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
            title="导入 URL"
            aria-label="导入 URL"
          >
            <Globe2 className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-3 flex items-center justify-end">
          <input
            ref={fileInputRef}
            data-testid="source-file-input"
            type="file"
            multiple
            accept=".pdf,.epub,.docx,.pptx,.xlsx,.csv,.txt,.md,.markdown,.html,.htm,.json,.xml,.png,.jpg,.jpeg,.webp,.gif,.mp3,.m4a,.wav,.ogg,.mp4,.mov,.webm,.mpeg,application/pdf,application/epub+zip,text/*,image/*,audio/*,video/*,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => void submitFiles(event.target.files)}
            className="hidden"
            disabled={disabled || isImporting}
          />
          <button
            type="button"
            onClick={() => void refreshSources()}
            disabled={disabled || isLoading}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-200 text-gray-500 transition hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
            title="刷新"
            aria-label="刷新资料状态"
          >
            <RefreshCw className={clsx("h-4 w-4", isLoading && "animate-spin")} />
          </button>
        </div>
      </div>

      <div
        aria-busy={isImporting}
        aria-disabled={disabled || isImporting}
        aria-label="资料上传区域"
        data-testid="source-upload-dropzone"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={clsx(
          "rounded-lg transition",
          isDragActive && !disabled && !isImporting && "bg-blue-50/70 ring-2 ring-blue-200"
        )}
      >
        {sources.length ? (
          <div className="space-y-2">
            <div
              className={clsx(
                "flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
              )}
            >
              {uploadButton}
              {isImporting ? (
                <SourceProcessingProgress
                  className="w-full max-w-60 text-left"
                  label={uploadProgress === 100 ? "上传完成，正在创建处理任务" : "正在上传资料"}
                  value={uploadProgress ?? undefined}
                />
              ) : (
                <span>继续拖入资料，或点击上传。</span>
              )}
            </div>
            <SourceBatchControls
              sourceCount={sources.length}
              selectedCount={batchManagement.selectedCount}
              allSelected={batchManagement.allSelected}
              isActive={batchManagement.isActive}
              isRemoving={batchManagement.isRemoving}
              disabled={disabled || isImporting || Boolean(removingSourceId)}
              sortOption={sortOption}
              onSortChange={setSortOption}
              onStart={batchManagement.start}
              onCancel={batchManagement.cancel}
              onToggleAll={batchManagement.toggleAll}
              onClear={batchManagement.clear}
              onRemove={() => void batchManagement.removeSelected()}
            />
            {sortedSources.map((source) => (
              <SourceRow
                key={source.id}
                packageId={packageId}
                source={source}
                catalogModel={activeCatalogModel}
                catalog={catalogCache.catalogsBySourceId.get(source.id) ?? null}
                isCatalogLoading={
                  catalogCache.prefetchingPackageIds.has(packageId) ||
                  catalogCache.loadingSourceIds.has(source.id)
                }
                isRemoving={removingSourceId === source.id}
                onRemove={() => void removeSource(source.id)}
                selectionMode={batchManagement.isActive}
                isSelected={batchManagement.selectedSourceIds.has(source.id)}
                selectionDisabled={batchManagement.isRemoving}
                onToggleSelection={() => batchManagement.toggle(source.id)}
                onError={onError}
                onSourceReference={onSourceReference}
                onSourceUpdate={(updatedSource) =>
                  setSources((current) =>
                    current.map((item) => (item.id === updatedSource.id ? updatedSource : item))
                  )
                }
                onCatalogUpdate={(catalog) => {
                  putCatalog(catalog);
                  setSources((current) =>
                    current.map((item) =>
                      item.id === catalog.source.id ? mergeSourceWithCatalog(item, catalog) : item
                    )
                  );
                }}
                onCatalogInvalidate={() => invalidateSource(source.id)}
                onRefresh={refreshSources}
              />
            ))}
          </div>
        ) : (
          <div
            className={clsx(
              "flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
              isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
            )}
          >
            <UploadCloud className={clsx("h-8 w-8", isDragActive ? "text-blue-600" : "text-gray-300")} />
            {uploadButton}
            {isImporting ? (
              <SourceProcessingProgress
                className="w-full max-w-60 text-left"
                label={uploadProgress === 100 ? "上传完成，正在创建处理任务" : "正在上传资料"}
                value={uploadProgress ?? undefined}
              />
            ) : (
              <span>{isDragActive ? "松开上传资料。" : "拖拽文件到这里，或点击上传资料。"}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const SOURCE_TITLE_COLLATOR = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});

function sortSources(sources: SourceIngestionRecord[], sortOption: SourceSortOption) {
  return sources
    .map((source, index) => ({ source, index }))
    .sort((left, right) => {
      if (sortOption === "name_asc" || sortOption === "name_desc") {
        const titleOrder = SOURCE_TITLE_COLLATOR.compare(
          left.source.title || left.source.file_name,
          right.source.title || right.source.file_name
        );
        if (titleOrder !== 0) {
          return sortOption === "name_asc" ? titleOrder : -titleOrder;
        }
      } else {
        const leftCreatedAt = Date.parse(left.source.created_at) || 0;
        const rightCreatedAt = Date.parse(right.source.created_at) || 0;
        const createdAtOrder = leftCreatedAt - rightCreatedAt;
        if (createdAtOrder !== 0) {
          return sortOption === "uploaded_asc" ? createdAtOrder : -createdAtOrder;
        }
      }
      return left.index - right.index;
    })
    .map(({ source }) => source);
}

function SourceRow({
  packageId,
  source,
  catalogModel,
  catalog,
  isCatalogLoading,
  isRemoving,
  onRemove,
  selectionMode,
  isSelected,
  selectionDisabled,
  onToggleSelection,
  onError,
  onSourceReference,
  onSourceUpdate,
  onCatalogUpdate,
  onCatalogInvalidate,
  onRefresh,
}: {
  packageId: string;
  source: SourceIngestionRecord;
  catalogModel: AIModelSelection | null;
  catalog: SourceCatalogView | null;
  isCatalogLoading: boolean;
  isRemoving: boolean;
  onRemove: () => void;
  selectionMode: boolean;
  isSelected: boolean;
  selectionDisabled: boolean;
  onToggleSelection: () => void;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  onSourceUpdate: (source: SourceIngestionRecord) => void;
  onCatalogUpdate: (catalog: SourceCatalogView) => void;
  onCatalogInvalidate: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [isStructureOpen, setIsStructureOpen] = useState(false);
  const [isRebuildingStructure, setIsRebuildingStructure] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(source.title);
  const [content, setContent] = useState<string | null>(null);
  const [draftContent, setDraftContent] = useState("");
  const [isContentOpen, setIsContentOpen] = useState(false);
  const [isLoadingContent, setIsLoadingContent] = useState(false);
  const [isEditingContent, setIsEditingContent] = useState(false);
  const [isSavingContent, setIsSavingContent] = useState(false);
  const [expandedChapterIds, setExpandedChapterIds] = useState<Set<string>>(new Set());
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
  const isOpenNotebookManaged = metadataString(source, "source_processing_owner") === "open_notebook";
  const isDirectoryOnlyCatalog =
    isDirectoryCatalogSource(source) ||
    catalog?.strategy === "codex_directory_v1" ||
    catalog?.catalog_schema_version === "codex_directory_v1";
  const sourceQuality = source.structure_quality;
  const viewQuality = catalog?.quality;
  const structureQuality =
    viewQuality?.level && viewQuality.level !== "unassessed"
      ? viewQuality
      : sourceQuality;
  const structureQualityLevel = sourceStructureQualityLevel(source, structureQuality);
  const structureLabel = sourceStructureBadgeLabel(
    source,
    structureQualityLevel,
    structureQuality
  );
  const structureNote = sourceStructureQualityNote(
    source,
    structureQuality,
    structureQualityLevel
  );
  const processingState = getSourceProcessingState(source);

  useEffect(() => {
    if (!isRebuildingStructure && !isRetrying) {
      return;
    }
    void onRefresh();
    const intervalId = window.setInterval(() => {
      void onRefresh();
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [isRebuildingStructure, isRetrying, onRefresh]);

  function toggleStructure() {
    if (!isReady) {
      return;
    }
    setIsStructureOpen((current) => !current);
  }

  async function rebuildStructure() {
    if (!isReady || isRebuildingStructure) {
      return;
    }
    setIsRebuildingStructure(true);
    try {
      const usesDirectoryCatalog =
        isDirectoryCatalogSource(source) ||
        catalog?.strategy === "codex_directory_v1" ||
        catalog?.catalog_schema_version === "codex_directory_v1";
      if (usesDirectoryCatalog) {
        onCatalogUpdate(
          await api.rebuildPackageSourceCatalog(packageId, source.id, catalogModel)
        );
      } else {
        const legacyView = await api.rebuildPackageSourceStructure(packageId, source.id);
        onSourceUpdate(legacyView.source);
        onCatalogUpdate(await api.getPackageSourceCatalog(packageId, source.id));
      }
      setIsStructureOpen(true);
      setExpandedChapterIds(new Set());
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料目录重建失败");
    } finally {
      setIsRebuildingStructure(false);
    }
  }

  async function retrySource() {
    if (isRetrying) {
      return;
    }
    setIsRetrying(true);
    try {
      onCatalogInvalidate();
      onSourceUpdate(await api.retryPackageSource(packageId, source.id, catalogModel));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料重试失败");
    } finally {
      setIsRetrying(false);
    }
  }

  async function saveTitle() {
    const nextTitle = draftTitle.trim();
    if (!nextTitle || nextTitle === source.title) {
      setDraftTitle(source.title);
      setIsEditingTitle(false);
      return;
    }
    try {
      onSourceUpdate(await api.renamePackageSource(packageId, source.id, nextTitle));
      setIsEditingTitle(false);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料重命名失败");
    }
  }

  async function toggleContent() {
    const nextOpen = !isContentOpen;
    setIsContentOpen(nextOpen);
    if (!nextOpen || content !== null || isLoadingContent) {
      return;
    }
    setIsLoadingContent(true);
    try {
      const nextContent = (await api.getPackageSourceContent(packageId, source.id)).content;
      setContent(nextContent);
      setDraftContent(nextContent);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料正文读取失败");
    } finally {
      setIsLoadingContent(false);
    }
  }

  async function saveContent() {
    const nextContent = draftContent.trim();
    if (!nextContent || isSavingContent) {
      return;
    }
    setIsSavingContent(true);
    try {
      const result = await api.updatePackageSourceContent(packageId, source.id, nextContent);
      setContent(result.content);
      setDraftContent(result.content);
      onCatalogInvalidate();
      setExpandedChapterIds(new Set());
      setIsEditingContent(false);
      onSourceUpdate(result.source);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料正文保存失败");
    } finally {
      setIsSavingContent(false);
    }
  }

  async function downloadSource() {
    try {
      const blob = await api.downloadPackageSource(packageId, source.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = source.file_name || source.title;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料下载失败");
    }
  }

  const hasChapters = Boolean(catalog?.chapters.length);
  const canViewDirectory = Boolean(
    hasChapters ||
      source.structure_has_verified_toc ||
      source.structure_quality?.total_chapter_count
  );
  function toggleChapter(chapterId: string) {
    setExpandedChapterIds((current) => {
      const next = new Set(current);
      if (next.has(chapterId)) {
        next.delete(chapterId);
      } else {
        next.add(chapterId);
      }
      return next;
    });
  }

  return (
    <div
      className={clsx(
        "rounded-lg border bg-white p-3 transition",
        selectionMode && isSelected ? "border-blue-300 ring-1 ring-blue-100" : "border-gray-200"
      )}
    >
      <div className="flex items-start gap-3">
        {selectionMode ? (
          <label className="mt-0.5 flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md bg-blue-50">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={onToggleSelection}
              disabled={selectionDisabled}
              className="h-4 w-4 rounded border-blue-300 accent-blue-600 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={`选择资料 ${source.title}`}
            />
          </label>
        ) : (
          <div
            className={clsx(
              "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
              isReady ? "bg-emerald-50 text-emerald-700" : isFailed ? "bg-rose-50 text-rose-700" : "bg-gray-50 text-gray-500"
            )}
          >
            <UploadCloud className="h-4 w-4" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="space-y-2">
            {isEditingTitle ? (
              <div className="flex min-w-0 flex-1 items-center gap-1">
                <input
                  value={draftTitle}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void saveTitle();
                    if (event.key === "Escape") setIsEditingTitle(false);
                  }}
                  className="h-7 min-w-0 flex-1 rounded border border-gray-300 px-2 text-sm outline-none focus:border-black"
                  autoFocus
                />
                <button type="button" onClick={() => void saveTitle()} className="rounded p-1 text-emerald-700" aria-label="保存资料标题">
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button type="button" onClick={() => setIsEditingTitle(false)} className="rounded p-1 text-gray-500" aria-label="取消修改资料标题">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex min-w-0 items-start gap-1">
                <p className="min-w-0 flex-1 break-words text-sm font-semibold leading-5 text-gray-900">{source.title}</p>
                <button type="button" onClick={() => setIsEditingTitle(true)} className="shrink-0 rounded p-1 text-gray-400 hover:text-black" aria-label={`重命名资料 ${source.title}`}>
                  <Pencil className="h-3 w-3" />
                </button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={clsx(
                  "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                  isReady
                    ? "bg-emerald-50 text-emerald-700"
                    : isFailed
                    ? "bg-rose-50 text-rose-700"
                    : "bg-gray-100 text-gray-600"
                )}
              >
                {source.status === "indexing" && isDirectoryCatalogSource(source)
                  ? "建目录"
                  : STATUS_LABELS[source.status]}
              </span>
              {isReady ? (
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    sourceStructureBadgeClass(
                      source,
                      structureQualityLevel,
                      structureQuality
                    )
                  )}
                >
                  {isOpenNotebookManaged ? "OpenNotebook" : structureLabel}
                </span>
              ) : null}
              {isReady && !isOpenNotebookManaged && !isDirectoryOnlyCatalog ? (
                <button
                  type="button"
                  onClick={() => void toggleContent()}
                  disabled={isLoadingContent}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black disabled:opacity-50"
                  title="查看完整正文"
                  aria-label={`查看资料正文 ${source.title}`}
                >
                  {isLoadingContent ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
                </button>
              ) : null}
              {isReady && isOpenNotebookManaged && onSourceReference ? (
                <button
                  type="button"
                  onClick={() => onSourceReference(createOpenNotebookSourceSelection(source))}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-blue-600 transition hover:bg-blue-50"
                  title="引用整份 OpenNotebook 资料"
                  aria-label={`引用整份资料 ${source.title}`}
                >
                  <TextQuote className="h-3.5 w-3.5" />
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void downloadSource()}
                className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black"
                title="下载原始资料"
                aria-label={`下载资料 ${source.title}`}
              >
                <Download className="h-3.5 w-3.5" />
              </button>
              {isFailed ? (
                <button
                  type="button"
                  onClick={() => void retrySource()}
                  disabled={isRetrying}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-amber-600 transition hover:bg-amber-50 disabled:opacity-50"
                  title="重试资料处理"
                  aria-label={`重试资料 ${source.title}`}
                >
                  <RotateCcw className={clsx("h-3.5 w-3.5", isRetrying && "animate-spin")} />
                </button>
              ) : null}
              {isReady && !isOpenNotebookManaged ? (
                <button
                  type="button"
                  onClick={toggleStructure}
                  className={clsx(
                    "flex min-w-10 flex-col items-center justify-center gap-0.5 rounded-md border border-transparent px-1 py-1 text-[10px] leading-none transition disabled:cursor-not-allowed disabled:opacity-50",
                    canViewDirectory
                      ? "text-blue-600 hover:border-blue-100 hover:bg-blue-50"
                      : "text-gray-400 hover:border-gray-200 hover:bg-gray-50 hover:text-gray-600"
                  )}
                  title={canViewDirectory ? "查看目录" : "查看目录状态"}
                  aria-label={`${canViewDirectory ? "查看资料目录" : "查看资料目录状态"} ${source.title}`}
                >
                  {isCatalogLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <BookOpen className="h-3.5 w-3.5" />}
                  <span>{isStructureOpen ? "收起" : "展开"}</span>
                </button>
              ) : null}
              {!selectionMode ? (
                <button
                  type="button"
                  onClick={onRemove}
                  disabled={isRemoving}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-gray-400 transition hover:border-rose-100 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
                  title="移除资料"
                  aria-label={`移除资料 ${source.title}`}
                >
                  {isRemoving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                </button>
              ) : null}
            </div>
          </div>
          <p className="mt-2 break-all text-xs leading-5 text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {processingState ? (
            <SourceProcessingProgress
              className="mt-2"
              label={processingState.label}
              detail={processingState.detail}
              value={processingState.value}
              activity={processingState.activity}
            />
          ) : null}
          {!processingState && source.ingestion_job?.agent_activity?.length ? (
            <SourceCodexActivity
              className="mt-2"
              events={source.ingestion_job.agent_activity}
              title="最近一次后端 OpenClass 输出"
              expandedByDefault={false}
            />
          ) : null}
          {isReady ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">
              {isOpenNotebookManaged
                ? "资料正文由 OpenNotebook 处理；引用后按本轮问题检索相关片段。"
                : structureNote}
            </p>
          ) : null}
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
          {source.structure_error ? <p className="mt-2 text-xs leading-5 text-amber-700">{source.structure_error}</p> : null}
          {isContentOpen ? (
            <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-2">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[11px] font-semibold text-gray-600">可检索正文</p>
                {isEditingContent ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => void saveContent()}
                      disabled={!draftContent.trim() || isSavingContent}
                      className="rounded p-1 text-emerald-700 disabled:opacity-40"
                      aria-label={`保存资料正文 ${source.title}`}
                    >
                      {isSavingContent ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDraftContent(content ?? "");
                        setIsEditingContent(false);
                      }}
                      disabled={isSavingContent}
                      className="rounded p-1 text-gray-500 disabled:opacity-40"
                      aria-label={`取消编辑资料正文 ${source.title}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setDraftContent(content ?? "");
                      setIsEditingContent(true);
                    }}
                    disabled={!content}
                    className="rounded p-1 text-gray-500 hover:bg-white hover:text-black disabled:opacity-40"
                    aria-label={`编辑资料正文 ${source.title}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              {isEditingContent ? (
                <textarea
                  value={draftContent}
                  onChange={(event) => setDraftContent(event.target.value)}
                  rows={14}
                  className="w-full resize-y rounded border border-gray-200 bg-white px-2 py-2 text-[11px] leading-5 text-gray-700 outline-none focus:border-black"
                />
              ) : (
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-gray-700">
                  {content || "资料中没有可显示的文字正文。"}
                </pre>
              )}
            </div>
          ) : null}
          {isStructureOpen ? (
            <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/40 p-2">
              <SourceStructureQualitySummary
                source={source}
                quality={structureQuality}
                warnings={catalog?.warnings}
              />
              <div className="mb-1 mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => void rebuildStructure()}
                  disabled={isRebuildingStructure}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-blue-100 bg-white text-blue-600 transition hover:border-blue-200 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                  title="重新建立目录"
                  aria-label={`重新建立资料目录 ${source.title}`}
                >
                  <RefreshCw className={clsx("h-3.5 w-3.5", isRebuildingStructure && "animate-spin")} />
                </button>
              </div>
              {isCatalogLoading ? (
                <p className="text-xs leading-5 text-gray-600">正在读取目录…</p>
              ) : catalog && hasChapters ? (
                <SourceChapterTree
                  source={source}
                  catalog={catalog}
                  expandedIds={expandedChapterIds}
                  onToggle={toggleChapter}
                  onSourceReference={onSourceReference}
                />
              ) : (
                <SourceStructureEmptyState source={source} catalog={catalog} />
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SourceStructureEmptyState({
  source,
  catalog,
}: {
  source: SourceIngestionRecord;
  catalog: SourceCatalogView | null;
}) {
  const status = catalog?.status ?? source.structure_status;
  const message =
    status === "failed"
      ? catalog?.error || source.structure_error || "目录建立失败。上一个可用目录会继续保留。"
      : status === "linear_only"
        ? "未识别到目录节点。可以明确点击重建，但普通展开不会重新处理资料。"
        : status === "pending" || status === "building"
          ? "目录正在建立并保存，完成后会自动更新。"
          : "这份资料当前没有已保存的目录节点。";
  return (
    <p className="text-xs leading-5 text-gray-600">{message}</p>
  );
}

function mergeSourceWithCatalog(source: SourceIngestionRecord, catalog: SourceCatalogView): SourceIngestionRecord {
  return {
    ...source,
    ...catalog.source,
    structure_status: catalog.status,
    structure_strategy: catalog.strategy,
    structure_has_verified_toc: catalog.has_verified_toc,
    structure_quality: catalog.quality,
    structure_error: catalog.error,
    structure_updated_at: catalog.catalog_updated_at,
  };
}

function sourceNeedsRefresh(source: SourceIngestionRecord) {
  if (ACTIVE_SOURCE_STATUSES.has(source.status)) {
    return true;
  }
  return source.status === "ready" && ACTIVE_STRUCTURE_STATUSES.has(source.structure_status);
}

function metadataString(source: SourceIngestionRecord, key: string) {
  const value = source.metadata?.[key];
  return typeof value === "string" ? value : "";
}

"use client";

import clsx from "clsx";
import { BookOpen, Check, Download, FileText, GitFork, Globe2, Pencil, RefreshCw, RotateCcw, TextQuote, Trash2, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { SourceBatchControls, type SourceSortOption } from "@/components/course-studio/source-batch-controls";
import { SourceCatalogModelPicker } from "@/components/course-studio/source-catalog-model-picker";
import { SourceChapterTree } from "@/components/course-studio/source-chapter-tree";
import { RepositorySourceMap } from "@/components/course-studio/repository-source-map";
import {
  findModelOption,
  persistModelSelection,
  readStoredModelSelection,
  resolveModelSelection,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import {
  createWholeSourceSelection,
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
import { useSourcePolling } from "@/hooks/course-studio/use-source-polling";
import type { SourceCatalogCacheController } from "@/hooks/course-studio/use-source-catalog-cache";
import type { AIModelOption, AIModelSelection, RepositoryMapView, SelectionRef, SourceCatalogView, SourceIngestionRecord } from "@/types";

type SourceImportPanelProps = {
  packageId: string;
  catalogCache: SourceCatalogCacheController;
  catalogModelOptions: AIModelOption[];
  defaultCatalogModel: AIModelSelection;
  disabled?: boolean;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  onAllReadySourcesReference?: () => void;
};

const STATUS_LABELS: Record<SourceIngestionRecord["status"], string> = {
  queued: "wait",
  fetching: "get",
  parsing: "parse",
  indexing: "index",
  ready: "ready",
  failed: "fail",
};

const CATALOG_MODEL_STORAGE_KEY = "blackboard-ai:selected-catalog-model";
const sourceRefreshRequests = new Map<string, Promise<SourceIngestionRecord[]>>();

function listPackageSourcesOnce(packageId: string) {
  const currentRequest = sourceRefreshRequests.get(packageId);
  if (currentRequest) {
    return currentRequest;
  }
  const request = api.listPackageSources(packageId).finally(() => {
    if (sourceRefreshRequests.get(packageId) === request) {
      sourceRefreshRequests.delete(packageId);
    }
  });
  sourceRefreshRequests.set(packageId, request);
  return request;
}

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
  onAllReadySourcesReference,
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
  const isRefreshingSourcesRef = useRef(false);
  const removingSourceIdsRef = useRef<Set<string>>(new Set());
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
  const readyForQueryCount = sources.filter((source) => {
    if (!sourceIsReadyForQuery(source)) return false;
    const catalog = catalogCache.catalogsBySourceId.get(source.id);
    return !catalog || catalog.strategy !== "codex_directory_v1" || catalog.directory_status === "complete";
  }).length;

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
        onError(error instanceof Error ? error.message : "Failed to read data directory");
      }
    });
    return () => {
      active = false;
    };
  }, [onError, packageId, prefetchPackage]);

  const refreshSources = useCallback(async () => {
    if (!packageId || isRefreshingSourcesRef.current) {
      return null;
    }
    isRefreshingSourcesRef.current = true;
    setIsLoading(true);
    try {
      const nextSources = await listPackageSourcesOnce(packageId);
      setSources(nextSources);
      return nextSources;
    } catch (error) {
      onError(error instanceof Error ? error.message : "Failed to read data list");
    } finally {
      setIsLoading(false);
      isRefreshingSourcesRef.current = false;
    }
  }, [onError, packageId]);

  useSourcePolling({ disabled, sources, refreshSources });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshSources();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshSources]);

  useEffect(() => {
    if (!catalogCache.prefetchedPackageIds.has(packageId)) {
      return;
    }
    let active = true;
    void Promise.all(
      sources
        .filter((source) => source.source_type !== "code_repository")
        .map((source) => ensureCurrentSource(packageId, source))
    ).catch(
      (error) => {
        if (active) {
          onError(error instanceof Error ? error.message : "Data directory update failed");
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
      const record = await api.importPackageSource(packageId, {
        sourceUri: uri,
        title: title.trim(),
        catalogModel: activeCatalogModel,
      });
      setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
      setSourceUri("");
      setTitle("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "URL import failed");
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
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "Import failed"}`);
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
      onError(`${imported.length} files imported; ${failures.length} failed: ${failures.join("; ")}`);
    }
  }

  async function removeSource(sourceId: string) {
    if (!sourceId || disabled || removingSourceId || removingSourceIdsRef.current.has(sourceId)) {
      return;
    }
    removingSourceIdsRef.current.add(sourceId);
    setRemovingSourceId(sourceId);
    try {
      await api.deletePackageSource(packageId, sourceId);
      setSources((current) => current.filter((source) => source.id !== sourceId));
      invalidateSource(sourceId);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Data removal failed");
    } finally {
      removingSourceIdsRef.current.delete(sourceId);
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
      {isImporting ? "Processing" : isDragActive ? "Release upload" : "Upload information"}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <label className="text-[11px] font-bold uppercase tracking-widest text-gray-500">title</label>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Optional"
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

          It is only used to create a table of contents after uploading; subsequent chapter-by-chapter reading uses the current model of the chat box.
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
            title="Import URL"
            aria-label="Import URL"
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
            title="refresh"
            aria-label="Refresh data status"
          >
            <RefreshCw className={clsx("h-4 w-4", isLoading && "animate-spin")} />
          </button>
        </div>
      </div>

      <div
        aria-busy={isImporting}
        aria-disabled={disabled || isImporting}
        aria-label="Source upload area"
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
                  label={uploadProgress === 100 ? "Upload completed, processing task is being created" : "Uploading data"}
                  value={uploadProgress ?? undefined}
                />
              ) : (
                <span>Continue to drag in the data, or click to upload.</span>
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
            {readyForQueryCount && onAllReadySourcesReference ? (
              <button
                type="button"
                onClick={onAllReadySourcesReference}
                className="flex w-full items-center justify-between rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-left text-xs text-blue-800 transition hover:border-blue-200 hover:bg-blue-50"
              >
                <span className="font-semibold">Retrieve all Q&A information</span>
                <span>{readyForQueryCount}  share</span>
              </button>
            ) : null}
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
                removeDisabled={disabled || Boolean(removingSourceId)}
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
                onRefresh={async () => {
                  await refreshSources();
                }}
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
                label={uploadProgress === 100 ? "Upload completed, processing task is being created" : "Uploading data"}
                value={uploadProgress ?? undefined}
              />
            ) : (
              <span>{isDragActive ? "Release to upload data." : "Drag and drop files here, or click to upload data."}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const SOURCE_TITLE_COLLATOR = new Intl.Collator("en-US", {
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
  removeDisabled,
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
  removeDisabled: boolean;
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
  const [repositoryMap, setRepositoryMap] = useState<RepositoryMapView | null>(null);
  const [isLoadingRepositoryMap, setIsLoadingRepositoryMap] = useState(false);
  const [isRebuildingStructure, setIsRebuildingStructure] = useState(false);
  const [isRefiningCatalog, setIsRefiningCatalog] = useState(false);
  const [isPausingCatalog, setIsPausingCatalog] = useState(false);
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
  const isRepository = source.source_type === "code_repository";
  const isOpenNotebookManaged = metadataString(source, "source_processing_owner") === "open_notebook";
  const isDirectoryOnlyCatalog =
    isDirectoryCatalogSource(source) ||
    catalog?.strategy === "codex_directory_v1" ||
    catalog?.catalog_schema_version === "codex_directory_v1";
  const visibleCatalogSummary =
    catalog?.summary && !/(继续核对|持续完善)/.test(catalog.summary) ? catalog.summary : "";
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
  const queryReady = sourceIsReadyForQuery(source);
  const wholeSourceReady = Boolean(
    queryReady &&
      (!isDirectoryOnlyCatalog || catalog?.directory_status === "complete")
  );
  const chapterReferenceReady = Boolean(
    onSourceReference &&
      catalog &&
      catalog.catalog_version > 0 &&
      catalog.source_content_hash
  );
  const nextWork = catalog?.remaining_work?.[0];

  async function toggleStructure() {
    if (!isReady && !catalog?.chapters.length) {
      return;
    }
    const nextOpen = !isStructureOpen;
    setIsStructureOpen(nextOpen);
    if (!nextOpen || !isRepository || repositoryMap || isLoadingRepositoryMap) {
      return;
    }
    setIsLoadingRepositoryMap(true);
    try {
      setRepositoryMap(await api.getRepositoryMap(packageId, source.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Failed to read warehouse structure");
    } finally {
      setIsLoadingRepositoryMap(false);
    }
  }

  async function refineCatalog() {
    if (
      !catalog ||
      isRefiningCatalog ||
      catalog.work_state === "working" ||
      catalog.work_state === "partial"
    ) {
      return;
    }
    setIsRefiningCatalog(true);
    try {
      onCatalogUpdate(await api.refinePackageSourceCatalog(packageId, source.id));
      setIsStructureOpen(true);
      await onRefresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "Catalog refinement could not start");
    } finally {
      setIsRefiningCatalog(false);
    }
  }

  async function pauseCatalog() {
    if (
      !catalog ||
      isPausingCatalog ||
      !["working", "partial"].includes(catalog.work_state)
    ) {
      return;
    }
    setIsPausingCatalog(true);
    try {
      onCatalogUpdate(await api.pausePackageSourceCatalog(packageId, source.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Catalog pause could not be requested");
    } finally {
      setIsPausingCatalog(false);
    }
  }

  async function rebuildStructure() {
    if (!isReady || isRebuildingStructure) {
      return;
    }
    setIsRebuildingStructure(true);
    try {
      if (isRepository) {
        await api.refreshRepositorySource(packageId, source.id);
        await onRefresh();
        return;
      }
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
      onError(error instanceof Error ? error.message : "Data directory reconstruction failed");
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
      if (isRepository) {
        await api.refreshRepositorySource(packageId, source.id);
        await onRefresh();
        return;
      }
      onCatalogInvalidate();
      onSourceUpdate(await api.retryPackageSource(packageId, source.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Data retry failed");
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
      onError(error instanceof Error ? error.message : "Failed to rename data");
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
      onError(error instanceof Error ? error.message : "Failed to read data text");
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
      onError(error instanceof Error ? error.message : "Failed to save data text");
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
      onError(error instanceof Error ? error.message : "Data download failed");
    }
  }

  const hasChapters = Boolean(catalog?.chapters.length);
  const canViewDirectory = Boolean(
    isRepository ||
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
              aria-label={`Select source ${source.title}`}
            />
          </label>
        ) : (
          <div
            className={clsx(
              "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
              isReady ? "bg-emerald-50 text-emerald-700" : isFailed ? "bg-rose-50 text-rose-700" : "bg-gray-50 text-gray-500"
            )}
          >
            {isRepository ? <GitFork className="h-4 w-4" /> : <UploadCloud className="h-4 w-4" />}
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
                <button type="button" onClick={() => void saveTitle()} className="rounded p-1 text-emerald-700" aria-label="Save data title">
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button type="button" onClick={() => setIsEditingTitle(false)} className="rounded p-1 text-gray-500" aria-label="Cancel modification of data title">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex min-w-0 items-start gap-1">
                <p className="min-w-0 flex-1 break-words text-sm font-semibold leading-5 text-gray-900">{source.title}</p>
                <button type="button" onClick={() => setIsEditingTitle(true)} className="shrink-0 rounded p-1 text-gray-400 hover:text-black" aria-label={`Rename source ${source.title}`}>
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
                  ? "Create directory"
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
              {isReady && !isRepository && !isOpenNotebookManaged && !isDirectoryOnlyCatalog ? (
                <button
                  type="button"
                  onClick={() => void toggleContent()}
                  disabled={isLoadingContent}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black disabled:opacity-50"
                  title="View full text"
                  aria-label={`View source content ${source.title}`}
                >
                  {isLoadingContent ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
                </button>
              ) : null}
              {wholeSourceReady && onSourceReference ? (
                <button
                  type="button"
                  onClick={() => onSourceReference(createWholeSourceSelection(source))}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-blue-600 transition hover:bg-blue-50"
                  title="Questions and answers throughout the material"
                  aria-label={`Use entire source for Q&A: ${source.title}`}
                >
                  <TextQuote className="h-3.5 w-3.5" />
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void downloadSource()}
                className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black"
                title="Download source material"
                aria-label={`Download source ${source.title}`}
              >
                <Download className="h-3.5 w-3.5" />
              </button>
              {isFailed ? (
                <button
                  type="button"
                  onClick={() => void retrySource()}
                  disabled={isRetrying}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-amber-600 transition hover:bg-amber-50 disabled:opacity-50"
                  title="Retry data processing"
                  aria-label={`Retry source ${source.title}`}
                >
                  <RotateCcw className={clsx("h-3.5 w-3.5", isRetrying && "animate-spin")} />
                </button>
              ) : null}
              {(isReady || hasChapters) && !isOpenNotebookManaged ? (
                <button
                  type="button"
                  onClick={() => void toggleStructure()}
                  className={clsx(
                    "flex min-w-10 flex-col items-center justify-center gap-0.5 rounded-md border border-transparent px-1 py-1 text-[10px] leading-none transition disabled:cursor-not-allowed disabled:opacity-50",
                    canViewDirectory
                      ? "text-blue-600 hover:border-blue-100 hover:bg-blue-50"
                      : "text-gray-400 hover:border-gray-200 hover:bg-gray-50 hover:text-gray-600"
                  )}
                  title={canViewDirectory ? "View catalog" : "View directory status"}
                  aria-label={`${canViewDirectory ? "View data directory" : "View directory status"} ${source.title}`}
                >
                  {isCatalogLoading || isLoadingRepositoryMap ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <BookOpen className="h-3.5 w-3.5" />}
                  <span>{isStructureOpen ? "close" : "Expand"}</span>
                </button>
              ) : null}
              {!selectionMode ? (
                <button
                  type="button"
                  onClick={onRemove}
                  disabled={removeDisabled}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-gray-400 transition hover:border-rose-100 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
                  title="Remove data"
                  aria-label={`Remove source ${source.title}`}
                >
                  {isRemoving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                </button>
              ) : null}
            </div>
          </div>
          <p className="mt-2 break-all text-xs leading-5 text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {isRepository && isReady ? (
            <p className="mt-1 text-[10px] leading-4 text-gray-500">
              {metadataString(source, "repository_visibility") === "private" ? "Private warehouse" : "public repository"}
              {" · "}commit {metadataString(source, "repository_commit_sha").slice(0, 12)}
              {" · "}learning coverage {Math.round(Number(source.metadata.repository_learning_coverage || 0) * 100)}%
            </p>
          ) : null}
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
              title="The latest backend OpenClass output"
              expandedByDefault={false}
            />
          ) : null}
          {isReady ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">
              {isRepository
                ? "The repository is pinned to immutable commits; source code evidence will not enter the chat until a project or learning node is selected."
                : isOpenNotebookManaged
                ? "The text of the data is processed by OpenNotebook; after citation, relevant fragments are retrieved according to the current round of questions."
                : structureNote}
            </p>
          ) : null}
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
          {source.structure_error ? <p className="mt-2 text-xs leading-5 text-amber-700">{source.structure_error}</p> : null}
          {isContentOpen ? (
            <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-2">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[11px] font-semibold text-gray-600">Searchable text</p>
                {isEditingContent ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => void saveContent()}
                      disabled={!draftContent.trim() || isSavingContent}
                      className="rounded p-1 text-emerald-700 disabled:opacity-40"
                      aria-label={`Save source content ${source.title}`}
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
                      aria-label={`Cancel editing source content ${source.title}`}
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
                    aria-label={`Edit source content ${source.title}`}
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
                  {content || "There is no text to display in the profile."}
                </pre>
              )}
            </div>
          ) : null}
          {isStructureOpen ? (
            <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/40 p-2">
              {isRepository && repositoryMap ? (
                <RepositorySourceMap
                  source={source}
                  map={repositoryMap}
                  onSourceReference={onSourceReference}
                />
              ) : !isRepository && catalog?.task_contract !== "directory_pages_offset_tree_v1" ? (
                <div>
                  {catalog?.catalog_schema_version === "agent_catalog_v2" || catalog?.catalog_schema_version === "agent_catalog_v3" ? (
                    <p className="mb-1 text-[10px] text-gray-500">目录完整性与正文索引由文件解析 Agent 自主判断</p>
                  ) : null}
                  <SourceStructureQualitySummary
                    source={source}
                    quality={structureQuality}
                    warnings={catalog?.warnings}
                  />
                </div>
              ) : null}
              {!isRepository && catalog ? (
                <div className="mt-2 rounded-md border border-blue-100 bg-white/80 p-2 text-[11px] leading-4 text-gray-600">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-gray-800">
                      目录版本 {catalog.catalog_version} 已发布
                    </span>
                    <span>{catalog.chapter_count} 个节点，{catalog.verified_chapter_count} 个可引用，{catalog.unresolved_node_count} 个待处理</span>
                  </div>
                  <div className="mt-2 grid gap-1 rounded border border-gray-100 bg-gray-50/70 p-2 text-gray-700">
                    <p>
                      目录发现：{catalog.directory_status === "complete" ? `完成，${catalog.chapter_count} 个节点` : `未完成，${catalog.directory_gaps.length} 个明确缺口`}
                    </p>
                    <p>
                      页码标定：{catalog.locator_method === "native_navigation"
                        ? "原生书签"
                        : catalog.locator_method === "exact_p_regimes"
                          ? `${catalog.pagination_regime_count}/${catalog.pagination_regime_count} 个精确 P 区段`
                          : "尚无合法精确定位"}
                    </p>
                    <p>可引用范围：{catalog.verified_chapter_count}/{catalog.chapter_count}</p>
                  </div>
                  {visibleCatalogSummary ? <p className="mt-1">{visibleCatalogSummary}</p> : null}
                  {catalog.background_refine_active && (nextWork?.reason || catalog.next_plan) ? (
                    <p className="mt-1"><span className="font-medium">后台继续：</span> {nextWork?.reason || catalog.next_plan}</p>
                  ) : null}
                  {catalog.unresolved_node_count > 0 ? (
                    <p className="mt-1 text-amber-700">
                      未定位节点暂不可引用；已验证章节现在即可使用。
                    </p>
                  ) : null}
                  {catalog.stop_reason ? <p className="mt-1 text-gray-500">{catalog.stop_reason}</p> : null}
                  {catalog.recent_tool_activity.length ? (
                    <p className="mt-1 text-gray-500">
                      Recent tools: {catalog.recent_tool_activity.slice(-3).map((activity) => String(activity.tool || "inspection")).join(" · ")}
                    </p>
                  ) : null}
                </div>
              ) : null}
              <div className="mb-1 mt-2 flex justify-end gap-1">
                {!isRepository && catalog && ["working", "partial"].includes(catalog.work_state) ? (
                  <button
                    type="button"
                    onClick={() => void pauseCatalog()}
                    disabled={isPausingCatalog}
                    className="rounded-md border border-amber-200 bg-white px-2 py-1 text-[11px] text-amber-700 disabled:opacity-50"
                  >
                    {isPausingCatalog ? "正在暂停…" : "暂停"}
                  </button>
                ) : null}
                {!isRepository && catalog?.can_refine && catalog.work_state === "paused" ? (
                  <button
                    type="button"
                    onClick={() => void refineCatalog()}
                    disabled={isRefiningCatalog}
                    className="rounded-md border border-blue-200 bg-white px-2 py-1 text-[11px] text-blue-700 disabled:opacity-50"
                  >
                    {isRefiningCatalog ? "正在恢复…" : "恢复自动解析"}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void rebuildStructure()}
                  disabled={isRebuildingStructure}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-blue-100 bg-white text-blue-600 transition hover:border-blue-200 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                  title={isRepository ? "Create a new snapshot from the current branch" : "Re-create directory"}
                  aria-label={`${isRepository ? "Create a new snapshot of the warehouse" : "Re-create the data directory"} ${source.title}`}
                >
                  <RefreshCw className={clsx("h-3.5 w-3.5", isRebuildingStructure && "animate-spin")} />
                </button>
              </div>
              {isRepository ? (
                isLoadingRepositoryMap || !repositoryMap ? (
                  <p className="text-xs leading-5 text-gray-600">Reading warehouse structure...</p>
                ) : null
              ) : catalog && hasChapters ? (
                <SourceChapterTree
                  source={source}
                  catalog={catalog}
                  expandedIds={expandedChapterIds}
                  onToggle={toggleChapter}
                  onSourceReference={chapterReferenceReady ? onSourceReference : undefined}
                />
              ) : isCatalogLoading ? (
                <p className="text-xs leading-5 text-gray-600">Reading directory…</p>
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
      ? catalog?.error || source.structure_error || "Directory creation failed. The last available directory will remain."
      : status === "linear_only"
        ? "Directory node not recognized. You can click Rebuild explicitly, but normal expansion will not reprocess the data."
        : status === "pending" || status === "building"
          ? "The catalog is being created and saved, and will be updated automatically when completed."
          : "There are currently no saved directory nodes for this document.";
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

function sourceIsReadyForQuery(source: SourceIngestionRecord) {
  return (
    source.status === "ready" &&
    Boolean(metadataString(source, "content_hash") || metadataString(source, "source_content_hash"))
  );
}

function metadataString(source: SourceIngestionRecord, key: string) {
  const value = source.metadata?.[key];
  return typeof value === "string" ? value : "";
}

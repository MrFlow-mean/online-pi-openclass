import type { SelectionRef, SourceQueryScope } from "@/types";
import type { LessonComposerState } from "@/components/course-studio/history-utils";

function validSourceSelection(selection: SelectionRef) {
  return (
    selection.kind === "source" &&
    Boolean(selection.source_ingestion_id?.trim()) &&
    Boolean(selection.source_content_hash?.trim())
  );
}

function selectionKey(selection: SelectionRef) {
  return [
    selection.source_ingestion_id,
    selection.source_scope_kind,
    selection.source_repository_node_id,
    selection.source_chapter_id,
    selection.source_page_start,
    selection.source_page_end,
  ].join(":");
}

export function appendSourceQuerySelection(
  current: SelectionRef[],
  next: SelectionRef
): SelectionRef[] {
  if (!validSourceSelection(next)) {
    return current;
  }
  const nextKind = next.source_scope_kind ?? (next.source_chapter_id ? "chapter" : "source");
  const compatible = current.filter(
    (item) => validSourceSelection(item) && (item.source_scope_kind ?? (item.source_chapter_id ? "chapter" : "source")) === nextKind
  );
  const withoutSameSourceScope = compatible.filter((item) => {
    if (selectionKey(item) === selectionKey(next)) {
      return false;
    }
    return !(nextKind === "source" && item.source_ingestion_id === next.source_ingestion_id);
  });
  return [...withoutSameSourceScope, next];
}

export function applySourceReferenceToComposerState(
  current: LessonComposerState,
  sourceReference: SelectionRef
): LessonComposerState {
  if (sourceReference.source_scope_kind === "repository_node") {
    return {
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: sourceReference,
      composerSelections: [sourceReference],
      sourceQuerySelections: [],
      sourceQueryAllReady: false,
    };
  }
  return {
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
  };
}

export function sourceQueryScopeFromComposer(
  selections: SelectionRef[],
  allReadySources: boolean
): SourceQueryScope | null {
  if (allReadySources) {
    return { mode: "all_ready_sources", refs: [] };
  }
  const valid = selections.filter(validSourceSelection);
  if (!valid.length) {
    return null;
  }
  if (valid.some((selection) => selection.source_scope_kind === "repository_node")) {
    // Repository nodes are frozen through the full SelectionRef contract.
    // Sending them as a generic source query scope discards the node identity
    // and is rejected because repository selections also carry a node ID.
    return null;
  }
  const kinds = new Set(
    valid.map((selection) => selection.source_scope_kind ?? (selection.source_chapter_id ? "chapter" : "source"))
  );
  if (kinds.size !== 1) {
    return null;
  }
  const kind = [...kinds][0];
  const refs = valid.map((selection) => ({
    source_ingestion_id: selection.source_ingestion_id!,
    source_content_hash: selection.source_content_hash!,
    ...(selection.source_chapter_id ? { source_chapter_id: selection.source_chapter_id } : {}),
    ...(typeof selection.source_page_start === "number" ? { page_start: selection.source_page_start } : {}),
    ...(typeof selection.source_page_end === "number" ? { page_end: selection.source_page_end } : {}),
  }));
  if (kind === "page_range") {
    return { mode: "page_range", refs };
  }
  if (kind === "chapter") {
    return { mode: "chapter", refs };
  }
  return { mode: refs.length === 1 ? "source" : "sources", refs };
}

export function sourceQuerySelectionLabel(selection: SelectionRef) {
  if (selection.source_scope_kind === "repository_node") {
    return selection.source_chapter_title || selection.source_locator || selection.excerpt;
  }
  if (selection.source_scope_kind === "chapter") {
    return selection.source_chapter_title || selection.excerpt;
  }
  if (selection.source_scope_kind === "page_range") {
    return `${selection.source_title || "资料"} · ${selection.source_page_range || "指定页段"}`;
  }
  return selection.source_title || selection.excerpt;
}

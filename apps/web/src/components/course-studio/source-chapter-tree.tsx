import clsx from "clsx";
import { ChevronDown, ChevronRight, TextQuote } from "lucide-react";

import {
  createSourceChapterSelection,
  sourceChapterLabel,
} from "@/components/course-studio/source-reference";
import { sourceRangeDisplayLabel } from "@/lib/source-range-display";
import type {
  SelectionRef,
  SourceCatalogView,
  SourceChapter,
  SourceIngestionRecord,
} from "@/types";

type ChapterTreeNode = {
  chapter: SourceChapter;
  children: ChapterTreeNode[];
};

export function SourceChapterTree({
  source,
  catalog,
  expandedIds,
  onToggle,
  onSourceReference,
}: {
  source: SourceIngestionRecord;
  catalog: SourceCatalogView;
  expandedIds: Set<string>;
  onToggle: (chapterId: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  const nodes = buildChapterTree(catalog.chapters);
  return (
    <div className="space-y-1">
      {nodes.map((node) => (
        <SourceChapterNode
          key={node.chapter.id}
          source={source}
          catalog={catalog}
          node={node}
          expandedIds={expandedIds}
          onToggle={onToggle}
          onSourceReference={onSourceReference}
          depth={0}
        />
      ))}
    </div>
  );
}

function SourceChapterNode({
  source,
  catalog,
  node,
  expandedIds,
  onToggle,
  onSourceReference,
  depth,
}: {
  source: SourceIngestionRecord;
  catalog: SourceCatalogView;
  node: ChapterTreeNode;
  expandedIds: Set<string>;
  onToggle: (chapterId: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  depth: number;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expandedIds.has(node.chapter.id);
  const directoryOnly = catalog.task_contract === "directory_pages_offset_tree_v1";
  const isVerified = !directoryOnly && hasVerifiedChapterRange(node.chapter, catalog);
  const hasConflict = catalog.remaining_work.some(
    (item) => item.kind === "conflict_resolution" && item.node_keys.includes(agentNodeKey(node.chapter))
  );
  const title = sourceChapterLabel(node.chapter);
  const rangeLabel = directoryOnly ? "" : sourceChapterRangeLabel(node.chapter);
  return (
    <div>
      <div
        className="group flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-gray-700 transition hover:bg-white"
        style={{ paddingLeft: `${Math.min(depth, 5) * 12 + 6}px` }}
      >
        <button
          type="button"
          onClick={() => (hasChildren ? onToggle(node.chapter.id) : undefined)}
          className={clsx("flex min-w-0 flex-1 items-center gap-1 text-left", !hasChildren && "cursor-default")}
          title={[node.chapter.path.join(" > ") || title, rangeLabel].filter(Boolean).join(" · ")}
        >
          {hasChildren ? (
            isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-blue-600" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-gray-400" />
            )
          ) : (
            <span className="h-3.5 w-3.5 shrink-0" />
          )}
          <span className="min-w-0 flex-1 truncate">{title || "Unnamed chapter"}</span>
          {rangeLabel ? (
            <span className="shrink-0 text-[10px] tabular-nums text-gray-400">{rangeLabel}</span>
          ) : null}
        </button>
        {!directoryOnly && hasConflict ? (
          <span className="shrink-0 text-[10px] font-medium text-rose-700" title="Published evidence conflicts and must be reviewed">
            存在冲突，暂不可引用
          </span>
        ) : !directoryOnly && !isVerified ? (
          <span className="shrink-0 text-[10px] font-medium text-amber-700" title="Table of contents recognized, body range not mapped">
            待定位
          </span>
        ) : !directoryOnly && isVerified ? (
          <span className="shrink-0 text-[10px] font-medium text-emerald-700">
            可引用
          </span>
        ) : null}
        {onSourceReference && !directoryOnly && isVerified && !hasConflict ? (
          <button
            type="button"
            onClick={() => onSourceReference(createSourceChapterSelection(source, node.chapter, catalog))}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-emerald-200 bg-emerald-50 text-emerald-700 shadow-sm transition hover:border-emerald-300 hover:bg-emerald-100 hover:text-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
            title="Reference to input box"
            aria-label={`Reference chapter in composer: ${title || "Unnamed chapter"}`}
          >
            <TextQuote className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      {hasChildren && isExpanded ? (
        <div className="mt-0.5 space-y-0.5">
          {node.children.map((child) => (
            <SourceChapterNode
              key={child.chapter.id}
              source={source}
              catalog={catalog}
              node={child}
              expandedIds={expandedIds}
              onToggle={onToggle}
              onSourceReference={onSourceReference}
              depth={depth + 1}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function agentNodeKey(chapter: SourceChapter) {
  const value = chapter.metadata?.agent_catalog_key;
  return typeof value === "string" ? value : "";
}

function sourceChapterRangeLabel(chapter: SourceChapter) {
  const authoritative = sourceRangeDisplayLabel(chapter.range);
  if (authoritative) {
    return authoritative;
  }
  if (typeof chapter.page_start !== "number") {
    return "";
  }
  const inclusiveEnd =
    typeof chapter.page_end === "number"
      ? Math.max(chapter.page_start, chapter.page_end - 1)
      : chapter.page_start;
  return inclusiveEnd === chapter.page_start
    ? `PDF p. ${chapter.page_start}`
    : `PDF pp. ${chapter.page_start}-${inclusiveEnd}`;
}

function hasVerifiedChapterRange(chapter: SourceChapter, catalog: SourceCatalogView) {
  if (
    catalog.strategy === "codex_directory_v1" ||
    catalog.catalog_schema_version === "codex_directory_v1" ||
    catalog.catalog_schema_version === "agent_catalog_v2" ||
    catalog.catalog_schema_version === "agent_catalog_v3"
  ) {
    return chapter.mapping_status === "verified" && Boolean(chapter.range);
  }
  return chapter.anchor_status === "verified";
}

function buildChapterTree(chapters: SourceChapter[]): ChapterTreeNode[] {
  const sorted = [...chapters].sort((left, right) => left.order_index - right.order_index);
  if (sorted.some((chapter) => chapter.parent_id)) {
    const nodeById = new Map(sorted.map((chapter) => [chapter.id, { chapter, children: [] as ChapterTreeNode[] }]));
    const roots: ChapterTreeNode[] = [];
    for (const chapter of sorted) {
      const node = nodeById.get(chapter.id);
      if (!node) {
        continue;
      }
      const parent = chapter.parent_id ? nodeById.get(chapter.parent_id) : null;
      if (parent) {
        parent.children.push(node);
      } else {
        roots.push(node);
      }
    }
    return roots;
  }
  const roots: ChapterTreeNode[] = [];
  const stack: ChapterTreeNode[] = [];
  for (const chapter of sorted) {
    const node: ChapterTreeNode = { chapter, children: [] };
    while (stack.length && stack[stack.length - 1].chapter.level >= chapter.level) {
      stack.pop();
    }
    const parent = stack[stack.length - 1];
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
    stack.push(node);
  }
  return roots;
}

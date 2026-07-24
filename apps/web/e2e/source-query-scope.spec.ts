import { expect, test } from "@playwright/test";

import {
  appendSourceQuerySelection,
  sourceQueryScopeFromComposer,
} from "../src/components/course-studio/source-query-scope";
import { buildLessonMessagesFromHistory } from "../src/components/course-studio/history-utils";
import { createWholeSourceSelection } from "../src/components/course-studio/source-reference";
import type { Lesson, SelectionRef, SourceIngestionRecord } from "../src/types";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function wholeSource(id: string, hash: string): SelectionRef {
  return createWholeSourceSelection({
    id,
    title: `资料 ${id}`,
    source_uri: null,
    metadata: { content_hash: hash },
  } as unknown as SourceIngestionRecord);
}

function chapter(id: string, chapterId: string, hash: string): SelectionRef {
  return {
    kind: "source",
    excerpt: `资料 ${id} · ${chapterId}`,
    source_ingestion_id: id,
    source_title: `资料 ${id}`,
    source_chapter_id: chapterId,
    source_chapter_title: chapterId,
    source_scope_kind: "chapter",
    source_content_hash: hash,
  };
}

test("builds one-source, multi-source, chapter, page-range, and all-ready scopes", () => {
  const sourceA = wholeSource("source-a", HASH_A);
  const sourceB = wholeSource("source-b", HASH_B);

  expect(sourceQueryScopeFromComposer([sourceA], false)).toEqual({
    mode: "source",
    refs: [{ source_ingestion_id: "source-a", source_content_hash: HASH_A }],
  });
  expect(sourceQueryScopeFromComposer([sourceA, sourceB], false)?.mode).toBe("sources");
  expect(sourceQueryScopeFromComposer([chapter("source-a", "chapter-1", HASH_A)], false)).toEqual({
    mode: "chapter",
    refs: [{
      source_ingestion_id: "source-a",
      source_content_hash: HASH_A,
      source_chapter_id: "chapter-1",
    }],
  });
  expect(sourceQueryScopeFromComposer([{
    ...sourceA,
    source_scope_kind: "page_range",
    source_page_start: 301,
    source_page_end: 320,
  }], false)).toEqual({
    mode: "page_range",
    refs: [{
      source_ingestion_id: "source-a",
      source_content_hash: HASH_A,
      page_start: 301,
      page_end: 320,
    }],
  });
  expect(sourceQueryScopeFromComposer([], true)).toEqual({ mode: "all_ready_sources", refs: [] });
});

test("keeps compatible multi-source selections and replaces incompatible scope kinds", () => {
  const sourceA = wholeSource("source-a", HASH_A);
  const sourceB = wholeSource("source-b", HASH_B);
  const twoSources = appendSourceQuerySelection(
    appendSourceQuerySelection([], sourceA),
    sourceB
  );
  expect(twoSources.map((selection) => selection.source_ingestion_id)).toEqual(["source-a", "source-b"]);

  const oneChapter = appendSourceQuerySelection(twoSources, chapter("source-a", "chapter-1", HASH_A));
  expect(oneChapter).toHaveLength(1);
  expect(oneChapter[0].source_scope_kind).toBe("chapter");
});

test("refuses client selections without a verified content hash", () => {
  const invalid = { ...wholeSource("source-a", HASH_A), source_content_hash: "" };
  expect(appendSourceQuerySelection([], invalid)).toEqual([]);
  expect(sourceQueryScopeFromComposer([invalid], false)).toBeNull();
});

test("restores source citations from persisted chat history", () => {
  const lesson = {
    history_graph: {
      current_branch: "main",
      branches: { main: "commit-1" },
      commits: [{
        id: "commit-1",
        parent_ids: [],
        kind: "chat_flow",
        created_at: new Date().toISOString(),
        metadata: {
          user_message: "定义是什么？",
          assistant_message: "资料中的定义。",
          assistant_message_source: "source_qa",
          source_citations: [{
            evidence_id: "evidence-1",
            source_id: "source-a",
            source_title: "资料 A",
            section_path: ["第一章", "定义"],
            page_start: 12,
            page_end: 12,
            excerpt: "这是原文定义。",
            chunk_ids: ["chunk-1"],
            bbox: [10, 20, 200, 80],
            source_content_hash: HASH_A,
            parser_run_id: "parser-run-1",
          }],
        },
      }],
    },
  } as unknown as Lesson;

  const assistant = buildLessonMessagesFromHistory(lesson).find((message) => message.role === "assistant");
  expect(assistant?.sourceCitations).toEqual([expect.objectContaining({
    source_id: "source-a",
    page_start: 12,
    excerpt: "这是原文定义。",
  })]);
});

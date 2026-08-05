import { expect, test } from "@playwright/test";

import { chatMessageSelections } from "../src/components/chatbot";
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
          user_message: "What is the definition?",
          assistant_message: "Definitions in the data.",
          assistant_message_source: "source_qa",
          source_query_scope: {
            mode: "chapter",
            refs: [{
              source_ingestion_id: "source-a",
              source_content_hash: HASH_A,
              source_chapter_id: "chapter-1",
            }],
          },
          source_citations: [{
            evidence_id: "evidence-1",
            source_id: "source-a",
            source_title: "Data A",
            section_path: ["Chapter 1", "definition"],
            page_start: 12,
            page_end: 12,
            excerpt: "This is the original definition.",
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
  const user = buildLessonMessagesFromHistory(lesson).find((message) => message.role === "user");
  expect(user?.sourceSelections).toEqual([expect.objectContaining({
    source_ingestion_id: "source-a",
    source_chapter_id: "chapter-1",
    source_chapter_title: "definition",
  })]);
  expect(assistant?.sourceCitations).toEqual([expect.objectContaining({
    source_id: "source-a",
    page_start: 12,
    excerpt: "This is the original definition.",
  })]);
});

test("restores the referenced chapter on a persisted source-grounded board turn", () => {
  const lesson = {
    history_graph: {
      current_branch: "main",
      branches: { main: { head_commit_id: "commit-1" } },
      commits: [{
        id: "commit-1",
        parent_ids: [],
        kind: "board_document_generation",
        created_at: new Date().toISOString(),
        metadata: {
          user_message: "Explain this chapter to me",
          assistant_message: "",
          requirement_phase: "consumed",
          frozen_requirement_payload: {
            source_grounding: {
              confirmed_references: [{
                source_ingestion_id: "source-a",
                source_title: "Machine Learning",
                source_chapter_id: "chapter-8",
                chapter_number: "8",
                chapter_title: "Rule learning",
                scope_kind: "chapter",
                section_path: ["Rule learning"],
                page_range: "physical PDF pages 363-386",
                content_hash: HASH_A,
              }],
            },
          },
        },
      }],
    },
  } as unknown as Lesson;

  const user = buildLessonMessagesFromHistory(lesson).find((message) => message.role === "user");
  expect(user?.sourceSelections).toEqual([expect.objectContaining({
    excerpt: "Machine Learning · Rule learning · physical PDF pages 363-386",
    source_chapter_id: "chapter-8",
    source_chapter_title: "Rule learning",
    source_scope_kind: "chapter",
  })]);
});

test("keeps the referenced chapter in the submitted user message view", () => {
  const selections = chatMessageSelections({
    id: "message-1",
    role: "user",
    content: "Explain this chapter to me",
    sourceSelections: [chapter("source-a", "Rule learning", HASH_A)],
  });

  expect(selections).toEqual([expect.objectContaining({
    source_chapter_id: "Rule learning",
    source_chapter_title: "Rule learning",
  })]);
});

test("restores a downloaded public conversation before the learner's new turns", () => {
  const lesson = {
    history_graph: {
      current_branch: "main",
      branches: { main: { head_commit_id: "commit-2" } },
      commits: [
        {
          id: "commit-1",
          parent_ids: [],
          kind: "initial_document",
          created_at: new Date().toISOString(),
          metadata: {
            history_node_kind: "system",
            published_conversation: [
              { role: "user", content: "Questions in public courses" },
              { role: "assistant", content: "Answers in public courses" },
            ],
          },
        },
        {
          id: "commit-2",
          parent_ids: ["commit-1"],
          kind: "basic_chat",
          created_at: new Date().toISOString(),
          metadata: {
            history_node_kind: "chat",
            user_message: "New questions after downloading",
            assistant_message: "New answers after downloading",
          },
        },
      ],
    },
  } as unknown as Lesson;

  expect(buildLessonMessagesFromHistory(lesson).map(({ role, content }) => ({
    role,
    content,
  }))).toEqual([
    { role: "user", content: "Questions in public courses" },
    { role: "assistant", content: "Answers in public courses" },
    { role: "user", content: "New questions after downloading" },
    { role: "assistant", content: "New answers after downloading" },
  ]);
});

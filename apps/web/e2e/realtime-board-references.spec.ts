import { expect, test } from "@playwright/test";

import {
  addRealtimeBoardReference,
  mergeRealtimeBoardReferenceResults,
} from "../src/lib/realtime-board-references";
import {
  resolveRealtimeTurnIdentity,
  resolveRealtimeTurnSnapshot,
} from "../src/hooks/course-studio/use-realtime-voice";
import type { AIModelSelection, RealtimeToolCallResponse, SelectionRef } from "../src/types";

function selection(excerpt: string, lessonId = "lesson_one"): SelectionRef {
  return {
    kind: "board",
    excerpt,
    lesson_id: lessonId,
    document_id: "board_one",
    heading_path: [],
    before_text: "",
    after_text: "",
    source_title: "",
    source_chapter_number: "",
    source_chapter_title: "",
    source_page_range: "",
    source_locator: "",
    source_scope_kind: "chapter",
  };
}

function toolResult(content: string, label: string): RealtimeToolCallResponse {
  return {
    status: "ok",
    model_output: {
      status: "ok",
      document_title: "Board",
      range_label: label,
      content,
    },
    resolved_focus: {
      source: "board",
      lesson_id: "lesson_one",
      document_id: "board_one",
      segment_id: label,
      kind: "paragraph",
      heading_path: [],
      excerpt: content,
      before_text: "",
      after_text: "",
      text_hash: label,
      confidence: 1,
      reason: "test",
      display_label: label,
      source_segment_ids: [label],
      order_start: 0,
      order_end: 0,
    },
  };
}

test("keeps two ordered realtime board references instead of replacing the first", () => {
  const first = selection("First referenced paragraph");
  const second = selection("Second referenced paragraph");

  const references = addRealtimeBoardReference(
    addRealtimeBoardReference([], first, "lesson_one"),
    second,
    "lesson_one"
  );

  expect(references.map((item) => item.excerpt)).toEqual([
    "First referenced paragraph",
    "Second referenced paragraph",
  ]);
});

test("does not duplicate a repeated reference", () => {
  const first = selection("Repeated paragraph");
  const references = addRealtimeBoardReference(
    addRealtimeBoardReference([], first, "lesson_one"),
    first,
    "lesson_one"
  );

  expect(references).toHaveLength(1);
});

test("does not carry references into another lesson", () => {
  const previous = [selection("Previous lesson paragraph")];

  expect(addRealtimeBoardReference(previous, selection("Wrong lesson", "lesson_one"), "lesson_two")).toEqual([]);
});

test("merges resolved board ranges into one numbered model context", () => {
  const merged = mergeRealtimeBoardReferenceResults([
    toolResult("First board content", "First range"),
    toolResult("Second board content", "Second range"),
  ]);

  expect(merged.status).toBe("ok");
  expect(merged.model_output.reference_count).toBe(2);
  expect(merged.model_output.references).toEqual([
    expect.objectContaining({ reference_index: 1, content: "First board content" }),
    expect.objectContaining({ reference_index: 2, content: "Second board content" }),
  ]);
  expect(merged.resolved_focus?.display_label).toBe("Second range");
});

test("keeps realtime turn and input event identities stable and independent", () => {
  const generatedPrefixes: string[] = [];
  const createId = (prefix: string) => {
    generatedPrefixes.push(prefix);
    return `${prefix}_${generatedPrefixes.length}`;
  };

  const first = resolveRealtimeTurnIdentity(null, "voice", createId);
  const repeated = resolveRealtimeTurnIdentity(first, "typed", createId);

  expect(first).toEqual({
    turnId: "turn_1",
    inputEventId: "input-event_2",
    inputKind: "voice",
  });
  expect(first.inputEventId).not.toBe(first.turnId);
  expect(repeated).toBe(first);
  expect(generatedPrefixes).toEqual(["turn", "input-event"]);
});

test("freezes a realtime input snapshot when the transcript arrives before speech-start", () => {
  const identity = {
    turnId: "turn_from_transcript",
    inputEventId: "event_from_transcript",
    inputKind: "voice" as const,
  };
  const references = [selection("Frozen at the first transcript delta")];
  const textModel: AIModelSelection = {
    provider: "openai",
    model: "gpt-5.5",
    access_method: "platform_credits",
  };

  const snapshot = resolveRealtimeTurnSnapshot(
    null,
    identity,
    "voice",
    references,
    textModel
  );
  references[0].excerpt = "Selection changed after submission";

  expect(snapshot.identity).toEqual(identity);
  expect(snapshot.references[0].excerpt).toBe("Frozen at the first transcript delta");
  expect(
    resolveRealtimeTurnSnapshot(snapshot, null, "typed", [], textModel)
  ).toBe(snapshot);
});

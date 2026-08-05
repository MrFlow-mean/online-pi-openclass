import { expect, test } from "@playwright/test";

import { createSourceChapterSelection } from "../src/components/course-studio/source-reference";
import { sourceStructureQualityNote } from "../src/components/course-studio/source-structure-quality";
import { buildLearningRequirementDisplay } from "../src/lib/learning-requirement-display";
import {
  sourceRangeDisplayLabel,
  sourceReferenceRangeDisplayLabel,
} from "../src/lib/source-range-display";
import type {
  SourceCatalogView,
  SourceChapter,
  SourceIngestionRecord,
  SourceRange,
} from "../src/types";

test("keeps EPUB authority fields raw while presenting a concise one-based range", () => {
  const rawRange: SourceRange = {
    kind: "epub_spine",
    start: 18,
    end: 18,
    container: "OPS/xhtml/chapter-1.xhtml",
    start_anchor: "threads",
    end_anchor: "virtual-memory",
    path: [],
    display_label: "OPS/xhtml/chapter-1.xhtml#threads",
    end_inclusive: true,
    metadata: { index_base: 0, href: "OPS/xhtml/chapter-1.xhtml" },
  };
  const source = {
    id: "source-epub",
    title: "Systems",
    source_uri: null,
  } as SourceIngestionRecord;
  const chapter = {
    id: "chapter-threads",
    number: "1.7.2",
    normalized_number: "1.7.2",
    title: "Threads",
    path: ["Chapter 1", "1.7 Operating Systems", "1.7.2 Threads"],
    source_locator: "epub:OPS/xhtml/chapter-1.xhtml#threads",
    range: rawRange,
    catalog_version: 4,
    source_content_hash: "a".repeat(64),
  } as SourceChapter;
  const catalog = {
    catalog_version: 4,
    source_content_hash: "a".repeat(64),
  } as SourceCatalogView;

  const selection = createSourceChapterSelection(source, chapter, catalog);

  expect(sourceRangeDisplayLabel(rawRange)).toBe("EPUB location 19");
  expect(selection.excerpt).toContain("EPUB location 19");
  expect(selection.excerpt).not.toContain("OPS/xhtml");
  expect(selection.source_page_range).toBe("EPUB location 19");
  expect(selection.source_range).toEqual(rawRange);
  expect(selection.source_range?.display_label).toBe("OPS/xhtml/chapter-1.xhtml#threads");
  expect(selection.catalog_version).toBe(4);
  expect(selection.source_content_hash).toBe("a".repeat(64));
});

test("preserves non-EPUB labels and hides archive members in confirmed-source display", () => {
  expect(
    sourceRangeDisplayLabel({
      kind: "pdf_pages",
      start: 12,
      end: 18,
      container: "",
      start_anchor: "",
      end_anchor: "",
      path: [],
      display_label: "PDF pp. 12-18",
      end_inclusive: true,
      metadata: {},
    })
  ).toBe("PDF pp. 12-18");
  expect(
    sourceReferenceRangeDisplayLabel({
      pageRange: "OPS/xhtml/chapter-1.xhtml#threads",
      sourceLocator: "epub:OPS/xhtml/chapter-1.xhtml#threads",
    })
  ).toBe("");
  expect(
    sourceReferenceRangeDisplayLabel({
      pageRange: "EPUB spine 18-20",
      sourceLocator: "epub:OPS/xhtml/chapter-1.xhtml#threads",
    })
  ).toBe("EPUB locations 19-21");

  const display = buildLearningRequirementDisplay({
    requirementSheet: {
      work_mode: "knowledge_board",
      granularity: "source_chapter",
      learning_goal: "Threads",
      source_grounding: {
        confirmation_status: "confirmed",
        confirmed_references: [
          {
            source_title: "Systems",
            section_path: ["Threads"],
            page_range: "EPUB spine 18",
            source_locator: "epub:OPS/xhtml/chapter-1.xhtml#threads",
          },
        ],
      },
    } as never,
    clarification: {
      summary: "Systems / Threads / EPUB spine 18",
      progress: 100,
      ready_for_board: true,
    } as never,
  });
  expect(display.summary).toBe("Systems / Threads / EPUB location 19");
});

test("describes directory-only trust without claiming full-body coverage", () => {
  const note = sourceStructureQualityNote(
    {
      structure_status: "ready",
      structure_strategy: "codex_directory_v1",
      metadata: { catalog_pipeline: "codex_directory_v1" },
    } as unknown as SourceIngestionRecord,
    {
      level: "fully_verified",
      text_readiness: "unknown",
    } as never,
    "fully_verified"
  );

  expect(note).toContain("location and level of the data directory have been verified");
  expect(note).toContain("currently only the directory list is displayed");
  expect(note).not.toContain("Data range verified");
  expect(note).not.toContain("Text boundaries");
  expect(note).not.toContain("Overall coverage verified");
});

test("describes an unmapped Source Codex catalog as viewable but not citable", () => {
  const note = sourceStructureQualityNote(
    {
      structure_status: "ready",
      structure_strategy: "codex_directory_v1",
      metadata: { catalog_pipeline: "codex_directory_v1" },
    } as unknown as SourceIngestionRecord,
    {
      level: "unverified",
      total_chapter_count: 12,
      verified_chapter_count: 0,
      text_readiness: "unknown",
    } as never,
    "unverified"
  );

  expect(note).toBe("Table of contents recognized, body range not mapped; currently only used to view the table of contents.");
});

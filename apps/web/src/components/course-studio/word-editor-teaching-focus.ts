import type { Editor as TiptapEditor } from "@tiptap/core";

import type { BoardFocusRef } from "@/types";

export type FlatEditorChar = {
  text: string;
  pos: number | null;
};

export type NormalizedEditorText = {
  text: string;
  map: number[];
};

export function normalizeLookupText(value: string) {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

export function appendBlockBoundary(chars: FlatEditorChar[]) {
  const last = chars[chars.length - 1]?.text ?? "";
  if (chars.length && !/\s/.test(last)) {
    chars.push({ text: " ", pos: null });
  }
}

export function flattenEditorText(editor: TiptapEditor) {
  const chars: FlatEditorChar[] = [];
  editor.state.doc.descendants((node, pos) => {
    if (node.isBlock) {
      appendBlockBoundary(chars);
    }
    if (!node.isText || !node.text) {
      return;
    }
    for (let offset = 0; offset < node.text.length; offset += 1) {
      chars.push({ text: node.text[offset] ?? "", pos: pos + offset });
    }
  });
  return chars;
}

export function normalizedEditorText(chars: FlatEditorChar[]): NormalizedEditorText {
  let text = "";
  const map: number[] = [];
  let lastWasSpace = true;
  chars.forEach((char, index) => {
    if (/\s/.test(char.text)) {
      if (!lastWasSpace && text.length) {
        text += " ";
        map.push(index);
        lastWasSpace = true;
      }
      return;
    }
    text += char.text.toLocaleLowerCase();
    map.push(index);
    lastWasSpace = false;
  });
  if (text.endsWith(" ")) {
    return { text: text.slice(0, -1), map: map.slice(0, -1) };
  }
  return { text, map };
}

export function uniqueNeedles(values: Array<string | undefined | null>) {
  const seen = new Set<string>();
  return values.flatMap((value) => {
    const normalized = normalizeLookupText(value ?? "");
    if (normalized.length < 2 || seen.has(normalized)) {
      return [];
    }
    seen.add(normalized);
    return [normalized];
  });
}

export function allMatchIndexes(haystack: string, needle: string) {
  const indexes: number[] = [];
  let fromIndex = 0;
  while (fromIndex < haystack.length) {
    const index = haystack.indexOf(needle, fromIndex);
    if (index < 0) {
      break;
    }
    indexes.push(index);
    fromIndex = index + Math.max(needle.length, 1);
  }
  return indexes;
}

export function contextScore(text: string, start: number, end: number, focus: BoardFocusRef) {
  const before = normalizeLookupText(focus.before_text).slice(-96);
  const after = normalizeLookupText(focus.after_text).slice(0, 96);
  const previous = text.slice(Math.max(0, start - 160), start);
  const next = text.slice(end, Math.min(text.length, end + 160));
  let score = 0;
  if (before && previous.endsWith(before)) {
    score += 2;
  } else if (before && previous.includes(before.slice(-48))) {
    score += 1;
  }
  if (after && next.startsWith(after)) {
    score += 2;
  } else if (after && next.includes(after.slice(0, 48))) {
    score += 1;
  }
  return score;
}

export function rangeFromNormalizedMatch(
  chars: FlatEditorChar[],
  map: number[],
  start: number,
  length: number
): { from: number; to: number } | null {
  const startFlatIndex = map[start];
  const endFlatIndex = map[start + length - 1];
  if (startFlatIndex === undefined || endFlatIndex === undefined) {
    return null;
  }
  let from: number | null = null;
  let to: number | null = null;
  for (let index = startFlatIndex; index <= endFlatIndex; index += 1) {
    const pos = chars[index]?.pos;
    if (typeof pos !== "number") {
      continue;
    }
    from ??= pos;
    to = pos + 1;
  }
  return from !== null && to !== null && from < to ? { from, to } : null;
}

export function findTeachingFocusRange(editor: TiptapEditor, focus: BoardFocusRef): { from: number; to: number } | null {
  const headingRange = findTeachingHeadingSectionRange(editor, focus);
  if (headingRange) {
    return headingRange;
  }
  const chars = flattenEditorText(editor);
  const normalized = normalizedEditorText(chars);
  const lastHeading = focus.heading_path[focus.heading_path.length - 1];
  const needles = uniqueNeedles([focus.excerpt, focus.display_label, lastHeading]);
  for (const needle of needles) {
    const matches = allMatchIndexes(normalized.text, needle)
      .map((index) => ({
        index,
        score: contextScore(normalized.text, index, index + needle.length, focus),
      }))
      .sort((a, b) => b.score - a.score || a.index - b.index);
    for (const match of matches) {
      const range = rangeFromNormalizedMatch(chars, normalized.map, match.index, needle.length);
      if (range) {
        return range;
      }
    }
  }
  return null;
}

export function findTeachingHeadingSectionRange(
  editor: TiptapEditor,
  focus: BoardFocusRef
): { from: number; to: number } | null {
  if (focus.kind !== "heading") {
    return null;
  }
  const targetHeading = normalizeLookupText(
    focus.display_label || focus.heading_path[focus.heading_path.length - 1] || ""
  );
  if (!targetHeading) {
    return null;
  }
  const headings: Array<{ pos: number; level: number; text: string }> = [];
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name === "heading") {
      headings.push({
        pos,
        level: typeof node.attrs.level === "number" ? node.attrs.level : 1,
        text: normalizeLookupText(node.textContent),
      });
    }
  });
  const teachableHeadings = headings[0]?.level === 1 ? headings.slice(1) : headings;
  const orderedHeading =
    typeof focus.order_start === "number" ? teachableHeadings[focus.order_start] : null;
  const heading =
    orderedHeading?.text === targetHeading
      ? orderedHeading
      : headings.find((candidate) => candidate.text === targetHeading);
  if (!heading) {
    return null;
  }
  const nextBoundary =
    headings.find(
      (candidate) => candidate.pos > heading.pos && candidate.level <= heading.level
    )?.pos ?? editor.state.doc.content.size;
  let from: number | null = null;
  let to: number | null = null;
  editor.state.doc.nodesBetween(heading.pos, nextBoundary, (node, pos) => {
    if (!node.isText || !node.text) {
      return;
    }
    from ??= pos;
    to = pos + node.nodeSize;
  });
  return from !== null && to !== null && from < to ? { from, to } : null;
}

export function scrollTeachingFocusIntoView(
  editor: TiptapEditor,
  pageScroll: HTMLDivElement | null,
  range: { from: number; to: number }
) {
  if (!pageScroll) {
    return;
  }
  window.requestAnimationFrame(() => {
    try {
      const coords = editor.view.coordsAtPos(range.from);
      const containerRect = pageScroll.getBoundingClientRect();
      const targetTop = pageScroll.scrollTop + coords.top - containerRect.top - pageScroll.clientHeight * 0.34;
      pageScroll.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    } catch {
      editor.commands.scrollIntoView();
    }
  });
}

export function teachingFocusKey(focus: BoardFocusRef | null | undefined) {
  if (!focus) {
    return "";
  }
  return [
    focus.document_id ?? "",
    focus.segment_id ?? "",
    focus.text_hash ?? "",
    focus.excerpt_hash ?? "",
    focus.heading_path.join("/"),
    focus.display_label ?? "",
    focus.excerpt,
  ].join("\u001f");
}

export function applyTeachingFocus(
  editor: TiptapEditor,
  documentId: string,
  focus: BoardFocusRef | null | undefined,
  pageScroll: HTMLDivElement | null,
  shouldScrollIntoView: boolean
) {
  if (
    !focus ||
    focus.source !== "board" ||
    (focus.document_id && focus.document_id !== documentId)
  ) {
    editor.commands.clearTeachingFocusHighlight();
    return;
  }
  const range = findTeachingFocusRange(editor, focus);
  if (!range) {
    editor.commands.clearTeachingFocusHighlight();
    return false;
  }
  editor.commands.setTeachingFocusHighlight(range);
  if (shouldScrollIntoView) {
    scrollTeachingFocusIntoView(editor, pageScroll, range);
  }
  return true;
}

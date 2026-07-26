import type { Editor as TiptapEditor } from "@tiptap/core";
import { NodeSelection } from "@tiptap/pm/state";

import {
  popoverPositionFromCaretRect,
  popoverPositionFromDomSelection,
  popoverPositionFromRect,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import type { BoardTaskLocationKind } from "@/types";

export type WordBoardSelectionPayload = {
  locationKind: BoardTaskLocationKind;
  excerpt: string;
  position: SelectionPopoverPosition | null;
  documentId: string;
  beforeText: string;
  afterText: string;
};

export type ActiveFormulaSelection = WordBoardSelectionPayload & {
  latex: string;
  nodeType: "inlineMath" | "blockMath";
};

export function compactAnchorContext(value: string, maxLength = 90) {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > maxLength ? compact.slice(-maxLength) : compact;
}

export function insertionAnchorExcerpt(beforeText: string, afterText: string) {
  const before = compactAnchorContext(beforeText);
  const after = compactAnchorContext(afterText);
  if (before && after) {
    return `${before}｜${after}`;
  }
  return before || after || "当前光标位置";
}

export function popoverPositionFromEditorCaret(editor: TiptapEditor, position: number) {
  try {
    return popoverPositionFromCaretRect(editor.view.coordsAtPos(position));
  } catch {
    return popoverPositionFromDomSelection();
  }
}

export function isFormulaNodeName(value: string): value is ActiveFormulaSelection["nodeType"] {
  return value === "inlineMath" || value === "blockMath";
}

export function formulaPopoverPositionFromNode(editor: TiptapEditor, position: number) {
  const dom = editor.view.nodeDOM(position);
  if (dom instanceof Element) {
    return popoverPositionFromRect(dom.getBoundingClientRect());
  }
  return popoverPositionFromEditorCaret(editor, position);
}

export function activeFormulaSelectionFromEditor(
  editor: TiptapEditor,
  {
    beforeText,
    afterText,
    documentId,
  }: {
    beforeText: string;
    afterText: string;
    documentId: string;
  }
): ActiveFormulaSelection | null {
  const { selection } = editor.state;
  if (!(selection instanceof NodeSelection) || !isFormulaNodeName(selection.node.type.name)) {
    return null;
  }
  const latex = String(selection.node.attrs.latex ?? "").trim();
  if (!latex) {
    return null;
  }
  return {
    locationKind: "target_range",
    excerpt: latex,
    position: formulaPopoverPositionFromNode(editor, selection.from),
    documentId,
    beforeText,
    afterText,
    latex,
    nodeType: selection.node.type.name,
  };
}

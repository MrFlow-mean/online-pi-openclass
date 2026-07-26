import { Extension, Node } from "@tiptap/core";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import ImageExtension from "@tiptap/extension-image";
import LinkExtension from "@tiptap/extension-link";
import { BlockMath, InlineMath } from "@tiptap/extension-mathematics";
import { Table } from "@tiptap/extension-table";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import TableRow from "@tiptap/extension-table-row";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyle } from "@tiptap/extension-text-style";
import UnderlineExtension from "@tiptap/extension-underline";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import StarterKit from "@tiptap/starter-kit";

import { ResourceVisualBlock } from "@/components/course-studio/resource-visual-block-extension";
import "@/lib/katex-mhchem";
import { RichCodeBlock } from "@/lib/rich-code-block-extension";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    teachingFocusHighlight: {
      setTeachingFocusHighlight: (range: { from: number; to: number }) => ReturnType;
      clearTeachingFocusHighlight: () => ReturnType;
    };
    fontSize: {
      setFontSize: (fontSize: string) => ReturnType;
      unsetFontSize: () => ReturnType;
    };
    fontFamily: {
      setFontFamily: (fontFamily: string) => ReturnType;
      unsetFontFamily: () => ReturnType;
    };
  }
}

export const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) => {
              if (!attributes.fontSize) {
                return {};
              }
              return { style: `font-size: ${attributes.fontSize}` };
            },
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setFontSize:
        (fontSize: string) =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize }).run(),
      unsetFontSize:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize: null }).removeEmptyTextStyle().run(),
    };
  },
});

export const FontFamily = Extension.create({
  name: "fontFamily",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontFamily: {
            default: null,
            parseHTML: (element) => element.style.fontFamily || null,
            renderHTML: (attributes) => {
              if (!attributes.fontFamily) {
                return {};
              }
              return { style: `font-family: ${attributes.fontFamily}` };
            },
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setFontFamily:
        (fontFamily: string) =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontFamily }).run(),
      unsetFontFamily:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontFamily: null }).removeEmptyTextStyle().run(),
    };
  },
});

export type TeachingFocusHighlightRange = { from: number; to: number };

export const teachingFocusHighlightPluginKey = new PluginKey<TeachingFocusHighlightRange | null>("teachingFocusHighlight");

export const TeachingFocusHighlight = Extension.create({
  name: "teachingFocusHighlight",
  addProseMirrorPlugins() {
    return [
      new Plugin<TeachingFocusHighlightRange | null>({
        key: teachingFocusHighlightPluginKey,
        state: {
          init: (): TeachingFocusHighlightRange | null => null,
          apply(transaction, currentRange): TeachingFocusHighlightRange | null {
            const meta = transaction.getMeta(teachingFocusHighlightPluginKey) as
              | { type: "set"; range: { from: number; to: number } }
              | { type: "clear" }
              | undefined;
            if (meta?.type === "clear") {
              return null;
            }
            if (meta?.type === "set") {
              return meta.range.from < meta.range.to ? meta.range : null;
            }
            if (!currentRange || !transaction.docChanged) {
              return currentRange;
            }
            const from = transaction.mapping.map(currentRange.from, -1);
            const to = transaction.mapping.map(currentRange.to, 1);
            return from < to && to <= transaction.doc.content.size ? { from, to } : null;
          },
        },
        props: {
          decorations(state) {
            const range = teachingFocusHighlightPluginKey.getState(state);
            if (!range || range.from >= range.to) {
              return null;
            }
            return DecorationSet.create(state.doc, [
              Decoration.inline(range.from, range.to, {
                class: "word-editor__teaching-focus-highlight",
                "data-teaching-focus": "true",
              }),
            ]);
          },
        },
      }),
    ];
  },
  addCommands() {
    return {
      setTeachingFocusHighlight:
        (range) =>
        ({ tr, dispatch }) => {
          if (dispatch) {
            dispatch(
              tr
                .setMeta(teachingFocusHighlightPluginKey, { type: "set", range })
                .setMeta("addToHistory", false)
            );
          }
          return true;
        },
      clearTeachingFocusHighlight:
        () =>
        ({ tr, dispatch }) => {
          if (dispatch) {
            dispatch(
              tr
                .setMeta(teachingFocusHighlightPluginKey, { type: "clear" })
                .setMeta("addToHistory", false)
            );
          }
          return true;
        },
    };
  },
});

export const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  selectable: true,
  parseHTML() {
    return [{ tag: 'div[data-type="page-break"]' }];
  },
  renderHTML() {
    return ["div", { "data-type": "page-break", class: "word-editor__page-break" }];
  },
});

export const FONT_FAMILY_OPTIONS = [
  { label: "Satoshi", value: '"Satoshi","Avenir Next","PingFang SC","Microsoft YaHei",sans-serif' },
  { label: "Serif", value: '"Iowan Old Style","Songti SC","Times New Roman",serif' },
  { label: "Mono", value: '"IBM Plex Mono","SFMono-Regular","Menlo",monospace' },
];

export const FONT_SIZE_OPTIONS = [
  { label: "12", value: "12px" },
  { label: "14", value: "14px" },
  { label: "16", value: "16px" },
  { label: "18", value: "18px" },
  { label: "24", value: "24px" },
];

export const TABLE_DIMENSION_MIN = 1;
export const TABLE_DIMENSION_MAX = 12;

export function normalizeTableDimension(value: number) {
  if (!Number.isFinite(value)) {
    return TABLE_DIMENSION_MIN;
  }
  return Math.min(TABLE_DIMENSION_MAX, Math.max(TABLE_DIMENSION_MIN, Math.round(value)));
}

export const WORD_EDITOR_EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    link: false,
    underline: false,
    codeBlock: false,
  }),
  RichCodeBlock,
  TextStyle,
  Color,
  Highlight.configure({ multicolor: true }),
  UnderlineExtension,
  LinkExtension.configure({
    autolink: true,
    openOnClick: false,
    defaultProtocol: "https",
  }),
  ImageExtension.configure({
    allowBase64: true,
    HTMLAttributes: {
      class: "word-editor__image",
    },
  }),
  TextAlign.configure({ types: ["heading", "paragraph"] }),
  Table.configure({ resizable: true, cellMinWidth: 72, lastColumnResizable: true }),
  TableRow,
  TableHeader,
  TableCell,
  TeachingFocusHighlight,
  BlockMath.configure({
    katexOptions: {
      displayMode: true,
      throwOnError: false,
    },
  }),
  InlineMath.configure({
    katexOptions: {
      throwOnError: false,
    },
  }),
  PageBreak,
  ResourceVisualBlock,
  FontSize,
  FontFamily,
];

export const WORD_EDITOR_PROPS = {
  attributes: {
    class: "word-editor__content",
  },
};

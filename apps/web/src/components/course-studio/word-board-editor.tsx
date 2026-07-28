import { type Editor as TiptapEditor } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import clsx from "clsx";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Bold,
  ClipboardList,
  Download,
  FilePlus,
  Files,
  FileText,
  Hash,
  Highlighter,
  ImagePlus,
  Italic,
  LayoutTemplate,
  Link as LinkIcon,
  List,
  ListOrdered,
  PanelTop,
  PencilLine,
  Quote,
  Redo2,
  Stamp,
  Table2,
  TextCursorInput,
  Type,
  Underline,
  Undo2,
  Upload,
} from "lucide-react";
import {
  PAGE_ZOOM_DEFAULT,
  PAGE_ZOOM_WHEEL_SENSITIVITY,
  normalizePageSettings,
  normalizePageZoom,
  pagePreviewMetrics,
} from "@/components/course-studio/page-settings";
import {
  popoverPositionFromDomSelection,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import { FormulaInkPopover, type FormulaInkSubmitPayload } from "@/components/course-studio/formula-ink-popover";
import {
  RibbonActionButton,
  RibbonTabButton,
  ToolbarButton,
  WordPageZoomControls,
} from "@/components/course-studio/word-editor-toolbar";
import "@/lib/katex-mhchem";
import { MATH_TEXT_SERIALIZERS, normalizeEditorMath } from "@/lib/math-content";
import { RIDOC_FILE_ACCEPT } from "@/lib/ridoc-file";
import type {
  BoardDocument,
  BoardFocusRef,
  BoardTaskLocationKind,
  DocumentPageSettings,
} from "@/types";

import {
  FONT_FAMILY_OPTIONS,
  FONT_SIZE_OPTIONS,
  WORD_EDITOR_EXTENSIONS,
  WORD_EDITOR_PROPS,
} from "@/components/course-studio/word-editor-extensions";
import {
  applyTeachingFocus,
  teachingFocusKey,
} from "@/components/course-studio/word-editor-teaching-focus";
import {
  TableDimensionFields,
  TableEditButtons,
} from "@/components/course-studio/word-editor-table-controls";
import { WordPageRibbon } from "@/components/course-studio/word-editor-page-ribbon";
import {
  activeFormulaSelectionFromEditor,
  insertionAnchorExcerpt,
  popoverPositionFromEditorCaret,
  type ActiveFormulaSelection,
  type WordBoardSelectionPayload,
} from "@/components/course-studio/word-editor-selection";

type WordRibbonTab = "home" | "insert" | "page";

export type FormulaInkEditorSubmitPayload = FormulaInkSubmitPayload & {
  selection: WordBoardSelectionPayload;
};

export function WordBoardEditor({
  document,
  readOnly,
  teachingFocus,
  toolbarCollapsed,
  onDocumentChange,
  onStructureRemovalIntent,
  onSelectionChange,
  onImportDocx,
  onExportDocx,
  onExportHtml,
  onImportRidoc,
  onExportRidoc,
  onFormulaReference,
  onFormulaGeometryReference,
  onFormulaInkSubmit,
}: {
  document: BoardDocument;
  readOnly: boolean;
  teachingFocus?: BoardFocusRef | null;
  toolbarCollapsed: boolean;
  onDocumentChange: (document: BoardDocument) => void;
  onStructureRemovalIntent: () => void;
  onSelectionChange: (
    selection: {
      locationKind: BoardTaskLocationKind;
      excerpt: string;
      position: SelectionPopoverPosition | null;
      documentId: string;
      beforeText: string;
      afterText: string;
    } | null
  ) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
  onExportHtml: () => void;
  onImportRidoc: (file: File) => void;
  onExportRidoc: () => void;
  onFormulaReference?: (selection: WordBoardSelectionPayload) => void;
  onFormulaGeometryReference?: (selection: WordBoardSelectionPayload) => void;
  onFormulaInkSubmit?: (payload: FormulaInkEditorSubmitPayload) => boolean;
}) {
  const importRef = useRef<HTMLInputElement | null>(null);
  const ridocImportRef = useRef<HTMLInputElement | null>(null);
  const imageUploadRef = useRef<HTMLInputElement | null>(null);
  const pageScrollRef = useRef<HTMLDivElement | null>(null);
  const pageZoomRef = useRef(PAGE_ZOOM_DEFAULT);
  const [activeRibbonTab, setActiveRibbonTab] = useState<WordRibbonTab>("home");
  const [tableRows, setTableRows] = useState(3);
  const [tableCols, setTableCols] = useState(3);
  const [tableHasHeaderRow, setTableHasHeaderRow] = useState(true);
  const [isTableActive, setIsTableActive] = useState(false);
  const [pageZoom, setPageZoom] = useState(PAGE_ZOOM_DEFAULT);
  const [activeFormulaSelection, setActiveFormulaSelection] = useState<ActiveFormulaSelection | null>(null);
  const documentJson =
    document.content_json && Object.keys(document.content_json).length ? document.content_json : null;
  const editorContent = documentJson ?? (document.content_html.trim() || "<p></p>");
  const latestDocumentRef = useRef(document);
  const latestReadOnlyRef = useRef(readOnly);
  const latestOnDocumentChangeRef = useRef(onDocumentChange);
  const latestOnSelectionChangeRef = useRef(onSelectionChange);
  const latestTeachingFocusRef = useRef(teachingFocus);
  const lastAutoScrolledTeachingFocusKeyRef = useRef("");
  const pageSettings = normalizePageSettings(document.page_settings);
  const pageMetrics = pagePreviewMetrics(pageSettings);
  const pageZoomScale = pageZoom / 100;
  const pageStyle = {
    "--page-padding-x": `${pageMetrics.paddingX}px`,
    "--page-padding-y": `${pageMetrics.paddingY}px`,
    "--page-content-min-height": `${pageMetrics.contentMinHeight}px`,
    "--word-page-preview-height": `${pageMetrics.height}px`,
    "--word-page-zoom": pageZoomScale.toString(),
    width: `${pageMetrics.width}px`,
    minHeight: `${pageMetrics.height}px`,
  } as CSSProperties;
  const pageChromeStyle = {
    paddingLeft: "var(--page-padding-x)",
    paddingRight: "var(--page-padding-x)",
  } as CSSProperties;
  const titleStyle = {
    ...pageChromeStyle,
    paddingTop: "calc(var(--page-padding-y) * 0.72)",
    paddingBottom: "calc(var(--page-padding-y) * 0.56)",
  } as CSSProperties;
  const contentStyle = {
    ...pageChromeStyle,
    paddingTop: "var(--page-padding-y)",
    paddingBottom: "calc(var(--page-padding-y) * 0.9)",
  } as CSSProperties;

  useEffect(() => {
    latestDocumentRef.current = document;
    latestReadOnlyRef.current = readOnly;
    latestOnDocumentChangeRef.current = onDocumentChange;
    latestOnSelectionChangeRef.current = onSelectionChange;
    latestTeachingFocusRef.current = teachingFocus;
  }, [document, onDocumentChange, onSelectionChange, readOnly, teachingFocus]);

  const handleEditorUpdate = useCallback(({ editor: currentEditor }: { editor: TiptapEditor }) => {
    if (latestReadOnlyRef.current) {
      return;
    }
    setIsTableActive(currentEditor.isActive("table"));
    latestOnDocumentChangeRef.current({
      ...latestDocumentRef.current,
      content_json: currentEditor.getJSON() as Record<string, unknown>,
      content_html: currentEditor.getHTML(),
      content_text: currentEditor.getText({ textSerializers: MATH_TEXT_SERIALIZERS }),
    });
  }, []);

  const handleEditorSelectionUpdate = useCallback(({ editor: currentEditor }: { editor: TiptapEditor }) => {
    if (latestReadOnlyRef.current) {
      setIsTableActive(false);
      setActiveFormulaSelection(null);
      latestOnSelectionChangeRef.current(null);
      return;
    }
    setIsTableActive(currentEditor.isActive("table"));
    const { from, to } = currentEditor.state.selection;
    const beforeText = currentEditor.state.doc
      .textBetween(Math.max(0, from - 240), from, " ")
      .trim();
    const afterText = currentEditor.state.doc
      .textBetween(to, Math.min(currentEditor.state.doc.content.size, to + 240), " ")
      .trim();
    const formulaSelection = activeFormulaSelectionFromEditor(currentEditor, {
      beforeText,
      afterText,
      documentId: latestDocumentRef.current.id,
    });
    if (formulaSelection) {
      setActiveFormulaSelection(formulaSelection);
      latestOnSelectionChangeRef.current({ ...formulaSelection, position: null });
      return;
    }
    setActiveFormulaSelection(null);
    if (from === to) {
      if (!currentEditor.isFocused) {
        latestOnSelectionChangeRef.current(null);
        return;
      }
      latestOnSelectionChangeRef.current({
        locationKind: "insertion_anchor",
        excerpt: insertionAnchorExcerpt(beforeText, afterText),
        position: popoverPositionFromEditorCaret(currentEditor, from),
        documentId: latestDocumentRef.current.id,
        beforeText,
        afterText,
      });
      return;
    }
    const excerpt = currentEditor.state.doc.textBetween(from, to, " ").trim();
    if (!excerpt) {
      latestOnSelectionChangeRef.current(null);
      return;
    }
    latestOnSelectionChangeRef.current({
      locationKind: "target_range",
      excerpt,
      position: popoverPositionFromDomSelection(),
      documentId: latestDocumentRef.current.id,
      beforeText,
      afterText,
    });
  }, []);

  const editor = useEditor({
    immediatelyRender: false,
    editable: !readOnly,
    extensions: WORD_EDITOR_EXTENSIONS,
    editorProps: WORD_EDITOR_PROPS,
    onUpdate: handleEditorUpdate,
    onSelectionUpdate: handleEditorSelectionUpdate,
  });

  useEffect(() => {
    if (!editor) {
      return;
    }
    if (editor.isEditable === readOnly) {
      editor.setEditable(!readOnly);
    }
    const incomingHtml = document.content_html.trim();
    const incomingPlainText = (document.content_text || incomingHtml.replace(/<[^>]+>/g, " "))
      .replace(/\s+/g, " ")
      .trim();
    const editorPlainText = editor.getText({ textSerializers: MATH_TEXT_SERIALIZERS }).replace(/\s+/g, " ").trim();
    const matchesIncomingDocument = documentJson
      ? JSON.stringify(editor.getJSON()) === JSON.stringify(documentJson)
      : readOnly
        ? incomingPlainText === editorPlainText
      : incomingHtml
        ? editor.getHTML() === incomingHtml
        : false;
    if (!matchesIncomingDocument) {
      const content = editorContent;
      const docId = document.id;
      let cancelled = false;
      queueMicrotask(() => {
        if (cancelled || editor.isDestroyed || latestDocumentRef.current.id !== docId) {
          return;
        }
        editor.commands.setContent(content, { emitUpdate: false });
        normalizeEditorMath(editor);
        const focus = latestTeachingFocusRef.current;
        const focusKey = teachingFocusKey(focus);
        const shouldScrollIntoView =
          Boolean(focusKey) && lastAutoScrolledTeachingFocusKeyRef.current !== focusKey;
        const didApplyFocus = applyTeachingFocus(
          editor,
          docId,
          focus,
          pageScrollRef.current,
          shouldScrollIntoView
        );
        if (didApplyFocus && shouldScrollIntoView) {
          lastAutoScrolledTeachingFocusKeyRef.current = focusKey;
        }
      });
      return () => {
        cancelled = true;
      };
    }
  }, [document.id, document.content_html, document.content_text, documentJson, editor, editorContent, readOnly]);

  const currentTeachingFocusKey = teachingFocusKey(teachingFocus);

  useEffect(() => {
    if (!editor) {
      return;
    }
    const focus = latestTeachingFocusRef.current;
    const shouldScrollIntoView =
      Boolean(currentTeachingFocusKey) &&
      lastAutoScrolledTeachingFocusKeyRef.current !== currentTeachingFocusKey;
    const didApplyFocus = applyTeachingFocus(
      editor,
      document.id,
      focus,
      pageScrollRef.current,
      shouldScrollIntoView
    );
    if (!currentTeachingFocusKey) {
      lastAutoScrolledTeachingFocusKeyRef.current = "";
    } else if (didApplyFocus && shouldScrollIntoView) {
      lastAutoScrolledTeachingFocusKeyRef.current = currentTeachingFocusKey;
    }
  }, [currentTeachingFocusKey, document.id, editor]);

  const currentFontSize =
    ((editor?.getAttributes("textStyle").fontSize as string | null) ?? "14px").replace("px", "");
  const currentFontFamily = (editor?.getAttributes("textStyle").fontFamily as string | null) ?? FONT_FAMILY_OPTIONS[0].value;
  const currentPageNumberLabel = pageSettings.show_page_number ? "第 1 页" : "";

  const updatePageSettings = useCallback(
    (patch: Partial<DocumentPageSettings>) => {
      if (readOnly) {
        return;
      }
      onDocumentChange({
        ...document,
        page_settings: {
          ...pageSettings,
          ...patch,
        },
      });
    },
    [document, onDocumentChange, pageSettings, readOnly]
  );

  const updatePageZoom = useCallback((value: number) => {
    const nextZoom = normalizePageZoom(value);
    pageZoomRef.current = nextZoom;
    setPageZoom(nextZoom);
  }, []);

  const fitPageToWidth = useCallback(() => {
    const viewportWidth = pageScrollRef.current?.clientWidth ?? 0;
    if (!viewportWidth) {
      updatePageZoom(PAGE_ZOOM_DEFAULT);
      return;
    }
    const horizontalBreathingRoom = viewportWidth >= 768 ? 96 : 48;
    updatePageZoom(((viewportWidth - horizontalBreathingRoom) / pageMetrics.width) * 100);
  }, [pageMetrics.width, updatePageZoom]);

  const handlePageWheelZoom = useCallback(
    (event: WheelEvent) => {
      if (!event.ctrlKey) {
        return;
      }
      const pageScroll = pageScrollRef.current;
      if (!pageScroll) {
        return;
      }

      event.preventDefault();
      const currentZoom = pageZoomRef.current;
      const nextZoom = normalizePageZoom(currentZoom - event.deltaY * PAGE_ZOOM_WHEEL_SENSITIVITY);
      if (nextZoom === currentZoom) {
        return;
      }

      const scrollRect = pageScroll.getBoundingClientRect();
      const pointerX = event.clientX - scrollRect.left;
      const pointerY = event.clientY - scrollRect.top;
      const currentScale = currentZoom / 100;
      const nextScale = nextZoom / 100;
      const worldX = (pageScroll.scrollLeft + pointerX) / currentScale;
      const worldY = (pageScroll.scrollTop + pointerY) / currentScale;

      updatePageZoom(nextZoom);
      window.requestAnimationFrame(() => {
        pageScroll.scrollLeft = worldX * nextScale - pointerX;
        pageScroll.scrollTop = worldY * nextScale - pointerY;
      });
    },
    [updatePageZoom]
  );

  useEffect(() => {
    const pageScroll = pageScrollRef.current;
    if (!pageScroll) {
      return;
    }

    pageScroll.addEventListener("wheel", handlePageWheelZoom, { capture: true, passive: false });
    return () => pageScroll.removeEventListener("wheel", handlePageWheelZoom, { capture: true });
  }, [handlePageWheelZoom]);

  const handleInsertBlankPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent([
        { type: "pageBreak" },
        { type: "paragraph" },
        { type: "pageBreak" },
        { type: "paragraph" },
      ])
      .run();
  }, [editor, readOnly]);

  const handleInsertCoverPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const coverTitle = document.title.trim() || "未命名讲义";
    editor
      .chain()
      .focus("start")
      .insertContent([
        { type: "paragraph" },
        {
          type: "heading",
          attrs: { level: 1, textAlign: "center" },
          content: [{ type: "text", text: coverTitle }],
        },
        {
          type: "paragraph",
          attrs: { textAlign: "center" },
          content: [{ type: "text", text: "课程讲义 / Lesson Notes" }],
        },
        {
          type: "paragraph",
          attrs: { textAlign: "center" },
          content: [{ type: "text", text: "在这里补充授课对象、目标和使用场景" }],
        },
        { type: "paragraph" },
        { type: "pageBreak" },
        { type: "paragraph" },
      ])
      .run();
  }, [document.title, editor, readOnly]);

  const handleInsertTableOfContents = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const headings: string[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "heading") {
        const text = node.textContent.trim();
        if (text) {
          headings.push(text);
        }
      }
    });

    editor
      .chain()
      .focus("start")
      .insertContent([
        {
          type: "heading",
          attrs: { level: 2 },
          content: [{ type: "text", text: "目录" }],
        },
        {
          type: "orderedList",
          content: (headings.length ? headings : ["正文里出现标题后，可再次插入目录页自动整理结构"]).map(
            (heading) => ({
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: heading }],
                },
              ],
            })
          ),
        },
        { type: "paragraph" },
      ])
      .run();
  }, [editor, readOnly]);

  const handleInsertTextBox = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent({
        type: "blockquote",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: "重点说明：在这里补充定义、提醒或课堂旁注。" }],
          },
        ],
      })
      .run();
  }, [editor, readOnly]);

  const handleInsertTable = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertTable({ rows: tableRows, cols: tableCols, withHeaderRow: tableHasHeaderRow })
      .run();
    setIsTableActive(true);
  }, [editor, readOnly, tableCols, tableHasHeaderRow, tableRows]);

  const handleInsertLink = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const selectedText = editor.state.doc
      .textBetween(editor.state.selection.from, editor.state.selection.to, " ")
      .trim();
    const hrefInput = window.prompt("请输入超链接地址", "https://");
    const href = hrefInput?.trim() ?? "";
    if (!href) {
      return;
    }
    if (selectedText) {
      editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
      return;
    }
    const labelInput = window.prompt("请输入显示文本", "查看资料");
    const label = labelInput?.trim() ?? "";
    if (!label) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent({
        type: "text",
        text: label,
        marks: [{ type: "link", attrs: { href } }],
      })
      .run();
  }, [editor, readOnly]);

  const handleInsertHeaderFooter = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextHeader = window.prompt("请输入页眉文字，留空则清除", pageSettings.header_text);
    if (nextHeader === null) {
      return;
    }
    const nextFooter = window.prompt("请输入页脚文字，留空则清除", pageSettings.footer_text);
    if (nextFooter === null) {
      return;
    }
    updatePageSettings({
      header_text: nextHeader.trim(),
      footer_text: nextFooter.trim(),
    });
  }, [pageSettings.footer_text, pageSettings.header_text, readOnly, updatePageSettings]);

  const handleInsertWatermark = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextWatermark = window.prompt("请输入水印文字，留空则清除", pageSettings.watermark_text || "内部讲义");
    if (nextWatermark === null) {
      return;
    }
    updatePageSettings({ watermark_text: nextWatermark.trim() });
  }, [pageSettings.watermark_text, readOnly, updatePageSettings]);

  const handleImageUpload = useCallback(
    (file: File) => {
      if (!editor || readOnly) {
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const src = typeof reader.result === "string" ? reader.result : "";
        if (!src) {
          return;
        }
        editor.chain().focus().setImage({ src, alt: file.name }).run();
      };
      reader.readAsDataURL(file);
    },
    [editor, readOnly]
  );

  const tableInsertHint = `${tableRows} x ${tableCols}${tableHasHeaderRow ? " · 表头" : ""}`;
  const tableInsertDisabled = !editor || readOnly;
  const tableEditDisabled = tableInsertDisabled || !isTableActive;

  const renderHomeRibbon = () => (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <select
          disabled={!editor || readOnly}
          value={currentFontFamily}
          onChange={(event) => editor?.chain().focus().setFontFamily(event.target.value).run()}
          className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[12px] font-medium outline-none"
        >
          {FONT_FAMILY_OPTIONS.map((option) => (
            <option key={option.label} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          disabled={!editor || readOnly}
          value={currentFontSize}
          onChange={(event) => editor?.chain().focus().setFontSize(`${event.target.value}px`).run()}
          className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[12px] font-medium outline-none"
        >
          {FONT_SIZE_OPTIONS.map((option) => (
            <option key={option.value} value={option.label}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="加粗"
          active={editor?.isActive("bold")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          <Bold className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="斜体"
          active={editor?.isActive("italic")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          <Italic className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="下划线"
          active={editor?.isActive("underline")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleUnderline().run()}
        >
          <Underline className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="高亮"
          active={editor?.isActive("highlight")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fef08a" }).run()}
        >
          <Highlighter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="文字颜色"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setColor("#c2410c").run()}
        >
          <Type className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="左对齐"
          active={editor?.isActive({ textAlign: "left" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("left").run()}
        >
          <AlignLeft className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="居中"
          active={editor?.isActive({ textAlign: "center" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("center").run()}
        >
          <AlignCenter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="右对齐"
          active={editor?.isActive({ textAlign: "right" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("right").run()}
        >
          <AlignRight className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="引用"
          active={editor?.isActive("blockquote")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          <Quote className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="项目符号"
          active={editor?.isActive("bulletList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          <List className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="编号列表"
          active={editor?.isActive("orderedList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleOrderedList().run()}
        >
          <ListOrdered className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <TableDimensionFields
          rows={tableRows}
          cols={tableCols}
          hasHeaderRow={tableHasHeaderRow}
          disabled={tableInsertDisabled}
          onRowsChange={setTableRows}
          onColsChange={setTableCols}
          onHeaderRowChange={setTableHasHeaderRow}
        />
        <ToolbarButton
          title={`插入 ${tableInsertHint} 表格`}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        >
          <Table2 className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <TableEditButtons
        editor={editor}
        disabled={tableEditDisabled}
        onStructureRemovalIntent={onStructureRemovalIntent}
      />

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="撤销"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().undo().run()}
        >
          <Undo2 className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="重做"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().redo().run()}
        >
          <Redo2 className="h-4 w-4" />
        </ToolbarButton>
      </div>
    </>
  );

  const renderInsertRibbon = () => (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入空白页"
          label="空白页"
          hint="分页占位"
          icon={<FilePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertBlankPage}
        />
        <RibbonActionButton
          title="插入封面"
          label="封面"
          hint="置顶模板"
          icon={<LayoutTemplate className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertCoverPage}
        />
        <RibbonActionButton
          title="插入目录页"
          label="目录页"
          hint="按标题生成"
          icon={<ClipboardList className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTableOfContents}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="切换页码"
          label="页码"
          hint={pageSettings.show_page_number ? "已显示" : "点击显示"}
          icon={<Hash className="h-4 w-4" />}
          active={pageSettings.show_page_number}
          disabled={readOnly}
          onClick={() => updatePageSettings({ show_page_number: !pageSettings.show_page_number })}
        />
        <RibbonActionButton
          title="设置页眉页脚"
          label="页眉页脚"
          hint="编辑文案"
          icon={<PanelTop className="h-4 w-4" />}
          active={Boolean(pageSettings.header_text || pageSettings.footer_text)}
          disabled={readOnly}
          onClick={handleInsertHeaderFooter}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <input
          ref={imageUploadRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              handleImageUpload(file);
            }
            event.currentTarget.value = "";
          }}
        />
        <RibbonActionButton
          title="插入图片"
          label="图片"
          hint="上传到讲义"
          icon={<ImagePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={() => imageUploadRef.current?.click()}
        />
        <TableDimensionFields
          compact={false}
          rows={tableRows}
          cols={tableCols}
          hasHeaderRow={tableHasHeaderRow}
          disabled={tableInsertDisabled}
          onRowsChange={setTableRows}
          onColsChange={setTableCols}
          onHeaderRowChange={setTableHasHeaderRow}
        />
        <RibbonActionButton
          title={`插入 ${tableInsertHint} 表格`}
          label="表格"
          hint={tableInsertHint}
          icon={<Table2 className="h-4 w-4" />}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        />
        <RibbonActionButton
          title="插入文本框"
          label="文本框"
          hint="重点旁注"
          icon={<TextCursorInput className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTextBox}
        />
      </div>

      <TableEditButtons
        compact={false}
        editor={editor}
        disabled={tableEditDisabled}
        onStructureRemovalIntent={onStructureRemovalIntent}
      />

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入超链接"
          label="超链接"
          hint="外部资料"
          icon={<LinkIcon className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertLink}
        />
        <RibbonActionButton
          title="插入水印"
          label="水印"
          hint="页面标识"
          icon={<Stamp className="h-4 w-4" />}
          active={Boolean(pageSettings.watermark_text)}
          disabled={readOnly}
          onClick={handleInsertWatermark}
        />
      </div>
    </>
  );

  const renderPageRibbon = () => (
    <WordPageRibbon
      pageSettings={pageSettings}
      readOnly={readOnly}
      onUpdatePageSettings={updatePageSettings}
    />
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div
        className={clsx(
          "shrink-0 overflow-hidden transition-all duration-300",
          toolbarCollapsed ? "max-h-0 opacity-0" : "max-h-52 opacity-100"
        )}
        aria-hidden={toolbarCollapsed}
      >
        <div className={clsx("border-b border-gray-200 bg-white", readOnly && "bg-gray-50")}>
          <div className="flex h-10 items-center border-b border-gray-100 px-6">
            <RibbonTabButton active={activeRibbonTab === "home"} onClick={() => setActiveRibbonTab("home")}>
              <PencilLine className="h-3.5 w-3.5" />
              开始 (HOME)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "insert"} onClick={() => setActiveRibbonTab("insert")}>
              <FilePlus className="h-3.5 w-3.5" />
              插入 (INSERT)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "page"} onClick={() => setActiveRibbonTab("page")}>
              <Files className="h-3.5 w-3.5" />
              页面 (PAGE)
            </RibbonTabButton>
          </div>
          <div className="custom-scrollbar flex items-center gap-3 overflow-x-auto px-5 py-3 whitespace-nowrap">
            {activeRibbonTab === "home" ? renderHomeRibbon() : null}
            {activeRibbonTab === "insert" ? renderInsertRibbon() : null}
            {activeRibbonTab === "page" ? renderPageRibbon() : null}

            <div className="ml-auto flex items-center gap-2">
              <WordPageZoomControls
                value={pageZoom}
                onChange={updatePageZoom}
                onFitToWidth={fitPageToWidth}
              />
              <input
                ref={importRef}
                type="file"
                accept=".docx"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    onImportDocx(file);
                  }
                  event.currentTarget.value = "";
                }}
              />
              <button
                type="button"
                onClick={() => importRef.current?.click()}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
              >
                <Upload className="h-4 w-4" />
                导入 DOCX
              </button>
              <button
                type="button"
                onClick={onExportDocx}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
              >
                <Download className="h-4 w-4" />
                导出 DOCX
              </button>
              <button
                type="button"
                onClick={onExportHtml}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
              >
                <FileText className="h-4 w-4" />
                导出 HTML
              </button>
              <input
                ref={ridocImportRef}
                type="file"
                accept={RIDOC_FILE_ACCEPT}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    onImportRidoc(file);
                  }
                  event.currentTarget.value = "";
                }}
              />
              <button
                type="button"
                onClick={onExportRidoc}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
              >
                <Download className="h-4 w-4" />
                导出课程包
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        ref={pageScrollRef}
        data-board-scroll-container="true"
        className="min-h-0 flex-1 overflow-auto bg-[radial-gradient(circle_at_top,#f7f5ef,transparent_28%),linear-gradient(180deg,#f3f0e7_0%,#eef2f8_100%)]"
      >
        <div className="mx-auto flex w-max min-w-full justify-center px-6 py-10 md:px-10">
          <div
            className={clsx(
              "word-editor__page word-editor__page--zoomable relative flex shrink-0 flex-col overflow-hidden",
              !pageSettings.page_border && "word-editor__page--borderless",
              pageSettings.background_style === "warm" && "word-editor__page--warm",
              pageSettings.background_style === "grid" && "word-editor__page--grid",
              pageSettings.columns === 2 && "word-editor__page--columns-2",
              pageSettings.line_numbers && "word-editor__page--line-numbers"
            )}
            style={pageStyle}
          >
            {pageSettings.watermark_text ? (
              <div className="word-editor__watermark pointer-events-none select-none">{pageSettings.watermark_text}</div>
            ) : null}
            {pageSettings.header_text ? (
              <div className="word-editor__chrome word-editor__chrome--header" style={pageChromeStyle}>
                <span>{pageSettings.header_text}</span>
              </div>
            ) : null}
            <div className="border-b border-[#ece4d9]" style={titleStyle}>
              <input
                value={document.title}
                disabled={readOnly}
                onChange={(event) => onDocumentChange({ ...document, title: event.target.value })}
                className="w-full border-0 bg-transparent text-[34px] font-semibold tracking-tight text-[#1a1a1a] outline-none placeholder:text-gray-300"
                placeholder="未命名讲义"
              />
            </div>
            <div className="flex-1" style={contentStyle}>
              {activeFormulaSelection && onFormulaInkSubmit ? (
                <FormulaInkPopover
                  position={activeFormulaSelection.position}
                  sourceLatex={activeFormulaSelection.latex}
                  disabled={readOnly}
                  onReference={() => onFormulaReference?.(activeFormulaSelection)}
                  onGeometryReference={() => onFormulaGeometryReference?.(activeFormulaSelection)}
                  onSubmit={(payload) =>
                    onFormulaInkSubmit({
                      ...payload,
                      selection: activeFormulaSelection,
                    })
                  }
                />
              ) : null}
              <EditorContent editor={editor} />
            </div>
            {pageSettings.footer_text || currentPageNumberLabel ? (
              <div className="word-editor__chrome word-editor__chrome--footer" style={pageChromeStyle}>
                <span>{pageSettings.footer_text}</span>
                <span>{currentPageNumberLabel}</span>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

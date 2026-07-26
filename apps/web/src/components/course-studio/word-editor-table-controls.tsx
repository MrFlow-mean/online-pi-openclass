import type { Editor as TiptapEditor } from "@tiptap/core";
import clsx from "clsx";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Columns3,
  PanelTop,
  Rows3,
  TableCellsMerge,
  TableCellsSplit,
  Trash2,
} from "lucide-react";

import {
  RibbonActionButton,
  ToolbarButton,
} from "@/components/course-studio/word-editor-toolbar";
import {
  TABLE_DIMENSION_MAX,
  TABLE_DIMENSION_MIN,
  normalizeTableDimension,
} from "@/components/course-studio/word-editor-extensions";

/** Row/column steppers shared by the compact toolbar and the full insert ribbon. */
export function TableDimensionFields({
  compact = true,
  rows,
  cols,
  hasHeaderRow,
  disabled,
  onRowsChange,
  onColsChange,
  onHeaderRowChange,
}: {
  compact?: boolean;
  rows: number;
  cols: number;
  hasHeaderRow: boolean;
  disabled: boolean;
  onRowsChange: (rows: number) => void;
  onColsChange: (cols: number) => void;
  onHeaderRowChange: (hasHeaderRow: boolean) => void;
}) {
  return (
    <div
      className={clsx(
        "flex items-center gap-1 rounded-lg border border-gray-200 bg-white text-gray-600",
        compact ? "h-9 px-2" : "h-[58px] px-2.5"
      )}
    >
      <Rows3 className="h-3.5 w-3.5 shrink-0" />
      <input
        type="number"
        min={TABLE_DIMENSION_MIN}
        max={TABLE_DIMENSION_MAX}
        value={rows}
        aria-label="表格行数"
        disabled={disabled}
        onChange={(event) => onRowsChange(normalizeTableDimension(Number(event.target.value)))}
        className="w-9 border-0 bg-transparent text-center text-[12px] font-semibold outline-none disabled:cursor-not-allowed"
      />
      <span className="text-[10px] text-gray-300">x</span>
      <Columns3 className="h-3.5 w-3.5 shrink-0" />
      <input
        type="number"
        min={TABLE_DIMENSION_MIN}
        max={TABLE_DIMENSION_MAX}
        value={cols}
        aria-label="表格列数"
        disabled={disabled}
        onChange={(event) => onColsChange(normalizeTableDimension(Number(event.target.value)))}
        className="w-9 border-0 bg-transparent text-center text-[12px] font-semibold outline-none disabled:cursor-not-allowed"
      />
      <label
        title="首行设为表头"
        className={clsx(
          "ml-1 flex items-center gap-1 border-l border-gray-100 pl-2 text-[11px] font-medium",
          disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
        )}
      >
        <input
          type="checkbox"
          checked={hasHeaderRow}
          disabled={disabled}
          onChange={(event) => onHeaderRowChange(event.target.checked)}
          className="h-3.5 w-3.5 accent-black"
        />
        表头
      </label>
    </div>
  );
}

/** Table structure actions; both layouts drive the same editor commands. */
export function TableEditButtons({
  compact = true,
  editor,
  disabled,
  onStructureRemovalIntent,
}: {
  compact?: boolean;
  editor: TiptapEditor | null;
  disabled: boolean;
  onStructureRemovalIntent: () => void;
}) {
  if (compact) {
    return (
      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="上方插入行"
          disabled={disabled}
          onClick={() => editor?.chain().focus().addRowBefore().run()}
        >
          <ChevronUp className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="下方插入行"
          disabled={disabled}
          onClick={() => editor?.chain().focus().addRowAfter().run()}
        >
          <ChevronDown className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="左侧插入列"
          disabled={disabled}
          onClick={() => editor?.chain().focus().addColumnBefore().run()}
        >
          <ArrowLeft className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="右侧插入列"
          disabled={disabled}
          onClick={() => editor?.chain().focus().addColumnAfter().run()}
        >
          <ArrowRight className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="合并单元格"
          disabled={disabled}
          onClick={() => editor?.chain().focus().mergeCells().run()}
        >
          <TableCellsMerge className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="拆分单元格"
          disabled={disabled}
          onClick={() => editor?.chain().focus().splitCell().run()}
        >
          <TableCellsSplit className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="删除表格"
          disabled={disabled}
          onClick={() => {
            onStructureRemovalIntent();
            editor?.chain().focus().deleteTable().run();
          }}
        >
          <Trash2 className="h-4 w-4" />
        </ToolbarButton>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
      <RibbonActionButton
        title="下方插入一行"
        label="加行"
        hint="当前表格"
        icon={<Rows3 className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().addRowAfter().run()}
      />
      <RibbonActionButton
        title="右侧插入一列"
        label="加列"
        hint="当前表格"
        icon={<Columns3 className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().addColumnAfter().run()}
      />
      <RibbonActionButton
        title="删除当前行"
        label="删行"
        hint="当前表格"
        icon={<Rows3 className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().deleteRow().run()}
      />
      <RibbonActionButton
        title="删除当前列"
        label="删列"
        hint="当前表格"
        icon={<Columns3 className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().deleteColumn().run()}
      />
      <RibbonActionButton
        title="合并单元格"
        label="合并"
        hint="选中单元格"
        icon={<TableCellsMerge className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().mergeCells().run()}
      />
      <RibbonActionButton
        title="拆分单元格"
        label="拆分"
        hint="当前单元格"
        icon={<TableCellsSplit className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().splitCell().run()}
      />
      <RibbonActionButton
        title="切换表头行"
        label="表头行"
        hint="当前表格"
        icon={<PanelTop className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => editor?.chain().focus().toggleHeaderRow().run()}
      />
      <RibbonActionButton
        title="删除表格"
        label="删表"
        hint="当前表格"
        icon={<Trash2 className="h-4 w-4" />}
        disabled={disabled}
        onClick={() => {
          onStructureRemovalIntent();
          editor?.chain().focus().deleteTable().run();
        }}
      />
    </div>
  );
}

import {
  AlignHorizontalSpaceAround,
  Columns2,
  FileText,
  Files,
  Frame,
  ListOrdered,
  PaintBucket,
  RectangleHorizontal,
  RectangleVertical,
} from "lucide-react";

import {
  PAGE_BACKGROUND_OPTIONS,
  PAGE_MARGIN_OPTIONS,
  PAGE_SIZE_OPTIONS,
} from "@/components/course-studio/page-settings";
import { RibbonActionButton } from "@/components/course-studio/word-editor-toolbar";
import type { DocumentPageSettings } from "@/types";

/** "Page" ribbon tab: margins, orientation, paper size, columns, chrome, background. */
export function WordPageRibbon({
  pageSettings,
  readOnly,
  onUpdatePageSettings,
}: {
  pageSettings: DocumentPageSettings;
  readOnly: boolean;
  onUpdatePageSettings: (patch: Partial<DocumentPageSettings>) => void;
}) {
  return (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_MARGIN_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`页边距：${option.label}`}
            label={option.label}
            hint="页边距"
            icon={<AlignHorizontalSpaceAround className="h-4 w-4" />}
            active={pageSettings.margin_preset === option.value}
            disabled={readOnly}
            onClick={() => onUpdatePageSettings({ margin_preset: option.value })}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="纵向排版"
          label="纵向"
          hint="纸张方向"
          icon={<RectangleVertical className="h-4 w-4" />}
          active={pageSettings.orientation === "portrait"}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ orientation: "portrait" })}
        />
        <RibbonActionButton
          title="横向排版"
          label="横向"
          hint="纸张方向"
          icon={<RectangleHorizontal className="h-4 w-4" />}
          active={pageSettings.orientation === "landscape"}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ orientation: "landscape" })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_SIZE_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`纸张大小：${option.label}`}
            label={option.label}
            hint="纸张大小"
            icon={<Files className="h-4 w-4" />}
            active={pageSettings.page_size === option.value}
            disabled={readOnly}
            onClick={() => onUpdatePageSettings({ page_size: option.value })}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="单栏排版"
          label="单栏"
          hint="分栏"
          icon={<FileText className="h-4 w-4" />}
          active={pageSettings.columns === 1}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ columns: 1 })}
        />
        <RibbonActionButton
          title="双栏排版"
          label="双栏"
          hint="分栏"
          icon={<Columns2 className="h-4 w-4" />}
          active={pageSettings.columns === 2}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ columns: 2 })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="页面边框"
          label="页面边框"
          hint={pageSettings.page_border ? "已开启" : "已关闭"}
          icon={<Frame className="h-4 w-4" />}
          active={pageSettings.page_border}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ page_border: !pageSettings.page_border })}
        />
        <RibbonActionButton
          title="行号"
          label="行号"
          hint={pageSettings.line_numbers ? "已显示" : "点击显示"}
          icon={<ListOrdered className="h-4 w-4" />}
          active={pageSettings.line_numbers}
          disabled={readOnly}
          onClick={() => onUpdatePageSettings({ line_numbers: !pageSettings.line_numbers })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_BACKGROUND_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`页面背景：${option.label}`}
            label={option.label}
            hint="背景"
            icon={<PaintBucket className="h-4 w-4" />}
            active={pageSettings.background_style === option.value}
            disabled={readOnly}
            onClick={() => onUpdatePageSettings({ background_style: option.value })}
          />
        ))}
      </div>
    </>
  );
}

"use client";

import clsx from "clsx";
import { ArrowLeft, ChevronDown, ChevronUp, PanelRight, X } from "lucide-react";
import type { ReactNode } from "react";

import type { StudioUiBundle } from "@/lib/i18n/product-ui";

type CourseStudioPageShellProps = {
  texts: StudioUiBundle;
  workspaceTitle: string;
  topCollapsed: boolean;
  rightSidebarOpen: boolean;
  error: string | null;
  tabs: ReactNode;
  accountMenu: ReactNode;
  selectionPopover?: ReactNode;
  children: ReactNode;
  onReturnHome: () => void;
  onTopCollapsedChange: (collapsed: boolean) => void;
  onRightSidebarOpenChange: (open: boolean) => void;
  onClearError: () => void;
};

export function CourseStudioPageShell({
  texts,
  workspaceTitle,
  topCollapsed,
  rightSidebarOpen,
  error,
  tabs,
  accountMenu,
  selectionPopover,
  children,
  onReturnHome,
  onTopCollapsedChange,
  onRightSidebarOpenChange,
  onClearError,
}: CourseStudioPageShellProps) {
  return (
    <main className="flex h-screen flex-col overflow-hidden bg-[#f8f6f0] text-[#1a1a1a]">
      <div
        className={clsx(
          "relative z-[60] flex shrink-0 flex-col bg-white transition-all duration-300",
          topCollapsed && "-translate-y-full -mb-12"
        )}
      >
        <header className="flex h-12 items-center justify-between border-b border-gray-200 px-4">
          <div className="flex min-w-0 items-center gap-6">
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={onReturnHome}
                className="group flex h-8 w-8 items-center justify-center rounded-full text-gray-600 transition-colors duration-150 hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300"
                title={texts.returnHome}
                aria-label={texts.returnHome}
              >
                <ArrowLeft className="h-5 w-5 stroke-[1.8] transition-transform duration-150 group-hover:-translate-x-0.5" />
              </button>
              <span className="text-[13px] font-semibold tracking-tight">{workspaceTitle}</span>
            </div>

            {tabs}
          </div>

          <div className="flex shrink-0 items-center gap-4">
            <div className="mr-12 flex items-center gap-1 border-l border-gray-200 pl-4">
              <button
                type="button"
                onClick={() => onRightSidebarOpenChange(!rightSidebarOpen)}
                aria-pressed={rightSidebarOpen}
                className={clsx(
                  "rounded-md border p-1.5 transition-colors",
                  rightSidebarOpen
                    ? "border-gray-200 bg-gray-100 text-gray-700 shadow-sm"
                    : "border-transparent bg-white text-gray-500 hover:border-gray-200 hover:bg-gray-50"
                )}
                title={rightSidebarOpen ? texts.collapseRightSidebar : texts.expandRightSidebar}
                aria-label={rightSidebarOpen ? texts.collapseRightSidebar : texts.expandRightSidebar}
              >
                <PanelRight className="h-4.5 w-4.5" />
              </button>
              <button
                type="button"
                onClick={() => onTopCollapsedChange(true)}
                aria-pressed={!topCollapsed}
                className={clsx(
                  "rounded-md border p-1.5 transition-colors",
                  !topCollapsed
                    ? "border-gray-200 bg-gray-100 text-gray-700 shadow-sm"
                    : "border-transparent bg-white text-gray-500 hover:border-gray-200 hover:bg-gray-50"
                )}
                title={texts.collapseTopToolbar}
                aria-label={texts.collapseTopToolbar}
              >
                <ChevronUp className="h-4.5 w-4.5" />
              </button>
            </div>
          </div>
        </header>
      </div>

      <div className="fixed right-3 top-1 z-[80]">
        {accountMenu}
      </div>

      <button
        type="button"
        onClick={() => onTopCollapsedChange(false)}
        className={clsx(
          "fixed left-1/2 top-0 z-[70] flex h-4 w-16 -translate-x-1/2 items-center justify-center rounded-b-lg border border-t-0 border-gray-200 bg-white shadow-sm transition-all hover:h-5 hover:bg-gray-50",
          topCollapsed ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
        title={texts.expandTopToolbar}
        aria-label={texts.expandTopToolbar}
      >
        <ChevronDown className="h-3 w-3 text-gray-400" />
      </button>

      {error ? (
        <div
          role="alert"
          className="mx-4 mt-3 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 md:mx-6"
        >
          <span className="min-w-0 flex-1">{error}</span>
          <button
            type="button"
            onClick={onClearError}
            aria-label={texts.closeErrorAria}
            title={texts.closeErrorTitle}
            className="-mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-rose-500 transition-colors hover:bg-rose-100 hover:text-rose-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : null}

      {selectionPopover}
      {children}
    </main>
  );
}

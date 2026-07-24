"use client";

import clsx from "clsx";
import { useMemo } from "react";

import { markdownToChatHtml } from "@/lib/markdown";


export function CommunityMarkdown({ content, compact = false }: { content: string; compact?: boolean }) {
  const html = useMemo(() => markdownToChatHtml(content), [content]);

  return (
    <div
      className={clsx(
        "community-markdown break-words text-stone-700",
        compact ? "text-sm leading-6" : "text-[15px] leading-7",
        "[&_a]:font-medium [&_a]:text-sky-700 [&_a]:underline [&_a]:underline-offset-2",
        "[&_blockquote]:my-4 [&_blockquote]:border-l-4 [&_blockquote]:border-stone-300 [&_blockquote]:pl-4 [&_blockquote]:text-stone-600",
        "[&_code]:rounded [&_code]:bg-stone-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.9em]",
        "[&_h1]:mb-3 [&_h1]:mt-6 [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:text-stone-950",
        "[&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-stone-950",
        "[&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-stone-900",
        "[&_li]:my-1 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-3 [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-stone-950 [&_pre]:p-4 [&_pre]:text-stone-100 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-stone-200 [&_td]:p-2 [&_th]:border [&_th]:border-stone-200 [&_th]:bg-stone-50 [&_th]:p-2 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-6",
      )}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

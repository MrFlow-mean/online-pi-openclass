"use client";

import clsx from "clsx";
import { Bold, Code2, Eye, Italic, Link2, List, Sigma, Text } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CommunityMarkdown } from "@/components/community/community-markdown";


type CommunityEditorProps = {
  value: string;
  onChange: (value: string) => void;
  draftKey?: string;
  label: string;
  placeholder: string;
  rows?: number;
};

const toolbarItems = [
  { label: "Bold", icon: Bold, before: "**", after: "**", sample: "focus" },
  { label: "italics", icon: Italic, before: "*", after: "*", sample: "emphasize" },
  { label: "inline code", icon: Code2, before: "`", after: "`", sample: "code" },
  { label: "Link", icon: Link2, before: "[", after: "](https://)", sample: "link text" },
  { label: "unordered list", icon: List, before: "- ", after: "", sample: "list item" },
  { label: "formula", icon: Sigma, before: "$$\n", after: "\n$$", sample: "x^2 + y^2 = r^2" },
] as const;


export function clearCommunityDraft(draftKey?: string) {
  if (draftKey && typeof window !== "undefined") {
    window.localStorage.removeItem(draftKey);
  }
}


export function CommunityEditor({
  value,
  onChange,
  draftKey,
  label,
  placeholder,
  rows = 7,
}: CommunityEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const restoredRef = useRef(false);
  const [mode, setMode] = useState<"write" | "preview">("write");

  useEffect(() => {
    if (!draftKey || restoredRef.current) return;
    restoredRef.current = true;
    const draft = window.localStorage.getItem(draftKey);
    if (!value && draft) onChange(draft);
  }, [draftKey, onChange, value]);

  useEffect(() => {
    if (!draftKey || !restoredRef.current) return;
    const timeoutId = window.setTimeout(() => {
      if (value) window.localStorage.setItem(draftKey, value);
      else window.localStorage.removeItem(draftKey);
    }, 250);
    return () => window.clearTimeout(timeoutId);
  }, [draftKey, value]);

  function insert(before: string, after: string, sample: string) {
    const textarea = textareaRef.current;
    const start = textarea?.selectionStart ?? value.length;
    const end = textarea?.selectionEnd ?? value.length;
    const selected = value.slice(start, end) || sample;
    const next = `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`;
    onChange(next);
    window.requestAnimationFrame(() => {
      textarea?.focus();
      const cursor = start + before.length + selected.length + after.length;
      textarea?.setSelectionRange(cursor, cursor);
    });
  }

  return (
    <div className="overflow-hidden rounded-xl border border-stone-200 bg-white focus-within:border-stone-500">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-200 bg-stone-50 px-2 py-1.5">
        <div className="flex flex-wrap gap-0.5">
          {toolbarItems.map(({ label: itemLabel, icon: Icon, before, after, sample }) => (
            <button key={itemLabel} type="button" aria-label={itemLabel} title={itemLabel} onClick={() => insert(before, after, sample)} className="rounded-lg p-2 text-stone-500 hover:bg-white hover:text-stone-950">
              <Icon className="h-4 w-4" />
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-stone-200 bg-white p-0.5 text-xs font-semibold">
          <button type="button" onClick={() => setMode("write")} className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-1", mode === "write" ? "bg-stone-950 text-white" : "text-stone-500")}><Text className="h-3.5 w-3.5" />edit</button>
          <button type="button" onClick={() => setMode("preview")} className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-1", mode === "preview" ? "bg-stone-950 text-white" : "text-stone-500")}><Eye className="h-3.5 w-3.5" />Preview</button>
        </div>
      </div>
      {mode === "write" ? (
        <textarea ref={textareaRef} aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} rows={rows} className="w-full resize-y bg-transparent px-4 py-3 text-sm leading-7 outline-none placeholder:text-stone-400" />
      ) : (
        <div className="min-h-36 px-4 py-3">
          {value.trim() ? <CommunityMarkdown content={value} /> : <p className="text-sm text-stone-400">There is nothing to preview yet.</p>}
        </div>
      )}
      <div className="border-t border-stone-100 px-3 py-2 text-[11px] text-stone-400">Supports Markdown, code blocks, links, tables and LaTeX formulas; drafts are saved in the current browser.</div>
    </div>
  );
}

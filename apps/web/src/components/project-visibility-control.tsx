"use client";

import clsx from "clsx";
import { Globe2, LockKeyhole } from "lucide-react";

import type { ProjectVisibility } from "@/lib/project-visibility";

type ProjectVisibilityControlProps = {
  visibility: ProjectVisibility;
  onChange: (visibility: ProjectVisibility) => void;
  disabled?: boolean;
  compact?: boolean;
  label?: string;
  ariaLabelPrefix?: string;
};

export function ProjectVisibilityControl({
  visibility,
  onChange,
  disabled = false,
  compact = false,
  label = "可见权限",
  ariaLabelPrefix,
}: ProjectVisibilityControlProps) {
  const buttons = (["private", "public"] as const).map((option) => {
    const selected = visibility === option;
    const optionLabel = option === "public" ? "Public" : "Private";
    const Icon = option === "public" ? Globe2 : LockKeyhole;
    return (
      <button
        key={option}
        type="button"
        onClick={() => onChange(option)}
        disabled={disabled}
        aria-label={ariaLabelPrefix ? `${ariaLabelPrefix} ${optionLabel}` : optionLabel}
        aria-pressed={selected}
        className={clsx(
          "font-semibold transition disabled:cursor-wait disabled:opacity-50",
          compact
            ? "inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border px-1 text-[8px] font-normal leading-none"
            : "rounded-lg px-2 py-1.5 text-xs",
          selected
            ? option === "public"
              ? "border-emerald-600 bg-emerald-600 text-white shadow-sm"
              : "border-stone-950 bg-stone-950 text-white shadow-sm"
            : compact
              ? "border-stone-200 bg-white text-stone-500 hover:border-stone-300"
              : "text-stone-500 hover:bg-white"
        )}
      >
        {compact ? <Icon className="h-2 w-2" /> : null}
        {optionLabel}
      </button>
    );
  });

  if (compact) {
    return <>{buttons}</>;
  }

  return (
    <div className="px-2 py-1.5">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-stone-500">
        {visibility === "public" ? (
          <Globe2 className="h-3.5 w-3.5 text-emerald-600" />
        ) : (
          <LockKeyhole className="h-3.5 w-3.5" />
        )}
        {label}
      </div>
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-stone-100 p-1">{buttons}</div>
    </div>
  );
}

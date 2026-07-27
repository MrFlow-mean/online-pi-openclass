"use client";

import clsx from "clsx";
import { CircleCheckBig, Globe2, LoaderCircle, LockKeyhole, ShieldAlert } from "lucide-react";

import type { ProjectVisibility } from "@/lib/project-visibility";
import type { PublicationReview } from "@/types";

type ProjectVisibilityControlProps = {
  visibility: ProjectVisibility;
  onChange: (visibility: ProjectVisibility) => void;
  disabled?: boolean;
  compact?: boolean;
  label?: string;
  ariaLabelPrefix?: string;
  review?: PublicationReview;
  reviewing?: boolean;
};

type PublicationReviewProgressProps = {
  label: string;
  ariaLabel: string;
  className?: string;
};

export function ProjectVisibilityControl({
  visibility,
  onChange,
  disabled = false,
  compact = false,
  label = "可见权限",
  ariaLabelPrefix,
  review,
  reviewing = false,
}: ProjectVisibilityControlProps) {
  const buttons = (["private", "public"] as const).map((option) => {
    const selected = visibility === option;
    const optionLabel = option === "public" && reviewing ? "AI 扫描中" : option === "public" ? "Public" : "Private";
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
        {compact ? (
          option === "public" && reviewing ? <LoaderCircle className="h-2 w-2 animate-spin" /> : <Icon className="h-2 w-2" />
        ) : null}
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
      <PublicationReviewNotice review={review} reviewing={reviewing} />
    </div>
  );
}

export function PublicationReviewNotice({
  review,
  reviewing = false,
}: {
  review?: PublicationReview;
  reviewing?: boolean;
}) {
  if (reviewing) {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-xl bg-blue-50 px-2.5 py-2 text-[11px] leading-4 text-blue-700">
        <LoaderCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />
        AI 正在扫描所有上传资料的非正文内容。扫描完成前课程保持 Private。
      </div>
    );
  }
  if (!review || review.status === "not_started") {
    return null;
  }
  if (review.status === "approved") {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-xl bg-emerald-50 px-2.5 py-2 text-[11px] leading-4 text-emerald-700">
        <CircleCheckBig className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {review.message || "资料发布审查已通过。"}
      </div>
    );
  }
  const finding = review.findings[0];
  return (
    <div className="mt-2 rounded-xl bg-rose-50 px-2.5 py-2 text-[11px] leading-4 text-rose-700">
      <div className="flex items-start gap-2 font-semibold">
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{review.message || "资料发布审查未通过，课程保持 Private。"}</span>
      </div>
      {finding ? (
        <div className="mt-1.5 border-l-2 border-rose-200 pl-2 font-normal">
          <div>{finding.source_title}{finding.location ? ` · ${finding.location}` : ""}</div>
          <q className="mt-0.5 block text-rose-800">{finding.evidence_excerpt}</q>
        </div>
      ) : null}
    </div>
  );
}

export function PublicationReviewProgress({
  label,
  ariaLabel,
  className,
}: PublicationReviewProgressProps) {
  return (
    <div className={clsx("min-w-0", className)} aria-live="polite">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium text-blue-700">
        <LoaderCircle className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />
        <span>{label}</span>
      </div>
      <div
        role="progressbar"
        aria-label={ariaLabel}
        aria-valuetext={label}
        className="h-1.5 w-full overflow-hidden rounded-full bg-blue-100"
      >
        <div className="source-processing-progress__indeterminate h-full rounded-full bg-blue-500" />
      </div>
    </div>
  );
}

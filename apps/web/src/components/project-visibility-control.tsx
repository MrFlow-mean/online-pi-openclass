"use client";

import clsx from "clsx";
import { CircleCheckBig, LoaderCircle, ShieldAlert, UploadCloud } from "lucide-react";

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
  detail?: string;
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
  const buttonLabel = reviewing ? "AI 审查中" : compact ? "上传" : "上传课程";
  const uploadButton = (
    <button
      type="button"
      onClick={() => onChange("public")}
      disabled={disabled}
      aria-label={ariaLabelPrefix ? `${ariaLabelPrefix} ${buttonLabel}` : buttonLabel}
      className={clsx(
        "inline-flex items-center justify-center gap-2 font-semibold transition disabled:cursor-wait disabled:opacity-50",
        compact
          ? "h-3.5 shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1 text-[8px] font-normal leading-none text-emerald-700 hover:bg-emerald-100"
          : "w-full rounded-xl bg-stone-950 px-3 py-2 text-xs text-white shadow-sm hover:bg-stone-800",
      )}
    >
      {reviewing ? (
        <LoaderCircle className={clsx("animate-spin", compact ? "h-2 w-2" : "h-3.5 w-3.5")} />
      ) : (
        <UploadCloud className={compact ? "h-2 w-2" : "h-3.5 w-3.5"} />
      )}
      {buttonLabel}
    </button>
  );

  if (compact) {
    return uploadButton;
  }

  return (
    <div className="px-2 py-1.5">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-stone-500">
        <UploadCloud className="h-3.5 w-3.5" />
        {label === "可见权限" || label === "Visibility" ? "课程发布" : label}
      </div>
      {uploadButton}
      <p className="mt-2 text-[10px] leading-4 text-stone-500">
        {visibility === "public"
          ? "当前公开版本保持不变；再次上传后才会更新。"
          : "上传当前版本；后续编辑不会自动更新公开课程。"}
      </p>
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
        AI 正在核对课程实际引用资料的非正文范围。审查通过后才会生成新的公开版本。
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
  detail,
  ariaLabel,
  className,
}: PublicationReviewProgressProps) {
  return (
    <div className={clsx("min-w-0", className)} aria-live="polite">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium text-blue-700">
        <LoaderCircle className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />
        <span>{label}</span>
      </div>
      {detail ? <p className="mb-1.5 text-[10px] leading-4 text-stone-500">{detail}</p> : null}
      <div
        role="progressbar"
        aria-label={ariaLabel}
        aria-valuetext={detail ? `${label}：${detail}` : label}
        className="h-1.5 w-full overflow-hidden rounded-full bg-blue-100"
      >
        <div className="source-processing-progress__indeterminate h-full rounded-full bg-blue-500" />
      </div>
    </div>
  );
}

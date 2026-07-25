import type {
  SourceIngestionRecord,
  SourceStructureQuality,
  SourceStructureQualityLevel,
} from "@/types";

const QUALITY_LABELS: Record<SourceStructureQualityLevel, string> = {
  unassessed: "质量待评估",
  fully_verified: "目录完整可信",
  partially_verified: "目录部分可信",
  unverified: "目录待验证",
  search_only: "无可用目录",
};

export function sourceStructureQualityLevel(
  source: SourceIngestionRecord,
  quality: SourceStructureQuality | null | undefined = source.structure_quality
): SourceStructureQualityLevel {
  if (quality?.level && quality.level !== "unassessed") {
    return quality.level;
  }
  if (source.structure_status === "linear_only") {
    return source.structure_has_verified_toc ? "partially_verified" : "search_only";
  }
  if (source.structure_status === "ready" && source.structure_has_verified_toc) {
    // Legacy structures only exposed whether at least one node was verified.
    // They must be rebuilt before the UI can claim whole-document trust.
    return "partially_verified";
  }
  return "unassessed";
}

export function sourceStructureBadgeLabel(
  source: SourceIngestionRecord,
  level: SourceStructureQualityLevel,
  quality?: SourceStructureQuality | null
) {
  if (source.structure_status === "failed") {
    return "结构失败";
  }
  if (source.structure_status === "pending") {
    return "待建目录";
  }
  if (source.structure_status === "building") {
    return "目录建立中";
  }
  if (quality?.text_readiness === "empty") {
    return "范围不可用";
  }
  if (level === "unverified" && isDirectoryOnlyCatalog(source)) {
    return "目录已识别";
  }
  return QUALITY_LABELS[level];
}

export function sourceStructureBadgeClass(
  source: SourceIngestionRecord,
  level: SourceStructureQualityLevel,
  quality?: SourceStructureQuality | null
) {
  if (source.structure_status === "failed") {
    return "bg-rose-50 text-rose-700";
  }
  if (source.structure_status === "pending" || source.structure_status === "building") {
    return "bg-sky-50 text-sky-700";
  }
  if (quality?.text_readiness === "empty") {
    return "bg-rose-50 text-rose-700";
  }
  if (level === "fully_verified") {
    return "bg-emerald-50 text-emerald-700";
  }
  if (level === "partially_verified") {
    return "bg-amber-50 text-amber-700";
  }
  if (level === "unverified") {
    return "bg-orange-50 text-orange-700";
  }
  return "bg-gray-100 text-gray-600";
}

export function sourceStructureQualityNote(
  source: SourceIngestionRecord,
  quality: SourceStructureQuality | null | undefined,
  level: SourceStructureQualityLevel
) {
  if (source.structure_status === "failed") {
    return "目录建立失败；如果之前已有可用版本，系统会继续保留它。";
  }
  if (source.structure_status === "pending" || source.structure_status === "building") {
    return "正在读取文件并生成目录。";
  }
  if (quality?.text_readiness === "empty") {
    return "没有得到可验证的目录范围；请检查文件文字层或明确重建。";
  }
  if (level === "fully_verified") {
    if (isDirectoryOnlyCatalog(source)) {
      return "资料目录定位与层级已校验；当前仅显示目录列表。";
    }
    return "目录节点、正文边界与整体覆盖已通过验证，可以按章节引用。";
  }
  if (level === "partially_verified") {
    const counts = quality?.total_chapter_count
      ? `已验证 ${quality.verified_chapter_count}/${quality.total_chapter_count} 个目录节点；`
      : "目录只有部分节点完成整体验证；";
    return `${counts}只能引用标记为“已验证”的章节。`;
  }
  if (level === "unverified") {
    if (quality?.verified_chapter_count && quality.total_chapter_count) {
      return `仅 ${quality.verified_chapter_count}/${quality.total_chapter_count} 个节点可验证，整份目录暂不可信；已验证章节仍可单独引用。`;
    }
    if (isDirectoryOnlyCatalog(source) && quality?.total_chapter_count) {
      return "目录已识别，正文范围未映射；当前仅用于查看目录。";
    }
    return "识别到目录候选，但尚未建立可靠正文边界，当前不会把整份目录标为可信。";
  }
  if (level === "search_only") {
    return "未形成可安全引用的章节目录；普通展开不会触发重新处理。";
  }
  return "这份资料尚未完成目录质量评估；重建后会给出整体验证结果。";
}

export function SourceStructureQualitySummary({
  source,
  quality,
  warnings = [],
}: {
  source: SourceIngestionRecord;
  quality: SourceStructureQuality | null | undefined;
  warnings?: string[];
}) {
  const level = sourceStructureQualityLevel(source, quality);
  const note = sourceStructureQualityNote(source, quality, level);
  const showMetrics = Boolean(
    quality && quality.level !== "unassessed" && quality.total_chapter_count
  );
  const directoryOnlyCatalog = isDirectoryOnlyCatalog(source);
  const diagnostics = Array.from(
    new Set([...(warnings ?? []), ...(quality?.diagnostics ?? [])])
  ).slice(0, 3);

  return (
    <div className="rounded-md border border-blue-100 bg-white/80 p-2">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] font-semibold text-gray-700">目录质量</p>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${sourceStructureBadgeClass(source, level, quality)}`}
        >
          {sourceStructureBadgeLabel(source, level, quality)}
        </span>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-gray-600">{note}</p>
      {showMetrics && quality ? (
        <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-gray-600">
          <span className="rounded bg-gray-50 px-1.5 py-1">
            {directoryOnlyCatalog && quality.verified_chapter_count === 0
              ? `目录节点 ${quality.total_chapter_count}`
              : `节点 ${quality.verified_chapter_count}/${quality.total_chapter_count}`}
          </span>
          <span className="rounded bg-gray-50 px-1.5 py-1">
            边界 {formatPercent(quality.boundary_valid_ratio)}
          </span>
          {directoryOnlyCatalog ? (
            <span className="rounded bg-blue-50 px-1.5 py-1 text-blue-700">正文按需读取</span>
          ) : (
            <span className="rounded bg-gray-50 px-1.5 py-1">
              范围覆盖 {formatPercent(quality.body_coverage_ratio)}
            </span>
          )}
          {quality.text_readiness === "sparse" || quality.text_readiness === "very_sparse" ? (
            <span className="rounded bg-amber-50 px-1.5 py-1 text-amber-700">文字层稀疏</span>
          ) : null}
        </div>
      ) : null}
      {diagnostics.length ? (
        <div className="mt-2 space-y-1">
          {diagnostics.map((diagnostic) => (
            <p key={diagnostic} className="text-[10px] leading-4 text-amber-700">
              {diagnostic}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function isDirectoryOnlyCatalog(source: SourceIngestionRecord) {
  return (
    source.structure_strategy === "codex_directory_v1" ||
    source.metadata?.catalog_pipeline === "codex_directory_v1"
  );
}

function formatPercent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

import type {
  SourceIngestionRecord,
  SourceStructureQuality,
  SourceStructureQualityLevel,
} from "@/types";

const QUALITY_LABELS: Record<SourceStructureQualityLevel, string> = {
  unassessed: "Quality to be assessed",
  fully_verified: "Directory is complete and trustworthy",
  partially_verified: "Directory partially trusted",
  unverified: "Directory to be verified",
  search_only: "No directory available",
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
    return "Structure failed";
  }
  if (source.structure_status === "pending") {
    return "Directory to be created";
  }
  if (source.structure_status === "building") {
    return "Directory being created";
  }
  if (quality?.text_readiness === "empty") {
    return "Range not available";
  }
  if (level === "unverified" && isDirectoryOnlyCatalog(source)) {
    return "Directory recognized";
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
    return "Catalog creation failed; if a version was previously available, it will be retained.";
  }
  if (source.structure_status === "pending" || source.structure_status === "building") {
    return "Reading files and generating directories.";
  }
  if (quality?.text_readiness === "empty") {
    return "No verifiable directory range obtained; please check file literal layer or rebuild explicitly.";
  }
  if (level === "fully_verified") {
    if (isDirectoryOnlyCatalog(source)) {
      return "The location and level of the data directory have been verified; currently only the directory list is displayed.";
    }
    return "Table of contents nodes, text boundaries and overall coverage have been verified and can be referenced by chapter.";
  }
  if (level === "partially_verified") {
    const counts = quality?.total_chapter_count
      ? `${quality.verified_chapter_count}/${quality.total_chapter_count} outline nodes verified; `
      : "Only some nodes in the directory have completed overall verification;";
    return `${counts}only chapters marked as verified can be referenced.`;
  }
  if (level === "unverified") {
    if (quality?.verified_chapter_count && quality.total_chapter_count) {
      return `Only ${quality.verified_chapter_count}/${quality.total_chapter_count} nodes could be verified. The full outline is not yet trusted, but verified chapters can still be referenced individually.`;
    }
    if (isDirectoryOnlyCatalog(source) && quality?.total_chapter_count) {
      return "Table of contents recognized, body range not mapped; currently only used to view the table of contents.";
    }
    return "Directory candidates have been identified, but reliable text boundaries have not yet been established, and the entire directory will not be marked as trusted at this time.";
  }
  if (level === "search_only") {
    return "No chapter table of contents that can be safely referenced is formed; normal expansion does not trigger reprocessing.";
  }
  return "Catalog quality assessment has not yet been completed for this material; overall verification results will be provided after reconstruction.";
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
        <p className="text-[11px] font-semibold text-gray-700">Catalog quality</p>
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
              ? `Outline nodes ${quality.total_chapter_count}`
              : `Nodes ${quality.verified_chapter_count}/${quality.total_chapter_count}`}
          </span>
          <span className="rounded bg-gray-50 px-1.5 py-1">

            boundary {formatPercent(quality.boundary_valid_ratio)}
          </span>
          {directoryOnlyCatalog ? (
            <span className="rounded bg-blue-50 px-1.5 py-1 text-blue-700">Text is read on demand</span>
          ) : (
            <span className="rounded bg-gray-50 px-1.5 py-1">

              range coverage {formatPercent(quality.body_coverage_ratio)}
            </span>
          )}
          {quality.text_readiness === "sparse" || quality.text_readiness === "very_sparse" ? (
            <span className="rounded bg-amber-50 px-1.5 py-1 text-amber-700">Text layer is sparse</span>
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

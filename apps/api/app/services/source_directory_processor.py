from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    SourceCatalogRun,
    SourceChapter,
    SourceIngestionRecord,
    SourceRange,
    SourceStructure,
    SourceStructureQuality,
    now_iso,
)
from app.services.codex_app_server import CodexAppServerTextClient
from app.services.source_chapter_identity import stable_source_chapter_id
from app.services.source_codex_catalog import (
    SourceCodexCatalogError,
    generate_codex_direct_catalog,
)
from app.services.source_directory_extractor import (
    CatalogProgressCallback,
    DirectoryCandidate,
    DirectoryExtraction,
    extract_directory,
)
from app.services.source_structure_store import SourceStructureStore, source_structure_store


CATALOG_SCHEMA_VERSION = "codex_directory_v1"
MAX_CODEX_BATCH_NODES = 120
MAX_CODEX_BATCH_CHARS = 48_000
NORMALIZATION_PROGRESS_START = 64
NORMALIZATION_PROGRESS_END = 80
NUMERIC_CONTAINMENT_RANGE_KINDS = frozenset(
    {
        "pdf_pages",
        "docx_paragraphs",
        "ppt_slides",
        "sheet_rows",
        "text_lines",
    }
)


class SourceDirectoryProcessingError(RuntimeError):
    pass


class DirectoryNodeDecision(BaseModel):
    local_key: str = Field(min_length=1, max_length=160)
    keep: bool = True
    title: str = Field(default="", max_length=300)
    number: str = Field(default="", max_length=80)
    level: int = Field(default=1, ge=1, le=12)
    reason: str = Field(default="", max_length=300)


class DirectoryBatchDecision(BaseModel):
    batch_hash: str
    decisions: list[DirectoryNodeDecision] = Field(default_factory=list, max_length=MAX_CODEX_BATCH_NODES)


@dataclass(frozen=True)
class DirectoryNormalizationResult:
    candidates: tuple[DirectoryCandidate, ...]
    turn_count: int
    metadata: dict[str, object]


class DirectoryNormalizer(Protocol):
    def normalize(
        self,
        *,
        record: SourceIngestionRecord,
        candidates: Sequence[DirectoryCandidate],
        selection: AIModelSelection,
    ) -> DirectoryNormalizationResult: ...


class CodexDirectoryNormalizer:
    """Run bounded directory-only Codex turns serially.

    The model receives headings and locators only. It never receives the source
    file path or extracted body text, and it cannot alter authoritative ranges.
    """

    def __init__(
        self,
        *,
        user_id: str,
        progress_callback: CatalogProgressCallback | None = None,
    ) -> None:
        self.user_id = user_id
        self.progress_callback = progress_callback

    def normalize(
        self,
        *,
        record: SourceIngestionRecord,
        candidates: Sequence[DirectoryCandidate],
        selection: AIModelSelection,
    ) -> DirectoryNormalizationResult:
        if not selection.model.strip():
            raise SourceDirectoryProcessingError("A configured text model is required for cataloging.")
        if not candidates:
            return DirectoryNormalizationResult(candidates=(), turn_count=0, metadata={"batch_count": 0})

        batches = _bounded_candidate_batches(candidates)
        normalized: list[DirectoryCandidate] = []
        batch_hashes: list[str] = []
        client = CodexAppServerTextClient(self.user_id)
        for batch_index, batch in enumerate(batches):
            packet = {
                "schema": CATALOG_SCHEMA_VERSION,
                "source": {
                    "id": record.id,
                    "title": record.title,
                    "file_name": record.file_name,
                    "mime_type": record.mime_type,
                },
                "batch_index": batch_index,
                "batch_count": len(batches),
                "nodes": [_candidate_packet(candidate) for candidate in batch],
            }
            batch_hash = _hash_json(packet)
            batch_hashes.append(batch_hash)
            response = client.parse(
                provider=selection.provider,
                model=selection.model,
                system_prompt=_directory_system_prompt(),
                user_prompt=(
                    "Review this bounded directory-evidence packet. Copy batch_hash exactly and "
                    "return one decision for every local_key. Do not invent nodes or ranges.\n"
                    + json.dumps({**packet, "batch_hash": batch_hash}, ensure_ascii=False)
                ),
                schema=DirectoryBatchDecision,
                allow_live_web_search=False,
                reasoning_effort=selection.reasoning_effort,
                service_tier=selection.service_tier,
                service_tier_is_set="service_tier" in selection.model_fields_set,
            )
            decision = DirectoryBatchDecision.model_validate(response.output_parsed)
            normalized.extend(_apply_batch_decision(batch, decision, expected_hash=batch_hash))
            _report(
                self.progress_callback,
                "normalizing_directory",
                _normalization_batch_progress(batch_index + 1, len(batches)),
            )

        _validate_locked_navigation_invariants(candidates, normalized)

        return DirectoryNormalizationResult(
            candidates=tuple(normalized),
            turn_count=len(batches),
            metadata={
                "batch_count": len(batches),
                "batch_hashes": batch_hashes,
                "execution": "serial_bounded_turns",
            },
        )


class SourceDirectoryProcessor:
    def __init__(
        self,
        *,
        store: SourceStructureStore = source_structure_store,
        normalizer_factory: Callable[[SourceIngestionRecord], DirectoryNormalizer] | None = None,
    ) -> None:
        self.store = store
        self.normalizer_factory = normalizer_factory

    def process(
        self,
        *,
        record: SourceIngestionRecord,
        path: Path,
        catalog_model: AIModelSelection,
        progress_callback: CatalogProgressCallback | None = None,
        activity_callback: Callable[[AgentActivityEvent], None] | None = None,
    ) -> SourceStructure:
        started = time.perf_counter()
        metadata_hash = str(record.metadata.get("content_hash") or "").strip()
        content_hash = _file_hash(path)
        if not content_hash:
            raise SourceDirectoryProcessingError("The source content fingerprint is unavailable.")
        run = SourceCatalogRun(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="running",
            model=catalog_model.model,
            stage_history=["queued", "reading_directory_metadata"],
            metadata={
                "catalog_schema_version": CATALOG_SCHEMA_VERSION,
                "catalog_model_selection": catalog_model.model_dump(mode="json"),
                "source_content_hash": content_hash,
                "no_full_text_index": True,
            },
        )
        self.store.save_catalog_run(run)
        extraction: DirectoryExtraction | None = None
        try:
            if metadata_hash and metadata_hash != content_hash:
                raise SourceDirectoryProcessingError(
                    "The source file fingerprint no longer matches the uploaded source."
                )
            warnings: list[str]
            catalog_complete: bool
            execution_metadata: dict[str, object]
            structure_execution_metadata: dict[str, object]
            turn_count: int
            has_authoritative_ranges = False

            if self.normalizer_factory is None:
                # The production catalog path has exactly one semantic owner:
                # Pi owns directory and bounded range investigation. The host
                # mechanically validates and persists its exact typed artifact.
                _report(progress_callback, "source_codex_investigation", 30)
                direct_catalog = generate_codex_direct_catalog(
                    record=record,
                    source_path=path,
                    source_content_hash=content_hash,
                    selection=catalog_model,
                    on_activity=activity_callback,
                )
                chapters = list(direct_catalog.chapters)
                execution_metadata = dict(direct_catalog.audit_metadata)
                stage_history = [*run.stage_history, "source_codex_investigation"]
                has_authoritative_ranges = any(
                    chapter.mapping_status == "verified"
                    and chapter.range is not None
                    and chapter.catalog_evidence
                    for chapter in chapters
                )
                stage_history.append("directory_and_ranges_verified")
                run = self.store.save_catalog_run(
                    run.model_copy(
                        update={
                            "stage_history": stage_history,
                            "metadata": {**run.metadata, **execution_metadata},
                        }
                    )
                )
                turn_count = direct_catalog.turn_count
                warnings = []
                if not chapters:
                    warnings.append("The source agent returned an empty directory list.")
                catalog_complete = True
                structure_execution_metadata = {
                    key: value
                    for key, value in execution_metadata.items()
                    if key
                    not in {
                        "codex_directory_payload",
                        "codex_raw_output",
                    }
                }
                run = self.store.save_catalog_run(
                    run.model_copy(
                        update={
                            "stage_history": stage_history,
                            "metadata": {**run.metadata, **execution_metadata},
                        }
                    )
                )
            else:
                # Compatibility seam for legacy unit tests that explicitly
                # inject a host normalizer. Production construction never sets
                # this factory and therefore cannot enter this branch.
                extraction = extract_directory(
                    record,
                    path,
                    progress_callback=progress_callback,
                )
                run = self.store.save_catalog_run(
                    run.model_copy(
                        update={
                            "page_count": extraction.page_count,
                            "inspected_page_count": extraction.inspected_page_count,
                            "ocr_page_count": extraction.ocr_page_count,
                            "stage_history": [*run.stage_history, "normalizing_directory"],
                            "metadata": {**run.metadata, "extraction": extraction.metadata},
                        }
                    )
                )
                _report(progress_callback, "normalizing_directory", 64)
                normalization = self.normalizer_factory(record).normalize(
                    record=record,
                    candidates=extraction.candidates,
                    selection=catalog_model,
                )
                _validate_locked_navigation_invariants(
                    extraction.candidates,
                    normalization.candidates,
                )
                normalized_candidates = _reclose_normalized_ranges(
                    normalization.candidates,
                    extraction=extraction,
                )
                chapters = _materialize_chapters(
                    record=record,
                    candidates=normalized_candidates,
                    content_hash=content_hash,
                )
                warnings = list(extraction.warnings)
                if not chapters:
                    warnings.append(
                        "No citable directory node was found without extracting document body text."
                    )
                catalog_complete = not bool(extraction.metadata.get("navigation_truncated"))
                execution_metadata = {
                    "catalog_authority": "legacy_explicit_test_injection",
                    "extraction": extraction.metadata,
                    "normalization": normalization.metadata,
                }
                structure_execution_metadata = execution_metadata
                turn_count = normalization.turn_count

            validation_stage = (
                "validating_directory_ranges"
                if self.normalizer_factory is not None or has_authoritative_ranges
                else "validating_directory"
            )
            _report(progress_callback, validation_stage, 92)
            _validate_chapters(chapters)
            verified_count = sum(chapter.mapping_status == "verified" for chapter in chapters)
            quality = _catalog_quality(
                chapters,
                catalog_complete=catalog_complete,
                directory_only_complete=(
                    execution_metadata.get("catalog_task_contract")
                    == "directory_pages_offset_tree_v1"
                ),
            )
            status = "ready" if chapters else "linear_only"
            structure = SourceStructure(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                status=status,
                strategy="codex_directory_v1",
                has_verified_toc=bool(chapters) and catalog_complete,
                quality=quality,
                chapter_count=len(chapters),
                chunk_count=0,
                visual_count=0,
                visual_index_status="unsupported",
                visual_index_version=0,
                confidence=quality.confidence,
                source_content_hash=content_hash,
                catalog_schema_version=CATALOG_SCHEMA_VERSION,
                catalog_model=catalog_model.model,
                warnings=list(dict.fromkeys(warnings)),
                metadata={
                    "catalog_pipeline": CATALOG_SCHEMA_VERSION,
                    "content_hash": content_hash,
                    "catalog_model_selection": catalog_model.model_dump(mode="json"),
                    **structure_execution_metadata,
                    "body_text_extracted": False,
                    "source_chunks_created": False,
                    "vector_index_created": False,
                    "visual_index_created": False,
                    "open_notebook_called": False,
                },
            )
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            succeeded_run = run.model_copy(
                update={
                    "status": "succeeded",
                    "turn_count": turn_count,
                    "chapter_count": len(chapters),
                    "verified_chapter_count": verified_count,
                    "verification_rate": verified_count / len(chapters) if chapters else 0.0,
                    "duration_ms": duration_ms,
                    "stage_history": [
                        *run.stage_history,
                        validation_stage,
                        "publishing_catalog",
                        "succeeded",
                    ],
                    "completed_at": now_iso(),
                    "metadata": {
                        **run.metadata,
                        **execution_metadata,
                        "warning_count": len(structure.warnings),
                    },
                }
            )
            _report(progress_callback, "publishing_catalog", 97)
            if _file_hash(path) != content_hash:
                raise SourceDirectoryProcessingError(
                    "The source file changed while its directory catalog was being built."
                )
            published = self.store.publish_catalog(
                structure=structure,
                chapters=chapters,
                run=succeeded_run,
            )
            # Publication is the commit boundary. A UI/job progress callback is
            # auxiliary after that point and must never turn an already
            # committed catalog into an apparent processing failure.
            try:
                _report(progress_callback, "catalog_ready", 99)
            except Exception:
                pass
            return published
        except Exception as exc:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            failed = run.model_copy(
                update={
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error": str(exc),
                    "stage_history": [*run.stage_history, "failed"],
                    "completed_at": now_iso(),
                    "page_count": extraction.page_count if extraction is not None else run.page_count,
                    "inspected_page_count": (
                        extraction.inspected_page_count if extraction is not None else run.inspected_page_count
                    ),
                    "ocr_page_count": extraction.ocr_page_count if extraction is not None else run.ocr_page_count,
                }
            )
            self.store.save_catalog_run(failed)
            if isinstance(exc, SourceDirectoryProcessingError):
                raise
            raise SourceDirectoryProcessingError(str(exc)) from exc


def _bounded_candidate_batches(
    candidates: Sequence[DirectoryCandidate],
) -> list[list[DirectoryCandidate]]:
    batches: list[list[DirectoryCandidate]] = []
    pending: list[DirectoryCandidate] = []
    pending_chars = 0
    for candidate in candidates:
        candidate_chars = len(json.dumps(_candidate_packet(candidate), ensure_ascii=False))
        if candidate_chars > MAX_CODEX_BATCH_CHARS:
            raise SourceDirectoryProcessingError("One directory node exceeds the bounded Codex packet limit.")
        if pending and (
            len(pending) >= MAX_CODEX_BATCH_NODES
            or pending_chars + candidate_chars > MAX_CODEX_BATCH_CHARS
        ):
            batches.append(pending)
            pending = []
            pending_chars = 0
        pending.append(candidate)
        pending_chars += candidate_chars
    if pending:
        batches.append(pending)
    return batches


def _reclose_normalized_ranges(
    candidates: Sequence[DirectoryCandidate],
    *,
    extraction: DirectoryExtraction,
) -> tuple[DirectoryCandidate, ...]:
    format_name = str(extraction.metadata.get("format") or "")
    maximum_by_kind: dict[str, int] = {}
    if format_name == "pdf" and extraction.page_count:
        maximum_by_kind["pdf_pages"] = extraction.page_count
    elif format_name == "epub":
        maximum_by_kind["epub_spine"] = max(
            0,
            int(extraction.metadata.get("spine_count") or 0) - 1,
        )
    elif format_name == "docx":
        maximum_by_kind["docx_paragraphs"] = max(
            0,
            int(extraction.metadata.get("paragraph_count") or 0) - 1,
        )
    elif format_name == "pptx":
        maximum_by_kind["ppt_slides"] = max(
            1,
            int(extraction.metadata.get("slide_count") or extraction.page_count or 1),
        )
    elif format_name in {"markdown", "text"}:
        maximum_by_kind["text_lines"] = max(
            1,
            int(extraction.metadata.get("line_count") or 1),
        )
    elif format_name == "csv":
        maximum_by_kind["sheet_rows"] = max(
            1,
            int(extraction.metadata.get("row_count") or 1),
        )
    elif format_name == "html":
        maximum_by_kind["dom_anchor"] = max(
            (
                int(candidate.source_range.end)
                for candidate in extraction.candidates
                if candidate.source_range is not None
                and candidate.source_range.kind == "dom_anchor"
                and isinstance(candidate.source_range.end, int)
            ),
            default=0,
        )

    result: list[DirectoryCandidate] = []
    for index, candidate in enumerate(candidates):
        source_range = candidate.source_range
        if (
            source_range is not None
            and source_range.kind == "epub_spine"
            and _is_hierarchy_locked_navigation(candidate)
        ):
            # The extractor closes native EPUB navigation with authoritative
            # anchor evidence, including its N+1 truncation lookahead. Codex
            # cannot change this hierarchy or range, so a second close over
            # only the published prefix would discard the lookahead boundary.
            result.append(candidate)
            continue
        if (
            source_range is None
            or source_range.kind not in maximum_by_kind
            or not isinstance(source_range.start, int)
        ):
            result.append(candidate)
            continue
        boundary_index = next(
            (
                following_index
                for following_index in range(index + 1, len(candidates))
                if candidates[following_index].level <= candidate.level
            ),
            len(candidates),
        )
        boundary = candidates[boundary_index] if boundary_index < len(candidates) else None
        boundary_range = boundary.source_range if boundary is not None else None
        if boundary is not None and (
            boundary.mapping_status != "verified"
            or not _range_is_same_series(source_range, boundary_range)
            or not isinstance(boundary_range.start, int)
            or boundary_range.start < source_range.start
        ):
            result.append(
                replace(
                    candidate,
                    mapping_status="partial",
                    confidence=min(candidate.confidence, 0.64),
                    metadata={
                        **candidate.metadata,
                        "range_boundary_status": "unverified_successor",
                        "range_boundary_local_key": boundary.local_key,
                    },
                )
            )
            continue
        descendant_start: int | None = None
        descendant_end: int | None = None
        if candidate.mapping_status == "verified" and _is_numeric_containment_range(source_range):
            descendant_ranges = [
                descendant.source_range
                for descendant in candidates[index + 1 : boundary_index]
                if descendant.mapping_status == "verified"
                and _is_numeric_containment_range(descendant.source_range)
                and _range_is_same_series(source_range, descendant.source_range)
            ]
            if descendant_ranges:
                descendant_start = min(int(descendant.start) for descendant in descendant_ranges)
                descendant_end = max(int(descendant.end) for descendant in descendant_ranges)
        next_start = (
            int(boundary_range.start)
            if boundary_range is not None and isinstance(boundary_range.start, int)
            else None
        )
        if descendant_start is not None and (
            descendant_start < int(source_range.start)
            or (next_start is not None and descendant_end is not None and descendant_end > next_start)
        ):
            result.append(
                replace(
                    candidate,
                    mapping_status="partial",
                    confidence=min(candidate.confidence, 0.64),
                    metadata={
                        **candidate.metadata,
                        "range_boundary_status": (
                            "descendant_precedes_parent"
                            if descendant_start < int(source_range.start)
                            else "descendant_crosses_successor"
                        ),
                        **(
                            {"range_boundary_local_key": boundary.local_key}
                            if boundary is not None
                            else {}
                        ),
                    },
                )
            )
            continue
        maximum = maximum_by_kind[source_range.kind]
        boundary_anchor = boundary_range.start_anchor if boundary_range is not None else ""
        end = (
            maximum
            if next_start is None
            else max(
                source_range.start,
                next_start
                if source_range.kind == "epub_spine" and boundary_anchor
                else next_start - 1,
            )
        )
        if descendant_end is not None:
            end = max(end, descendant_end)
        updates: dict[str, object] = {"end": end}
        display_label = _normalized_range_label(source_range.kind, source_range.start, end)
        if display_label:
            updates["display_label"] = display_label
        if source_range.kind == "epub_spine":
            updates["end_anchor"] = boundary_anchor
        elif source_range.kind == "dom_anchor":
            updates["end_anchor"] = boundary_range.start_anchor if boundary_range is not None else ""
            updates["metadata"] = {
                **source_range.metadata,
                "end_heading_ordinal": next_start if next_start is not None else maximum + 1,
            }
        result.append(
            replace(
                candidate,
                source_range=source_range.model_copy(update=updates),
            )
        )
    return tuple(result)


def _is_numeric_containment_range(source_range: SourceRange | None) -> bool:
    return bool(
        source_range is not None
        and source_range.kind in NUMERIC_CONTAINMENT_RANGE_KINDS
        and isinstance(source_range.start, int)
        and not isinstance(source_range.start, bool)
        and isinstance(source_range.end, int)
        and not isinstance(source_range.end, bool)
    )


def _range_is_same_series(
    source_range: SourceRange,
    following_range: SourceRange | None,
) -> bool:
    if source_range is None or following_range is None:
        return False
    if source_range.kind != following_range.kind:
        return False
    if source_range.kind in {"sheet_rows", "dom_anchor"}:
        return source_range.container == following_range.container
    return isinstance(following_range.start, int)


def _normalized_range_label(kind: str, start: int, end: int) -> str:
    if kind == "pdf_pages":
        return f"PDF p. {start}" if start == end else f"PDF pp. {start}-{end}"
    if kind == "text_lines":
        return f"Line {start}" if start == end else f"Lines {start}-{end}"
    if kind == "docx_paragraphs":
        return f"Paragraph {start + 1}" if start == end else f"Paragraphs {start + 1}-{end + 1}"
    if kind == "ppt_slides":
        return f"Slide {start}" if start == end else f"Slides {start}-{end}"
    if kind == "sheet_rows":
        return f"Row {start}" if start == end else f"Rows {start}-{end}"
    if kind == "epub_spine":
        return f"EPUB spine {start}" if start == end else f"EPUB spine {start}-{end}"
    return ""


def _candidate_packet(candidate: DirectoryCandidate) -> dict[str, object]:
    source_range = candidate.source_range
    return {
        "local_key": candidate.local_key,
        "title": candidate.title[:300],
        "number": candidate.number[:80],
        "level": candidate.level,
        "order_index": candidate.order_index,
        "source_locator": candidate.source_locator[:500],
        "source_range": (
            {
                "kind": source_range.kind,
                "start": source_range.start,
                "end": source_range.end,
                "container": source_range.container[:300],
                "display_label": source_range.display_label[:300],
                "path_depth": len(source_range.path),
                "end_inclusive": source_range.end_inclusive,
            }
            if source_range is not None
            else None
        ),
        "mapping_status": candidate.mapping_status,
        "confidence": candidate.confidence,
        "navigation_provenance": candidate.metadata.get("navigation_provenance"),
        "hierarchy_locked": bool(candidate.metadata.get("hierarchy_locked")),
        "native_level": candidate.metadata.get("native_level"),
        "evidence": [
            {
                "method": item.method[:120],
                "source_locator": item.source_locator[:500],
                "page_start": item.page_start,
                "page_end": item.page_end,
                "excerpt": item.excerpt[:300],
                "confidence": item.confidence,
            }
            for item in candidate.evidence[:4]
        ],
    }


def _apply_batch_decision(
    candidates: Sequence[DirectoryCandidate],
    result: DirectoryBatchDecision,
    *,
    expected_hash: str,
) -> list[DirectoryCandidate]:
    if result.batch_hash != expected_hash:
        raise SourceDirectoryProcessingError("Codex returned decisions for a different directory packet.")
    expected_keys = [candidate.local_key for candidate in candidates]
    decisions_by_key = {decision.local_key: decision for decision in result.decisions}
    if len(decisions_by_key) != len(result.decisions) or set(decisions_by_key) != set(expected_keys):
        raise SourceDirectoryProcessingError("Codex must decide every directory candidate exactly once.")
    normalized: list[DirectoryCandidate] = []
    for candidate in candidates:
        decision = decisions_by_key[candidate.local_key]
        hierarchy_locked = _is_hierarchy_locked_navigation(candidate)
        # Native navigation hierarchy is host evidence. Codex may clean its
        # label/number, but cannot erase it or change a native level just
        # because a bounded packet starts in the middle of the tree.
        keep = hierarchy_locked or decision.keep or not bool(candidate.metadata.get("codex_may_reject"))
        if not keep:
            continue
        title = " ".join((decision.title or candidate.title).split()).strip()
        if not title:
            raise SourceDirectoryProcessingError("Codex returned a kept directory node without a title.")
        normalized.append(
            replace(
                candidate,
                title=title,
                number=" ".join((decision.number or candidate.number).split()).strip(),
                level=(
                    int(candidate.metadata["native_level"])
                    if hierarchy_locked
                    else max(1, min(12, decision.level))
                ),
            )
        )
    return normalized


def _is_hierarchy_locked_navigation(candidate: DirectoryCandidate) -> bool:
    native_level = candidate.metadata.get("native_level")
    return bool(
        candidate.metadata.get("hierarchy_locked")
        and candidate.metadata.get("navigation_provenance") == "native"
        and isinstance(native_level, int)
        and not isinstance(native_level, bool)
    )


def _validate_locked_navigation_invariants(
    original: Sequence[DirectoryCandidate],
    normalized: Sequence[DirectoryCandidate],
) -> None:
    expected = [candidate for candidate in original if _is_hierarchy_locked_navigation(candidate)]
    if not expected:
        return
    actual = [candidate for candidate in normalized if _is_hierarchy_locked_navigation(candidate)]
    if len(actual) != len(expected):
        raise SourceDirectoryProcessingError(
            "Native navigation normalization changed the number of hierarchy-locked nodes."
        )
    for before, after in zip(expected, actual, strict=True):
        native_level = int(before.metadata["native_level"])
        if (
            after.local_key != before.local_key
            or after.order_index != before.order_index
            or after.source_locator != before.source_locator
            or after.source_range != before.source_range
            or after.metadata.get("native_level") != native_level
            or after.level != native_level
        ):
            raise SourceDirectoryProcessingError(
                "Native navigation normalization violated a hierarchy-locked host invariant."
            )


def _materialize_chapters(
    *,
    record: SourceIngestionRecord,
    candidates: Sequence[DirectoryCandidate],
    content_hash: str,
) -> list[SourceChapter]:
    chapters: list[SourceChapter] = []
    level_stack: list[SourceChapter] = []
    semantic_occurrences: Counter[tuple[tuple[str, ...], str, str, int]] = Counter()
    for order_index, candidate in enumerate(candidates):
        level = max(1, candidate.level)
        while level_stack and level_stack[-1].level >= level:
            level_stack.pop()
        parent = level_stack[-1] if level_stack else None
        parent_path = parent.path if parent else []
        normalized_number = _normalize_number(candidate.number)
        semantic_key = (
            tuple(_normalize_label(value) for value in parent_path),
            normalized_number,
            _normalize_label(candidate.title),
            level,
        )
        occurrence = semantic_occurrences[semantic_key]
        semantic_occurrences[semantic_key] += 1
        chapter_id = stable_source_chapter_id(
            source_ingestion_id=record.id,
            parent_path=parent_path,
            normalized_number=normalized_number,
            title=candidate.title,
            level=level,
            source_locator=candidate.source_locator,
            order_index=occurrence,
        )
        page_start: int | None = None
        page_end_exclusive: int | None = None
        if (
            candidate.source_range is not None
            and candidate.source_range.kind == "pdf_pages"
            and isinstance(candidate.source_range.start, int)
            and isinstance(candidate.source_range.end, int)
        ):
            page_start = candidate.source_range.start
            page_end_exclusive = candidate.source_range.end + 1
        chapter = SourceChapter(
            id=chapter_id,
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            parent_id=parent.id if parent else None,
            number=candidate.number,
            normalized_number=normalized_number,
            title=candidate.title,
            level=level,
            path=[*parent_path, candidate.title],
            order_index=order_index,
            source_locator=candidate.source_locator,
            body_start_offset=None,
            body_end_offset=None,
            page_start=page_start,
            page_end=page_end_exclusive,
            anchor_status="verified" if candidate.mapping_status == "verified" else "unverified",
            range=candidate.source_range,
            mapping_status=candidate.mapping_status,
            source_content_hash=content_hash,
            catalog_evidence=list(candidate.evidence),
            confidence=max(0.0, min(1.0, candidate.confidence)),
            excerpt=candidate.title,
            metadata={
                **candidate.metadata,
                "catalog_pipeline": CATALOG_SCHEMA_VERSION,
                "semantic_identity_version": 2,
                "semantic_occurrence": occurrence,
                "legacy_page_end_is_exclusive": True,
            },
        )
        chapters.append(chapter)
        level_stack.append(chapter)
    return chapters


def _validate_chapters(chapters: Sequence[SourceChapter]) -> None:
    seen_ids: set[str] = set()
    known_chapters: dict[str, SourceChapter] = {}
    previous_order = -1
    for chapter in chapters:
        if chapter.id in seen_ids:
            raise SourceDirectoryProcessingError("Directory chapter ids are not unique.")
        if chapter.parent_id and chapter.parent_id not in known_chapters:
            raise SourceDirectoryProcessingError("A directory child appeared before its parent.")
        if chapter.order_index <= previous_order:
            raise SourceDirectoryProcessingError("Directory order is not strictly increasing.")
        if chapter.mapping_status == "verified" and chapter.range is None:
            raise SourceDirectoryProcessingError("A verified directory node has no authoritative range.")
        if chapter.range is not None and not chapter.range.end_inclusive:
            raise SourceDirectoryProcessingError("Authoritative source ranges must have inclusive end bounds.")
        parent = known_chapters.get(chapter.parent_id or "")
        if (
            parent is not None
            and parent.mapping_status == "verified"
            and chapter.mapping_status == "verified"
            and _is_numeric_containment_range(parent.range)
            and _is_numeric_containment_range(chapter.range)
            and _range_is_same_series(parent.range, chapter.range)
            and (
                int(chapter.range.start) < int(parent.range.start)
                or int(chapter.range.end) > int(parent.range.end)
            )
        ):
            raise SourceDirectoryProcessingError(
                "A verified directory child range falls outside its verified parent range."
            )
        seen_ids.add(chapter.id)
        known_chapters[chapter.id] = chapter
        previous_order = chapter.order_index


def _catalog_quality(
    chapters: Sequence[SourceChapter],
    *,
    catalog_complete: bool = True,
    directory_only_complete: bool = False,
) -> SourceStructureQuality:
    total = len(chapters)
    verified = sum(chapter.mapping_status == "verified" for chapter in chapters)
    unverified = total - verified
    ratio = verified / total if total else 0.0
    if total and catalog_complete and (directory_only_complete or verified == total):
        level = "fully_verified"
    elif verified:
        level = "partially_verified"
    else:
        level = "unverified"
    if total and directory_only_complete:
        diagnostics = ["目录页、PDF 页码偏移 P 与目录层级已通过目录任务合同校验。"]
    elif total and not verified:
        diagnostics = ["目录结构已识别；正文范围尚未映射。"]
    else:
        diagnostics = ["目录仅保存结构与范围，正文将在引用章节时按需读取。"]
    if not catalog_complete:
        diagnostics.append(
            "目录超过结构节点上限，当前只发布部分导航；已验证节点仍可单独引用。"
        )
    parent_ids = {chapter.parent_id for chapter in chapters if chapter.parent_id}
    leaf_chapters = [chapter for chapter in chapters if chapter.id not in parent_ids]
    return SourceStructureQuality(
        evaluator_version=2,
        level=level,
        text_readiness="unknown",
        confidence=(
            1.0
            if total and catalog_complete and (directory_only_complete or not verified)
            else ratio
            if catalog_complete
            else min(ratio, 0.9)
        ),
        total_chapter_count=total,
        verified_chapter_count=verified,
        unverified_chapter_count=unverified,
        verified_leaf_count=sum(
            chapter.mapping_status == "verified" for chapter in leaf_chapters
        ),
        expected_leaf_count=len(leaf_chapters),
        verified_ratio=ratio,
        boundary_valid_ratio=ratio,
        body_coverage_ratio=0.0,
        independent_anchor_ratio=ratio,
        meaningful_characters_per_page=0.0,
        diagnostics=diagnostics,
    )


def _directory_system_prompt() -> str:
    return """
You are the single OpenClass file-directory Codex. You review only bounded
navigation evidence prepared by the host. Never infer subject knowledge or add
headings that are absent from the packet. Keep a node only when it is a genuine
document navigation unit; remove repeated running headers, page numbers, and
decorative labels. Preserve local_key exactly, return every candidate exactly
once, preserve order, and never change source_range or locator values. You may
clean a title, normalize its visible number, and correct its hierarchy level
only when hierarchy_locked is false. For hierarchy_locked native navigation,
preserve every node and native_level even when a packet begins below level 1.
Verified host ranges are authoritative and must be kept. Return schema-valid
JSON only. Do not use web search.
""".strip()


def _normalize_number(value: str) -> str:
    normalized = _normalize_label(value).strip(".。")
    parts = [part for part in normalized.split(".") if part]
    if parts and all(part.isdigit() for part in parts):
        return ".".join(str(int(part)) for part in parts)
    return normalized


def _normalize_label(value: str) -> str:
    return " ".join(str(value or "").casefold().split()).strip()


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _report(callback: CatalogProgressCallback | None, phase: str, progress: int) -> None:
    if callback is not None:
        callback(phase, progress)


def _normalization_batch_progress(completed: int, total: int) -> int:
    if total <= 0:
        return NORMALIZATION_PROGRESS_END
    bounded_completed = max(0, min(completed, total))
    span = NORMALIZATION_PROGRESS_END - NORMALIZATION_PROGRESS_START
    return NORMALIZATION_PROGRESS_START + round(span * bounded_completed / total)


source_directory_processor = SourceDirectoryProcessor()

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (
    CoursePackage,
    PublicationReview,
    PublicationReviewFinding,
    ResourceLibraryItem,
    SourceIngestionRecord,
    now_iso,
)
from app.services.ai_execution_adapter import AIExecutionAdapter, build_ai_execution_adapter
from app.services.ai_model_catalog import resolve_text_model_selection
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_ingestion_service
from app.services.source_structure_store import source_structure_store


_MAX_UNIT_CHARACTERS = 6_000
_MAX_BATCH_CHARACTERS = 24_000
_MAX_BATCH_UNITS = 12


@dataclass(frozen=True)
class PublicationSourceUnit:
    source_id: str
    source_title: str
    unit_id: str
    order_index: int
    total_units: int
    location: str
    section_path: list[str]
    text: str


class _PublicationUnitDecision(BaseModel):
    unit_id: str
    region: Literal["body", "non_body", "uncertain"]
    copyright_declaration: bool
    evidence_excerpt: str = Field(default="", max_length=800)
    reason: str = Field(default="", max_length=500)


class _PublicationBatchDecision(BaseModel):
    decisions: list[_PublicationUnitDecision]


_PUBLICATION_REVIEW_PROMPT = """
You are the publication-safety reviewer for a general AI course workspace. Review every supplied
document unit exactly once. Use its position, locator, section path, and text to classify it as:

- body: the source's substantive main content;
- non_body: cover/title/imprint pages, copyright or license pages, contents, dedication,
  acknowledgements, foreword/preface, appendices, afterword/postscript, bibliography/index,
  colophon, or other front/back matter outside the substantive body;
- uncertain: the available evidence is insufficient to distinguish body from non-body.

For non_body units, detect any copyright, ownership, licensing, reproduction, redistribution, or
publication-rights declaration. A discussion of copyright as a topic, a citation, or an ordinary
quotation inside body content is not a declaration for this gate. When a declaration is present,
set copyright_declaration=true and copy a short, exact, contiguous excerpt from that unit into
evidence_excerpt. Never paraphrase evidence. Return one decision for every unit_id and no extras.
""".strip()


def scan_publication_units(
    *,
    units: list[PublicationSourceUnit],
    source_count: int,
    source_fingerprint: str,
    adapter: AIExecutionAdapter,
) -> PublicationReview:
    started_at = now_iso()
    findings: list[PublicationReviewFinding] = []
    try:
        for batch in _unit_batches(units):
            result = adapter.parse_structured(
                system_prompt=_PUBLICATION_REVIEW_PROMPT,
                user_prompt=json.dumps(
                    {
                        "units": [
                            {
                                "unit_id": unit.unit_id,
                                "source_title": unit.source_title,
                                "position": {
                                    "index": unit.order_index + 1,
                                    "total": unit.total_units,
                                },
                                "location": unit.location,
                                "section_path": unit.section_path,
                                "text": unit.text,
                            }
                            for unit in batch
                        ]
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                schema=_PublicationBatchDecision,
                allow_live_web_search=False,
            )
            parsed = _PublicationBatchDecision.model_validate(result.output_parsed)
            decisions = {decision.unit_id: decision for decision in parsed.decisions}
            expected_ids = {unit.unit_id for unit in batch}
            if len(decisions) != len(parsed.decisions) or set(decisions) != expected_ids:
                raise ValueError("The publication review did not cover every supplied unit exactly once.")
            unit_by_id = {unit.unit_id: unit for unit in batch}
            for unit_id, decision in decisions.items():
                unit = unit_by_id[unit_id]
                if decision.region == "uncertain":
                    raise ValueError(f"The publication review could not classify {unit.location or unit_id}.")
                if decision.region != "non_body" or not decision.copyright_declaration:
                    continue
                excerpt = decision.evidence_excerpt.strip()
                if not excerpt or not _grounded_excerpt(excerpt, unit.text):
                    raise ValueError("The publication review returned an ungrounded copyright finding.")
                findings.append(
                    PublicationReviewFinding(
                        source_id=unit.source_id,
                        source_title=unit.source_title,
                        location=unit.location,
                        evidence_excerpt=excerpt,
                        reason=decision.reason.strip(),
                    )
                )
    except Exception as exc:
        return PublicationReview(
            status="error",
            source_fingerprint=source_fingerprint,
            scanned_source_count=source_count,
            scanned_unit_count=len(units),
            message=f"资料发布审查未能完整完成：{exc}",
            started_at=started_at,
            completed_at=now_iso(),
        )

    if findings:
        return PublicationReview(
            status="blocked",
            source_fingerprint=source_fingerprint,
            scanned_source_count=source_count,
            scanned_unit_count=len(units),
            findings=findings,
            message="上传资料的非正文内容中发现版权声明，课程保持 Private。",
            started_at=started_at,
            completed_at=now_iso(),
        )
    return PublicationReview(
        status="approved",
        source_fingerprint=source_fingerprint,
        scanned_source_count=source_count,
        scanned_unit_count=len(units),
        message="资料发布审查已通过。",
        started_at=started_at,
        completed_at=now_iso(),
    )


def review_project_publication(
    *,
    owner_user_id: str,
    package: CoursePackage,
    lesson_id: str | None = None,
    adapter: AIExecutionAdapter | None = None,
) -> PublicationReview:
    all_sources = source_ingestion_service.list_sources(
        owner_user_id=owner_user_id,
        package_id=package.id,
    )
    all_legacy_resources = _legacy_resources_for_project(package, lesson_id=lesson_id)
    referenced_source_ids, unresolved_references = _referenced_source_ids(
        owner_user_id=owner_user_id,
        package=package,
        lesson_id=lesson_id,
    )
    if unresolved_references:
        stamp = now_iso()
        return PublicationReview(
            status="error",
            message="课程存在无法追溯的资料引用记录，无法完成版权审核。",
            started_at=stamp,
            completed_at=stamp,
        )
    if not referenced_source_ids:
        stamp = now_iso()
        message = (
            "课程没有上传资料，可以公开。"
            if not all_sources and not all_legacy_resources
            else "课程没有引用上传资料，可以公开。"
        )
        return PublicationReview(
            status="approved",
            message=message,
            started_at=stamp,
            completed_at=stamp,
        )

    sources = [source for source in all_sources if source.id in referenced_source_ids]
    legacy_resources = [
        resource for resource in all_legacy_resources if resource.id in referenced_source_ids
    ]
    available_source_ids = {source.id for source in sources}
    available_source_ids.update(resource.id for resource in legacy_resources)
    if available_source_ids != referenced_source_ids:
        stamp = now_iso()
        return PublicationReview(
            status="error",
            scanned_source_count=len(available_source_ids),
            message="课程存在无法追溯的资料引用记录，无法完成版权审核。",
            started_at=stamp,
            completed_at=stamp,
        )

    source_count = len(sources) + len(legacy_resources)
    fingerprint = _source_fingerprint(sources, legacy_resources)

    pending_titles = [source.title for source in sources if source.status != "ready"]
    pending_titles.extend(
        resource.name for resource in legacy_resources if resource.ingestion_status != "ready"
    )
    if pending_titles:
        stamp = now_iso()
        return PublicationReview(
            status="error",
            source_fingerprint=fingerprint,
            scanned_source_count=source_count,
            message="部分上传资料仍在解析或解析失败，请处理完成后重新公开。",
            started_at=stamp,
            completed_at=stamp,
        )

    try:
        units: list[PublicationSourceUnit] = []
        for source in sources:
            units.extend(_source_ingestion_units(source))
        for resource in legacy_resources:
            units.extend(_legacy_resource_units(resource))
        if not units:
            raise ValueError("上传资料没有可验证的抽取文本。")
    except Exception as exc:
        stamp = now_iso()
        return PublicationReview(
            status="error",
            source_fingerprint=fingerprint,
            scanned_source_count=source_count,
            message=f"资料发布审查无法读取全部上传资料：{exc}",
            started_at=stamp,
            completed_at=stamp,
        )

    try:
        selected_adapter = adapter
        if selected_adapter is None:
            selection = resolve_text_model_selection(None, user_id=owner_user_id)
            selected_adapter = build_ai_execution_adapter(selection, owner_user_id=owner_user_id)
        review = scan_publication_units(
            units=units,
            source_count=source_count,
            source_fingerprint=fingerprint,
            adapter=selected_adapter,
        )
    except Exception:
        stamp = now_iso()
        return PublicationReview(
            status="error",
            source_fingerprint=fingerprint,
            scanned_source_count=source_count,
            scanned_unit_count=len(units),
            message="资料发布审查未能启动，请稍后重试。",
            started_at=stamp,
            completed_at=stamp,
        )
    current_sources = source_ingestion_service.list_sources(
        owner_user_id=owner_user_id,
        package_id=package.id,
    )
    current_sources = [
        source for source in current_sources if source.id in referenced_source_ids
    ]
    if _source_fingerprint(current_sources, legacy_resources) != fingerprint:
        return review.model_copy(
            update={
                "status": "error",
                "findings": [],
                "message": "扫描期间上传资料发生变化，请重新申请公开。",
                "completed_at": now_iso(),
            }
        )
    return review


def _referenced_source_ids(
    *,
    owner_user_id: str,
    package: CoursePackage,
    lesson_id: str | None,
) -> tuple[set[str], list[str]]:
    lessons = (
        [lesson for lesson in package.lessons if lesson.id == lesson_id]
        if lesson_id is not None
        else package.lessons
    )
    source_ids: set[str] = set()
    bundle_source_ids: dict[str, set[str]] = {}
    bundle_ids: set[str] = set()
    unresolved: list[str] = []

    for lesson in lessons:
        requirements = [lesson.learning_requirements]
        requirements.extend(
            commit.runtime_snapshot.learning_requirements
            for commit in lesson.history_graph.commits
            if commit.runtime_snapshot is not None
        )
        for requirement in requirements:
            if requirement is None:
                continue
            grounding = requirement.source_grounding
            for reference in grounding.confirmed_references:
                source_id = reference.source_ingestion_id.strip()
                bundle_id = reference.evidence_bundle_id.strip()
                if not source_id:
                    unresolved.append(bundle_id or f"lesson:{lesson.id}")
                    continue
                source_ids.add(source_id)
                if bundle_id:
                    bundle_source_ids.setdefault(bundle_id, set()).add(source_id)
            confirmed_bundle_id = grounding.confirmed_bundle_id.strip()
            if confirmed_bundle_id:
                bundle_ids.add(confirmed_bundle_id)

        for commit in lesson.history_graph.commits:
            metadata_bundle_ids = commit.metadata.get("verified_source_bundle_ids")
            commit_bundle_ids = {
                value.strip()
                for value in metadata_bundle_ids
                if isinstance(value, str) and value.strip()
            } if isinstance(metadata_bundle_ids, list) else set()
            bundle_ids.update(commit_bundle_ids)
            if commit.metadata.get("verified_source_reference_used") and not commit_bundle_ids:
                runtime = commit.runtime_snapshot
                runtime_references = (
                    runtime.learning_requirements.source_grounding.confirmed_references
                    if runtime is not None and runtime.learning_requirements is not None
                    else []
                )
                if not runtime_references:
                    unresolved.append(f"commit:{commit.id}")

    for bundle_id in bundle_ids:
        if bundle_id in bundle_source_ids:
            continue
        bundle = source_evidence_store.get_bundle(
            owner_user_id=owner_user_id,
            bundle_id=bundle_id,
        )
        if bundle is None:
            unresolved.append(bundle_id)
            continue
        resolved = {
            source_id
            for source_id in [
                bundle.metadata.get("source_ingestion_id"),
                *(item.source_ingestion_id for item in bundle.evidence_items),
                *(item.source_ingestion_id for item in bundle.visual_items),
            ]
            if isinstance(source_id, str) and source_id.strip()
        }
        if not resolved:
            unresolved.append(bundle_id)
            continue
        source_ids.update(resolved)

    return source_ids, unresolved


def _source_ingestion_units(source: SourceIngestionRecord) -> list[PublicationSourceUnit]:
    view = source_structure_store.get_structure_view(source=source, chunk_limit=100_000)
    chapter_by_id = {chapter.id: chapter for chapter in view.chapters}
    raw_units: list[tuple[str, str, list[str], str]] = []
    for chunk in view.chunks:
        chapter = chapter_by_id.get(chunk.chapter_id or "")
        location = chunk.source_locator or _page_location(chunk.page_start, chunk.page_end)
        raw_units.append(
            (
                chunk.id,
                location,
                list(chapter.path if chapter else []),
                chunk.text,
            )
        )
    if not raw_units:
        content_result = source_ingestion_service.source_content(
            owner_user_id=source.owner_user_id,
            package_id=source.package_id,
            source_id=source.id,
        )
        content = content_result[1] if content_result is not None else ""
        raw_units = [(f"{source.id}:content", "full extracted text", [], content)]
    return _expand_units(source.id, source.title, raw_units)


def _legacy_resource_units(resource: ResourceLibraryItem) -> list[PublicationSourceUnit]:
    raw_units = [
        (
            unit.id,
            unit.source_locator or _page_location(unit.page_no, unit.page_no),
            list(unit.heading_path),
            unit.text,
        )
        for unit in resource.source_units
    ]
    if not raw_units:
        raw_units = [(f"{resource.id}:content", "full extracted text", [], resource.text_content or "")]
    return _expand_units(resource.id, resource.name, raw_units)


def _expand_units(
    source_id: str,
    source_title: str,
    raw_units: list[tuple[str, str, list[str], str]],
) -> list[PublicationSourceUnit]:
    expanded: list[tuple[str, str, list[str], str]] = []
    for raw_id, location, section_path, raw_text in raw_units:
        text = raw_text.strip()
        if not text:
            continue
        pieces = [text[index : index + _MAX_UNIT_CHARACTERS] for index in range(0, len(text), _MAX_UNIT_CHARACTERS)]
        for piece_index, piece in enumerate(pieces):
            expanded.append((f"{raw_id}:{piece_index}", location, section_path, piece))
    total = len(expanded)
    return [
        PublicationSourceUnit(
            source_id=source_id,
            source_title=source_title,
            unit_id=unit_id,
            order_index=index,
            total_units=total,
            location=location,
            section_path=section_path,
            text=text,
        )
        for index, (unit_id, location, section_path, text) in enumerate(expanded)
    ]


def _unit_batches(units: list[PublicationSourceUnit]) -> list[list[PublicationSourceUnit]]:
    batches: list[list[PublicationSourceUnit]] = []
    current: list[PublicationSourceUnit] = []
    current_characters = 0
    for unit in units:
        if current and (
            len(current) >= _MAX_BATCH_UNITS
            or current_characters + len(unit.text) > _MAX_BATCH_CHARACTERS
        ):
            batches.append(current)
            current = []
            current_characters = 0
        current.append(unit)
        current_characters += len(unit.text)
    if current:
        batches.append(current)
    return batches


def _legacy_resources_for_project(
    package: CoursePackage,
    *,
    lesson_id: str | None,
) -> list[ResourceLibraryItem]:
    if lesson_id is None:
        return list(package.resources)
    return [resource for resource in package.resources if resource.scope_lesson_id == lesson_id]


def _source_fingerprint(
    sources: list[SourceIngestionRecord],
    resources: list[ResourceLibraryItem],
) -> str:
    payload = [
        {
            "id": source.id,
            "status": source.status,
            "size_bytes": source.size_bytes,
            "content_hash": source.metadata.get("content_hash", ""),
        }
        for source in sources
    ]
    payload.extend(
        {
            "id": resource.id,
            "uploaded_at": resource.uploaded_at,
            "status": resource.ingestion_status,
            "size_bytes": resource.size_bytes,
        }
        for resource in resources
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _page_location(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"page {start}"
    return f"pages {start}-{end}"


def _grounded_excerpt(excerpt: str, text: str) -> bool:
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    return normalize(excerpt) in normalize(text)

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.models import (
    EvidenceBundle,
    LearningClarificationStatus,
    LearningRequirementAuxiliaryFactor,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
    LearningSourceGrounding,
    LearningSourceReference,
    Lesson,
    RetrievalEvidence,
    SelectionRef,
    now_iso,
)
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.source_chapter_identity import rebind_stale_source_chapter_selection
from app.services.source_evidence_store import source_evidence_store
from app.services.source_range_reader import (
    SourceRangeReadError,
    is_codex_directory_catalog,
    read_verified_source_range,
)
from app.services.source_structure_indexer import (
    source_structure_needs_upgrade,
)
from app.services.source_scope_ocr import (
    SourceScopeOcrError,
    has_usable_source_text,
    recover_pdf_scope_evidence,
)
from app.services.source_structure_store import source_structure_store
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION


SOURCE_BOARD_TOKEN_BUDGET = 48_000
SOURCE_FREEZE_TOKEN_BUDGET = 2_147_483_647
SOURCE_BOARD_EVIDENCE_LIMIT = 64


class SourceGroundedBoardError(RuntimeError):
    """Raised when a user-selected source cannot safely ground a new board."""


@dataclass(frozen=True)
class SourceGroundedBoardPlan:
    requirement: LearningRequirementSheet
    clarification: LearningClarificationStatus
    teaching_plan: str


def resolve_source_grounded_board_plan(
    *,
    owner_user_id: str,
    lesson: Lesson,
    selection: SelectionRef | None,
    query: str = "",
) -> SourceGroundedBoardPlan | None:
    """Turn one verified source selection into a frozen-ready blank-board input.

    A structured source click is an explicit learner choice of the material
    boundary.  It is therefore enough to start a knowledge board without
    collecting learner level or target-scenario fields.  This function never
    performs semantic search and never selects a different source on the
    learner's behalf.
    """
    if selection is None or selection.kind != "source":
        return None
    if selection.source_scope_kind == "repository_node":
        from app.services.repository_grounding import (
            RepositoryGroundingError,
            resolve_repository_grounded_board_plan,
        )

        try:
            return resolve_repository_grounded_board_plan(
                owner_user_id=owner_user_id,
                lesson=lesson,
                selection=selection,
                query=query,
            )
        except RepositoryGroundingError as exc:
            raise SourceGroundedBoardError(str(exc)) from exc
    if not selection.source_ingestion_id:
        raise SourceGroundedBoardError("这份资料引用缺少可验证的章节位置，请重新从资料目录中选择章节。")
    is_whole_source = selection.source_scope_kind == "source"
    is_page_range = selection.source_scope_kind == "page_range"
    if is_page_range and selection.source_range is None:
        if (
            selection.source_page_start is None
            or selection.source_page_end is None
            or selection.source_page_start < 1
            or selection.source_page_end <= selection.source_page_start
        ):
            raise SourceGroundedBoardError("这份资料引用缺少有效的页段边界。")
    elif not is_whole_source and not selection.source_chapter_id:
        raise SourceGroundedBoardError("这份资料引用缺少可验证的章节位置。")

    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    package, _current_lesson = workspace_state.find_lesson_package(workspace, lesson.id)
    source = source_evidence_store.get_source(
        owner_user_id=owner_user_id,
        package_id=package.id,
        source_id=selection.source_ingestion_id,
    )
    if source is None or source.status != "ready":
        raise SourceGroundedBoardError("这份资料尚未准备好，暂时不能据此生成板书。")
    source_catalog_pipeline = str(
        source.metadata.get("catalog_pipeline")
        or source.metadata.get("structure_pipeline")
        or ""
    )
    if is_whole_source and source_catalog_pipeline == "codex_directory_v1":
        raise SourceGroundedBoardError("新目录资料需要先选择一个已验证的章节范围。")
    if is_whole_source:
        stored_structure = source_structure_store.get_structure(
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_id=source.id,
        )
        if stored_structure is not None and is_codex_directory_catalog(stored_structure):
            raise SourceGroundedBoardError("新目录资料需要先选择一个已验证的章节范围。")
        from app.services.open_notebook_source_grounding import (
            OpenNotebookSourceGroundingError,
            resolve_open_notebook_source_plan,
        )

        try:
            plan = resolve_open_notebook_source_plan(
                owner_user_id=owner_user_id,
                package_id=package.id,
                lesson=lesson,
                source=source,
                query=query.strip() or selection.excerpt,
            )
        except OpenNotebookSourceGroundingError as exc:
            raise SourceGroundedBoardError(str(exc)) from exc
        return SourceGroundedBoardPlan(
            requirement=plan.requirement,
            clarification=plan.clarification,
            teaching_plan=plan.teaching_plan,
        )

    view = source_structure_store.get_structure_view(source=source, chunk_limit=0)
    if view.structure is None or view.structure.status not in {"ready", "linear_only"}:
        raise SourceGroundedBoardError("这份资料的结构索引尚未完成，请稍后重试。")
    uses_on_demand_range = is_codex_directory_catalog(view.structure)
    if uses_on_demand_range and is_whole_source:
        raise SourceGroundedBoardError("新目录资料需要先选择一个已验证的章节范围。")
    authoritative_chapter = None
    if uses_on_demand_range:
        catalog_pair = source_structure_store.get_catalog_chapter(
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_id=source.id,
            chapter_id=selection.source_chapter_id or "",
        )
        if catalog_pair is None:
            raise SourceGroundedBoardError("找不到这份引用对应的已验证目录范围，请重新选择。")
        view.structure, authoritative_chapter = catalog_pair
    structure_upgrade_deferred = (
        False if uses_on_demand_range else source_structure_needs_upgrade(view.structure)
    )
    visual_upgrade_deferred = (
        False
        if uses_on_demand_range
        else _needs_visual_index_upgrade(
            source.mime_type,
            source.file_name,
            view.structure.metadata,
        )
    )
    if structure_upgrade_deferred or visual_upgrade_deferred:
        # A verified, usable index is sufficient to honor the learner's selected
        # source boundary. Rebuilding a large source belongs to the ingestion or
        # explicit rebuild workflow; doing it synchronously here can make one
        # chat turn scan the entire file before board generation even starts.
        ai_usage_logger.log_event(
            "source_structure_upgrade_deferred",
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_ingestion_id=source.id,
            structure_status=view.structure.status,
            structure_upgrade_deferred=structure_upgrade_deferred,
            visual_upgrade_deferred=visual_upgrade_deferred,
            structure_index_version=view.structure.metadata.get(
                "structure_index_version"
            ),
            visual_index_version=view.structure.metadata.get("visual_index_version"),
        )
    chapter = authoritative_chapter or next(
        (
            candidate
            for candidate in view.chapters
            if candidate.id == selection.source_chapter_id
            and (
                candidate.mapping_status == "verified"
                if uses_on_demand_range
                else candidate.anchor_status == "verified"
            )
        ),
        None,
    )
    if chapter is None and uses_on_demand_range:
        raise SourceGroundedBoardError("找不到这份引用对应的已验证目录范围，请重新选择。")
    if chapter is None and not is_page_range and not uses_on_demand_range:
        rebound = rebind_stale_source_chapter_selection(
            selection=selection,
            source_ingestion_id=source.id,
            chapters=view.chapters,
        )
        if rebound.is_ambiguous:
            raise SourceGroundedBoardError("这份资料目录发生变化，当前引用对应多个章节，请重新选择一次。")
        chapter = rebound.chapter
    if chapter is None and not is_page_range:
        raise SourceGroundedBoardError("找不到这份引用对应的已验证正文范围，请重新从资料目录中选择章节。")
    following_chapter = None
    if chapter is not None:
        following_chapter = next(
            (
                candidate
                for candidate in sorted(view.chapters, key=lambda item: item.order_index)
                if candidate.order_index > chapter.order_index
                and candidate.anchor_status == "verified"
            ),
            None,
        )

    range_read = None
    if uses_on_demand_range:
        assert chapter is not None
        try:
            range_read = read_verified_source_range(
                owner_user_id=owner_user_id,
                package_id=package.id,
                source=source,
                structure=view.structure,
                chapter=chapter,
                selection=selection,
            )
        except SourceRangeReadError as exc:
            raise SourceGroundedBoardError(str(exc)) from exc
        evidence = range_read.evidence_items
    elif is_page_range:
        assert selection.source_page_start is not None
        assert selection.source_page_end is not None
        evidence = source_structure_store.page_range_evidence(
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_ingestion_id=source.id,
            page_start=selection.source_page_start,
            page_end=selection.source_page_end,
            token_budget=SOURCE_FREEZE_TOKEN_BUDGET,
        )
    else:
        assert chapter is not None
        evidence = source_structure_store.chapter_evidence_by_id(
            owner_user_id=owner_user_id,
            package_id=package.id,
            chapter_id=chapter.id,
            limit=SOURCE_BOARD_EVIDENCE_LIMIT,
            token_budget=SOURCE_FREEZE_TOKEN_BUDGET,
        )
    if not uses_on_demand_range and not any(
        has_usable_source_text(item.expanded_text) for item in evidence
    ):
        try:
            evidence = recover_pdf_scope_evidence(
                source=source,
                chapter=chapter,
                following_chapter=following_chapter,
                page_start=(
                    selection.source_page_start
                    if is_page_range
                    else chapter.page_start if chapter else None
                ),
                page_end_exclusive=(
                    selection.source_page_end
                    if is_page_range
                    else chapter.page_end if chapter else None
                ),
            )
        except SourceScopeOcrError as exc:
            raise SourceGroundedBoardError(str(exc)) from exc
    if not evidence or (
        not uses_on_demand_range
        and not any(has_usable_source_text(item.expanded_text) for item in evidence)
    ):
        raise SourceGroundedBoardError("所选资料范围尚未提取到可用正文。")

    visual_evidence = (
        []
        if uses_on_demand_range
        else source_structure_store.visual_evidence_for_scope(
            owner_user_id=owner_user_id,
            package_id=package.id,
            source_ingestion_id=source.id,
            chapter_id=chapter.id if chapter else None,
            page_start=(
                selection.source_page_start
                if is_page_range
                else chapter.page_start if chapter else None
            ),
            page_end=(
                selection.source_page_end
                if is_page_range
                else chapter.page_end if chapter else None
            ),
        )
    )

    bundle = EvidenceBundle(
        owner_user_id=owner_user_id,
        package_id=package.id,
        lesson_id=lesson.id,
        purpose="board_generation",
        status="confirmed",
        query=selection.excerpt,
        evidence_items=evidence,
        visual_items=visual_evidence,
        context_text=_evidence_context_text(evidence),
        token_count=sum(item.token_count for item in evidence),
        confirmed_by_user=True,
        confirmed_at=now_iso(),
        metadata={
            "origin": "structured_source_selection",
            "source_ingestion_id": source.id,
            "source_chapter_id": chapter.id if chapter else "",
            "source_scope_kind": selection.source_scope_kind,
            "source_structure_id": view.structure.id,
            "catalog_pipeline": (
                "codex_directory_v1" if uses_on_demand_range else "legacy_structure"
            ),
            "catalog_version": range_read.catalog_version if range_read else None,
            "source_content_hash": range_read.source_content_hash if range_read else "",
            "source_range": range_read.source_range if range_read else None,
        },
    )
    source_evidence_store.save_bundle(bundle)

    if chapter is not None:
        chapter_number = (chapter.normalized_number or chapter.number).strip()
        chapter_title = chapter.title.strip()
        chapter_label = chapter_title
        if chapter_number and not (
            chapter_title == chapter_number
            or chapter_title.startswith(f"{chapter_number} ")
            or chapter_title.startswith(f"{chapter_number}\t")
        ):
            chapter_label = f"{chapter_number} {chapter_title}".strip()
        chapter_label = chapter_label or chapter.title or source.title
    else:
        chapter_label = evidence[0].page_range or selection.source_page_range or source.title
    reference = LearningSourceReference(
        evidence_bundle_id=bundle.id,
        source_ingestion_id=source.id,
        source_title=source.title,
        source_chapter_id=chapter.id if chapter else "",
        chapter_number=(chapter.normalized_number or chapter.number) if chapter else "",
        chapter_title=chapter.title if chapter else "",
        scope_kind="page_range" if is_page_range else "chapter",
        scope_chapter_id=chapter.id if chapter else "",
        scope_chapter_number=(chapter.normalized_number or chapter.number) if chapter else "",
        scope_chapter_title=chapter.title if chapter else "",
        section_path=chapter.path if chapter else evidence[0].section_path,
        source_locator=chapter.source_locator if chapter else selection.source_locator,
        page_range=(
            _source_range_display_label(range_read.source_range)
            if range_read
            else evidence[0].page_range
        ),
        page_start=(
            _pdf_range_endpoint(range_read.source_range, "start")
            if range_read
            else selection.source_page_start if is_page_range else chapter.page_start if chapter else None
        ),
        page_end=(
            _pdf_range_endpoint(range_read.source_range, "end")
            if range_read
            else selection.source_page_end if is_page_range else chapter.page_end if chapter else None
        ),
        body_start_offset=chapter.body_start_offset if chapter else None,
        body_end_offset=chapter.body_end_offset if chapter else None,
        chunk_ids=_dedupe_chunk_ids(evidence),
        visual_ids=[item.visual_id for item in visual_evidence],
        source_structure_id=view.structure.id,
        source_structure_updated_at=view.structure.updated_at,
        content_hash=_evidence_hash(evidence),
    )
    grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id=bundle.id,
        confirmed_at=bundle.confirmed_at,
        confirmed_references=[reference],
        frozen_evidence=evidence,
        frozen_visual_evidence=visual_evidence,
    )
    source_label = " / ".join(part for part in [source.title, chapter_label, reference.page_range] if part)
    requirement = LearningRequirementSheet(
        teaching_type="knowledge_point",
        learning_content=chapter_label,
        current_level="",
        target_scenario="",
        auxiliary_factors=[
            LearningRequirementAuxiliaryFactor(
                label="confirmed_source",
                value=source_label,
                evidence="structured_source_selection",
            )
        ],
        theme=chapter_label,
        learning_goal=f"基于《{source.title}》的所选章节建立可学习的板书。",
        level="",
        known_background="",
        current_questions=[],
        learning_need_checklist=["已确认资料范围"],
        target_depth="按资料章节的实际结构组织讲解。",
        output_preference="结构化 Markdown 板书",
        boundary=source_label,
        board_scope=[source_label],
        success_criteria="覆盖所选资料范围的核心概念、结构关系与必要例证。",
        board_workflow="generate_from_scratch",
        work_mode="knowledge_board",
        granularity="source_range" if is_page_range else "source_chapter",
        source_grounding=grounding,
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="资料范围已确认",
        reason="用户已选择一个可验证的资料章节，系统将直接基于该章节生成板书。",
        missing_items=[],
        can_start=True,
        summary=source_label,
        key_facts=[
            LearningRequirementKeyFact(
                label="source_chapter",
                value=source_label,
                evidence="structured_source_selection",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="资料章节",
                is_clear=True,
                evidence="structured_source_selection",
            )
        ],
        work_mode="knowledge_board",
        granularity="source_range" if is_page_range else "source_chapter",
        ready_for_board=True,
    )
    return SourceGroundedBoardPlan(
        requirement=requirement,
        clarification=clarification,
        teaching_plan=(
            "以冻结的资料正文为唯一事实依据，保留章节结构，提炼核心概念、"
            "关键关系和必要例证，生成一份可独立学习的板书。"
        ),
    )


def _evidence_context_text(evidence: list[RetrievalEvidence]) -> str:
    return "\n\n".join(
        "\n".join(
            part
            for part in [
                item.source_title,
                " > ".join(item.section_path),
                item.page_range,
                item.expanded_text,
            ]
            if part
        )
        for item in evidence
    )


def _dedupe_chunk_ids(evidence: list[RetrievalEvidence]) -> list[str]:
    return list(dict.fromkeys(chunk_id for item in evidence for chunk_id in item.chunk_ids if chunk_id))


def _evidence_hash(evidence: list[RetrievalEvidence]) -> str:
    content = "\n".join(item.expanded_text for item in evidence if item.expanded_text)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _pdf_range_endpoint(source_range: dict[str, object], key: str) -> int | None:
    if source_range.get("kind") != "pdf_pages":
        return None
    value = source_range.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _source_range_display_label(source_range: dict[str, object]) -> str:
    display_label = source_range.get("display_label")
    if isinstance(display_label, str) and display_label.strip():
        return display_label.strip()
    if source_range.get("kind") == "pdf_pages":
        start = _pdf_range_endpoint(source_range, "start")
        end = _pdf_range_endpoint(source_range, "end")
        if start is not None and end is not None:
            return f"PDF p. {start}" if start == end else f"PDF pp. {start}-{end}"
    start = source_range.get("start")
    end = source_range.get("end")
    return str(start) if start == end else f"{start}-{end}"


def _needs_visual_index_upgrade(
    mime_type: str,
    file_name: str,
    metadata: dict[str, object],
) -> bool:
    normalized_mime = mime_type.lower()
    normalized_name = file_name.lower()
    supported_extensions = (
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".epub",
        ".html",
        ".htm",
        ".md",
        ".markdown",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".txt",
        ".json",
        ".xml",
    )
    supports_visuals = (
        normalized_mime.startswith(("image/", "text/"))
        or normalized_mime
        in {
            "application/pdf",
            "application/epub+zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        or normalized_name.endswith(supported_extensions)
    )
    try:
        version = int(metadata.get("visual_index_version") or 0)
    except (TypeError, ValueError):
        version = 0
    return supports_visuals and version < CURRENT_SOURCE_VISUAL_INDEX_VERSION

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


BlockType = Literal[
    "heading",
    "paragraph",
    "formula",
    "table",
    "image",
    "note",
    "exercise",
    "dialogue",
]

PatchOperationType = Literal[
    "insert_block",
    "delete_block",
    "update_block_content",
    "replace_range_in_block",
    "move_block",
    "update_block_style",
    "attach_asset",
]

CourseEdgeType = Literal[
    "recommended_next",
    "prerequisite",
    "deep_dive",
    "alternate_path",
    "derived_from",
]
PublicationVisibility = Literal["private", "public"]
PublicationReviewStatus = Literal["not_started", "scanning", "approved", "blocked", "error"]

TeachingMode = Literal["definition", "intuition", "analogy", "example", "dialogue"]
BoardAction = Literal["no_change", "edit_board"]
SelectionKind = Literal["chat", "board", "source"]
BoardFocusSource = Literal["board", "chat"]
BoardFocusLocationStatus = Literal["missing", "selected", "resolved", "ambiguous"]
BoardSegmentKind = Literal["heading", "paragraph", "list", "table", "code", "image", "formula", "other"]
AgentActivityStage = Literal[
    "turn_decision",
    "resolve_target",
    "build_context",
    "execute_role",
    "verify",
    "persist_history",
    "final",
]
AgentActivityStatus = Literal["pending", "running", "completed", "blocked", "failed", "skipped"]
LessonMergeMode = Literal["manual", "ai"]
LessonMergeStatus = Literal[
    "draft",
    "ai_running",
    "ready",
    "stale",
    "committed",
    "abandoned",
    "failed",
]
LessonMergeConflictKind = Literal["board", "learning_requirement", "board_task", "source_reference"]
LessonMergeResolutionKind = Literal["unresolved", "target", "source", "both", "custom", "clear", "ai"]
InitialLearningWorkMode = Literal["knowledge_board", "narrow_topic", "practice_artifact", "unknown"]
LearningTeachingType = Literal["knowledge_point", "skill_practice"]
GuidedRequirementDiscoveryStrategy = Literal[
    "entry_point_discovery",
    "level_discovery",
    "goal_discovery",
    "mode_discovery",
    "bottleneck_discovery",
]
GuidedRequirementSelectionTarget = Literal[
    "learning_content",
    "current_level",
    "target_scenario",
    "teaching_type",
    "bottleneck",
]
InitialLearningGranularity = Literal[
    "single_knowledge_point",
    "source_chapter",
    "source_range",
    "broad_topic",
    "practice_artifact",
    "unclear",
]
ConversationRole = Literal["user", "assistant"]
RealtimeTranscriptRole = Literal["user", "assistant", "tool"]
AIProvider = Literal[
    "openai",
    "openai_codex",
    "anthropic",
    "google",
    "deepseek",
    "kimi",
    "minimax",
    "openai_compatible",
    "anthropic_compatible",
]
AIModelAccessMethod = Literal[
    "chatgpt_subscription",
    "personal_api",
    "platform_credits",
]
AIModelCapability = Literal["text", "realtime"]
AIRealtimeTransport = Literal["openai_webrtc", "gemini_live_websocket"]
ResourceScanStrategy = Literal["outline_only", "heading_section", "page_window", "fulltext_match"]
ResourcePageRole = Literal["cover", "copyright", "toc", "preface", "body", "appendix", "back_matter", "unknown"]
ChatInteractionMode = Literal["ask", "direct_edit"]
FormulaInkAction = Literal["reference", "replace"]
TeachingAction = Literal["continue", "restart"]
BoardGenerationAction = Literal["start"]
BoardWorkflow = Literal["generate_from_scratch", "act_on_existing_board", "unknown"]
LearningRequirementFactCategory = Literal["learning", "level", "vocabulary", "scenario", "output", "other"]
LearningRequirementRunStatus = Literal["collecting", "ready", "frozen", "consumed", "archived"]
LearningRequirementChangeKind = Literal[
    "created",
    "updated",
    "completed",
    "refinement_failed",
    "source_reference_confirmed",
    "source_reference_declined",
    "frozen",
    "forced_frozen",
    "consumed",
    "archived",
    "generation_failed",
]
LearningSourceConfirmationStatus = Literal["none", "confirmed", "skipped", "stale"]
BoardTaskRunStatus = Literal["collecting", "ready", "awaiting_confirmation", "consumed", "not_executed", "archived"]
BoardTaskChangeKind = Literal[
    "created",
    "updated",
    "ready",
    "awaiting_confirmation",
    "consumed",
    "not_executed",
    "archived",
    "execution_failed",
]
BoardTaskRequestedAction = Literal["write", "edit", "explain", "chat"]
BoardTaskConfirmationStatus = Literal["none", "awaiting", "confirmed", "declined"]
BoardTaskRoute = Literal["write", "edit", "explain", "chat", "clarify_location", "await_write_confirmation"]
BoardTaskLocationStatus = Literal["missing", "selected", "resolved", "ambiguous", "content_absent"]
BoardTaskLocationKind = Literal["target_range", "insertion_anchor", "unspecified"]
BoardDocumentOperationStatus = Literal["none", "succeeded", "failed"]
LearningRequirementOperationStatus = Literal["none", "succeeded", "failed"]
BoardPatchContentFormat = Literal["markdown", "plain_text"]
DocumentMarginPreset = Literal["narrow", "normal", "wide"]
DocumentOrientation = Literal["portrait", "landscape"]
DocumentPageSize = Literal["a4", "letter", "a3"]
DocumentBackgroundStyle = Literal["plain", "warm", "grid"]


class BlockStyle(BaseModel):
    font_family: str = "sans"
    font_size: Literal["sm", "md", "lg", "xl"] = "md"
    alignment: Literal["left", "center", "right"] = "left"
    emphasis: Literal["plain", "accent", "callout"] = "plain"
    width: Literal["normal", "wide", "full"] = "normal"


class BoardBlock(BaseModel):
    id: str = Field(default_factory=lambda: new_id("block"))
    type: BlockType
    title: str
    content: str
    style: BlockStyle = Field(default_factory=BlockStyle)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPageSettings(BaseModel):
    margin_preset: DocumentMarginPreset = "normal"
    orientation: DocumentOrientation = "portrait"
    page_size: DocumentPageSize = "a4"
    columns: Literal[1, 2] = 1
    page_border: bool = True
    background_style: DocumentBackgroundStyle = "plain"
    watermark_text: str = ""
    line_numbers: bool = False
    show_page_number: bool = False
    header_text: str = ""
    footer_text: str = ""


def _plain_text_to_tiptap_doc(text: str) -> dict[str, Any]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ]
    return {"type": "doc", "content": paragraphs or [{"type": "paragraph"}]}


def _legacy_blocks_to_rich_document(blocks: list[Any]) -> tuple[str, str]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for raw_block in blocks:
        block = raw_block.model_dump(mode="json") if hasattr(raw_block, "model_dump") else raw_block
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
        content = str(block.get("content") or "").strip()
        block_type = block.get("type")
        if title:
            tag = "h1" if block_type == "heading" else "h2"
            html_parts.append(f"<{tag}>{title}</{tag}>")
            text_parts.append(title)
        if content:
            paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
            for paragraph in paragraphs:
                html_parts.append(f"<p>{paragraph}</p>")
                text_parts.append(paragraph)
    return "\n".join(html_parts), "\n".join(text_parts)


class BoardDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("doc"))
    title: str
    content_json: dict[str, Any] = Field(default_factory=lambda: {"type": "doc", "content": [{"type": "paragraph"}]})
    content_html: str = ""
    content_text: str = ""
    page_settings: DocumentPageSettings = Field(default_factory=DocumentPageSettings)

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_blocks(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "blocks" not in value:
            return value
        if value.get("content_html") or value.get("content_text") or value.get("content_json"):
            return value
        html, text = _legacy_blocks_to_rich_document(value.get("blocks") or [])
        return {
            "id": value.get("id"),
            "title": value.get("title") or "Untitled document",
            "content_json": _plain_text_to_tiptap_doc(text),
            "content_html": html,
            "content_text": text,
        }


class PatchOperation(BaseModel):
    op: PatchOperationType
    block_id: str | None = None
    after_block_id: str | None = None
    node_path: list[int] = Field(default_factory=list)
    title: str | None = None
    content: str | None = None
    content_format: BoardPatchContentFormat = "markdown"
    block: BoardBlock | None = None
    search: str | None = None
    replacement: str | None = None
    expected_text: str | None = None
    expected_text_hash: str | None = None
    style: BlockStyle | None = None
    asset_url: str | None = None
    note: str | None = None

    @field_validator("node_path", mode="before")
    @classmethod
    def _coerce_nullable_node_path(cls, value: object) -> object:
        if value is None:
            return []
        return value


class BoardSegment(BaseModel):
    segment_id: str
    document_id: str
    kind: BoardSegmentKind
    heading_path: list[str] = Field(default_factory=list)
    order_index: int = 0
    text: str = ""
    html: str = ""
    text_hash: str = ""
    parent_id: str | None = None
    before_segment_id: str | None = None
    after_segment_id: str | None = None


class BoardChunk(BaseModel):
    chunk_id: str
    document_id: str
    source_segment_ids: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    order_start: int = 0
    order_end: int = 0
    text: str = ""
    text_hash: str = ""


class BoardSegmentIndex(BaseModel):
    document_id: str
    document_title: str = ""
    segments: list[BoardSegment] = Field(default_factory=list)
    chunks: list[BoardChunk] = Field(default_factory=list)


class DocumentSegmentSearchResult(BaseModel):
    package_id: str
    package_title: str
    lesson_id: str
    lesson_title: str
    document_id: str
    document_title: str
    segment_id: str
    kind: BoardSegmentKind
    heading_path: list[str] = Field(default_factory=list)
    order_index: int = 0
    text: str = ""
    text_hash: str = ""


class DocumentSegmentSearchResponse(BaseModel):
    query: str = ""
    kind: BoardSegmentKind | None = None
    results: list[DocumentSegmentSearchResult] = Field(default_factory=list)


class AgentActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("agentevt"))
    turn_id: str
    stage: AgentActivityStage
    label: str
    status: AgentActivityStatus = "completed"
    role: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class BoardFocusRef(BaseModel):
    source: BoardFocusSource = "board"
    lesson_id: str | None = None
    document_id: str | None = None
    segment_id: str | None = None
    kind: BoardSegmentKind | None = None
    heading_path: list[str] = Field(default_factory=list)
    excerpt: str = ""
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None
    excerpt_hash: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    display_label: str = ""
    match_id: str | None = None
    source_segment_ids: list[str] = Field(default_factory=list)
    order_start: int | None = None
    order_end: int | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class BoardExplanationDirective(BaseModel):
    status: Literal["approved", "needs_clarification", "blocked"] = "approved"
    target_summary: str = ""
    target_excerpt: str = ""
    board_feedback: str = ""
    teaching_instruction: str = ""
    constraints: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    reason: str = ""


class LearningSourceReference(BaseModel):
    evidence_bundle_id: str
    source_ingestion_id: str
    source_title: str = ""
    source_chapter_id: str = ""
    chapter_number: str = ""
    chapter_title: str = ""
    scope_kind: str = "section"
    scope_chapter_id: str = ""
    scope_chapter_number: str = ""
    scope_chapter_title: str = ""
    section_path: list[str] = Field(default_factory=list)
    source_locator: str = ""
    page_range: str = ""
    page_start: int | None = None
    page_end: int | None = None
    body_start_offset: int | None = None
    body_end_offset: int | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    visual_ids: list[str] = Field(default_factory=list)
    source_structure_id: str = ""
    source_structure_updated_at: str = ""
    content_hash: str = ""


class LearningSourceGrounding(BaseModel):
    requested_by_user: bool = False
    confirmation_status: LearningSourceConfirmationStatus = "none"
    confirmed_bundle_id: str = ""
    confirmed_at: str | None = None
    confirmed_references: list[LearningSourceReference] = Field(default_factory=list)
    # This snapshot is intentionally carried with the frozen requirement rather
    # than looked up again during board generation.  A source may be rebuilt or
    # removed after the learner has selected it; the board run must remain
    # reproducible from the material they explicitly chose.
    frozen_evidence: list["RetrievalEvidence"] = Field(default_factory=list)
    frozen_visual_evidence: list["SourceVisualEvidence"] = Field(default_factory=list)


class LearningRequirementAuxiliaryFactor(BaseModel):
    label: str
    value: str
    evidence: str = ""


class LearningRequirementSheet(BaseModel):
    teaching_type: LearningTeachingType | None = None
    learning_content: str = ""
    current_level: str = ""
    target_scenario: str = ""
    auxiliary_factors: list[LearningRequirementAuxiliaryFactor] = Field(default_factory=list)
    theme: str
    learning_goal: str
    level: str
    known_background: str
    current_questions: list[str]
    learning_need_checklist: list[str] = Field(default_factory=list)
    target_depth: str
    output_preference: str
    boundary: str
    board_scope: list[str]
    success_criteria: str
    risk_notes: list[str] = Field(default_factory=list)
    board_workflow: BoardWorkflow = "unknown"
    work_mode: InitialLearningWorkMode | None = None
    granularity: InitialLearningGranularity | None = None
    source_grounding: LearningSourceGrounding = Field(default_factory=LearningSourceGrounding)


class BoardTaskRequirementSheet(BaseModel):
    board_workflow: BoardWorkflow = "act_on_existing_board"
    location_kind: BoardTaskLocationKind = "unspecified"
    target_hint: str = ""
    target_location: BoardFocusRef | None = None
    location_status: BoardTaskLocationStatus = "missing"
    requested_action: BoardTaskRequestedAction | None = None
    question_or_topic: str = ""
    missing_items: list[str] = Field(default_factory=list)
    progress: int = Field(default=0, ge=0, le=100)
    confirmation_status: BoardTaskConfirmationStatus = "none"
    clarification_question: str = ""
    failure_count: int = Field(default=0, ge=0)


class LearningRequirementChecklistItem(BaseModel):
    title: str
    is_clear: bool = False
    evidence: str = ""


class LearningRequirementKeyFact(BaseModel):
    label: str
    value: str
    evidence: str = ""
    category: LearningRequirementFactCategory | None = None


class LearningClarificationStatus(BaseModel):
    progress: int = Field(ge=0, le=100)
    label: str
    reason: str
    missing_items: list[str] = Field(default_factory=list)
    can_start: bool = False
    forced_start: bool = False
    summary: str = ""
    key_facts: list[LearningRequirementKeyFact] = Field(default_factory=list)
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    next_question: str = ""
    ready_for_board: bool = False
    teaching_type: LearningTeachingType | None = None
    work_mode: InitialLearningWorkMode | None = None
    granularity: InitialLearningGranularity | None = None


class GuidedRequirementEntryPoint(BaseModel):
    title: str
    description: str
    answer_value: str = ""
    why_it_matters: str = ""
    best_for: str = ""


class GuidedRequirementDiscovery(BaseModel):
    strategy: GuidedRequirementDiscoveryStrategy = "entry_point_discovery"
    selection_target: GuidedRequirementSelectionTarget = "learning_content"
    question_title: str = ""
    learning_map_summary: str = ""
    entry_point_options: list[GuidedRequirementEntryPoint] = Field(
        default_factory=list,
        max_length=6,
    )
    recommended_entry_point: str = ""
    reason_for_recommendation: str = ""
    learner_profile_inference: str = ""

    def is_empty(self) -> bool:
        return not self.entry_point_options


class TeachingGuideMapping(BaseModel):
    block_id: str
    supports_goal: str
    teaching_mode: TeachingMode
    focus_points: list[str]
    optional_points: list[str] = Field(default_factory=list)
    difficult_points: list[str] = Field(default_factory=list)
    check_questions: list[str] = Field(default_factory=list)


class TeachingGuide(BaseModel):
    lesson_id: str
    summary: str
    structure_note: str
    pacing: str
    mappings: list[TeachingGuideMapping]
    strategy: str


class CommitRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("commit"))
    label: str
    message: str
    branch_name: str
    created_at: str = Field(default_factory=now_iso)
    parent_ids: list[str] = Field(default_factory=list)
    operations: list[PatchOperation] = Field(default_factory=list)
    snapshot: BoardDocument
    runtime_snapshot: "LessonRuntimeSnapshot | None" = Field(default=None, exclude=True)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchRef(BaseModel):
    name: str
    head_commit_id: str
    base_commit_id: str
    created_at: str = Field(default_factory=now_iso)


class PublicationReviewFinding(BaseModel):
    source_id: str
    source_title: str
    location: str = ""
    evidence_excerpt: str
    reason: str = ""


class PublicationReview(BaseModel):
    id: str = Field(default_factory=lambda: new_id("publicationreview"))
    status: PublicationReviewStatus = "not_started"
    agent_backend: str = ""
    provider: str = ""
    model: str = ""
    source_fingerprint: str = ""
    scanned_source_count: int = 0
    scanned_unit_count: int = 0
    findings: list[PublicationReviewFinding] = Field(default_factory=list)
    message: str = ""
    started_at: str | None = None
    completed_at: str | None = None


class LessonHistoryGraph(BaseModel):
    branches: dict[str, BranchRef]
    commits: list[CommitRecord]
    current_branch: str = "main"


class Lesson(BaseModel):
    id: str = Field(default_factory=lambda: new_id("lesson"))
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    visibility: PublicationVisibility = "private"
    publication_review: PublicationReview = Field(default_factory=PublicationReview)
    board_document: BoardDocument
    board_teaching_guide: BoardTeachingGuide | None = None
    board_teaching_progress: BoardTeachingProgress | None = None
    learning_requirements: LearningRequirementSheet | None = None
    board_task_requirements: BoardTaskRequirementSheet | None = None
    teaching_guide: TeachingGuide
    history_graph: LessonHistoryGraph
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class LessonRuntimeSnapshot(BaseModel):
    learning_requirements: LearningRequirementSheet | None = None
    board_task_requirements: BoardTaskRequirementSheet | None = None
    board_teaching_guide: "BoardTeachingGuide | None" = None
    board_teaching_progress: "BoardTeachingProgress | None" = None


class LessonMergeConflict(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mergeconflict"))
    kind: LessonMergeConflictKind
    path: str = ""
    title: str = ""
    base_value: Any = None
    target_value: Any = None
    source_value: Any = None
    resolution: LessonMergeResolutionKind = "unresolved"
    custom_value: Any = None
    resolved: bool = False


class MergeRuntimeDraft(BaseModel):
    learning_requirements: LearningRequirementSheet | None = None
    board_task_requirements: BoardTaskRequirementSheet | None = None
    board_teaching_restart_heading_path: list[str] = Field(default_factory=list)
    invalidated_teaching_state: bool = False


class LessonMergeSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mergesession"))
    owner_user_id: str = Field(exclude=True)
    lesson_id: str
    target_branch_name: str
    source_branch_name: str
    base_commit_id: str
    target_head_commit_id: str
    source_head_commit_id: str
    mode: LessonMergeMode = "manual"
    status: LessonMergeStatus = "draft"
    version: int = Field(default=1, ge=1)
    conflicts: list[LessonMergeConflict] = Field(default_factory=list)
    merge_blueprint: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    draft_document: BoardDocument
    draft_runtime: MergeRuntimeDraft = Field(default_factory=MergeRuntimeDraft)
    ai_model: AIModelSelection | None = None
    agent_activity: list[AgentActivityEvent] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    supersedes_session_id: str | None = None
    committed_commit_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class LessonMergeConflictView(BaseModel):
    id: str
    kind: LessonMergeConflictKind
    path: str = ""
    title: str = ""
    base_value: Any = None
    target_value: Any = None
    source_value: Any = None
    resolution: LessonMergeResolutionKind = "unresolved"
    custom_value: Any = None
    resolved: bool = False


class LessonMergeSessionView(BaseModel):
    id: str
    lesson_id: str
    target_branch_name: str
    source_branch_name: str
    base_commit_id: str
    target_head_commit_id: str
    source_head_commit_id: str
    mode: LessonMergeMode
    status: LessonMergeStatus
    version: int
    conflicts: list[LessonMergeConflictView] = Field(default_factory=list)
    draft_document: BoardDocument
    draft_runtime: MergeRuntimeDraft = Field(default_factory=MergeRuntimeDraft)
    ai_model: AIModelSelection | None = None
    agent_activity: list[AgentActivityEvent] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    supersedes_session_id: str | None = None
    committed_commit_id: str | None = None
    created_at: str
    updated_at: str


class CreateLessonMergeSessionRequest(BaseModel):
    source_branch_name: str = Field(min_length=1, max_length=128)
    mode: LessonMergeMode = "manual"
    text_model: AIModelSelection | None = None


class LessonMergeConflictResolution(BaseModel):
    conflict_id: str
    resolution: LessonMergeResolutionKind
    custom_value: Any = None


class UpdateLessonMergeSessionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    draft_document: BoardDocument | None = None
    draft_runtime: MergeRuntimeDraft | None = None
    resolutions: list[LessonMergeConflictResolution] = Field(default_factory=list)


class SubmitLessonMergeSessionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class AIProposalLessonMergeSessionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class RecomputeLessonMergeSessionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    mode: LessonMergeMode | None = None
    text_model: AIModelSelection | None = None


class CourseGraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: new_id("edge"))
    source_lesson_id: str
    target_lesson_id: str
    relationship: CourseEdgeType


class ResourcePageSection(BaseModel):
    role: ResourcePageRole = "unknown"
    page_idx_start: int | None = None
    page_idx_end: int | None = None
    page_no_start: int | None = None
    page_no_end: int | None = None
    title: str = ""
    confidence: float = 0.0
    evidence_excerpt: str = ""


class ResourcePageMapEntry(BaseModel):
    page_idx: int
    page_no: int
    role: ResourcePageRole = "unknown"
    printed_page: int | None = None
    body_offset: int | None = None
    confidence: float = 0.0
    evidence_excerpt: str = ""


class ResourcePageStructure(BaseModel):
    page_count: int = 0
    body_start_page_idx: int | None = None
    body_start_page_no: int | None = None
    toc_page_indices: list[int] = Field(default_factory=list)
    sections: list[ResourcePageSection] = Field(default_factory=list)
    page_map: list[ResourcePageMapEntry] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    confidence: float = 0.0


ResourceSourceType = Literal[
    "local_file",
    "web_url",
    "audio_file",
    "video_file",
    "video_url",
    "pasted_text",
    "transcript",
]
SourceIngestionStatus = Literal["queued", "fetching", "parsing", "indexing", "ready", "failed"]
EvidenceBundleStatus = Literal["candidate", "confirmed", "consumed", "archived"]
EvidencePurpose = Literal["chat", "board_generation", "board_edit", "board_explain", "board_chat"]
SourceStructureStatus = Literal["pending", "building", "ready", "linear_only", "failed"]
SourceStructureQualityLevel = Literal[
    "unassessed",
    "fully_verified",
    "partially_verified",
    "unverified",
    "search_only",
]
SourceTextReadiness = Literal["unknown", "ready", "sparse", "very_sparse", "empty"]
SourceStructureStrategy = Literal[
    "codex_directory_v1",
    "codex_catalog",
    "epub_navigation",
    "epub_heading",
    "pdf_outline",
    "pdf_toc",
    "pdf_merged_toc",
    "pdf_layout_toc",
    "docx_heading",
    "markdown_heading",
    "linear_text",
    "open_notebook_search_only",
]
SourceChapterAnchorStatus = Literal["verified", "unverified"]
SourceChapterMappingStatus = Literal["verified", "partial", "unverified", "unmapped"]
SourceRangeKind = Literal[
    "pdf_pages",
    "epub_spine",
    "docx_paragraphs",
    "ppt_slides",
    "sheet_rows",
    "text_lines",
    "dom_anchor",
    "structured_path",
]
SourceCatalogRunStatus = Literal["queued", "running", "succeeded", "failed"]
SourceVisualIndexStatus = Literal["pending", "ready", "partial", "failed", "unsupported"]
SourceVisualAnchorStatus = Literal["verified", "unverified"]
SourceVisualKind = Literal["image", "chart", "table", "diagram", "page_snapshot"]
SourceScopeKind = Literal["source", "chapter", "page_range"]
PostGenerationAction = Literal["auto_explain", "stop_after_generation"]
AutoTeachingOperationStatus = Literal["none", "succeeded", "failed"]


class SourceRange(BaseModel):
    """A format-native, inclusive range that can be validated before source reading."""

    kind: SourceRangeKind
    start: int | str | None = None
    end: int | str | None = None
    container: str = ""
    start_anchor: str = ""
    end_anchor: str = ""
    path: list[str] = Field(default_factory=list)
    display_label: str = ""
    end_inclusive: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "SourceRange":
        if not self.end_inclusive:
            raise ValueError("SourceRange.end must be inclusive.")
        if self.kind == "pdf_pages":
            if not isinstance(self.start, int) or isinstance(self.start, bool):
                raise ValueError("PDF source ranges require an integer start page.")
            if not isinstance(self.end, int) or isinstance(self.end, bool):
                raise ValueError("PDF source ranges require an integer end page.")
            if self.start < 1 or self.end < 1:
                raise ValueError("PDF source ranges use 1-based physical pages.")
            if self.end < self.start:
                raise ValueError("SourceRange.end must not precede SourceRange.start.")
        elif isinstance(self.start, int) and isinstance(self.end, int) and self.end < self.start:
            raise ValueError("SourceRange.end must not precede SourceRange.start.")
        return self


class SourceCatalogEvidence(BaseModel):
    method: str = ""
    source_locator: str = ""
    page_start: int | None = None
    page_end: int | None = None
    excerpt: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceStructureQuality(BaseModel):
    evaluator_version: int = 1
    level: SourceStructureQualityLevel = "unassessed"
    text_readiness: SourceTextReadiness = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    total_chapter_count: int = 0
    verified_chapter_count: int = 0
    unverified_chapter_count: int = 0
    demoted_chapter_count: int = 0
    verified_leaf_count: int = 0
    expected_leaf_count: int = 0
    verified_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    boundary_valid_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    body_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    independent_anchor_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    meaningful_characters_per_page: float = Field(default=0.0, ge=0.0)
    duplicate_locator_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_range_count: int = 0
    overlap_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    non_monotonic_count: int = 0
    oversized_leaf_count: int = 0
    diagnostics: list[str] = Field(default_factory=list)


class SourceIngestionJob(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ingest"))
    resource_id: str | None = None
    source_type: ResourceSourceType = "local_file"
    source_uri: str | None = None
    adapter: str = ""
    status: SourceIngestionStatus = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    error: str = ""
    phase_history: list[str] = Field(default_factory=list)
    agent_activity: list[AgentActivityEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class LibraryChapter(BaseModel):
    id: str = Field(default_factory=lambda: new_id("chapter"))
    title: str
    level: int = 1
    page_range: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    summary: str
    keywords: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    parent_title: str | None = None
    path: list[str] = Field(default_factory=list)
    locator_hint: str | None = None
    order_index: int = 0
    scan_strategy: ResourceScanStrategy = "outline_only"
    body_start_order: int | None = None
    body_end_order: int | None = None
    body_page_start: int | None = None
    body_page_end: int | None = None
    body_match_status: str = ""
    body_match_confidence: float = 0.0
    body_match_reason: str = ""


class ResourceSourceUnit(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sourceunit"))
    content_type: str = "text"
    text: str = ""
    page_idx: int | None = None
    page_no: int | None = None
    source_locator: str | None = None
    url: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    paragraph_index: int | None = None
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    asset_path: str | None = None
    bbox: list[float] = Field(default_factory=list)
    order_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceLibraryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("resource"))
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str = Field(default_factory=now_iso)
    scope_lesson_id: str | None = None
    outline: list[LibraryChapter] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False
    text_content: str | None = None
    source_path: str | None = None
    source_type: ResourceSourceType = "local_file"
    source_uri: str | None = None
    ingestion_status: SourceIngestionStatus = "ready"
    ingestion_error: str = ""
    ingestion_progress: int = Field(default=100, ge=0, le=100)
    ingestion_adapter: str = ""
    ingestion_job: SourceIngestionJob | None = None
    parser_provider: str = "native"
    parser_artifacts_path: str | None = None
    parser_message: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    source_units: list[ResourceSourceUnit] = Field(default_factory=list)
    page_structure: ResourcePageStructure | None = None


class SourceIngestionRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("source"))
    owner_user_id: str = ""
    package_id: str
    title: str
    source_type: ResourceSourceType = "local_file"
    source_uri: str | None = None
    file_name: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    status: SourceIngestionStatus = "queued"
    error: str = ""
    open_notebook_notebook_id: str = ""
    open_notebook_source_id: str = ""
    open_notebook_command_id: str = ""
    structure_status: SourceStructureStatus = "pending"
    structure_strategy: SourceStructureStrategy | None = None
    structure_has_verified_toc: bool = False
    structure_quality: SourceStructureQuality = Field(default_factory=SourceStructureQuality)
    structure_error: str = ""
    structure_updated_at: str | None = None
    ingestion_job: SourceIngestionJob | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceUrlImportRequest(BaseModel):
    source_uri: str
    title: str = ""


class RetrievalEvidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evidence"))
    source_ingestion_id: str = ""
    open_notebook_source_id: str = ""
    source_title: str = ""
    source_uri: str | None = None
    chapter_id: str = ""
    section_path: list[str] = Field(default_factory=list)
    page_range: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""
    expanded_text: str = ""
    relevance_score: float = 0.0
    reason: str = ""
    token_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceStructure(BaseModel):
    id: str = Field(default_factory=lambda: new_id("structure"))
    owner_user_id: str = ""
    package_id: str
    source_ingestion_id: str
    status: SourceStructureStatus = "pending"
    strategy: SourceStructureStrategy = "linear_text"
    has_verified_toc: bool = False
    quality: SourceStructureQuality = Field(default_factory=SourceStructureQuality)
    chapter_count: int = 0
    chunk_count: int = 0
    visual_count: int = 0
    visual_index_status: SourceVisualIndexStatus = "pending"
    visual_index_version: int = 0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    catalog_version: int = Field(default=0, ge=0)
    catalog_updated_at: str | None = None
    source_content_hash: str = ""
    catalog_schema_version: str = "legacy"
    catalog_model: str = ""
    error: str = ""
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceChapter(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sourcechapter"))
    owner_user_id: str = ""
    package_id: str
    source_ingestion_id: str
    parent_id: str | None = None
    number: str = ""
    normalized_number: str = ""
    title: str
    level: int = 1
    path: list[str] = Field(default_factory=list)
    order_index: int = 0
    source_locator: str = ""
    body_start_offset: int | None = None
    body_end_offset: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    anchor_status: SourceChapterAnchorStatus = "unverified"
    range: SourceRange | None = None
    mapping_status: SourceChapterMappingStatus = "unverified"
    source_content_hash: str = ""
    catalog_evidence: list[SourceCatalogEvidence] = Field(default_factory=list)
    catalog_version: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    excerpt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCatalogRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("catalogrun"))
    owner_user_id: str = ""
    package_id: str
    source_ingestion_id: str
    status: SourceCatalogRunStatus = "queued"
    catalog_version: int = Field(default=0, ge=0)
    model: str = ""
    turn_count: int = Field(default=0, ge=0)
    page_count: int = Field(default=0, ge=0)
    inspected_page_count: int = Field(default=0, ge=0)
    ocr_page_count: int = Field(default=0, ge=0)
    chapter_count: int = Field(default=0, ge=0)
    verified_chapter_count: int = Field(default=0, ge=0)
    verification_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    duration_ms: int = Field(default=0, ge=0)
    stage_history: list[str] = Field(default_factory=list)
    error: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceChunk(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sourcechunk"))
    owner_user_id: str = ""
    package_id: str
    source_ingestion_id: str
    chapter_id: str | None = None
    order_index: int = 0
    source_locator: str = ""
    text: str = ""
    start_offset: int = 0
    end_offset: int = 0
    page_start: int | None = None
    page_end: int | None = None
    token_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceVisualAsset(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sourcevisual"))
    owner_user_id: str = ""
    package_id: str
    source_ingestion_id: str
    structure_id: str = ""
    structure_version: int = 0
    chapter_id: str | None = None
    kind: SourceVisualKind = "image"
    source_locator: str = ""
    page_start: int | None = None
    page_end: int | None = None
    paragraph_index: int | None = None
    slide_no: int | None = None
    sheet_name: str = ""
    bbox: list[float] = Field(default_factory=list)
    before_chunk_id: str | None = None
    after_chunk_id: str | None = None
    caption: str = ""
    extracted_text: str = ""
    surrounding_text: str = ""
    anchor_status: SourceVisualAnchorStatus = "unverified"
    mime_type: str = ""
    asset_path: str = Field(default="", exclude=True)
    storage_key: str = Field(default="", exclude=True)
    order_index: int = 0
    content_hash: str = ""
    position_hash: str = ""
    width: int | None = None
    height: int | None = None
    table_data: list[list[str]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceVisualEvidence(BaseModel):
    visual_id: str
    package_id: str = ""
    source_ingestion_id: str
    source_chapter_id: str = ""
    kind: SourceVisualKind = "image"
    source_locator: str = ""
    page_start: int | None = None
    page_end: int | None = None
    paragraph_index: int | None = None
    slide_no: int | None = None
    sheet_name: str = ""
    bbox: list[float] = Field(default_factory=list)
    before_chunk_id: str | None = None
    after_chunk_id: str | None = None
    caption: str = ""
    extracted_text: str = ""
    surrounding_text: str = ""
    anchor_status: SourceVisualAnchorStatus = "unverified"
    mime_type: str = ""
    order_index: int = 0
    content_hash: str = ""
    position_hash: str = ""
    width: int | None = None
    height: int | None = None
    table_data: list[list[str]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceStructureView(BaseModel):
    source: SourceIngestionRecord
    structure: SourceStructure | None = None
    chapters: list[SourceChapter] = Field(default_factory=list)
    chunks: list[SourceChunk] = Field(default_factory=list)
    visuals: list[SourceVisualAsset] = Field(default_factory=list)


class SourceCatalogSourceSummary(BaseModel):
    id: str
    title: str
    file_name: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    status: SourceIngestionStatus = "queued"
    structure_status: SourceStructureStatus = "pending"


class SourceCatalogView(BaseModel):
    source: SourceCatalogSourceSummary
    structure_id: str | None = None
    status: SourceStructureStatus = "pending"
    strategy: SourceStructureStrategy | None = None
    has_verified_toc: bool = False
    catalog_version: int = Field(default=0, ge=0)
    catalog_updated_at: str | None = None
    source_content_hash: str = ""
    catalog_schema_version: str = "legacy"
    catalog_model: str = ""
    task_contract: str = ""
    chapter_count: int = Field(default=0, ge=0)
    verified_chapter_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quality: SourceStructureQuality = Field(default_factory=SourceStructureQuality)
    error: str = ""
    warnings: list[str] = Field(default_factory=list)
    chapters: list[SourceChapter] = Field(default_factory=list)


class SourceCatalogBatchView(BaseModel):
    package_id: str
    catalogs: list[SourceCatalogView] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    id: str = Field(default_factory=lambda: new_id("bundle"))
    owner_user_id: str = ""
    package_id: str
    lesson_id: str | None = None
    requirement_run_id: str | None = None
    board_task_run_id: str | None = None
    purpose: EvidencePurpose = "chat"
    status: EvidenceBundleStatus = "candidate"
    query: str = ""
    evidence_items: list[RetrievalEvidence] = Field(default_factory=list)
    visual_items: list[SourceVisualEvidence] = Field(default_factory=list)
    context_text: str = ""
    token_count: int = 0
    confirmed_by_user: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    confirmed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceLibraryItemView(BaseModel):
    id: str
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str
    scope_lesson_id: str | None = None
    outline: list[LibraryChapter] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False
    source_type: ResourceSourceType = "local_file"
    source_uri: str | None = None
    ingestion_status: SourceIngestionStatus = "ready"
    ingestion_error: str = ""
    ingestion_progress: int = Field(default=100, ge=0, le=100)
    ingestion_adapter: str = ""
    ingestion_job: SourceIngestionJob | None = None
    parser_provider: str = "native"
    parser_artifacts_path: str | None = None
    parser_message: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    source_units: list[ResourceSourceUnit] = Field(default_factory=list)
    page_structure: ResourcePageStructure | None = None


class CoursePackage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("course"))
    title: str
    summary: str
    visibility: PublicationVisibility = "private"
    publication_review: PublicationReview = Field(default_factory=PublicationReview)
    lessons: list[Lesson]
    course_graph: list[CourseGraphEdge] = Field(default_factory=list)
    resources: list[ResourceLibraryItem] = Field(default_factory=list)
    open_lesson_ids: list[str] = Field(default_factory=list)
    active_lesson_id: str | None = None
    workspace_tab_order: list[str] = Field(default_factory=list)


class WorkspaceState(BaseModel):
    packages: list[CoursePackage] = Field(default_factory=list)
    active_package_id: str | None = None

    @model_validator(mode="after")
    def ensure_active_package(self) -> "WorkspaceState":
        if self.active_package_id and any(package.id == self.active_package_id for package in self.packages):
            return self
        self.active_package_id = self.packages[0].id if self.packages else None
        return self


class SelectionRef(BaseModel):
    kind: SelectionKind
    excerpt: str
    location_kind: BoardTaskLocationKind | None = None
    lesson_id: str | None = None
    block_id: str | None = None
    document_id: str | None = None
    segment_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None
    source_ingestion_id: str | None = None
    source_title: str = ""
    source_uri: str | None = None
    source_chapter_id: str | None = None
    source_chapter_number: str = ""
    source_chapter_title: str = ""
    source_page_range: str = ""
    source_locator: str = ""
    source_page_start: int | None = None
    source_page_end: int | None = None
    source_range: SourceRange | None = None
    catalog_version: int | None = Field(default=None, ge=0)
    source_content_hash: str | None = None
    source_scope_kind: SourceScopeKind = "chapter"


class FormulaInkPayload(BaseModel):
    image_data_url: str
    source_latex: str | None = None
    action: FormulaInkAction


class ChatAttachmentRef(BaseModel):
    source_ingestion_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    size_bytes: int = Field(default=0, ge=0)
    kind: Literal["image", "file"] = "file"
    status: SourceIngestionStatus = "queued"


class ConversationTurn(BaseModel):
    role: ConversationRole
    content: str


class AIReasoningEffortOption(BaseModel):
    reasoning_effort: str = Field(min_length=1)
    description: str = ""


class AIServiceTierOption(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""


class AIAgentBackendOption(BaseModel):
    id: Literal["codex", "pi"]
    label: str
    description: str = ""
    enabled: bool = False


class AIModelSelection(BaseModel):
    # Retain the legacy value for request and stored-metadata compatibility.
    # Server-side runtime routing normalizes every text task to Pi.
    agent_backend: Literal["codex", "pi"] = "pi"
    provider: AIProvider
    model: str
    access_method: AIModelAccessMethod | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None

    @model_validator(mode="after")
    def validate_access_method(self) -> "AIModelSelection":
        if self.access_method is None:
            return self
        supported_access_methods: dict[
            AIProvider,
            set[AIModelAccessMethod],
        ] = {
            "openai_codex": {"chatgpt_subscription"},
            "openai": {"platform_credits"},
            "deepseek": {"personal_api", "platform_credits"},
        }
        allowed = supported_access_methods.get(self.provider)
        if allowed is not None and self.access_method not in allowed:
            raise ValueError(
                f"Access method {self.access_method!r} is not available "
                f"for provider {self.provider!r}"
            )
        return self


class AIModelOption(BaseModel):
    provider: AIProvider
    model: str
    access_method: AIModelAccessMethod
    label: str
    capability: AIModelCapability
    enabled: bool = False
    configured: bool = False
    default: bool = False
    transport: AIRealtimeTransport | None = None
    default_reasoning_effort: str | None = None
    supported_reasoning_efforts: list[AIReasoningEffortOption] = Field(
        default_factory=list
    )
    default_service_tier: str | None = None
    service_tiers: list[AIServiceTierOption] = Field(default_factory=list)
    input_price_credits_per_million: int | None = Field(default=None, ge=0)


class AIModelCatalog(BaseModel):
    text: list[AIModelOption] = Field(default_factory=list)
    realtime: list[AIModelOption] = Field(default_factory=list)
    defaults: dict[AIModelCapability, AIModelSelection]
    agent_backends: dict[
        Literal["teaching", "source"],
        list[AIAgentBackendOption],
    ] = Field(default_factory=dict)


class AIProviderCredentialInput(BaseModel):
    api_key: SecretStr


class AIProviderCredentialStatus(BaseModel):
    provider: AIProvider
    label: str
    configured: bool = False
    manageable: bool = False


class CodexAccountView(BaseModel):
    type: str | None = None
    email: str | None = None
    plan_type: str | None = None


class CodexProviderStatus(BaseModel):
    enabled: bool
    available: bool
    configured: bool
    account: CodexAccountView | None = None
    rate_limits: dict[str, Any] | None = None
    message: str = ""


class CodexLoginStartResponse(BaseModel):
    login_id: str
    verification_url: str
    user_code: str
    expires_at: str | None = None


class CodexLoginStatusResponse(BaseModel):
    login_id: str
    status: Literal["pending", "succeeded", "failed", "cancelled", "expired"]
    error: str | None = None
    account: CodexAccountView | None = None


class BoardDecision(BaseModel):
    action: BoardAction
    reason: str


# Reserved AI teaching workflow schema.
# Current public routes preserve these fields for stored lesson compatibility,
# but realtime teaching execution is disabled and the next orchestration layer
# should decide whether these models still match the product workflow.
class BoardTeachingSelectedItem(BaseModel):
    excerpt: str
    source_heading: str | None = None
    reason: str
    mapped_needs: list[str] = Field(default_factory=list)
    teaching_role: str = "main_idea"
    order_index: int = 0


class BoardNeedMapping(BaseModel):
    need: str
    matched_excerpt: str
    source_heading: str | None = None
    rationale: str


class BoardSectionTeachingPlan(BaseModel):
    order_index: int = 0
    heading: str
    heading_level: int = Field(default=0, ge=0, le=12)
    heading_path: list[str] = Field(default_factory=list)
    parent_heading: str = ""
    heading_order_index: int = Field(default=0, ge=0)
    line_start: int = Field(default=0, ge=0)
    line_end: int = Field(default=0, ge=0)
    has_child_headings: bool = False
    board_excerpt: str = ""
    core_points: list[str] = Field(default_factory=list)
    teaching_steps: list[str] = Field(default_factory=list)
    teaching_method: str = ""
    example_or_analogy: str = ""
    common_pitfalls: list[str] = Field(default_factory=list)
    check_question: str = ""
    transition_to_next: str = ""


class BoardTeachingGuide(BaseModel):
    board_document_id: str = ""
    board_snapshot_hash: str = ""
    board_title: str = ""
    selected_items: list[BoardTeachingSelectedItem] = Field(default_factory=list)
    need_mappings: list[BoardNeedMapping] = Field(default_factory=list)
    teaching_flow: list[str] = Field(default_factory=list)
    generation_rationale: str = ""
    chatbot_brief: str = ""
    lecture_handout: str = ""
    section_plans: list[BoardSectionTeachingPlan] = Field(default_factory=list)
    target_heading: str = ""
    target_heading_path: list[str] = Field(default_factory=list)
    sequence_mode: str = "heading_tree_preorder"


class BoardTeachingProgress(BaseModel):
    board_document_id: str = ""
    board_snapshot_hash: str = ""
    current_section_index: int = 0
    completed_section_indexes: list[int] = Field(default_factory=list)
    waiting_for_continue: bool = False
    target_heading_path: list[str] = Field(default_factory=list)
    current_heading_path: list[str] = Field(default_factory=list)


class SectionTeachingProgressView(BaseModel):
    section_index: int = 0
    section_count: int = 0
    current_section_title: str = ""
    has_next_section: bool = False
    waiting_for_continue: bool = False
    target_heading_path: list[str] = Field(default_factory=list)
    current_heading_path: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    text_model: AIModelSelection | None = None
    selection: SelectionRef | None = None
    selections: list[SelectionRef] = Field(default_factory=list, max_length=8)
    formula_ink: FormulaInkPayload | None = None
    attachments: list[ChatAttachmentRef] = Field(default_factory=list, max_length=10)
    interaction_mode: ChatInteractionMode = "ask"
    board_generation_action: BoardGenerationAction | None = None
    teaching_action: TeachingAction | None = None
    post_generation_action: PostGenerationAction = "auto_explain"
    chat_edit_source_commit_id: str | None = None
    chat_edit_base_commit_id: str | None = None
    chat_edit_original_message: str | None = None
    conversation: list[ConversationTurn] = Field(default_factory=list)


class LessonView(BaseModel):
    id: str
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    visibility: PublicationVisibility = "private"
    publication_review: PublicationReview = Field(default_factory=PublicationReview)
    board_document: BoardDocument
    learning_requirements: LearningRequirementSheet | None = None
    board_task_requirements: BoardTaskRequirementSheet | None = None
    history_graph: LessonHistoryGraph
    created_at: str
    updated_at: str


class CoursePackageView(BaseModel):
    id: str
    title: str
    summary: str
    visibility: PublicationVisibility = "private"
    publication_review: PublicationReview = Field(default_factory=PublicationReview)
    is_standalone: bool = False
    lessons: list[LessonView]
    course_graph: list[CourseGraphEdge] = Field(default_factory=list)
    resources: list[ResourceLibraryItemView] = Field(default_factory=list)
    open_lesson_ids: list[str] = Field(default_factory=list)
    active_lesson_id: str | None = None
    workspace_tab_order: list[str] = Field(default_factory=list)


class PublicLessonView(BaseModel):
    id: str
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    board_document: BoardDocument
    updated_at: str


class PublicCoursePackageView(BaseModel):
    id: str
    title: str
    summary: str
    lessons: list[PublicLessonView] = Field(default_factory=list)


class WorkspaceStateView(BaseModel):
    packages: list[CoursePackageView] = Field(default_factory=list)
    active_package_id: str | None = None


class AuthIdentityView(BaseModel):
    provider: str
    provider_label: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    created_at: str
    last_login_at: str | None = None


class UserView(BaseModel):
    id: str
    email: str
    phone: str | None = None
    role: Literal["user", "admin", "guest"]
    display_name: str | None = None
    avatar_url: str | None = None
    created_at: str
    last_login_at: str | None = None
    auth_identities: list[AuthIdentityView] = Field(default_factory=list)


CommunityPostType = Literal["question", "discussion", "resource", "study_note"]
CommunityFeedSort = Literal["recent", "hot", "unanswered"]
CommunityProvider = Literal["answer"]


class CommunityIntegrationView(BaseModel):
    provider: CommunityProvider
    public_url: str | None = None
    entry_url: str
    available: bool
    sso_enabled: bool
    setup_required: bool


class CommunitySpaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    slug: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=500)

    @field_validator("name", "slug", "description")
    @classmethod
    def strip_space_fields(cls, value: str) -> str:
        return value.strip()


class CommunitySpaceView(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    creator_user_id: str
    created_at: str
    updated_at: str
    post_count: int = 0
    follower_count: int = 0


class CommunityPostCreate(BaseModel):
    community_slug: str = Field(min_length=1, max_length=80)
    post_type: CommunityPostType = "discussion"
    title: str = Field(min_length=4, max_length=180)
    body: str = Field(min_length=1, max_length=40_000)
    tags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("community_slug", "title", "body")
    @classmethod
    def strip_post_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw_tag in tags:
            tag = raw_tag.strip().lstrip("#").strip()
            if not tag:
                continue
            if len(tag) > 40:
                raise ValueError("标签不能超过 40 个字符")
            key = tag.casefold()
            if key not in seen:
                result.append(tag)
                seen.add(key)
        return result


class CommunityPostUpdate(BaseModel):
    title: str = Field(min_length=4, max_length=180)
    body: str = Field(min_length=1, max_length=40_000)
    tags: list[str] = Field(default_factory=list, max_length=8)

    _strip_fields = field_validator("title", "body")(CommunityPostCreate.strip_post_fields.__func__)
    _clean_tags = field_validator("tags")(CommunityPostCreate.clean_tags.__func__)


class CommunityPostView(BaseModel):
    id: str
    community_id: str
    community_slug: str
    community_name: str
    author_user_id: str
    author_display_name: str
    post_type: CommunityPostType
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    vote_score: int = 0
    comment_count: int = 0
    answer_count: int = 0
    accepted_answer_id: str | None = None
    viewer_vote: int = 0
    created_at: str
    updated_at: str


class CommunityCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    parent_comment_id: str | None = None

    @field_validator("body")
    @classmethod
    def strip_comment_body(cls, value: str) -> str:
        return value.strip()


class CommunityContentUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=40_000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        return value.strip()


class CommunityCommentView(BaseModel):
    id: str
    post_id: str
    parent_comment_id: str | None = None
    author_user_id: str
    author_display_name: str
    body: str
    created_at: str
    updated_at: str


class CommunityAnswerCreate(BaseModel):
    body: str = Field(min_length=1, max_length=40_000)

    @field_validator("body")
    @classmethod
    def strip_answer_body(cls, value: str) -> str:
        return value.strip()


class CommunityAnswerView(BaseModel):
    id: str
    post_id: str
    author_user_id: str
    author_display_name: str
    body: str
    vote_score: int = 0
    viewer_vote: int = 0
    is_accepted: bool = False
    author_reputation: int = 0
    created_at: str
    updated_at: str


class CommunityPostDetail(BaseModel):
    post: CommunityPostView
    answers: list[CommunityAnswerView] = Field(default_factory=list)
    comments: list[CommunityCommentView] = Field(default_factory=list)


class CommunityVoteRequest(BaseModel):
    value: Literal[-1, 0, 1]


class CommunityVoteView(BaseModel):
    post_id: str
    viewer_vote: int
    vote_score: int


class CommunityAnswerVoteView(BaseModel):
    answer_id: str
    viewer_vote: int
    vote_score: int


class CommunityAcceptedAnswerRequest(BaseModel):
    answer_id: str | None = None


class CommunityAcceptedAnswerView(BaseModel):
    post_id: str
    accepted_answer_id: str | None = None


class CommunityFollowView(BaseModel):
    community_id: str
    following: bool
    follower_count: int


class AuthRequest(BaseModel):
    identifier: str | None = None
    email: str | None = None
    phone: str | None = None
    guest_token: str | None = None
    password: str = Field(min_length=8, max_length=256)

    def account_identifier(self) -> str:
        return self.identifier or self.email or self.phone or ""


class AuthSessionResponse(BaseModel):
    token: str
    user: UserView


class EmailCodeRequest(BaseModel):
    email: str


class EmailCodeRequestResponse(BaseModel):
    challenge_id: str
    expires_in_seconds: int
    message: str


class EmailCodeVerifyRequest(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    guest_token: str | None = None


class EmailRegistrationRequest(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)
    challenge_id: str
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    guest_token: str | None = None

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip()
        if len(username) < 2:
            raise ValueError("用户名至少需要 2 个字符")
        return username

    @model_validator(mode="after")
    def passwords_must_match(self) -> EmailRegistrationRequest:
        if self.password != self.password_confirmation:
            raise ValueError("两次输入的密码不一致")
        return self


class AuthProviderView(BaseModel):
    id: str
    label: str
    description: str
    configured: bool
    kind: Literal["password", "oauth", "device"] = "oauth"


class AdminStats(BaseModel):
    users: int
    admins: int
    packages: int
    lessons: int
    resources: int


class AdminOverview(BaseModel):
    stats: AdminStats
    users: list[UserView] = Field(default_factory=list)


class ChatResponse(BaseModel):
    chatbot_message: str
    follow_up_suggestions: list[str] = Field(default_factory=list, max_length=4)
    agent_activity: list[AgentActivityEvent] = Field(default_factory=list)
    learning_requirement_sheet: LearningRequirementSheet
    active_requirement_sheet: LearningRequirementSheet | None = None
    learning_clarification: LearningClarificationStatus
    requirement_run_id: str | None = None
    requirement_version_id: str | None = None
    requirement_phase: LearningRequirementRunStatus | None = None
    learning_requirement_operation_status: LearningRequirementOperationStatus = "none"
    learning_requirement_operation_failure_reason: str | None = None
    board_task_sheet: BoardTaskRequirementSheet | None = None
    active_board_task_sheet: BoardTaskRequirementSheet | None = None
    board_task_run_id: str | None = None
    board_task_version_id: str | None = None
    board_task_phase: BoardTaskRunStatus | None = None
    board_task_questions: list[str] = Field(default_factory=list)
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    guided_requirement_discovery: GuidedRequirementDiscovery | None = None
    requirement_cleared: bool = False
    board_document_operation_status: BoardDocumentOperationStatus = "none"
    board_document_operation_failure_reason: str | None = None
    teaching_progress: SectionTeachingProgressView | None = None
    auto_teaching_operation_status: AutoTeachingOperationStatus = "none"
    auto_teaching_operation_failure_reason: str | None = None
    course_package: CoursePackageView


class RequirementUpdateStreamPayload(BaseModel):
    learning_requirement_sheet: LearningRequirementSheet
    active_requirement_sheet: LearningRequirementSheet | None = None
    learning_clarification: LearningClarificationStatus
    requirement_run_id: str | None = None
    requirement_version_id: str | None = None
    requirement_phase: LearningRequirementRunStatus | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    guided_requirement_discovery: GuidedRequirementDiscovery | None = None


class BoardTaskUpdateStreamPayload(BaseModel):
    board_task_sheet: BoardTaskRequirementSheet
    active_board_task_sheet: BoardTaskRequirementSheet | None = None
    board_task_run_id: str | None = None
    board_task_version_id: str | None = None
    board_task_phase: BoardTaskRunStatus | None = None
    board_task_questions: list[str] = Field(default_factory=list)


class CreatePackageRequest(BaseModel):
    title: str
    summary: str = ""


class UpdatePackageRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    visibility: PublicationVisibility | None = None


class UpdateLessonRequest(BaseModel):
    title: str


class UpdateVisibilityRequest(BaseModel):
    visibility: PublicationVisibility


class MoveLessonRequest(BaseModel):
    target_package_id: str


class BatchLessonActionRequest(BaseModel):
    action: Literal["move", "delete"]
    lesson_ids: list[str] = Field(default_factory=list)
    target_package_id: str | None = None


class GenerateLessonRequest(BaseModel):
    topic: str | None = None
    timezone: str | None = None
    branch_from_lesson_id: str | None = None
    target_package_id: str | None = None
    start_blank: bool = False


class ManualCommitRequest(BaseModel):
    document: BoardDocument | None = None
    label: str = "Manual document edit"
    message: str = "Saved rich document changes from the editor"


class DocumentSaveRequest(BaseModel):
    document: BoardDocument
    label: str = "Manual document edit"
    message: str = "Saved rich document changes from the editor"
    metadata: dict[str, Any] = Field(default_factory=dict)
    base_commit_id: str | None = None


class DocumentAIEditRequest(BaseModel):
    instruction: str
    selection_text: str | None = None
    replace_whole: bool = False
    conversation: list[ConversationTurn] = Field(default_factory=list)


class CreateBranchRequest(BaseModel):
    name: str
    from_commit_id: str | None = None


class SwitchBranchRequest(BaseModel):
    name: str


class RestoreCommitRequest(BaseModel):
    commit_id: str
    label: str = "Restore snapshot"


class ReorderTabsRequest(BaseModel):
    ordered_lesson_ids: list[str]
    active_lesson_id: str | None = None


class RealtimeConnectRequest(BaseModel):
    offer_sdp: str
    latest_assistant_message: str | None = None
    client_session_id: str | None = None
    realtime_model: AIModelSelection | None = None
    selection: SelectionRef | None = None


class RealtimeConnectResponse(BaseModel):
    answer_sdp: str
    provider: str = "openai"
    model: str
    voice: str
    call_id: str | None = None
    tools_enabled: bool = False
    client_session_id: str | None = None


class GoogleRealtimeSessionRequest(BaseModel):
    latest_assistant_message: str | None = None
    client_session_id: str | None = None
    realtime_model: AIModelSelection | None = None


class GoogleRealtimeSessionResponse(BaseModel):
    websocket_url: str
    setup: dict[str, object]
    provider: str = "google"
    model: str
    voice: str


class RealtimeTranscriptLogRequest(BaseModel):
    client_event_id: str | None = Field(default=None, min_length=1, max_length=200)
    client_session_id: str | None = None
    turn_id: str | None = Field(default=None, max_length=200)
    occurred_at: datetime | None = None
    lesson_title: str | None = None
    role: RealtimeTranscriptRole
    transport_event_type: str
    transcript: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_status: str | None = None


RealtimeToolName = Literal["read_board_context", "run_chatbot_workflow"]


class RealtimeToolCallRequest(BaseModel):
    client_session_id: str = Field(min_length=1, max_length=160)
    turn_id: str | None = Field(default=None, max_length=200)
    call_id: str = Field(min_length=1, max_length=160)
    name: RealtimeToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    selection: SelectionRef | None = None


class RealtimeToolCallResponse(BaseModel):
    status: Literal["ok", "error"]
    model_output: dict[str, Any] = Field(default_factory=dict)
    resolved_focus: BoardFocusRef | None = None
    course_package: CoursePackageView | None = None

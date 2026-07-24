export type BoardAction = "no_change" | "edit_board";
export type ChatInteractionMode = "ask" | "direct_edit";
export type AIProvider =
  | "openai"
  | "openai_codex"
  | "anthropic"
  | "google"
  | "deepseek"
  | "kimi"
  | "minimax"
  | "openai_compatible"
  | "anthropic_compatible";
export type AIModelCapability = "text" | "realtime";
export type AIRealtimeTransport = "openai_webrtc" | "gemini_live_websocket";
export type DocumentMarginPreset = "narrow" | "normal" | "wide";
export type DocumentOrientation = "portrait" | "landscape";
export type DocumentPageSize = "a4" | "letter" | "a3";
export type DocumentBackgroundStyle = "plain" | "warm" | "grid";

export interface DocumentPageSettings {
  margin_preset: DocumentMarginPreset;
  orientation: DocumentOrientation;
  page_size: DocumentPageSize;
  columns: 1 | 2;
  page_border: boolean;
  background_style: DocumentBackgroundStyle;
  watermark_text: string;
  line_numbers: boolean;
  show_page_number: boolean;
  header_text: string;
  footer_text: string;
}

export interface BoardDocument {
  id: string;
  title: string;
  content_json: Record<string, unknown>;
  content_html: string;
  content_text: string;
  page_settings: DocumentPageSettings;
}

export type LearningSourceConfirmationStatus = "none" | "confirmed" | "skipped" | "stale";

export interface LearningSourceReference {
  evidence_bundle_id: string;
  source_ingestion_id: string;
  source_title: string;
  source_chapter_id: string;
  chapter_number: string;
  chapter_title: string;
  scope_kind: "chapter" | "page_range";
  scope_chapter_id: string;
  scope_chapter_number: string;
  scope_chapter_title: string;
  section_path: string[];
  source_locator: string;
  page_range: string;
  page_start?: number | null;
  page_end?: number | null;
  body_start_offset?: number | null;
  body_end_offset?: number | null;
  chunk_ids: string[];
  visual_ids: string[];
  source_structure_id: string;
  source_structure_updated_at: string;
  content_hash: string;
}

export interface LearningSourceGrounding {
  requested_by_user: boolean;
  confirmation_status: LearningSourceConfirmationStatus;
  confirmed_bundle_id: string;
  confirmed_at?: string | null;
  confirmed_references: LearningSourceReference[];
}

export interface LearningRequirementSheet {
  theme: string;
  learning_goal: string;
  level: string;
  known_background: string;
  current_questions: string[];
  learning_need_checklist: string[];
  target_depth: string;
  output_preference: string;
  boundary: string;
  board_scope: string[];
  success_criteria: string;
  risk_notes: string[];
  board_workflow?: BoardWorkflow | null;
  work_mode?: InitialLearningWorkMode | null;
  granularity?: InitialLearningGranularity | null;
  source_grounding?: LearningSourceGrounding;
}

export type BoardWorkflow = "generate_from_scratch" | "act_on_existing_board" | "unknown";
export type InitialLearningWorkMode = "knowledge_board" | "narrow_topic" | "practice_artifact" | "unknown";
export type InitialLearningGranularity = "single_knowledge_point" | "source_chapter" | "source_range" | "broad_topic" | "practice_artifact" | "unclear";
export type BoardTaskRunStatus = "collecting" | "ready" | "awaiting_confirmation" | "consumed" | "not_executed" | "archived";
export type BoardDocumentOperationStatus = "none" | "succeeded" | "failed";
export type BoardTaskRequestedAction = "write" | "edit" | "explain" | "chat";
export type BoardTaskConfirmationStatus = "none" | "awaiting" | "confirmed" | "declined";
export type BoardTaskLocationStatus = "missing" | "selected" | "resolved" | "ambiguous" | "content_absent";
export type BoardTaskLocationKind = "target_range" | "insertion_anchor" | "unspecified";

export interface BoardTaskRequirementSheet {
  board_workflow?: BoardWorkflow | null;
  location_kind?: BoardTaskLocationKind;
  target_hint: string;
  target_location?: BoardFocusRef | null;
  location_status: BoardTaskLocationStatus;
  requested_action?: BoardTaskRequestedAction | null;
  question_or_topic: string;
  missing_items: string[];
  progress: number;
  confirmation_status: BoardTaskConfirmationStatus;
  clarification_question: string;
  failure_count: number;
}

export interface LearningRequirementChecklistItem {
  title: string;
  is_clear: boolean;
  evidence: string;
}

export interface LearningRequirementKeyFact {
  label: string;
  value: string;
  evidence: string;
  category?: "learning" | "level" | "vocabulary" | "scenario" | "output" | "other" | null;
}

export interface LearningClarificationStatus {
  progress: number;
  label: string;
  reason: string;
  missing_items: string[];
  can_start: boolean;
  forced_start: boolean;
  summary: string;
  key_facts: LearningRequirementKeyFact[];
  checklist: LearningRequirementChecklistItem[];
  next_question: string;
  ready_for_board: boolean;
  work_mode?: InitialLearningWorkMode | null;
  granularity?: InitialLearningGranularity | null;
}

export type LearningRequirementRunStatus = "collecting" | "ready" | "frozen" | "consumed" | "archived";

export interface TeachingGuideMapping {
  block_id: string;
  supports_goal: string;
  teaching_mode: "definition" | "intuition" | "analogy" | "example" | "dialogue";
  focus_points: string[];
  optional_points: string[];
  difficult_points: string[];
  check_questions: string[];
}

export interface TeachingGuide {
  lesson_id: string;
  summary: string;
  structure_note: string;
  pacing: string;
  mappings: TeachingGuideMapping[];
  strategy: string;
}

export interface CommitRecord {
  id: string;
  label: string;
  message: string;
  branch_name: string;
  created_at: string;
  parent_ids: string[];
  operations: Array<Record<string, unknown>>;
  snapshot: BoardDocument;
  metadata?: Record<string, unknown> & {
    history_node_kind?: "chat" | "document" | "restore" | "merge" | "system";
    history_node_title?: string;
    history_node_summary?: string;
  };
}

export type LessonMergeMode = "manual" | "ai";
export type LessonMergeStatus =
  | "draft"
  | "ai_running"
  | "ready"
  | "stale"
  | "committed"
  | "abandoned"
  | "failed";
export type LessonMergeResolution =
  | "unresolved"
  | "target"
  | "source"
  | "both"
  | "custom"
  | "clear"
  | "ai";

export interface LessonMergeConflictView {
  id: string;
  kind: "board" | "learning_requirement" | "board_task" | "source_reference";
  path: string;
  title: string;
  base_value: unknown;
  target_value: unknown;
  source_value: unknown;
  resolution: LessonMergeResolution;
  custom_value: unknown;
  resolved: boolean;
}

export interface MergeRuntimeDraftView {
  learning_requirements?: LearningRequirementSheet | null;
  board_task_requirements?: BoardTaskRequirementSheet | null;
  board_teaching_restart_heading_path: string[];
  invalidated_teaching_state: boolean;
}

export interface LessonMergeSessionView {
  id: string;
  lesson_id: string;
  target_branch_name: string;
  source_branch_name: string;
  base_commit_id: string;
  target_head_commit_id: string;
  source_head_commit_id: string;
  mode: LessonMergeMode;
  status: LessonMergeStatus;
  version: number;
  conflicts: LessonMergeConflictView[];
  draft_document: BoardDocument;
  draft_runtime: MergeRuntimeDraftView;
  ai_model?: AIModelSelection | null;
  agent_activity: AgentActivityEvent[];
  audit: Record<string, unknown>;
  supersedes_session_id?: string | null;
  committed_commit_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface BranchRef {
  name: string;
  head_commit_id: string;
  base_commit_id: string;
  created_at: string;
}

export interface LessonHistoryGraph {
  branches: Record<string, BranchRef>;
  commits: CommitRecord[];
  current_branch: string;
}

export interface Lesson {
  id: string;
  title: string;
  slug: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
  learning_requirements?: LearningRequirementSheet | null;
  board_task_requirements?: BoardTaskRequirementSheet | null;
  history_graph: LessonHistoryGraph;
  created_at: string;
  updated_at: string;
}

export interface CourseGraphEdge {
  id: string;
  source_lesson_id: string;
  target_lesson_id: string;
  relationship:
    | "recommended_next"
    | "prerequisite"
    | "deep_dive"
    | "alternate_path"
    | "derived_from";
}

export interface LibraryChapter {
  id: string;
  title: string;
  level: number;
  page_range?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  summary: string;
  keywords: string[];
  prerequisites: string[];
  parent_id?: string | null;
  parent_title?: string | null;
  path: string[];
  locator_hint?: string | null;
  order_index: number;
  scan_strategy: "outline_only" | "heading_section" | "page_window" | "fulltext_match";
}

export interface ResourceSourceUnit {
  id: string;
  content_type: string;
  text: string;
  page_idx?: number | null;
  page_no?: number | null;
  source_locator?: string | null;
  url?: string | null;
  heading_path: string[];
  paragraph_index?: number | null;
  timestamp_start?: number | null;
  timestamp_end?: number | null;
  asset_path?: string | null;
  bbox: number[];
  order_index: number;
  metadata: Record<string, unknown>;
}

export type ResourceSourceType =
  | "local_file"
  | "web_url"
  | "audio_file"
  | "video_file"
  | "video_url"
  | "pasted_text"
  | "transcript";

export type SourceIngestionStatus = "queued" | "fetching" | "parsing" | "indexing" | "ready" | "failed";
export type EvidenceBundleStatus = "candidate" | "confirmed" | "consumed" | "archived";
export type EvidencePurpose = "chat" | "board_generation" | "board_edit" | "board_explain" | "board_chat";
export type SourceStructureStatus = "pending" | "building" | "ready" | "linear_only" | "failed";
export type SourceChapterMappingStatus = "verified" | "partial" | "unverified" | "unmapped";
export type SourceStructureQualityLevel =
  | "unassessed"
  | "fully_verified"
  | "partially_verified"
  | "unverified"
  | "search_only";
export type SourceTextReadiness = "unknown" | "ready" | "sparse" | "very_sparse" | "empty";
export type SourceStructureStrategy =
  | "codex_directory_v1"
  | "codex_catalog"
  | "epub_navigation"
  | "epub_heading"
  | "pdf_outline"
  | "pdf_toc"
  | "pdf_merged_toc"
  | "pdf_layout_toc"
  | "docx_heading"
  | "markdown_heading"
  | "linear_text"
  | "open_notebook_search_only";

export interface SourceStructureQuality {
  evaluator_version: number;
  level: SourceStructureQualityLevel;
  text_readiness: SourceTextReadiness;
  confidence: number;
  total_chapter_count: number;
  verified_chapter_count: number;
  unverified_chapter_count: number;
  demoted_chapter_count: number;
  verified_leaf_count: number;
  expected_leaf_count: number;
  verified_ratio: number;
  boundary_valid_ratio: number;
  body_coverage_ratio: number;
  independent_anchor_ratio: number;
  meaningful_characters_per_page: number;
  duplicate_locator_ratio: number;
  duplicate_range_count: number;
  overlap_ratio: number;
  non_monotonic_count: number;
  oversized_leaf_count: number;
  diagnostics: string[];
}

export interface SourceIngestionJob {
  id: string;
  resource_id?: string | null;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  adapter: string;
  status: SourceIngestionStatus;
  progress: number;
  error: string;
  phase_history: string[];
  agent_activity: AgentActivityEvent[];
  created_at: string;
  updated_at: string;
}

export interface SourceIngestionRecord {
  id: string;
  owner_user_id: string;
  package_id: string;
  title: string;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  status: SourceIngestionStatus;
  error: string;
  structure_status: SourceStructureStatus;
  structure_strategy?: SourceStructureStrategy | null;
  structure_has_verified_toc: boolean;
  structure_quality?: SourceStructureQuality | null;
  structure_error: string;
  structure_updated_at?: string | null;
  ingestion_job?: SourceIngestionJob | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface RetrievalEvidence {
  id: string;
  source_ingestion_id: string;
  source_title: string;
  source_uri?: string | null;
  chapter_id: string;
  section_path: string[];
  page_range: string;
  chunk_ids: string[];
  excerpt: string;
  expanded_text: string;
  relevance_score: number;
  reason: string;
  token_count: number;
  metadata: Record<string, unknown>;
}

export type SourceQueryScopeMode =
  | "chapter"
  | "page_range"
  | "source"
  | "sources"
  | "all_ready_sources";

export interface SourceQueryRef {
  source_ingestion_id: string;
  source_content_hash: string;
  source_chapter_id?: string | null;
  page_start?: number | null;
  page_end?: number | null;
}

export interface SourceQueryScope {
  mode: SourceQueryScopeMode;
  refs: SourceQueryRef[];
}

export interface SourceCitation {
  evidence_id: string;
  source_id: string;
  source_title: string;
  section_path: string[];
  page_start?: number | null;
  page_end?: number | null;
  excerpt: string;
  chunk_ids: string[];
  bbox: number[];
  source_content_hash: string;
  parser_run_id: string;
}

export interface SourceStructure {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  status: SourceStructureStatus;
  strategy: SourceStructureStrategy;
  has_verified_toc: boolean;
  quality?: SourceStructureQuality | null;
  chapter_count: number;
  chunk_count: number;
  confidence: number;
  error: string;
  warnings: string[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface SourceChapter {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  parent_id?: string | null;
  number: string;
  normalized_number: string;
  title: string;
  level: number;
  path: string[];
  order_index: number;
  source_locator: string;
  body_start_offset?: number | null;
  body_end_offset?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  anchor_status: "verified" | "unverified";
  range?: SourceRange | null;
  mapping_status?: SourceChapterMappingStatus;
  source_content_hash?: string;
  catalog_evidence?: SourceCatalogEvidence[];
  catalog_version?: number;
  confidence: number;
  excerpt: string;
  metadata: Record<string, unknown>;
}

export interface SourceCatalogEvidence {
  method: string;
  source_locator: string;
  page_start?: number | null;
  page_end?: number | null;
  excerpt: string;
  confidence: number;
  metadata: Record<string, unknown>;
}

export type SourceRangeKind =
  | "pdf_pages"
  | "epub_spine"
  | "docx_paragraphs"
  | "ppt_slides"
  | "sheet_rows"
  | "text_lines"
  | "dom_anchor"
  | "structured_path";

export interface SourceRange {
  kind: SourceRangeKind;
  start: number | string | null;
  end: number | string | null;
  container: string;
  start_anchor: string;
  end_anchor: string;
  path: string[];
  display_label: string;
  end_inclusive: true;
  metadata: Record<string, unknown>;
}

export interface SourceCatalogSourceSummary {
  id: string;
  title: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  status: SourceIngestionStatus;
  structure_status: SourceStructureStatus;
}

export interface SourceCatalogView {
  source: SourceCatalogSourceSummary;
  structure_id: string | null;
  status: SourceStructureStatus;
  strategy: SourceStructureStrategy | null;
  has_verified_toc: boolean;
  catalog_version: number;
  catalog_updated_at: string | null;
  source_content_hash: string;
  catalog_schema_version: string;
  catalog_model: string;
  task_contract: string;
  chapter_count: number;
  verified_chapter_count: number;
  confidence: number;
  quality: SourceStructureQuality;
  error: string;
  warnings: string[];
  chapters: SourceChapter[];
}

export interface SourceCatalogBatchView {
  package_id: string;
  catalogs: SourceCatalogView[];
}

export interface SourceChunk {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  chapter_id?: string | null;
  order_index: number;
  source_locator: string;
  text: string;
  start_offset: number;
  end_offset: number;
  page_start?: number | null;
  page_end?: number | null;
  token_count: number;
  metadata: Record<string, unknown>;
}

export type SourceVisualKind = "image" | "chart" | "table" | "diagram" | "page_snapshot";
export type SourceVisualAnchorStatus = "verified" | "unverified";

export interface SourceVisualAsset {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  structure_id: string;
  structure_version: number;
  chapter_id?: string | null;
  kind: SourceVisualKind;
  source_locator: string;
  page_start?: number | null;
  page_end?: number | null;
  paragraph_index?: number | null;
  slide_no?: number | null;
  sheet_name: string;
  bbox: number[];
  before_chunk_id?: string | null;
  after_chunk_id?: string | null;
  caption: string;
  extracted_text: string;
  surrounding_text: string;
  anchor_status: SourceVisualAnchorStatus;
  mime_type: string;
  order_index: number;
  content_hash: string;
  position_hash: string;
  width?: number | null;
  height?: number | null;
  table_data: string[][];
  confidence: number;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SourceVisualEvidence {
  visual_id: string;
  source_ingestion_id: string;
  source_chapter_id: string;
  kind: SourceVisualKind;
  source_locator: string;
  page_start?: number | null;
  page_end?: number | null;
  paragraph_index?: number | null;
  slide_no?: number | null;
  sheet_name: string;
  bbox: number[];
  before_chunk_id?: string | null;
  after_chunk_id?: string | null;
  caption: string;
  extracted_text: string;
  surrounding_text: string;
  anchor_status: SourceVisualAnchorStatus;
  mime_type: string;
  content_hash: string;
  position_hash: string;
  width?: number | null;
  height?: number | null;
  table_data: string[][];
  confidence: number;
  metadata: Record<string, unknown>;
}

export interface SourceStructureView {
  source: SourceIngestionRecord;
  structure?: SourceStructure | null;
  chapters: SourceChapter[];
  chunks: SourceChunk[];
  visuals: SourceVisualAsset[];
}

export interface SourceContentView {
  source: SourceIngestionRecord;
  content: string;
}

export interface EvidenceBundle {
  id: string;
  owner_user_id: string;
  package_id: string;
  lesson_id?: string | null;
  requirement_run_id?: string | null;
  board_task_run_id?: string | null;
  purpose: EvidencePurpose;
  status: EvidenceBundleStatus;
  query: string;
  evidence_items: RetrievalEvidence[];
  visual_items: SourceVisualEvidence[];
  context_text: string;
  token_count: number;
  confirmed_by_user: boolean;
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  metadata: Record<string, unknown>;
}

export type ResourcePageRole =
  | "cover"
  | "copyright"
  | "toc"
  | "preface"
  | "body"
  | "appendix"
  | "back_matter"
  | "unknown";

export interface ResourcePageSection {
  role: ResourcePageRole;
  page_idx_start?: number | null;
  page_idx_end?: number | null;
  page_no_start?: number | null;
  page_no_end?: number | null;
  title: string;
  confidence: number;
  evidence_excerpt: string;
}

export interface ResourcePageMapEntry {
  page_idx: number;
  page_no: number;
  role: ResourcePageRole;
  printed_page?: number | null;
  body_offset?: number | null;
  confidence: number;
  evidence_excerpt: string;
}

export interface ResourcePageStructure {
  page_count: number;
  body_start_page_idx?: number | null;
  body_start_page_no?: number | null;
  toc_page_indices: number[];
  sections: ResourcePageSection[];
  page_map: ResourcePageMapEntry[];
  diagnostics: string[];
  confidence: number;
}

export interface ResourceLibraryItem {
  id: string;
  name: string;
  mime_type: string;
  resource_type: string;
  size_bytes: number;
  uploaded_at: string;
  scope_lesson_id?: string | null;
  outline: LibraryChapter[];
  concept_index: Record<string, string[]>;
  extracted_text_available: boolean;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  ingestion_status: SourceIngestionStatus;
  ingestion_error: string;
  ingestion_progress: number;
  ingestion_adapter: string;
  ingestion_job?: SourceIngestionJob | null;
  parser_provider: string;
  parser_artifacts_path?: string | null;
  parser_message: string;
  parse_warnings: string[];
  source_units: ResourceSourceUnit[];
  page_structure?: ResourcePageStructure | null;
}

export interface CoursePackage {
  id: string;
  title: string;
  summary: string;
  is_standalone: boolean;
  lessons: Lesson[];
  course_graph: CourseGraphEdge[];
  resources: ResourceLibraryItem[];
  open_lesson_ids: string[];
  active_lesson_id?: string | null;
  workspace_tab_order: string[];
}

export interface WorkspaceState {
  packages: CoursePackage[];
  active_package_id?: string | null;
}

export interface BatchLessonActionRequest {
  action: "move" | "delete";
  lesson_ids: string[];
  target_package_id?: string | null;
}

export interface UserView {
  id: string;
  email: string;
  phone?: string | null;
  role: "user" | "admin" | "guest";
  display_name?: string | null;
  avatar_url?: string | null;
  created_at: string;
  last_login_at?: string | null;
  auth_identities: AuthIdentityView[];
}

export interface AuthSessionResponse {
  token: string;
  user: UserView;
}

export interface AuthIdentityView {
  provider: string;
  provider_label: string;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
  created_at: string;
  last_login_at?: string | null;
}

export interface AuthProviderView {
  id: string;
  label: string;
  description: string;
  configured: boolean;
  kind: "password" | "oauth" | "device";
}

export interface AdminOverview {
  stats: {
    users: number;
    admins: number;
    packages: number;
    lessons: number;
    resources: number;
  };
  users: UserView[];
}

export interface AIModelSelection {
  agent_backend?: "codex" | "pi";
  provider: AIProvider;
  model: string;
  reasoning_effort?: string | null;
  service_tier?: string | null;
}

export interface AIAgentBackendOption {
  id: "codex" | "pi";
  label: string;
  description: string;
  enabled: boolean;
}

export interface AIReasoningEffortOption {
  reasoning_effort: string;
  description: string;
}

export interface AIServiceTierOption {
  id: string;
  name: string;
  description: string;
}

export interface AIModelOption {
  provider: AIProvider;
  model: string;
  label: string;
  capability: AIModelCapability;
  enabled: boolean;
  configured: boolean;
  default: boolean;
  transport?: AIRealtimeTransport | null;
  default_reasoning_effort?: string | null;
  supported_reasoning_efforts?: AIReasoningEffortOption[];
  default_service_tier?: string | null;
  service_tiers?: AIServiceTierOption[];
}

export interface AIModelCatalog {
  text: AIModelOption[];
  realtime: AIModelOption[];
  defaults: {
    text: AIModelSelection;
    realtime: AIModelSelection;
  };
  agent_backends?: {
    teaching: AIAgentBackendOption[];
    source: AIAgentBackendOption[];
  };
}

export interface CodexAccountView {
  type?: string | null;
  email?: string | null;
  plan_type?: string | null;
}

export interface CodexProviderStatus {
  enabled: boolean;
  available: boolean;
  configured: boolean;
  account?: CodexAccountView | null;
  rate_limits?: Record<string, unknown> | null;
  message: string;
}

export interface CodexLoginStartResponse {
  login_id: string;
  verification_url: string;
  user_code: string;
  expires_at?: string | null;
}

export interface CodexLoginStatusResponse {
  login_id: string;
  status: "pending" | "succeeded" | "failed" | "cancelled" | "expired";
  error?: string | null;
  account?: CodexAccountView | null;
}

export interface SelectionRef {
  kind: "chat" | "board" | "source";
  excerpt: string;
  location_kind?: BoardTaskLocationKind | null;
  lesson_id?: string | null;
  block_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  heading_path?: string[];
  before_text?: string;
  after_text?: string;
  text_hash?: string | null;
  source_ingestion_id?: string | null;
  source_title?: string;
  source_uri?: string | null;
  source_chapter_id?: string | null;
  source_chapter_number?: string;
  source_chapter_title?: string;
  source_page_range?: string;
  source_locator?: string;
  source_page_start?: number | null;
  source_page_end?: number | null;
  source_scope_kind?: "source" | "chapter" | "page_range";
  source_range?: SourceRange | null;
  catalog_version?: number | null;
  source_content_hash?: string;
}

export type FormulaInkAction = "reference" | "replace";

export interface FormulaInkPayload {
  image_data_url: string;
  source_latex?: string | null;
  action: FormulaInkAction;
}

export interface ChatAttachmentRef {
  source_ingestion_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  kind: "image" | "file";
  status: SourceIngestionStatus;
}

export interface BoardFocusRef {
  source: "board" | "chat";
  lesson_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  kind?: "heading" | "paragraph" | "list" | "table" | "code" | "image" | "formula" | "other" | null;
  heading_path: string[];
  excerpt: string;
  before_text: string;
  after_text: string;
  text_hash?: string | null;
  excerpt_hash?: string | null;
  confidence: number;
  reason: string;
  display_label?: string;
  match_id?: string | null;
  source_segment_ids?: string[];
  order_start?: number | null;
  order_end?: number | null;
  score_breakdown?: Record<string, number>;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface BoardDecision {
  action: BoardAction;
  reason: string;
}

export interface ChatRequestPayload {
  message: string;
  text_model?: AIModelSelection | null;
  selection?: SelectionRef | null;
  selections?: SelectionRef[];
  formula_ink?: FormulaInkPayload | null;
  attachments?: ChatAttachmentRef[];
  source_query_scope?: SourceQueryScope | null;
  interaction_mode?: ChatInteractionMode;
  board_generation_action?: "start" | null;
  teaching_action?: "continue" | "restart" | null;
  post_generation_action?: "auto_explain" | "stop_after_generation";
  chat_edit_source_commit_id?: string | null;
  chat_edit_base_commit_id?: string | null;
  chat_edit_original_message?: string | null;
  conversation?: ConversationTurn[];
}

export interface SectionTeachingProgress {
  section_index: number;
  section_count: number;
  current_section_title: string;
  has_next_section: boolean;
  waiting_for_continue: boolean;
  target_heading_path?: string[];
  current_heading_path?: string[];
}

export type AgentActivityStage =
  | "turn_decision"
  | "resolve_target"
  | "build_context"
  | "execute_role"
  | "verify"
  | "persist_history"
  | "final";

export type AgentActivityStatus = "pending" | "running" | "completed" | "blocked" | "failed" | "skipped";

export interface AgentActivityEvent {
  id: string;
  turn_id: string;
  stage: AgentActivityStage;
  label: string;
  status: AgentActivityStatus;
  role: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export type GuidedRequirementDiscoveryStrategy =
  | "entry_point_discovery"
  | "level_discovery"
  | "goal_discovery"
  | "mode_discovery"
  | "bottleneck_discovery";

export interface GuidedRequirementEntryPoint {
  title: string;
  description: string;
  answer_value: string;
  why_it_matters: string;
  best_for: string;
}

export type GuidedRequirementSelectionTarget =
  | "learning_content"
  | "current_level"
  | "target_scenario"
  | "teaching_type"
  | "bottleneck";

export interface GuidedRequirementDiscovery {
  strategy: GuidedRequirementDiscoveryStrategy;
  selection_target: GuidedRequirementSelectionTarget;
  question_title: string;
  learning_map_summary: string;
  entry_point_options: GuidedRequirementEntryPoint[];
  recommended_entry_point: string;
  reason_for_recommendation: string;
  learner_profile_inference: string;
}

export interface ChatResponse {
  chatbot_message: string;
  source_citations?: SourceCitation[];
  follow_up_suggestions?: string[];
  agent_activity?: AgentActivityEvent[];
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  learning_clarification: LearningClarificationStatus;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
  learning_requirement_operation_status?: "none" | "succeeded" | "failed";
  learning_requirement_operation_failure_reason?: string | null;
  board_task_sheet?: BoardTaskRequirementSheet | null;
  active_board_task_sheet?: BoardTaskRequirementSheet | null;
  board_task_run_id?: string | null;
  board_task_version_id?: string | null;
  board_task_phase?: BoardTaskRunStatus | null;
  board_task_questions?: string[];
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  guided_requirement_discovery?: GuidedRequirementDiscovery | null;
  requirement_cleared?: boolean;
  board_document_operation_status?: BoardDocumentOperationStatus;
  board_document_operation_failure_reason?: string | null;
  teaching_progress?: SectionTeachingProgress | null;
  auto_teaching_operation_status?: "none" | "succeeded" | "failed";
  auto_teaching_operation_failure_reason?: string | null;
  course_package: CoursePackage;
}

export interface RequirementUpdateStreamPayload {
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  learning_clarification: LearningClarificationStatus;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
  clarification_questions: string[];
}

export interface BoardTaskUpdateStreamPayload {
  board_task_sheet: BoardTaskRequirementSheet;
  active_board_task_sheet?: BoardTaskRequirementSheet | null;
  board_task_run_id?: string | null;
  board_task_version_id?: string | null;
  board_task_phase?: BoardTaskRunStatus | null;
  board_task_questions: string[];
}

export interface RealtimeConnectPayload {
  offer_sdp: string;
  latest_assistant_message?: string | null;
  client_session_id?: string | null;
  realtime_model?: AIModelSelection | null;
  selection?: SelectionRef | null;
}

export interface RealtimeConnectResponse {
  answer_sdp: string;
  provider: AIProvider;
  model: string;
  voice: string;
  call_id?: string | null;
  tools_enabled?: boolean;
  client_session_id?: string | null;
}

export interface GoogleRealtimeSessionPayload {
  latest_assistant_message?: string | null;
  client_session_id?: string | null;
  realtime_model?: AIModelSelection | null;
}

export interface GoogleRealtimeSessionResponse {
  websocket_url: string;
  setup: Record<string, unknown>;
  provider: "google";
  model: string;
  voice: string;
}

export interface RealtimeEventLogPayload {
  client_event_id: string;
  client_session_id?: string | null;
  turn_id?: string | null;
  occurred_at: string;
  lesson_title?: string | null;
  role: "user" | "assistant" | "tool";
  transport_event_type: string;
  transcript: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_status?: string | null;
}

export type RealtimeToolName = "read_board_context" | "run_chatbot_workflow";

export interface RealtimeToolCallPayload {
  client_session_id: string;
  turn_id?: string | null;
  call_id: string;
  name: RealtimeToolName;
  arguments: Record<string, unknown>;
  selection?: SelectionRef | null;
}

export interface RealtimeToolCallResponse {
  status: "ok" | "error";
  model_output: Record<string, unknown>;
  resolved_focus?: BoardFocusRef | null;
  course_package?: CoursePackage | null;
}

export interface DocumentSavePayload {
  document: BoardDocument;
  label?: string;
  message?: string;
  metadata?: Record<string, unknown>;
  base_commit_id?: string | null;
}

export interface DocumentAIEditPayload {
  instruction: string;
  selection_text?: string | null;
  replace_whole?: boolean;
  conversation?: ConversationTurn[];
}

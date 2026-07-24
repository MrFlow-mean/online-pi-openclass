from __future__ import annotations

import json
import base64
import hashlib
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    BoardExplanationDirective,
    ChatAttachmentRef,
    ChatRequest,
    GuidedRequirementDiscovery,
    GuidedRequirementEntryPoint,
    LearningSourceGrounding,
    LearningSourceReference,
    RetrievalEvidence,
    SelectionRef,
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services import (
    ai_execution_adapter,
    blank_board_intake,
    board_visual_insertion,
    chat_attachments,
    codex_app_server,
    codex_chat,
    source_scope_ocr,
    turn_intent,
    workspace_state,
)
from app.services.board_asset_store import BoardAssetStore
from app.services.blank_board_intake import (
    BlankBoardAuxiliaryFactor,
    BlankBoardTurnDecision,
    OrdinaryChatTurnResponse,
    SourceResolutionTurnResponse,
    evaluate_blank_board_decision,
)
from app.services.codex_app_server import (
    CodexAppServerError,
    CodexTurnCancelledError,
    CodexTurnResult,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations, create_branch, current_head_commit
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.rich_document import build_document, rebuild_document_from_content_json
from app.services.source_evidence_store import source_evidence_store
from app.services.source_structure_indexer import (
    CURRENT_SOURCE_STRUCTURE_INDEX_VERSION,
    SourceStructureIndexer,
)
from app.services.source_structure_store import source_structure_store
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION


TEST_USER_ID = "user_codex_chat"
PRODUCTION_TEXT_MODEL_SELECTION = codex_chat._text_model_selection


def test_chat_attachments_are_verified_materialized_and_sent_as_images(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    codex_store.save_for_user(TEST_USER_ID, workspace)
    upload_root = tmp_path / "uploads"
    source_dir = upload_root / "sources"
    source_dir.mkdir(parents=True)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_root)
    image_path = source_dir / "source_attachment.png"
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    image_path.write_bytes(image_bytes)
    source = SourceIngestionRecord(
        id="source_chat_attachment",
        owner_user_id=TEST_USER_ID,
        package_id=package.id,
        title="Attachment image",
        file_name="diagram.png",
        mime_type="image/png",
        size_bytes=len(image_bytes),
        status="queued",
        metadata={"local_source_path": str(image_path)},
    )
    source_evidence_store.save_source(source)
    attachment = ChatAttachmentRef(
        source_ingestion_id=source.id,
        name="client-name-is-not-trusted.png",
        mime_type="text/plain",
        size_bytes=1,
        kind="file",
        status="queued",
    )

    verified = chat_attachments.verify_chat_attachments(
        owner_user_id=TEST_USER_ID,
        package_id=package.id,
        attachments=[attachment, attachment],
    )
    prepared = chat_attachments.prepare_chat_attachments(
        attachments=verified,
    )

    assert len(verified) == 1
    assert prepared.image_inputs == [f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"]
    assert prepared.metadata == [
        {
            "source_ingestion_id": source.id,
            "name": "diagram.png",
            "mime_type": "image/png",
            "size_bytes": len(image_bytes),
            "kind": "image",
        }
    ]
    assert "diagram.png" in prepared.prompt_context


def test_chat_attachment_verification_rejects_a_source_outside_the_current_package(
    codex_store: SqliteCourseStore,
) -> None:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    codex_store.save_for_user(TEST_USER_ID, workspace)

    with pytest.raises(CodexAppServerError, match="不属于当前课程"):
        chat_attachments.verify_chat_attachments(
            owner_user_id=TEST_USER_ID,
            package_id=package.id,
            attachments=[
                ChatAttachmentRef(
                    source_ingestion_id="source_from_another_package",
                    name="outside.pdf",
                    status="ready",
                )
            ],
        )


def test_ready_file_attachment_uses_indexed_text_as_verified_context(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    codex_store.save_for_user(TEST_USER_ID, workspace)
    upload_root = tmp_path / "uploads"
    source_dir = upload_root / "sources"
    source_dir.mkdir(parents=True)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_root)
    file_path = source_dir / "source_attachment.txt"
    file_path.write_text("Raw attachment copy", encoding="utf-8")
    source = SourceIngestionRecord(
        id="source_chat_file_attachment",
        owner_user_id=TEST_USER_ID,
        package_id=package.id,
        title="Attachment notes",
        file_name="notes.txt",
        mime_type="text/plain",
        size_bytes=file_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(file_path)},
    )
    source_evidence_store.save_source(source)
    source_structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=TEST_USER_ID,
            package_id=package.id,
            source_ingestion_id=source.id,
            status="linear_only",
        ),
        chapters=[],
        chunks=[
            SourceChunk(
                owner_user_id=TEST_USER_ID,
                package_id=package.id,
                source_ingestion_id=source.id,
                order_index=0,
                text="Backend-indexed attachment evidence.",
            )
        ],
    )

    verified = chat_attachments.verify_chat_attachments(
        owner_user_id=TEST_USER_ID,
        package_id=package.id,
        attachments=[
            ChatAttachmentRef(
                source_ingestion_id=source.id,
                name="spoofed.txt",
                status="ready",
            )
        ],
    )
    prepared = chat_attachments.prepare_chat_attachments(attachments=verified)

    assert prepared.image_inputs == []
    assert "Backend-indexed attachment evidence." in prepared.prompt_context
    assert "Raw attachment copy" not in prepared.prompt_context
    assert "spoofed.txt" not in prepared.prompt_context


def _thread_result(thread_id: str, cwd: Path) -> dict:
    return {
        "thread": {"id": thread_id},
        "activePermissionProfile": {"id": "openclass_board"},
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [str((cwd / "board.md").resolve())],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


def _seed_workspace(store: SqliteCourseStore, *, content_text: str = "# Existing board"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Codex document")
    document = build_document(
        title=lesson.board_document.title,
        content_text=content_text,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.board_document = document
    lesson.history_graph.commits[0].snapshot = document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_codex_text_round_trip_preserves_existing_visual_nodes() -> None:
    base = build_document(title="Board", content_text="Before\n\nAfter")
    content_json = base.content_json.copy()
    content_json["content"] = [
        base.content_json["content"][0],
        {
            "type": "resourceVisualBlock",
            "attrs": {
                "assetId": "basset_preserved",
                "visualId": "visual_preserved",
                "caption": "Preserved figure",
                "kind": "image",
            },
        },
        base.content_json["content"][1],
    ]
    document = rebuild_document_from_content_json(base, content_json)

    serialized, preserved = codex_chat._document_for_codex(document)
    marker = next(iter(preserved))
    edited = build_document(
        title=document.title,
        content_text=serialized.replace("Before", "Edited before"),
        document_id=document.id,
        page_settings=document.page_settings,
    )
    restored = codex_chat._restore_preserved_visuals(edited, preserved)

    assert marker in serialized
    assert "Edited before" in restored.content_text
    visual = next(
        node for node in restored.content_json["content"] if node["type"] == "resourceVisualBlock"
    )
    assert visual["attrs"]["assetId"] == "basset_preserved"
    assert visual["attrs"]["visualId"] == "visual_preserved"

    removed = build_document(
        title=document.title,
        content_text=serialized.replace(marker, ""),
        document_id=document.id,
        page_settings=document.page_settings,
    )
    with pytest.raises(CodexAppServerError, match="protected visual placeholders"):
        codex_chat._restore_preserved_visuals(removed, preserved)


def test_frozen_visual_reader_rejects_position_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"source-image"
    content_hash = hashlib.sha256(content).hexdigest()
    evidence = SourceVisualEvidence(
        visual_id="visual_frozen",
        package_id="package_frozen",
        source_ingestion_id="source_frozen",
        anchor_status="verified",
        mime_type="image/png",
        content_hash=content_hash,
        position_hash="position_frozen",
    )
    stored = SourceVisualAsset(
        id=evidence.visual_id,
        owner_user_id=TEST_USER_ID,
        package_id=evidence.package_id,
        source_ingestion_id=evidence.source_ingestion_id,
        anchor_status="verified",
        mime_type="image/png",
        content_hash=content_hash,
        position_hash="position_changed",
    )
    monkeypatch.setattr(
        source_structure_store,
        "read_visual_bytes",
        lambda **_kwargs: (stored, content),
    )

    assert codex_chat._read_frozen_source_visual(
        user_id=TEST_USER_ID,
        evidence=evidence,
    ) is None


def test_codex_turn_prompt_does_not_trust_unverified_source_chip_text() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Explain the current document.",
            interaction_mode="ask",
            selection=SelectionRef(
                kind="source",
                excerpt="uploaded source excerpt must not be sent",
                source_title="Uploaded source title",
                source_chapter_title="Uploaded chapter",
                source_page_range="10-12",
            ),
        ),
        is_new_thread=True,
        board_state="empty",
    )

    assert "Interaction mode: ask" in prompt
    assert "Board state (computed by OpenClass): EMPTY." in prompt
    assert "Current user message:\nExplain the current document." in prompt
    assert "uploaded source excerpt" not in prompt
    assert "Uploaded source title" not in prompt
    assert "Uploaded chapter" not in prompt
    assert "10-12" not in prompt


def test_document_for_codex_serializes_canonical_rich_structure_to_markdown() -> None:
    document = build_document(
        title="Board",
        content_text="# Main heading\n\n## Detail heading\n\n- First item",
    )
    editor_projection = document.model_copy(
        update={"content_text": "Main heading\nDetail heading\nFirst item"}
    )

    serialized, preserved = codex_chat._document_for_codex(editor_projection)

    assert preserved == {}
    assert serialized.startswith("# Main heading")
    assert "## Detail heading" in serialized
    assert "- First item" in serialized


def test_codex_turn_prompt_keeps_current_board_selection_for_editing() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Rewrite this paragraph.",
            interaction_mode="direct_edit",
            selection=SelectionRef(
                kind="board",
                excerpt="Current board paragraph",
                heading_path=["Section"],
            ),
        ),
        is_new_thread=False,
        board_state="non_empty",
    )

    assert "Interaction mode: direct_edit" in prompt
    assert "Board state (computed by OpenClass): NON_EMPTY." in prompt
    assert "kind: board" in prompt
    assert "excerpt: Current board paragraph" in prompt
    assert "heading path: Section" in prompt


def test_turn_intent_requires_explicit_board_write_authorization() -> None:
    explicit = turn_intent.decide_board_write_action(
        message="把这段的结论写入板书",
        interaction_mode="ask",
        has_pending_offer=False,
        has_board_selection=True,
    )
    question = turn_intent.decide_board_write_action(
        message="这项投资应该怎么分类？",
        interaction_mode="ask",
        has_pending_offer=False,
    )
    advice_only = turn_intent.decide_board_write_action(
        message="是否应该把这些内容写入板书？",
        interaction_mode="ask",
        has_pending_offer=False,
    )
    conversation = turn_intent.decide_board_write_action(
        message="谢谢，我明白了",
        interaction_mode="ask",
        has_pending_offer=False,
    )
    long_title_edit = turn_intent.decide_board_write_action(
        message=(
            "请把板书的一级标题“面向真实场景的完整课程标题”"
            "修改为“新的课程标题”，其他内容保持不变。"
        ),
        interaction_mode="ask",
        has_pending_offer=False,
    )
    heading_confirmation = turn_intent.decide_board_write_action(
        message="确认，请修改这个一级标题",
        interaction_mode="ask",
        has_pending_offer=True,
    )
    how_to_question = turn_intent.decide_board_write_action(
        message="如何修改文档标题？",
        interaction_mode="ask",
        has_pending_offer=False,
    )

    assert explicit.action == "edit_now"
    assert long_title_edit.action == "edit_now"
    assert heading_confirmation.action == "edit_now"
    assert question.action == "answer_then_offer"
    assert advice_only.action == "answer_then_offer"
    assert how_to_question.action == "answer_then_offer"
    assert conversation.action == "chat_without_offer"


def test_turn_intent_only_accepts_standalone_pending_offer_replies() -> None:
    confirmed = turn_intent.decide_board_write_action(
        message="可以，写进去吧",
        interaction_mode="ask",
        has_pending_offer=True,
    )
    declined = turn_intent.decide_board_write_action(
        message="先不写",
        interaction_mode="ask",
        has_pending_offer=True,
    )
    follow_up_question = turn_intent.decide_board_write_action(
        message="可以再解释一下判断依据吗？",
        interaction_mode="ask",
        has_pending_offer=True,
    )
    disagreement = turn_intent.decide_board_write_action(
        message="我不同意这个结论，为什么？",
        interaction_mode="ask",
        has_pending_offer=True,
    )

    assert confirmed.action == "confirm_offered_write"
    assert declined.action == "decline_offered_write"
    assert follow_up_question.action == "answer_then_offer"
    assert disagreement.action == "answer_then_offer"


def test_board_state_detector_treats_whitespace_as_empty() -> None:
    assert codex_chat._board_state("") == "empty"
    assert codex_chat._board_state(" \n\t") == "empty"
    assert codex_chat._board_state("# Lesson") == "non_empty"


def test_blank_board_ordinary_chat_does_not_create_requirement_sheet() -> None:
    decision = BlankBoardTurnDecision(
        intent="ordinary_chat",
        chatbot_message="A conversational reply.",
        reason="The user is not asking to learn.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "ordinary_chat"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False
    assert outcome.clarification.reason == ""
    assert outcome.clarification.summary == ""
    assert outcome.clarification.next_question == ""
    assert outcome.guidance.is_empty()


def test_blank_board_unclear_intent_guides_without_creating_requirement_sheet() -> None:
    decision = BlankBoardTurnDecision(
        intent="unclear",
        chatbot_message="A recommendation-led clarification.",
        next_question="Which direction should we narrow first?",
        reason="A learning purpose is not confirmed yet.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False


def test_blank_board_learning_intent_without_teaching_type_guides_instead_of_failing() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A broad learning theme",
        content_is_specific=False,
        chatbot_message="Two contextual directions and one narrowing question.",
        next_question="Which direction should we narrow first?",
        reason="The learning intent is clear, but the teaching type cannot be selected yet.",
        guidance=GuidedRequirementDiscovery(
            strategy="level_discovery",
            selection_target="current_level",
            question_title="Which starting level is closest to you?",
            learning_map_summary="A short map of useful starting directions.",
            entry_point_options=[
                GuidedRequirementEntryPoint(
                    title="Start from a first foundation",
                    description="Build one dependable first step before expanding.",
                    answer_value="New to the prerequisites",
                    why_it_matters="The first board should establish the prerequisites.",
                    best_for="A learner without a dependable starting point.",
                ),
                GuidedRequirementEntryPoint(
                    title="Start from a real task",
                    description="Use a concrete outcome to choose the first topic.",
                    answer_value="Can complete a related task with support",
                    why_it_matters="A task can expose the next useful knowledge gap.",
                    best_for="A learner with some practical exposure.",
                ),
            ],
            recommended_entry_point="Start from a first foundation",
            reason_for_recommendation="No prior level or target task is confirmed yet.",
            learner_profile_inference="The learner may be at an early exploratory stage.",
        ),
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type is None
    assert outcome.requirement.learning_content == "A broad learning theme"
    assert outcome.requirement.work_mode == "unknown"
    assert outcome.requirement.granularity == "unclear"
    assert outcome.requirement_changed is True
    assert outcome.ready_for_board is False
    assert outcome.chatbot_message == "Two contextual directions and one narrowing question."
    assert outcome.clarification.next_question == "Which direction should we narrow first?"
    assert outcome.guidance.recommended_entry_point == "Start from a first foundation"
    assert outcome.guidance.selection_target == "current_level"


def test_untyped_learning_turn_merges_a_selected_level_before_mode_is_resolved() -> None:
    previous = build_requirements("A broad learning theme")
    previous.teaching_type = None
    previous.learning_content = "A broad learning theme"
    previous.current_level = ""
    previous.target_scenario = ""
    previous.theme = previous.learning_content
    previous.learning_goal = previous.learning_content
    previous.work_mode = "unknown"
    previous.granularity = "unclear"
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A broad learning theme",
        current_level="Has some prerequisites but has not studied systematically",
        chatbot_message="That gives me your starting point. Now choose the first content direction.",
        next_question="Which content direction should we narrow first?",
        reason="The selected level is confirmed while the content is still broad.",
        guidance=GuidedRequirementDiscovery(
            strategy="entry_point_discovery",
            selection_target="learning_content",
            question_title="Which content direction should we narrow first?",
            learning_map_summary="The next step is to choose one focused content entry.",
            entry_point_options=[
                GuidedRequirementEntryPoint(
                    title="Build the conceptual foundation",
                    description="Choose one core idea and its meaning.",
                    answer_value="A core concept and its meaning",
                ),
                GuidedRequirementEntryPoint(
                    title="Start from a representative problem",
                    description="Use one problem to identify the required concept.",
                    answer_value="A representative problem and its method",
                ),
                GuidedRequirementEntryPoint(
                    title="Connect ideas through a structure map",
                    description="Narrow the broad direction through its main relationships.",
                    answer_value="A focused relationship between core ideas",
                ),
            ],
            recommended_entry_point="Build the conceptual foundation",
            reason_for_recommendation="It best matches the selected starting level.",
        ),
    )

    outcome = evaluate_blank_board_decision(
        decision,
        previous_requirement=previous,
    )

    assert outcome.requirement is not None
    assert outcome.requirement.current_level == (
        "Has some prerequisites but has not studied systematically"
    )
    assert outcome.requirement.learning_content == "A broad learning theme"
    assert outcome.clarification.missing_items == ["target_scenario", "teaching_type"]
    assert outcome.guidance.selection_target == "learning_content"


def test_untyped_zero_baseline_uses_an_ai_selected_beginner_entry() -> None:
    previous = build_requirements("A broad learning theme")
    previous.teaching_type = None
    previous.learning_content = "A broad learning theme"
    previous.current_level = ""
    previous.target_scenario = ""
    previous.theme = previous.learning_content
    previous.learning_goal = previous.learning_content
    previous.work_mode = "unknown"
    previous.granularity = "unclear"
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A focused beginner-safe entry selected from the field context",
        content_is_specific=True,
        current_level="No prior learning or usable experience in this field",
        zero_baseline_confirmed=True,
        chatbot_message=(
            "I selected a low-threshold starting point and will now clarify the learning format."
        ),
        next_question="Which learning format should we use?",
        reason="The learner explicitly confirmed a zero baseline, so the AI selected the entry.",
        guidance=GuidedRequirementDiscovery(
            strategy="mode_discovery",
            selection_target="teaching_type",
            question_title="Which learning format should we use?",
            learning_map_summary="The selected entry is suitable for a first focused board.",
            entry_point_options=[
                GuidedRequirementEntryPoint(
                    title="Concept explanation",
                    description="Build the first mental model before practice.",
                    answer_value="knowledge_point",
                )
            ],
            recommended_entry_point="Concept explanation",
            reason_for_recommendation="It introduces the selected entry from first principles.",
        ),
    )

    outcome = evaluate_blank_board_decision(
        decision,
        previous_requirement=previous,
    )

    assert outcome.requirement is not None
    assert outcome.requirement.learning_content == (
        "A focused beginner-safe entry selected from the field context"
    )
    assert outcome.requirement.target_scenario == "无明确应用场景"
    assert outcome.clarification.missing_items == ["teaching_type"]
    assert outcome.guidance.strategy == "mode_discovery"
    assert outcome.guidance.selection_target == "teaching_type"


def test_zero_baseline_does_not_replace_a_confirmed_target_scenario() -> None:
    previous = build_requirements("A broad learning theme")
    previous.teaching_type = None
    previous.learning_content = "A broad learning theme"
    previous.current_level = ""
    previous.target_scenario = "A concrete learner-stated purpose"
    previous.theme = previous.learning_content
    previous.learning_goal = previous.learning_content
    previous.work_mode = "unknown"
    previous.granularity = "unclear"
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A broad learning theme",
        current_level="No prior learning or usable experience in this field",
        zero_baseline_confirmed=True,
        chatbot_message="The learner's starting point is now clear.",
        next_question="Which foundational entry should we start with?",
        reason="The learner explicitly confirmed a zero baseline.",
    )

    outcome = evaluate_blank_board_decision(
        decision,
        previous_requirement=previous,
    )

    assert outcome.requirement is not None
    assert outcome.requirement.target_scenario == "A concrete learner-stated purpose"


def test_requirement_refinement_preserves_confirmed_source_context() -> None:
    previous = build_requirements("Selected source scope")
    previous.teaching_type = "knowledge_point"
    previous.learning_content = "Selected source scope"
    previous.boundary = "Verified source / selected section / pp. 10-12"
    previous.board_scope = [previous.boundary]
    previous.target_depth = "Follow the selected source structure."
    previous.output_preference = "Structured Markdown board"
    previous.success_criteria = "Cover the selected source scope."
    previous.work_mode = "knowledge_board"
    previous.granularity = "source_chapter"
    previous.source_grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id="bundle_selected_scope",
        confirmed_references=[
            LearningSourceReference(
                evidence_bundle_id="bundle_selected_scope",
                source_ingestion_id="source_selected_scope",
                source_chapter_id="chapter_selected_scope",
                chapter_title="Selected section",
                page_range="pp. 10-12",
            )
        ],
    )
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="Selected source scope",
        content_is_specific=True,
        current_level="Can distinguish the main cases but cannot apply the rules",
        target_scenario="Practice applying the selected rules",
        chatbot_message="The refined requirement is ready.",
        teaching_plan="Build the board from the verified source scope.",
        reason="The learner added level and use-context details.",
    )

    outcome = evaluate_blank_board_decision(
        decision,
        previous_requirement=previous,
        previous_phase="frozen",
    )

    assert outcome.requirement is not None
    assert outcome.requirement.current_level == decision.current_level
    assert outcome.requirement.source_grounding == previous.source_grounding
    assert outcome.requirement.boundary == previous.boundary
    assert outcome.requirement.board_scope == previous.board_scope
    assert outcome.requirement.granularity == "source_chapter"


def test_unclear_turn_does_not_persist_an_unconfirmed_learning_topic() -> None:
    decision = BlankBoardTurnDecision(
        intent="unclear",
        learning_content="A possible topic",
        chatbot_message="A contextual direction and one question.",
        next_question="Which kind of learning outcome are you looking for?",
        reason="The user has not confirmed a learning request.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False


def test_untyped_learning_turn_preserves_a_previous_requirement_when_no_topic_is_confirmed() -> None:
    previous = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad learning theme",
            content_is_specific=False,
            chatbot_message="One focused question.",
            next_question="Which part should become the first focused topic?",
            reason="The theme is still broad.",
        ),
        previous_requirement=None,
    ).requirement
    assert previous is not None

    outcome = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type=None,
            chatbot_message="A recommendation-led clarification.",
            next_question="Which starting direction fits best?",
            reason="The current turn does not add a confirmed topic.",
        ),
        previous_requirement=previous,
    )

    assert outcome.route == "guided_discovery"
    assert outcome.requirement == previous
    assert outcome.requirement_changed is False


def test_broad_knowledge_direction_collects_until_content_is_specific() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A broad field",
        content_is_specific=False,
        chatbot_message="Several possible entry points and one narrowing question.",
        next_question="Which entry point do you want to understand first?",
        reason="The requested field is too broad for a focused board.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type == "knowledge_point"
    assert outcome.requirement.learning_content == "A broad field"
    assert outcome.clarification.missing_items == [
        "learning_content",
        "current_level",
        "target_scenario",
    ]
    assert outcome.ready_for_board is False


def test_blank_board_question_wording_does_not_create_a_requirement_version() -> None:
    first = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad field",
            content_is_specific=False,
            chatbot_message="First guidance.",
            next_question="Which part should we narrow first?",
            reason="The content is broad.",
        ),
        previous_requirement=None,
    )
    assert first.requirement is not None

    second = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad field",
            content_is_specific=False,
            chatbot_message="Refined guidance.",
            next_question="What single concept matters most right now?",
            reason="The same core factor is still broad.",
        ),
        previous_requirement=first.requirement,
    )

    assert second.requirement == first.requirement
    assert second.requirement_changed is False
    assert second.clarification.next_question == (
        "What single concept matters most right now?"
    )


def test_specific_knowledge_point_requires_level_and_target_scenario() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A specific concept",
        content_is_specific=True,
        chatbot_message="One question about the learner context.",
        next_question="What is your current level and where will you use this?",
        reason="The current level and target scenario are still missing.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type == "knowledge_point"
    assert outcome.requirement.learning_goal == "A specific concept"
    assert outcome.clarification.missing_items == ["current_level", "target_scenario"]
    assert outcome.ready_for_board is False


def test_specific_knowledge_point_is_ready_with_all_core_factors() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A specific concept",
        content_is_specific=True,
        current_level="Can describe the basic idea with support",
        target_scenario="Preparing for an upcoming course",
        chatbot_message="The focused board is ready.",
        teaching_plan="Explain the concept through a definition, mechanism, and example.",
        reason="All core factors are resolved.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "generate_board"
    assert outcome.requirement is not None
    assert outcome.requirement.current_level == "Can describe the basic idea with support"
    assert outcome.requirement.target_scenario == "Preparing for an upcoming course"
    assert outcome.clarification.missing_items == []
    assert outcome.ready_for_board is True
    assert outcome.guidance.is_empty()


def test_zero_baseline_knowledge_point_is_ready_without_scenario_question() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A focused foundational concept",
        content_is_specific=True,
        current_level="No prior learning or usable experience in this field",
        zero_baseline_confirmed=True,
        chatbot_message="The foundational board is ready.",
        teaching_plan="Introduce the concept from first principles with one simple example.",
        reason="The learner explicitly confirmed a zero baseline and a focused entry point.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "generate_board"
    assert outcome.requirement is not None
    assert outcome.requirement.target_scenario == "无明确应用场景"
    assert outcome.clarification.missing_items == []
    assert outcome.ready_for_board is True


def test_skill_practice_auxiliary_factors_cannot_replace_missing_core_factors() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="skill_practice",
        learning_content="A specific skill",
        content_is_specific=True,
        auxiliary_factors=[
            BlankBoardAuxiliaryFactor(
                label="preferred_format",
                value="short rounds",
                evidence="Explicitly requested by the learner.",
            )
        ],
        chatbot_message="One question about the learner's current level.",
        next_question="What can you already do independently?",
        reason="Current level and target scenario are still missing.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.clarification.missing_items == ["current_level", "target_scenario"]
    assert outcome.ready_for_board is False


def test_skill_practice_accepts_explicit_no_specific_scenario() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="skill_practice",
        learning_content="A specific skill",
        content_is_specific=True,
        current_level="Can complete the basic form with occasional help",
        target_scenario="无明确应用场景",
        chatbot_message="The practice board is ready.",
        teaching_plan="Create progressive practice with feedback checkpoints.",
        reason="All skill-practice core factors are resolved.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "generate_board"
    assert outcome.requirement is not None
    assert outcome.requirement.target_scenario == "无明确应用场景"
    assert outcome.ready_for_board is True


def test_empty_board_ordinary_chat_is_isolated_from_requirements_and_board_agent(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text=" \n")
    parse_calls: list[dict[str, object]] = []

    def fake_parse(_self, **kwargs):
        parse_calls.append(kwargs)
        if kwargs.get("allow_live_web_search"):
            return SimpleNamespace(
                output_parsed=OrdinaryChatTurnResponse(
                    chatbot_message="A live, network-backed conversational reply."
                )
            )
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="There is no learning request in this turn.",
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("ordinary chat must not enter the board agent")

    class FakePiAdapter:
        def parse_structured(self, **kwargs):
            return fake_parse(self, **kwargs)

    monkeypatch.setattr(
        codex_chat,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakePiAdapter(),
    )
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Just chatting.", conversation=[]),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "A live, network-backed conversational reply."
    assert response.active_requirement_sheet is None
    assert response.board_document_operation_status == "none"
    assert response.learning_clarification.reason == ""
    assert response.learning_clarification.summary == ""
    assert len(parse_calls) == 2
    assert parse_calls[0].get("allow_live_web_search") is not True
    assert parse_calls[1]["allow_live_web_search"] is True
    assert "Current user message:\nJust chatting." in parse_calls[0]["user_prompt"]
    assert "Current user message:\nJust chatting." in parse_calls[1]["user_prompt"]
    assert "board summary" not in parse_calls[1]["user_prompt"].lower()
    assert "active learning requirement" not in parse_calls[1]["user_prompt"].lower()
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text.strip() == ""
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["requirement_changed"] is False
    assert commit.metadata["chatbot_web_search_mode"] == "live"
    assert commit.metadata["chatbot_raw_network_access"] is False
    assert "requirement_version_id" not in commit.metadata
    assert "active_requirement_sheet_after" not in commit.metadata
    assert "learning_clarification_after" not in commit.metadata


def test_empty_board_live_chat_failure_does_not_commit_or_change_requirements(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    before_head = current_head_commit(lesson).id
    parse_calls = 0

    def fake_parse(_self, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        if kwargs.get("allow_live_web_search"):
            raise CodexAppServerError("live web search failed")
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="There is no learning request in this turn.",
            )
        )

    class FakePiAdapter:
        def parse_structured(self, **kwargs):
            return fake_parse(self, **kwargs)

    monkeypatch.setattr(
        codex_chat,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakePiAdapter(),
    )

    with pytest.raises(CodexAppServerError, match="live web search failed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="What is happening right now?"),
            user_id=TEST_USER_ID,
        )

    assert parse_calls == 2
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == ""
    assert blank_board_intake.active_requirement_from_history(saved_lesson) is None


def test_empty_board_requirement_collection_persists_and_ordinary_chat_preserves_it(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    requirement_updates: list[dict[str, object]] = []
    parse_calls: list[dict[str, object]] = []
    decisions = iter(
        [
            BlankBoardTurnDecision(
                intent="learning_need",
                teaching_type="knowledge_point",
                learning_content="A broad field",
                content_is_specific=False,
                chatbot_message="Two contextual directions and one narrowing question.",
                next_question="Which direction should become the first focused concept?",
                reason="The learning content is still too broad.",
            ),
            BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="This turn does not change the learning request.",
            ),
            OrdinaryChatTurnResponse(
                chatbot_message="A separate live conversational reply."
            ),
        ]
    )

    def fake_parse(_self, **kwargs):
        parse_calls.append(kwargs)
        return SimpleNamespace(output_parsed=next(decisions))

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("an incomplete requirement must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    collecting = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="I want to learn a broad field."),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )
    preserved = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Unrelated small talk."),
        user_id=TEST_USER_ID,
    )

    assert collecting.active_requirement_sheet is not None
    assert collecting.active_requirement_sheet.teaching_type == "knowledge_point"
    assert collecting.learning_clarification.missing_items == [
        "learning_content",
        "current_level",
        "target_scenario",
    ]
    assert collecting.requirement_version_id is not None
    assert [update["requirement_phase"] for update in requirement_updates] == [
        "collecting"
    ]
    assert preserved.active_requirement_sheet == collecting.active_requirement_sheet
    assert preserved.chatbot_message == "A separate live conversational reply."
    assert preserved.learning_clarification == collecting.learning_clarification
    assert preserved.requirement_phase == "collecting"
    assert preserved.requirement_version_id is None
    assert parse_calls[2]["allow_live_web_search"] is True
    assert "A broad field" not in parse_calls[2]["user_prompt"]
    assert "Active learning requirement" not in parse_calls[2]["user_prompt"]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text == ""
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["requirement_changed"] is False
    assert commit.metadata["requirement_version_id"] is None
    assert commit.metadata["requirement_phase"] == "collecting"
    assert commit.metadata["active_requirement_sheet_after"] == (
        collecting.active_requirement_sheet.model_dump(mode="json")
    )
    assert commit.metadata["learning_clarification_after"] == (
        collecting.learning_clarification.model_dump(mode="json")
    )
    restored = blank_board_intake.active_requirement_from_history(saved_lesson)
    assert restored is not None
    assert restored.learning_content == "A broad field"


def test_empty_board_unclear_guidance_is_saved_as_displayable_basic_chat(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="unclear",
                chatbot_message="Contextual directions with one choice.",
                next_question="Which direction fits what you want right now?",
                reason="The learning purpose is not confirmed yet.",
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("unclear intent must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Maybe I should learn something useful."),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert response.active_requirement_sheet is None
    assert response.needs_clarification is True
    assert response.clarification_questions == [
        "Which direction fits what you want right now?"
    ]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["blank_board_route"] == "guided_discovery"
    assert "active_requirement_sheet_after" not in commit.metadata


def test_empty_board_untyped_learning_guidance_persists_confirmed_theme(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    requirement_updates: list[dict[str, object]] = []

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="learning_need",
                teaching_type=None,
                learning_content="A broad learning theme",
                chatbot_message="Here are two ways to begin, with one recommended first step.",
                next_question="Which starting direction feels closest to where you are now?",
                reason="The theme is clear but the learning mode is not yet resolved.",
                guidance=GuidedRequirementDiscovery(
                    strategy="level_discovery",
                    selection_target="current_level",
                    question_title="Which starting level is closest to you?",
                    learning_map_summary="A short map of the available starting directions.",
                    entry_point_options=[
                        GuidedRequirementEntryPoint(
                            title="Build a first foundation",
                            description="Establish one dependable entry point.",
                            answer_value="New to the prerequisites",
                            why_it_matters="The first board should establish the prerequisites.",
                            best_for="A learner without a dependable starting point.",
                        ),
                        GuidedRequirementEntryPoint(
                            title="Start from a concrete outcome",
                            description="Choose the entry point through a task you want to complete.",
                            answer_value="Can complete a related task with support",
                            why_it_matters="A task can expose the next useful gap.",
                            best_for="A learner with some practical exposure.",
                        ),
                    ],
                    recommended_entry_point="Build a first foundation",
                    reason_for_recommendation="No level or target outcome is confirmed yet.",
                ),
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("untyped learning guidance must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="I want to learn a broad learning theme."),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.teaching_type is None
    assert response.active_requirement_sheet.work_mode == "unknown"
    assert response.requirement_phase == "collecting"
    assert response.requirement_version_id is not None
    assert [update["requirement_phase"] for update in requirement_updates] == ["collecting"]
    assert response.guided_requirement_discovery is not None
    assert response.guided_requirement_discovery.recommended_entry_point == "Build a first foundation"
    assert requirement_updates[0]["guided_requirement_discovery"] == {
        "strategy": "level_discovery",
        "selection_target": "current_level",
        "question_title": "Which starting level is closest to you?",
        "learning_map_summary": "A short map of the available starting directions.",
        "entry_point_options": [
            {
                "title": "Build a first foundation",
                "description": "Establish one dependable entry point.",
                "answer_value": "New to the prerequisites",
                "why_it_matters": "The first board should establish the prerequisites.",
                "best_for": "A learner without a dependable starting point.",
            },
            {
                "title": "Start from a concrete outcome",
                "description": "Choose the entry point through a task you want to complete.",
                "answer_value": "Can complete a related task with support",
                "why_it_matters": "A task can expose the next useful gap.",
                "best_for": "A learner with some practical exposure.",
            },
        ],
        "recommended_entry_point": "Build a first foundation",
        "reason_for_recommendation": "No level or target outcome is confirmed yet.",
        "learner_profile_inference": "",
    }
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["blank_board_route"] == "guided_discovery"
    assert commit.metadata["requirement_phase"] == "collecting"
    assert commit.metadata["guided_requirement_discovery"]["recommended_entry_point"] == (
        "Build a first foundation"
    )


def test_complete_empty_board_requirement_is_frozen_before_board_generation(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    generation_prompts: list[str] = []
    requirement_updates: list[dict[str, object]] = []

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A specific concept",
                    content_is_specific=True,
                    current_level="Can describe the basic idea with support",
                    target_scenario="Preparing for an upcoming course",
                    chatbot_message="The board has been prepared.",
                    teaching_plan="Build a focused explanation with checks for understanding.",
                    reason="All core factors are complete.",
            )
        )

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        generation_prompts.append(kwargs["user_prompt"])
        assert [update["requirement_phase"] for update in requirement_updates] == [
            "ready",
            "frozen",
        ]
        frozen_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
        frozen_commit = current_head_commit(frozen_lesson)
        assert frozen_commit.metadata["kind"] == "learning_requirement_frozen"
        assert frozen_commit.metadata["requirement_phase"] == "frozen"
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Focused board\n\nBoard content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_frozen_generation",
            turn_id="turn_frozen_generation",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Start now, but this exact request text is not board input.",
            conversation=[],
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )

    assert response.board_document_operation_status == "succeeded"
    assert response.requirement_phase == "consumed"
    assert response.active_requirement_sheet is None
    assert [update["requirement_phase"] for update in requirement_updates] == [
        "ready",
        "frozen",
    ]
    assert len(generation_prompts) == 1
    assert '"learning_content":"A specific concept"' in generation_prompts[0]
    assert '"teaching_plan":"Build a focused explanation with checks for understanding."' in generation_prompts[0]
    assert "this exact request text" not in generation_prompts[0]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    generation_commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text == "# Focused board\n\nBoard content."
    assert generation_commit.metadata["kind"] == "board_document_generation"
    assert generation_commit.metadata["requirement_phase"] == "consumed"
    assert generation_commit.metadata["board_generation_codex_thread_id"] == (
        "thread_frozen_generation"
    )
    assert "codex_thread_id" not in generation_commit.metadata
    assert codex_chat._thread_reference_for_current_branch(saved_lesson) == (None, None)
    assert blank_board_intake._latest_requirement_run_id(saved_lesson) is None
    frozen_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_frozen"
    )
    assert generation_commit.parent_ids == [frozen_commit.id]
    ready_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_completed"
    )
    assert frozen_commit.parent_ids == [ready_commit.id]
    assert ready_commit.metadata["requirement_phase"] == "ready"
    assert frozen_commit.metadata["requirement_parent_version_id"] == (
        ready_commit.metadata["requirement_version_id"]
    )


def test_pi_board_handoff_runs_only_after_the_document_commit(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    observed_board_commit_id = ""

    class FakePiAdapter:
        def parse_structured(self, **kwargs):
            assert kwargs["schema"] is BlankBoardTurnDecision
            return SimpleNamespace(
                output_parsed=BlankBoardTurnDecision(
                    intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A bounded topic",
                    content_is_specific=True,
                    current_level="Known level",
                    target_scenario="Known purpose",
                    chatbot_message="The requirement is ready.",
                    teaching_plan="Build a focused board.",
                    reason="All core factors are complete.",
                ),
                activity=[],
            )

        def generate_board(self, _request, **_kwargs):
            return (
                ai_execution_adapter.StructuredBoardGenerationResult(
                    thread_id="piturn_board",
                    turn_id="piturn_board",
                    final_response="",
                    activity=[],
                ),
                "# Saved board\n\nGenerated learning content.",
            )

        def complete_text(self, **kwargs):
            nonlocal observed_board_commit_id
            saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
            board_commit = current_head_commit(saved_lesson)
            observed_board_commit_id = board_commit.id
            assert saved_lesson.board_document.content_text.startswith("# Saved board")
            assert board_commit.metadata["kind"] == "board_document_generation"
            assert board_commit.metadata["assistant_message"] == ""
            assert kwargs["is_cancelled"] is not None
            return SimpleNamespace(
                output_text="The saved board is ready for us to work through.",
                activity=[],
            )

    monkeypatch.setattr(
        codex_chat,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakePiAdapter(),
    )
    monkeypatch.setattr(
        codex_chat,
        "_text_model_selection",
        lambda *_args, **_kwargs: AIModelSelection(
            agent_backend="pi",
            provider="openai_codex",
            model="gpt-5.5",
        ),
    )
    monkeypatch.setattr(
        blank_board_intake,
        "generate_follow_up_suggestions",
        lambda **_kwargs: [],
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Generate the learning board now.",
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
        is_cancelled=lambda: False,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    handoff_commit = current_head_commit(saved_lesson)
    board_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.id == observed_board_commit_id
    )
    assert response.chatbot_message == "The saved board is ready for us to work through."
    assert response.board_document_operation_status == "succeeded"
    assert saved_lesson.board_document.content_text.startswith("# Saved board")
    assert handoff_commit.metadata["kind"] == "board_generation_handoff"
    assert handoff_commit.metadata["user_message"] == ""
    assert handoff_commit.metadata["document_changed"] is False
    assert handoff_commit.parent_ids == [board_commit.id]


def test_source_chapter_selection_generates_blank_board_without_requirement_questions(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    package_id = codex_store.load_for_user(TEST_USER_ID).packages[0].id
    source_path = tmp_path / "source.md"
    source_path.write_text(
        "# Chapter One\n\n"
        "The selected source explains a durable concept and its key relationship.\n\n"
        "## Detail\n\n"
        "This supporting paragraph must be present in the frozen source evidence.",
        encoding="utf-8",
    )
    source = SourceIngestionRecord(
        id="source_direct_generation",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Selected source",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="text/markdown",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_evidence_store.save_source(source)
    SourceStructureIndexer(store=source_structure_store).rebuild_structure(source)
    chapter = source_structure_store.get_structure_view(source=source).chapters[0]
    visual_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    visual = SourceVisualEvidence(
        visual_id="sourcevisual_selected",
        package_id=package_id,
        source_ingestion_id=source.id,
        source_chapter_id=chapter.id,
        kind="diagram",
        source_locator="source:visual:1",
        caption="Selected diagram",
        anchor_status="verified",
        mime_type="image/png",
        content_hash=hashlib.sha256(visual_bytes).hexdigest(),
        position_hash="position_selected",
        confidence=0.9,
    )
    stored_visual = SourceVisualAsset(
        id=visual.visual_id,
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        source_ingestion_id=source.id,
        chapter_id=chapter.id,
        kind="diagram",
        source_locator=visual.source_locator,
        anchor_status="verified",
        mime_type="image/png",
        content_hash=visual.content_hash,
        position_hash=visual.position_hash,
        confidence=0.9,
    )
    monkeypatch.setattr(
        source_structure_store,
        "visual_evidence_for_scope",
        lambda **_kwargs: [visual],
    )
    monkeypatch.setattr(
        source_structure_store,
        "read_visual_bytes",
        lambda **_kwargs: (stored_visual, visual_bytes),
    )

    def fail_if_intake_runs(**_kwargs):
        raise AssertionError("a verified source selection must bypass requirement questions")

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        prompt = kwargs["user_prompt"]
        assert '"granularity":"source_chapter"' in prompt
        assert "The selected source explains a durable concept" in prompt
        assert "为我讲解" not in prompt
        assert kwargs["image_urls"] and kwargs["image_urls"][0].startswith("data:image/png;base64,")
        payload = json.loads(prompt.split("\n", 1)[1])
        marker = payload["visual_manifest"][0]["marker"]
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text(
            f"# Source-grounded board\n\nGrounded content.\n\n{marker}",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="thread_source_generation",
            turn_id="turn_source_generation",
            final_response="Generated from the selected source.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fail_if_intake_runs)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)
    board_asset_store = BoardAssetStore(
        tmp_path / "board-assets.sqlite3",
        tmp_path / "board-assets",
    )
    monkeypatch.setattr(
        board_visual_insertion,
        "get_board_asset_store",
        lambda: board_asset_store,
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="为我讲解",
            selection=SelectionRef(
                kind="source",
                excerpt="Selected source · Chapter One",
                source_ingestion_id=source.id,
                source_title=source.title,
                source_chapter_id=chapter.id,
                source_chapter_number=chapter.number,
                source_chapter_title=chapter.title,
                source_locator=chapter.source_locator,
                source_page_start=chapter.page_start,
                source_page_end=chapter.page_end,
                heading_path=chapter.path,
            ),
        ),
        user_id=TEST_USER_ID,
    )

    assert response.requirement_phase == "consumed"
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text.startswith(
        "# Source-grounded board\n\nGrounded content."
    )
    assert "OPENCLASS_VISUAL" not in saved_lesson.board_document.content_text
    generation_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "board_document_generation"
    )
    assert generation_commit.metadata["board_visual_applied_ids"] == [
        visual.visual_id
    ], generation_commit.metadata["skipped_visual_placements"]
    node_types = [
        node["type"] for node in saved_lesson.board_document.content_json["content"]
    ]
    assert node_types == ["heading", "paragraph", "resourceVisualBlock"], node_types
    frozen_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_frozen"
    )
    frozen_requirement = frozen_commit.metadata["frozen_requirement_payload"]
    assert frozen_requirement["granularity"] == "source_chapter"
    assert frozen_requirement["source_grounding"]["confirmation_status"] == "confirmed"
    assert frozen_requirement["source_grounding"]["frozen_evidence"][0]["chapter_id"] == chapter.id
    assert frozen_requirement["source_grounding"]["frozen_visual_evidence"][0]["visual_id"] == visual.visual_id
    bundle_id = frozen_requirement["source_grounding"]["confirmed_bundle_id"]
    saved_bundle = source_evidence_store.get_bundle(owner_user_id=TEST_USER_ID, bundle_id=bundle_id)
    assert saved_bundle is not None
    assert saved_bundle.status == "confirmed"
    assert saved_bundle.visual_items[0].visual_id == visual.visual_id
    assert generation_commit.metadata["board_visual_requested_count"] == 1
    assert generation_commit.metadata["skipped_visual_placements"] == []


def test_existing_board_source_selection_is_frozen_and_mandatory_for_codex(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(
        codex_store,
        content_text="# Existing board\n\n## Existing section\n\nExisting material.",
    )
    package_id = codex_store.load_for_user(TEST_USER_ID).packages[0].id
    source_path = tmp_path / "continued-source.md"
    source_path.write_text(
        "# Referenced chapter\n\n"
        "MANDATORY_SOURCE_FACT connects the selected chapter to its worked example.",
        encoding="utf-8",
    )
    source = SourceIngestionRecord(
        id="source_existing_board_reference",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Continued source",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="text/markdown",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_evidence_store.save_source(source)
    SourceStructureIndexer(store=source_structure_store).rebuild_structure(source)
    chapter = source_structure_store.get_structure_view(source=source).chapters[0]
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        prompt = kwargs["user_prompt"]
        assert "Verified source context (mandatory for this turn)" in prompt
        assert "MANDATORY_SOURCE_FACT" in prompt
        assert "forged visible chip text" not in prompt
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        current = board_path.read_text(encoding="utf-8")
        assert current.startswith("# Existing board")
        assert "## Existing section" in current
        board_path.write_text(
            current
            + "\n\n## Referenced continuation\n\n"
            + "MANDATORY_SOURCE_FACT is now grounded in the board.\n",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="thread_existing_source",
            turn_id="turn_existing_source",
            final_response="Updated from the verified reference.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Continue the board from my reference.",
            selection=SelectionRef(
                kind="source",
                excerpt="forged visible chip text",
                source_ingestion_id=source.id,
                source_title=source.title,
                source_chapter_id=chapter.id,
                source_chapter_number=chapter.number,
                source_chapter_title=chapter.title,
                source_locator=chapter.source_locator,
                source_page_start=chapter.page_start,
                source_page_end=chapter.page_end,
                heading_path=chapter.path,
            ),
        ),
        user_id=TEST_USER_ID,
    )

    assert len(calls) == 1
    document = response.course_package.lessons[0].board_document
    node_types = [node["type"] for node in document.content_json["content"]]
    assert node_types.count("heading") == 3
    assert "MANDATORY_SOURCE_FACT" in document.content_text
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["verified_source_reference_used"] is True
    assert commit.metadata["verified_source_bundle_ids"]
    assert commit.metadata["verified_source_chapter_ids"] == [chapter.id]
    assert commit.metadata["verified_source_evidence_ids"]


def test_generation_auto_explains_first_section_then_continues_or_restarts(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    directive_prompts: list[str] = []
    chatbot_prompts: list[str] = []
    explanation_messages = iter(
        ["First section explanation.", "Second section explanation.", "First section again."]
    )

    def fake_parse(_self, **kwargs):
        schema = kwargs["schema"]
        if schema is BlankBoardTurnDecision:
            parsed = BlankBoardTurnDecision(
                intent="learning_need",
                teaching_type="knowledge_point",
                learning_content="A bounded topic",
                content_is_specific=True,
                current_level="Known level",
                target_scenario="Known purpose",
                chatbot_message="Board generation is ready.",
                teaching_plan="Create a structured board.",
                reason="The requirement is complete.",
            )
        elif schema is BoardExplanationDirective:
            directive_prompts.append(kwargs["user_prompt"])
            excerpt = "## Second\n\nSecond evidence." if "## Second" in kwargs["user_prompt"] else "## First\n\nFirst evidence."
            parsed = BoardExplanationDirective(
                status="approved",
                target_summary="Current section",
                target_excerpt=excerpt,
                teaching_instruction="Explain this section in order.",
                constraints=["Use only the target excerpt."],
            )
        else:
            chatbot_prompts.append(kwargs["user_prompt"])
            parsed = schema(chatbot_message=next(explanation_messages))
        return SimpleNamespace(output_parsed=parsed, activity=[])

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text(
            "# Generated board\n\n## First\n\nFirst evidence.\n\n## Second\n\nSecond evidence.",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="thread_auto_teaching",
            turn_id="turn_auto_teaching",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    generated = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Generate and teach this topic."),
        user_id=TEST_USER_ID,
    )

    assert generated.chatbot_message == "First section explanation."
    assert generated.auto_teaching_operation_status == "succeeded"
    assert generated.teaching_progress is not None
    assert generated.teaching_progress.section_index == 0
    assert generated.teaching_progress.has_next_section is True
    assert "Second evidence." not in directive_prompts[0]
    assert "Second evidence." not in chatbot_prompts[0]

    continued = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Continue.", teaching_action="continue"),
        user_id=TEST_USER_ID,
    )
    assert continued.chatbot_message == "Second section explanation."
    assert continued.teaching_progress is not None
    assert continued.teaching_progress.section_index == 1
    assert continued.teaching_progress.has_next_section is False

    restarted = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Start again.", teaching_action="restart"),
        user_id=TEST_USER_ID,
    )
    assert restarted.chatbot_message == "First section again."
    assert restarted.teaching_progress is not None
    assert restarted.teaching_progress.section_index == 0

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit_kinds = [commit.metadata.get("kind") for commit in saved_lesson.history_graph.commits]
    assert "board_document_generation" in commit_kinds
    assert commit_kinds.count("board_task_requirement_ready") == 3
    assert commit_kinds.count("board_directed_explanation") == 3
    explanation_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "board_directed_explanation"
    )
    assert explanation_commit.metadata["board_task_route"] == "explain"
    assert explanation_commit.metadata["board_task_phase"] == "consumed"
    assert explanation_commit.metadata["board_task_cleared"] is True


def test_auto_explanation_failure_preserves_generated_board(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")

    def fake_parse(_self, **kwargs):
        if kwargs["schema"] is BlankBoardTurnDecision:
            return SimpleNamespace(
                output_parsed=BlankBoardTurnDecision(
                    intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A bounded topic",
                    content_is_specific=True,
                    current_level="Known level",
                    target_scenario="Known purpose",
                    chatbot_message="Board generation is ready.",
                    teaching_plan="Create a structured board.",
                    reason="The requirement is complete.",
                ),
                activity=[],
            )
        raise CodexAppServerError("directive unavailable")

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Generated board\n\n## First\n\nPreserved.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_auto_teaching_failure",
            turn_id="turn_auto_teaching_failure",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Generate and teach this topic."),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert response.auto_teaching_operation_status == "failed"
    assert response.auto_teaching_operation_failure_reason == "directive unavailable"
    assert saved_lesson.board_document.content_text.endswith("Preserved.")
    assert current_head_commit(saved_lesson).metadata["kind"] == "auto_explain_failed"
    assert saved_lesson.board_task_requirements is None


def test_source_page_range_selection_generates_from_only_that_range(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    package_id = codex_store.load_for_user(TEST_USER_ID).packages[0].id
    source_path = tmp_path / "pages.txt"
    source_path.write_text("page four evidence", encoding="utf-8")
    source = SourceIngestionRecord(
        id="source_page_range",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Paged source",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="text/plain",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_evidence_store.save_source(source)
    source_structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=TEST_USER_ID,
            package_id=package_id,
            source_ingestion_id=source.id,
            status="linear_only",
            metadata={
                "structure_index_version": CURRENT_SOURCE_STRUCTURE_INDEX_VERSION,
            },
        ),
        chapters=[],
        chunks=[
            SourceChunk(
                owner_user_id=TEST_USER_ID,
                package_id=package_id,
                source_ingestion_id=source.id,
                text="page four evidence",
                start_offset=0,
                end_offset=18,
                page_start=4,
                page_end=5,
                token_count=5,
            )
        ],
    )

    def fail_if_intake_runs(**_kwargs):
        raise AssertionError("a page-range source selection must bypass requirement questions")

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        assert '"granularity":"source_range"' in kwargs["user_prompt"]
        assert "page four evidence" in kwargs["user_prompt"]
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Page range board\n\nGrounded content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_page_range",
            turn_id="turn_page_range",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fail_if_intake_runs)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="生成板书",
            selection=SelectionRef(
                kind="source",
                excerpt="Paged source · pp. 4-4",
                source_ingestion_id=source.id,
                source_title=source.title,
                source_scope_kind="page_range",
                source_page_start=4,
                source_page_end=5,
            ),
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
    )

    assert response.requirement_phase == "consumed"
    assert response.course_package.lessons[0].board_document.content_text.startswith("# Page range board")


def test_empty_scanned_pdf_chapter_uses_on_demand_ocr_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    package_id = codex_store.load_for_user(TEST_USER_ID).packages[0].id
    source_path = tmp_path / "scanned.pdf"
    source_path.write_bytes(b"scanned-pdf-placeholder")
    source = SourceIngestionRecord(
        id="source_scanned_pdf",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Scanned reference",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="application/pdf",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    chapter = SourceChapter(
        id="sourcechapter_scanned",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        source_ingestion_id=source.id,
        number="3.1",
        normalized_number="3.1",
        title="3.1 Selected section",
        path=["Chapter 3", "3.1 Selected section"],
        source_locator="pdf:toc-page:11:printed:53",
        body_start_offset=807,
        body_end_offset=807,
        page_start=69,
        page_end=70,
        anchor_status="verified",
        confidence=0.87,
    )
    following_chapter = SourceChapter(
        id="sourcechapter_scanned_next",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        source_ingestion_id=source.id,
        number="3.2",
        normalized_number="3.2",
        title="3.2 Following section",
        path=["Chapter 3", "3.2 Following section"],
        order_index=1,
        source_locator="pdf:toc-page:11:printed:54",
        body_start_offset=807,
        body_end_offset=900,
        page_start=69,
        page_end=72,
        anchor_status="verified",
        confidence=0.87,
    )
    source_evidence_store.save_source(source)
    source_structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=TEST_USER_ID,
            package_id=package_id,
            source_ingestion_id=source.id,
            status="ready",
            strategy="pdf_merged_toc",
            metadata={
                "structure_index_version": CURRENT_SOURCE_STRUCTURE_INDEX_VERSION,
                "visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            },
        ),
        chapters=[chapter, following_chapter],
        chunks=[
            SourceChunk(
                owner_user_id=TEST_USER_ID,
                package_id=package_id,
                source_ingestion_id=source.id,
                chapter_id=chapter.id,
                text="[Page 69]",
                start_offset=797,
                end_offset=807,
                page_start=69,
                page_end=70,
                token_count=3,
            )
        ],
    )
    ocr_calls: list[dict[str, object]] = []

    def fake_ocr(_path: Path, **kwargs) -> str:
        ocr_calls.append(kwargs)
        return (
            "Chapter 3\n"
            "3.1 Selected section\n"
            "Recovered chapter body with concepts, relationships, and supporting evidence.\n"
            "supporting evidence.\n"
            "3.2 Following section\n"
            "This next section must not enter the selected evidence."
        )

    monkeypatch.setattr(source_scope_ocr, "source_local_path", lambda _source: source_path)
    monkeypatch.setattr(source_scope_ocr, "extract_pdf_pages_text", fake_ocr)
    monkeypatch.setattr(
        source_structure_store,
        "visual_evidence_for_scope",
        lambda **_kwargs: [],
    )

    def fail_if_intake_runs(**_kwargs):
        raise AssertionError("recovered source evidence must bypass requirement questions")

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        assert "Recovered chapter body" in kwargs["user_prompt"]
        assert "This next section must not enter" not in kwargs["user_prompt"]
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# OCR-grounded board\n\nRecovered content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_scanned_pdf",
            turn_id="turn_scanned_pdf",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fail_if_intake_runs)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Generate from this selected chapter.",
            selection=SelectionRef(
                kind="source",
                excerpt="Scanned reference · 3.1",
                source_ingestion_id=source.id,
                source_title=source.title,
                source_chapter_id=chapter.id,
                source_chapter_number=chapter.number,
                source_chapter_title=chapter.title,
                source_locator=chapter.source_locator,
                source_page_start=chapter.page_start,
                source_page_end=chapter.page_end,
                heading_path=chapter.path,
            ),
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
    )

    assert response.requirement_phase == "consumed"
    assert ocr_calls == [{"page_start": 69, "page_end": 69, "max_pages": 1}]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    frozen_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_frozen"
    )
    frozen_evidence = frozen_commit.metadata["frozen_requirement_payload"]["source_grounding"][
        "frozen_evidence"
    ]
    assert frozen_evidence[0]["metadata"]["retrieval_mode"] == "on_demand_pdf_ocr"


def test_source_text_quality_rejects_page_markers_without_rejecting_real_text() -> None:
    assert source_scope_ocr.has_usable_source_text("[Page 69]\n[Page 70]") is False
    assert source_scope_ocr.has_usable_source_text("page four evidence") is True


def test_legacy_pdf_source_defers_index_upgrade_during_chat_reference(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    package_id = codex_store.load_for_user(TEST_USER_ID).packages[0].id
    source_path = tmp_path / "legacy.pdf"
    source_path.write_bytes(b"legacy-pdf")
    source = SourceIngestionRecord(
        id="legacy_pdf_source",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Legacy PDF",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="application/pdf",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_evidence_store.save_source(source)
    source_structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=TEST_USER_ID,
            package_id=package_id,
            source_ingestion_id=source.id,
            status="ready",
            metadata={},
        ),
        chapters=[],
        chunks=[],
    )
    rebuild_calls: list[str] = []

    def fake_rebuild(_self, record):
        rebuild_calls.append(record.id)
        current = source_structure_store.get_structure(
            owner_user_id=TEST_USER_ID,
            package_id=package_id,
            source_id=source.id,
        )
        assert current is not None
        return source_structure_store.save_structure_bundle(
            structure=current.model_copy(
                update={"status": "linear_only", "metadata": {"visual_index_version": 1}}
            ),
            chapters=[],
            chunks=[],
        )

    monkeypatch.setattr(SourceStructureIndexer, "rebuild_structure", fake_rebuild)
    monkeypatch.setattr(
        blank_board_intake.CodexAppServerTextClient,
        "parse",
        lambda _self, **kwargs: SimpleNamespace(
            output_parsed=kwargs["schema"](chatbot_message="Select a verified range."),
            activity=[],
        ),
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Generate from pages.",
            selection=SelectionRef(
                kind="source",
                excerpt="Legacy PDF pages 1-2",
                source_ingestion_id=source.id,
                source_scope_kind="page_range",
                source_page_start=1,
                source_page_end=3,
            ),
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
    )

    assert rebuild_calls == []
    assert response.board_document_operation_status == "none"


def test_source_generation_batches_all_text_and_visual_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chunk_one = SourceChunk(
        id="chunk_one",
        package_id="package",
        source_ingestion_id="source",
        text="a" * 120_000,
        token_count=30_000,
    )
    chunk_two = SourceChunk(
        id="chunk_two",
        package_id="package",
        source_ingestion_id="source",
        text="b" * 120_000,
        token_count=30_000,
    )
    visuals = [
        SourceVisualEvidence(
            visual_id=f"visual_{index}",
            source_ingestion_id="source",
            kind="diagram",
            mime_type="image/png",
        )
        for index in range(9)
    ]
    visual_bytes = b"\x89PNG\r\n\x1a\nsource-visual"
    for visual in visuals:
        visual.package_id = "package"
        visual.anchor_status = "verified"
        visual.content_hash = hashlib.sha256(visual_bytes).hexdigest()
        visual.position_hash = f"position_{visual.visual_id}"
    requirement = build_requirements("Source batch test")
    requirement.source_grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id="bundle",
        confirmed_references=[
            LearningSourceReference(
                evidence_bundle_id="bundle",
                source_ingestion_id="source",
                chunk_ids=[chunk_one.id, chunk_two.id],
                visual_ids=[item.visual_id for item in visuals],
            )
        ],
        frozen_evidence=[
            RetrievalEvidence(
                id="full_evidence",
                source_ingestion_id="source",
                source_title="Source",
                chunk_ids=[chunk_one.id, chunk_two.id],
                expanded_text="full frozen source",
                token_count=60_000,
            )
        ],
        frozen_visual_evidence=visuals,
    )
    monkeypatch.setattr(
        source_structure_store,
        "source_chunks_by_ids",
        lambda **_kwargs: [chunk_one, chunk_two],
    )
    monkeypatch.setattr(
        source_structure_store,
        "read_visual_bytes",
        lambda **kwargs: (
            SourceVisualAsset(
                id=kwargs["visual_id"],
                owner_user_id=TEST_USER_ID,
                package_id="package",
                source_ingestion_id="source",
                kind="diagram",
                anchor_status="verified",
                mime_type="image/png",
                content_hash=hashlib.sha256(visual_bytes).hexdigest(),
                position_hash=f"position_{kwargs['visual_id']}",
            ),
            visual_bytes,
        ),
    )

    class FakeAdapter:
        def __init__(self) -> None:
            self.summary_batches: list[list[str]] = []
            self.visual_batch_sizes: list[int] = []

        def parse_structured(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            chunk_ids = [item["chunk_id"] for item in payload["chunks"]]
            self.summary_batches.append(chunk_ids)
            return SimpleNamespace(
                output_parsed=kwargs["schema"](summary=f"Summary for {','.join(chunk_ids)}"),
                activity=[],
            )

        def analyze_image_batch(self, **kwargs):
            self.visual_batch_sizes.append(len(kwargs["image_inputs"]))
            return f"Analysis batch {len(self.visual_batch_sizes)}"

    adapter = FakeAdapter()
    prepared, image_inputs = codex_chat._prepare_source_generation_inputs(
        adapter=adapter,
        requirement=requirement,
        owner_user_id=TEST_USER_ID,
        is_cancelled=None,
        on_activity=None,
    )

    assert adapter.summary_batches == [[chunk_one.id], [chunk_two.id]]
    assert [item.chunk_ids for item in prepared.source_grounding.frozen_evidence] == [
        [chunk_one.id],
        [chunk_two.id],
    ]
    assert adapter.visual_batch_sizes == [8, 1]
    assert len(prepared.source_grounding.frozen_visual_evidence) == 9
    assert all(item.extracted_text.startswith("Analysis batch") for item in prepared.source_grounding.frozen_visual_evidence)
    assert image_inputs == []


def test_invalid_source_selection_returns_source_error_without_running_intake(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")

    def answer_source_resolution(_self, **kwargs):
        assert kwargs["schema"] is SourceResolutionTurnResponse
        assert "可验证的章节位置" in kwargs["user_prompt"]
        return SimpleNamespace(
            output_parsed=SourceResolutionTurnResponse(
                chatbot_message="请从这份资料的目录中重新选择一个明确章节。"
            ),
            activity=[],
        )

    monkeypatch.setattr(
        blank_board_intake.CodexAppServerTextClient,
        "parse",
        answer_source_resolution,
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="为我讲解",
            selection=SelectionRef(
                kind="source",
                excerpt="Selected source without a verifiable chapter identity",
            ),
        ),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == (
        "请从这份资料的目录中重新选择一个明确章节。"
    )
    assert response.active_requirement_sheet is None
    assert response.learning_clarification.progress == 0
    assert response.learning_clarification.reason == ""
    assert response.board_document_operation_status == "none"


def test_failed_empty_board_generation_keeps_frozen_requirement_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    decisions = iter(
        [
            BlankBoardTurnDecision(
                intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A specific concept",
                    content_is_specific=True,
                    current_level="Can describe the basic idea with support",
                    target_scenario="Preparing for an upcoming course",
                    chatbot_message="Preparing the board.",
                    teaching_plan="Create a focused board.",
                reason="The requirement is complete.",
            ),
            BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="This turn is unrelated to the frozen requirement.",
            ),
            OrdinaryChatTurnResponse(
                chatbot_message="A separate live conversational reply."
            ),
            BlankBoardTurnDecision(
                intent="learning_need",
                requested_action="generate_board",
                chatbot_message="Retry the frozen board generation.",
                reason="The learner explicitly asked to continue the frozen board task.",
            ),
        ]
    )

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(output_parsed=next(decisions))

    generation_calls = 0
    generation_timeouts: list[float] = []

    def generate_with_one_failure(**kwargs):
        nonlocal generation_calls
        generation_calls += 1
        generation_timeouts.append(kwargs["timeout_seconds"])
        if generation_calls == 1:
            raise CodexAppServerError("generation failed")
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Retried board\n\nRecovered content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_retry_same_frozen_version",
            turn_id="turn_retry_same_frozen_version",
            final_response="The board is ready after the retry.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", generate_with_one_failure)

    with pytest.raises(CodexAppServerError, match="generation failed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Generate the focused board."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    failure_commit = current_head_commit(saved_lesson)
    failed_version_id = failure_commit.metadata["requirement_version_id"]
    restored = blank_board_intake.active_requirement_from_history(saved_lesson)
    assert saved_lesson.board_document.content_text == ""
    assert failure_commit.metadata["kind"] == "learning_requirement_generation_failed"
    assert failure_commit.metadata["requirement_phase"] == "frozen"
    assert restored is not None
    assert restored.learning_content == "A specific concept"

    preserved = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Unrelated small talk after the failure."),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ordinary_commit = current_head_commit(saved_lesson)
    assert preserved.requirement_phase == "frozen"
    assert preserved.active_requirement_sheet is not None
    assert preserved.active_requirement_sheet.learning_content == "A specific concept"
    assert ordinary_commit.metadata["kind"] == "basic_chat"
    assert ordinary_commit.metadata["requirement_phase"] == "frozen"
    assert ordinary_commit.metadata["requirement_version_id"] is None
    assert ordinary_commit.metadata["active_requirement_version_id"] == failed_version_id
    assert ordinary_commit.metadata["active_requirement_sheet_after"] == (
        failure_commit.metadata["active_requirement_sheet_after"]
    )
    assert ordinary_commit.metadata["learning_clarification_after"] == (
        failure_commit.metadata["learning_clarification_after"]
    )

    retried = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Start board generation again.",
            post_generation_action="stop_after_generation",
        ),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    retry_commit = current_head_commit(saved_lesson)
    assert generation_calls == 2
    assert generation_timeouts == [
        codex_chat.CODEX_BOARD_GENERATION_TIMEOUT_SECONDS,
        codex_chat.CODEX_BOARD_GENERATION_TIMEOUT_SECONDS,
    ]
    assert retried.chatbot_message == "The board is ready after the retry."
    assert retry_commit.metadata["kind"] == "board_document_generation"
    assert retry_commit.metadata["requirement_retry"] is True
    assert retry_commit.metadata["requirement_version_id"] == failed_version_id
    assert saved_lesson.board_document.content_text == (
        "# Retried board\n\nRecovered content."
    )


def test_codex_instructions_separate_blank_intake_from_board_grounded_teaching() -> None:
    instructions = codex_chat.CODEX_DEVELOPER_INSTRUCTIONS
    normalized_instructions = " ".join(instructions.split())
    intake = blank_board_intake.BLANK_BOARD_INTAKE_INSTRUCTIONS
    normalized_intake = " ".join(intake.split())
    generation = codex_chat.BOARD_GENERATION_DEVELOPER_INSTRUCTIONS
    normalized_generation = " ".join(generation.split())

    assert "start of every turn, read the current `board.md`" in instructions
    assert "additional mandatory source of truth" in instructions
    assert "Never ignore a `Verified source context`" in instructions
    assert "For a non-empty board" in instructions
    assert "fully answer the learner's question in the left conversation panel first" in normalized_instructions
    assert "naturally ask whether the learner wants the newly explained content written" in normalized_instructions
    assert "Do not edit merely because the answer concerns teaching material" in normalized_instructions
    assert "policy is `confirm_offered_write`" in normalized_instructions
    assert "use fenced code blocks only for executable or source code" in normalized_instructions
    assert "Write display formulas as `$$` on their own lines with LaTeX inside" in instructions
    assert "do not create or change the learning requirement sheet" in normalized_intake
    assert "Select exactly one teaching type before generation" in normalized_intake
    assert "they never compensate for a missing core factor" in normalized_intake
    assert "This intake role never writes `board.md` itself" in normalized_intake
    assert "Resolve exactly one blocking uncertainty per turn" in normalized_intake
    assert "selection_target=\"current_level\"" in normalized_intake
    assert "first option must always represent a true zero-baseline" in normalized_intake
    assert "Order every remaining option from lower to higher capability" in normalized_intake
    assert "recommend that first zero-baseline option" in normalized_intake
    assert "Do not infer `current_level` from age, education" in normalized_intake
    assert "choose one concrete, beginner-safe entry point yourself" in normalized_intake
    assert "do not ask the learner which subfield or starting route" in normalized_intake
    assert "unless the learner explicitly asks to compare or choose routes" in normalized_intake
    assert "Set `zero_baseline_confirmed=true` only when the learner explicitly" in normalized_intake
    assert "resolve `target_scenario=\"no_specific_scenario\"` automatically" in normalized_intake
    assert "do not ask how they will apply the knowledge" in normalized_intake
    assert "the choices as plain chat text" in normalized_intake
    assert "Do not rely on clickable cards" in normalized_intake
    assert "exactly one short line" in normalized_intake
    assert "Do not show `description`, `why_it_matters`, `best_for`" in normalized_intake
    assert "learning conversation that is already underway" in normalized_intake
    assert "discovery embedded inside the orientation" in normalized_intake
    assert "begin to understand the field while choosing a direction" in normalized_intake
    assert "one brief natural acknowledgement" in normalized_intake
    assert "meaningful relationships, contrasts, or possible paths" in normalized_intake
    assert "connect the learner's confirmed choice to the next part" in normalized_intake
    assert "must not become the substantive lesson" in normalized_intake
    assert "exactly one short conversational suggestion" in normalized_intake
    assert "Keep requirement collection invisible" in normalized_intake
    assert "Do not explain why the system needs an answer" in normalized_intake
    assert "mention `selection_target` or missing requirement fields" in normalized_intake
    assert "survey, placement test, funnel, or task checklist" in normalized_intake
    assert "arise naturally from the orientation" in normalized_intake
    assert "consistent with `reason_for_recommendation`" in normalized_intake
    assert "optional conversational starting point" in normalized_intake
    assert "only confirmed user information or the explicit absence" in normalized_intake
    assert "never claim or imply that the learner is a beginner" in normalized_intake
    assert "Do not present `learner_profile_inference` as a confirmed fact" in normalized_intake
    assert "AI-generated compact field map" in normalized_intake
    assert "first guidance turn after a broad learning direction becomes known" in normalized_intake
    assert "actual parts of the current field" in normalized_intake
    assert "Do not repeat the map" in normalized_intake
    assert "rebuild it when the learner changes the learning content" in normalized_intake
    assert "frozen, structured learning requirement" in generation
    assert "Generate a self-contained teaching board from only that payload" in generation
    assert "Use fenced code blocks only for real code" in normalized_generation


def test_turn_prompt_keeps_multiple_board_references_in_order() -> None:
    first = SelectionRef(kind="board", excerpt="First referenced paragraph")
    second = SelectionRef(kind="board", excerpt="Second referenced paragraph")

    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Compare these references.",
            selection=second,
            selections=[first, second],
        ),
        is_new_thread=False,
        board_state="non_empty",
    )

    assert "Reference 1:\nkind: board\nexcerpt: First referenced paragraph" in prompt
    assert "Reference 2:\nkind: board\nexcerpt: Second referenced paragraph" in prompt
    assert prompt.index("First referenced paragraph") < prompt.index("Second referenced paragraph")


def test_turn_prompt_preserves_single_selection_compatibility() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Explain this.",
            selection=SelectionRef(kind="board", excerpt="Only referenced paragraph"),
        ),
        is_new_thread=False,
        board_state="non_empty",
    )

    assert "Reference 1:\nkind: board\nexcerpt: Only referenced paragraph" in prompt
    assert "Reference 2:" not in prompt


@pytest.fixture
def codex_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setenv("OPENCLASS_CODEX_WORKSPACE_ROOT", str(tmp_path / "codex-workspaces"))
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda _thread_id, **_kwargs: None,
    )

    # This historical suite exercises the retained Codex rollback adapter by
    # replacing its app-server calls with fakes. Production routing is covered
    # separately and always normalizes the public request to Pi.
    def legacy_text_model_selection(request: ChatRequest, *, user_id: str):
        return PRODUCTION_TEXT_MODEL_SELECTION(
            request,
            user_id=user_id,
        ).model_copy(update={"agent_backend": "codex"})

    def legacy_adapter(selection, **kwargs):
        if selection.agent_backend == "pi":
            return ai_execution_adapter.PiAIExecutionAdapter(
                owner_user_id=kwargs["owner_user_id"],
                provider=selection.provider,
                model=selection.model,
                reasoning_effort=selection.reasoning_effort,
            )
        if selection.provider == "openai_codex":
            return ai_execution_adapter.CodexAIExecutionAdapter(
                owner_user_id=kwargs["owner_user_id"],
                model=selection.model,
                reasoning_effort=selection.reasoning_effort,
                service_tier=selection.service_tier,
                board_runner=kwargs.get("board_runner"),
                image_analysis_runner=kwargs.get("image_analysis_runner"),
            )
        return ai_execution_adapter.DeepSeekAIExecutionAdapter(model=selection.model)

    monkeypatch.setattr(codex_chat, "_text_model_selection", legacy_text_model_selection)
    monkeypatch.setattr(codex_chat, "build_ai_execution_adapter", legacy_adapter)
    return store


def test_codex_chat_preserves_frontend_contract_and_persists_thread(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        activity = AgentActivityEvent(
            id=f"activity_{len(calls)}",
            turn_id=f"turn_{len(calls)}",
            stage="build_context",
            label="OpenClass 已完成思考",
            role="OpenClass",
            metadata={"kind": "reasoning", "detail": "Inspected the current board."},
        )
        return CodexTurnResult(
            thread_id="thread_codex_1",
            turn_id=f"turn_{len(calls)}",
            final_response="Codex reply",
            activity=[activity],
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        codex_chat,
        "build_model_catalog",
        lambda _user_id: SimpleNamespace(
            defaults={"text": SimpleNamespace(model="gpt-5.6-sol")}
        ),
    )

    first = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Explain this without editing it.",
            text_model={
                "provider": "openai_codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
        ),
        user_id=TEST_USER_ID,
    )
    second = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Continue."),
        user_id=TEST_USER_ID,
    )

    assert first.chatbot_message == "Codex reply"
    assert first.agent_activity[0].metadata["detail"] == "Inspected the current board."
    assert first.board_decision.action == "no_change"
    assert first.board_document_operation_status == "none"
    assert first.requirement_cleared is True
    assert first.active_requirement_sheet is None
    assert first.active_board_task_sheet is None
    assert first.learning_clarification.can_start is False
    assert first.learning_clarification.ready_for_board is False
    assert first.course_package.lessons[0].learning_requirements is None
    assert second.chatbot_message == "Codex reply"
    assert calls[0]["thread_id"] is None
    assert calls[0]["model"] == "gpt-5.6-sol"
    assert calls[0]["reasoning_effort"] == "xhigh"
    assert calls[0]["service_tier"] == "priority"
    assert calls[0]["service_tier_is_set"] is True
    assert calls[1]["thread_id"] == "thread_codex_1"
    assert calls[1]["last_turn_id"] == "turn_1"
    assert calls[1]["model"] == "gpt-5.6-sol"
    assert calls[1]["service_tier_is_set"] is False

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert saved_lesson.learning_requirements is None
    assert saved_lesson.board_task_requirements is None
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["assistant_message_source"] == "codex"
    assert commit.metadata["codex_thread_id"] == "thread_codex_1"
    assert commit.metadata["document_changed"] is False
    assert commit.metadata["requirement_cleared"] is True
    assert commit.metadata["agent_activity"][0]["id"] == "activity_2"
    configured_commit = next(
        item
        for item in saved_lesson.history_graph.commits
        if item.metadata.get("codex_turn_id") == "turn_1"
    )
    assert configured_commit.metadata["codex_model"] == "gpt-5.6-sol"
    assert configured_commit.metadata["codex_reasoning_effort"] == "xhigh"
    assert configured_commit.metadata["codex_service_tier"] == "priority"
    assert configured_commit.metadata["codex_service_tier_is_set"] is True
    assert commit.metadata["codex_service_tier_is_set"] is False


def test_existing_board_question_answers_first_and_blocks_unapproved_write(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    original = "# Existing board\n\n## Preserved section\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    lesson = _seed_workspace(codex_store, content_text=original)

    def fake_turn(**kwargs) -> CodexTurnResult:
        prompt = kwargs["user_prompt"]
        assert "action: answer_then_offer" in prompt
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("Unauthorized replacement", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_question_first",
            turn_id="turn_question_first",
            final_response="A complete explanation followed by a natural board-write question.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(codex_chat, "generate_follow_up_suggestions", lambda **_kwargs: [])

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="How should this investment be classified?"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message.startswith("A complete explanation")
    assert response.board_document_operation_status == "none"
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == original
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["pending_board_write_offer_after"] == {
        "status": "awaiting_confirmation",
        "question": "How should this investment be classified?",
        "content": response.chatbot_message,
    }
    assert commit.metadata["document_change_attempted"] is True
    assert commit.metadata["document_write_authorized"] is False
    assert commit.metadata["unauthorized_document_change_blocked"] is True
    assert commit.metadata["decision_trace"]["selected_action"] == "answer_then_offer"
    assert commit.metadata["decision_trace"]["role_executed"] == "chatbot"


def test_pending_write_confirmation_reuses_offered_content_and_then_edits(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    prompts: list[str] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        prompts.append(kwargs["user_prompt"])
        if len(prompts) == 1:
            return CodexTurnResult(
                thread_id="thread_pending_write",
                turn_id="turn_answer",
                final_response="The new explanation. Would you like it added to the board?",
            )
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text(
            "# Existing board\n\n## Added explanation\n\nThe new explanation.",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="thread_pending_write",
            turn_id="turn_confirmed_write",
            final_response="The approved content has been added.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(codex_chat, "generate_follow_up_suggestions", lambda **_kwargs: [])

    first = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Why does this rule work?"),
        user_id=TEST_USER_ID,
    )
    second = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="可以，写进去吧"),
        user_id=TEST_USER_ID,
    )

    assert first.board_document_operation_status == "none"
    assert "action: confirm_offered_write" in prompts[1]
    assert "The new explanation." in prompts[1]
    assert second.board_document_operation_status == "succeeded"
    assert "Added explanation" in second.course_package.lessons[0].board_document.content_text
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["pending_board_write_offer_after"] is None
    assert commit.metadata["decision_trace"]["selected_action"] == "confirm_offered_write"
    assert commit.metadata["decision_trace"]["role_executed"] == "board_editor"


def test_explicit_edit_can_replace_rich_board_without_flatten_guard(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    rich_board = (
        "# Existing board\n\n## Detail\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        "- First\n- Second"
    )
    lesson = _seed_workspace(codex_store, content_text=rich_board)
    flattened = "\n\n".join(f"Plain paragraph {index}." for index in range(1, 10))

    def fake_turn(**kwargs) -> CodexTurnResult:
        assert "action: edit_now" in kwargs["user_prompt"]
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text(flattened, encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_explicit_flatten",
            turn_id="turn_explicit_flatten",
            final_response="The explicit board change is complete.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(codex_chat, "generate_follow_up_suggestions", lambda **_kwargs: [])

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Rewrite the board document as plain paragraphs."),
        user_id=TEST_USER_ID,
    )

    assert response.board_document_operation_status == "succeeded"
    assert response.course_package.lessons[0].board_document.content_text == flattened
    commit = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    )
    assert commit.metadata["document_write_authorized"] is True
    assert commit.metadata["unauthorized_document_change_blocked"] is False
    assert commit.metadata["decision_trace"]["selected_action"] == "edit_now"


def test_codex_chat_writes_only_final_markdown_back_to_rich_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    original_document_id = lesson.board_document.id
    original_page_settings = lesson.board_document.page_settings

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Revised board\n\n- First\n- Second\n", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_codex_edit",
            turn_id="turn_edit",
            final_response="Updated the document.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Rewrite the right document."),
        user_id=TEST_USER_ID,
    )

    document = response.course_package.lessons[0].board_document
    assert response.board_decision.action == "edit_board"
    assert response.board_document_operation_status == "succeeded"
    assert document.id == original_document_id
    assert document.page_settings == original_page_settings
    assert document.content_text == "# Revised board\n\n- First\n- Second"
    assert "<h1>Revised board</h1>" in document.content_html
    assert document.content_json["content"][0]["type"] == "heading"
    assert list(codex_chat.codex_workspace_root().iterdir()) == []

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "board_document_edit"
    assert commit.metadata["codex_turn_id"] == "turn_edit"
    assert commit.metadata["document_changed"] is True


def test_codex_chat_passes_formula_ink_as_image_input(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        return CodexTurnResult("thread_formula", "turn_formula", "formula reviewed")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Use this handwritten formula.",
            formula_ink={
                "image_data_url": "data:image/png;base64,YQ==",
                "source_latex": None,
                "action": "reference",
            },
        ),
        user_id=TEST_USER_ID,
    )

    assert calls[0]["image_urls"] == ["data:image/png;base64,YQ=="]


def test_codex_chat_rejects_unexpected_file_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    before = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    before_head = current_head_commit(before).id
    before_commit_count = len(before.history_graph.commits)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = Path(kwargs["cwd"])
        (workspace / "unexpected.txt").write_text("not allowed", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_bad",
            turn_id="turn_bad",
            final_response="done",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="unexpected file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Create another file."),
            user_id=TEST_USER_ID,
        )

    after = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(after).id == before_head
    assert len(after.history_graph.commits) == before_commit_count
    assert after.board_document.content_text == "# Existing board"


def test_codex_chat_rejects_html_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("<h1>HTML is not allowed</h1>", encoding="utf-8")
        return CodexTurnResult("thread_html", "turn_html", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="contains HTML"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Use HTML."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert discarded_threads == ["thread_html"]


def test_codex_chat_rejects_symlink_board_without_reading_target(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret outside content", encoding="utf-8")

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.unlink()
        board_path.symlink_to(outside)
        return CodexTurnResult("thread_symlink", "turn_symlink", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="regular file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Read another file."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"


def test_codex_chat_detects_concurrent_document_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = codex_store.load_for_user(TEST_USER_ID)
        current_lesson = workspace.packages[0].lessons[0]
        changed_document = build_document(
            title=current_lesson.board_document.title,
            content_text="# Manual change",
            document_id=current_lesson.board_document.id,
            page_settings=current_lesson.board_document.page_settings,
        )
        commit_operations(
            current_lesson,
            [],
            label="Manual edit",
            message="Concurrent manual edit",
            new_document=changed_document,
            metadata={"kind": "manual_document_edit"},
        )
        codex_store.save_for_user(TEST_USER_ID, workspace)
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="changed while Codex was working"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Rewrite it."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Manual change"
    assert current_head_commit(saved_lesson).metadata["kind"] == "manual_document_edit"


def test_codex_chat_serializes_turns_and_reloads_latest_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[tuple[str | None, str]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        calls.append((kwargs["thread_id"], board_path.read_text(encoding="utf-8")))
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
            board_path.write_text("# First update", encoding="utf-8")
            return CodexTurnResult("thread_shared", "turn_first", "first done")
        second_entered.set()
        board_path.write_text("# Second update", encoding="utf-8")
        return CodexTurnResult("thread_shared", "turn_second", "second done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    errors: list[BaseException] = []

    def run(message: str) -> None:
        try:
            codex_chat.process_codex_chat_on_lesson(
                lesson.id,
                ChatRequest(message=message, interaction_mode="direct_edit"),
                user_id=TEST_USER_ID,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert calls == [
        (None, "# Existing board"),
        ("thread_shared", "# First update"),
    ]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Second update"


def test_codex_chat_atomic_save_rejects_last_moment_target_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Codex update", encoding="utf-8")
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    original_atomic_save = workspace_state.save_lesson_for_user_if_head

    def conflicting_atomic_save(user_id, next_lesson, **kwargs) -> bool:
        concurrent_workspace = codex_store.load_for_user(TEST_USER_ID)
        _package, concurrent_lesson = workspace_state.find_lesson_package(
            concurrent_workspace,
            lesson.id,
        )
        concurrent_document = build_document(
            title=concurrent_lesson.board_document.title,
            content_text="# Concurrent user update",
            document_id=concurrent_lesson.board_document.id,
            page_settings=concurrent_lesson.board_document.page_settings,
        )
        commit_operations(
            concurrent_lesson,
            operations=[],
            label="Concurrent update",
            message="A different writer changed the lesson.",
            new_document=concurrent_document,
        )
        codex_store.save_for_user(TEST_USER_ID, concurrent_workspace)
        return original_atomic_save(user_id, next_lesson, **kwargs)

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        workspace_state,
        "save_lesson_for_user_if_head",
        conflicting_atomic_save,
    )

    with pytest.raises(CodexAppServerError, match="lesson changed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Update the document."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Concurrent user update"
    assert discarded_threads == ["thread_conflict"]


def test_lesson_atomic_save_preserves_concurrent_other_lesson_change(
    codex_store: SqliteCourseStore,
) -> None:
    target = _seed_workspace(codex_store)
    setup_workspace = codex_store.load_for_user(TEST_USER_ID)
    other = create_empty_lesson("Other lesson")
    setup_workspace.packages[0].lessons.append(other)
    codex_store.save_for_user(TEST_USER_ID, setup_workspace)

    codex_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, codex_target = workspace_state.find_lesson_package(codex_workspace, target.id)
    base_commit_id = current_head_commit(codex_target).id
    target_document = build_document(
        title=codex_target.board_document.title,
        content_text="# Codex target update",
        document_id=codex_target.board_document.id,
        page_settings=codex_target.board_document.page_settings,
    )
    commit_operations(
        codex_target,
        operations=[],
        label="Codex update",
        message="Update only the target lesson.",
        new_document=target_document,
    )

    latest_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, latest_other = workspace_state.find_lesson_package(latest_workspace, other.id)
    other_document = build_document(
        title=latest_other.board_document.title,
        content_text="# Other concurrent update",
        document_id=latest_other.board_document.id,
        page_settings=latest_other.board_document.page_settings,
    )
    commit_operations(
        latest_other,
        operations=[],
        label="Other update",
        message="Update a different lesson.",
        new_document=other_document,
    )
    codex_store.save_for_user(TEST_USER_ID, latest_workspace)

    assert codex_store.save_lesson_for_user_if_head(
        TEST_USER_ID,
        codex_target,
        expected_branch_name=codex_target.history_graph.current_branch,
        expected_head_commit_id=base_commit_id,
    )
    saved = codex_store.load_for_user(TEST_USER_ID)
    _package, saved_target = workspace_state.find_lesson_package(saved, target.id)
    _package, saved_other = workspace_state.find_lesson_package(saved, other.id)
    assert saved_target.board_document.content_text == "# Codex target update"
    assert saved_other.board_document.content_text == "# Other concurrent update"


def test_codex_chat_rejects_oversized_existing_board_before_turn(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="123456789")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "8")
    monkeypatch.setattr(
        codex_chat,
        "run_codex_thread_turn",
        lambda **_kwargs: pytest.fail("Codex must not start for an oversized board"),
    )

    with pytest.raises(CodexAppServerError, match="current board exceeds"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Continue."),
            user_id=TEST_USER_ID,
        )


def test_codex_chat_cancels_turn_when_board_exceeds_runtime_quota(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="# Board")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "64")
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def oversized_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_bytes(b"x" * 1024)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if kwargs["is_cancelled"]():
                raise CodexTurnCancelledError("cancelled by quota")
            time.sleep(0.01)
        pytest.fail("quota monitor did not cancel the Codex turn")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", oversized_turn)

    with pytest.raises(CodexAppServerError, match="configured size limit"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Make it very large."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Board"


def test_board_quota_accepts_large_explicit_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", str(32 * 1024 * 1024))

    assert codex_chat._board_max_bytes() == 32 * 1024 * 1024


def test_codex_app_server_command_uses_exact_board_permission_profile() -> None:
    command = codex_app_server._codex_app_server_command("/usr/local/bin/codex")
    rendered = "\n".join(command)

    assert 'default_permissions="openclass_board"' in rendered
    assert '":workspace_roots"={"board.md"="write"}' in rendered
    assert "permissions.openclass_board.network.enabled=false" in rendered
    assert 'permissions.openclass_chat.filesystem={":minimal"="read"}' in rendered
    assert "permissions.openclass_chat.network.enabled=false" in rendered
    assert 'approval_policy="never"' in rendered
    assert 'web_search="disabled"' in rendered
    assert "mcp_servers={}" in rendered
    assert "apps={_default={enabled=false}}" in rendered
    assert "features.apps=false" in rendered
    assert "features.hooks=false" in rendered
    assert "features.plugins=false" in rendered
    assert "features.computer_use=false" in rendered
    assert "--strict-config" in command
    assert "danger-full-access" not in rendered


def test_codex_app_server_process_uses_direct_command_without_file_size_limit() -> None:
    command = codex_app_server._codex_app_server_command("/usr/local/bin/codex")

    assert command[:3] == ["/usr/local/bin/codex", "app-server", "--strict-config"]
    assert "ulimit" not in " ".join(command)
    assert command[-2:] == ["-c", "features.workspace_dependencies=false"]


def test_effective_codex_config_rejects_legacy_sandbox_override() -> None:
    disabled_features = {
        feature: False
        for feature in (
            "apps",
            "auth_elicitation",
            "browser_use",
            "browser_use_external",
            "browser_use_full_cdp_access",
            "code_mode_host",
            "computer_use",
            "goals",
            "hooks",
            "image_generation",
            "in_app_browser",
            "multi_agent",
            "plugin_sharing",
            "plugins",
            "remote_plugin",
            "skill_mcp_dependency_install",
            "tool_call_mcp_elicitation",
            "tool_suggest",
            "workspace_dependencies",
        )
    }
    valid = {
        "config": {
            "sandbox_mode": None,
            "default_permissions": "openclass_board",
            "approval_policy": "never",
            "web_search": "disabled",
            "permissions": {
                "openclass_board": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                        ":workspace_roots": {"board.md": "write"},
                    },
                    "network": {"enabled": False, "domains": None},
                },
                "openclass_chat": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                    },
                    "network": {"enabled": False, "domains": None},
                },
            },
            "shell_environment_policy": {"inherit": "none"},
            "features": disabled_features,
            "mcp_servers": {},
            "apps": {"_default": {"enabled": False}},
            "hooks": None,
            "plugins": {},
        }
    }

    codex_app_server._validate_effective_permission_config(valid)
    invalid = {"config": {**valid["config"], "sandbox_mode": "workspace-write"}}
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(invalid)

    external_tool = {
        "config": {
            **valid["config"],
            "features": {**disabled_features, "plugins": True},
        }
    }
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(external_tool)

    broad_chat_read = {
        "config": {
            **valid["config"],
            "permissions": {
                **valid["config"]["permissions"],
                "openclass_chat": {
                    **valid["config"]["permissions"]["openclass_chat"],
                    "filesystem": {":root": "read"},
                },
            },
        }
    }
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(broad_chat_read)


def test_new_and_reloaded_lessons_hide_legacy_ai_runtime(
    codex_store: SqliteCourseStore,
) -> None:
    lesson = create_empty_lesson("Codex-only lesson")
    initial_metadata = current_head_commit(lesson).metadata

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None
    assert initial_metadata["active_requirement_sheet_after"] is None
    assert initial_metadata["active_board_task_sheet_after"] is None

    lesson.learning_requirements = build_requirements(lesson.title)
    workspace = build_initial_workspace_state()
    workspace.packages[0].lessons.append(lesson)
    codex_store.save_for_user(TEST_USER_ID, workspace)

    reloaded = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert reloaded.learning_requirements is None
    assert reloaded.board_task_requirements is None
    assert reloaded.board_teaching_guide is None
    assert reloaded.board_teaching_progress is None


def test_branch_restore_does_not_revive_legacy_ai_runtime() -> None:
    lesson = create_empty_lesson("Legacy runtime")
    lesson.history_graph.commits[0].metadata["active_requirement_sheet_after"] = (
        build_requirements(lesson.title).model_dump(mode="json")
    )
    lesson.learning_requirements = build_requirements(lesson.title)

    create_branch(lesson, "codex-only", lesson.history_graph.commits[0].id)

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None


def test_thread_permission_response_rejects_broad_writable_root(tmp_path: Path) -> None:
    codex_app_server._validate_thread_permission_response(
        _thread_result("thread_safe", tmp_path),
        cwd=tmp_path,
    )
    unsafe = _thread_result("thread_unsafe", tmp_path)
    unsafe["sandbox"]["writableRoots"] = [str(tmp_path.resolve())]

    with pytest.raises(CodexAppServerError, match="exact board.md-only sandbox"):
        codex_app_server._validate_thread_permission_response(unsafe, cwd=tmp_path)


def test_structured_chat_permission_response_rejects_broad_file_read() -> None:
    safe = {
        "thread": {"id": "thread_chat"},
        "activePermissionProfile": {"id": "openclass_chat"},
        "sandbox": {
            "type": "readOnly",
            "networkAccess": False,
        },
    }
    codex_app_server._validate_chat_thread_permission_response(safe)

    unsafe = {
        **safe,
        "activePermissionProfile": {"id": ":read-only"},
    }
    with pytest.raises(CodexAppServerError, match="exact isolated Chatbot profile"):
        codex_app_server._validate_chat_thread_permission_response(unsafe)


def test_structured_codex_adapter_keeps_default_decision_turn_offline() -> None:
    captured: dict[str, object] = {}

    class StopSession:
        deadline_monotonic = time.monotonic() + 5

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            captured.update(params)
            raise CodexAppServerError("stop after capturing thread params")

    with pytest.raises(CodexAppServerError, match="stop after capturing"):
        codex_app_server._run_structured_turn(
            session=StopSession(),  # type: ignore[arg-type]
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatRequest,
        )

    assert captured["config"] == {"default_permissions": "openclass_chat"}
    assert "sandbox" not in captured
    assert "call tools" in captured["developerInstructions"]


def test_structured_codex_adapter_uses_live_web_chat_profile() -> None:
    captured: dict[str, object] = {}

    class StopSession:
        deadline_monotonic = time.monotonic() + 5

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            captured.update(params)
            raise CodexAppServerError("stop after capturing thread params")

    with pytest.raises(CodexAppServerError, match="stop after capturing"):
        codex_app_server._run_structured_turn(
            session=StopSession(),  # type: ignore[arg-type]
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatRequest,
            allow_live_web_search=True,
        )

    assert captured["config"] == {
        "default_permissions": "openclass_chat",
        "web_search": "live",
    }
    assert "sandbox" not in captured
    assert "built-in web search" in captured["developerInstructions"]


def test_conversation_turn_collects_delta_and_final_message() -> None:
    class FakeSession:
        _next_id = 7

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.writes: list[dict] = []

        def _write(self, payload: dict) -> None:
            self.writes.append(payload)

        def _answer_server_request(self, message: dict) -> None:
            raise AssertionError(message)

    session = FakeSession()
    session._messages.put({"id": 7, "result": {"turn": {"id": "turn_7"}}})
    session._messages.put(
        {
            "method": "item/agentMessage/delta",
            "params": {"delta": "partial"},
        }
    )
    session._messages.put(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "final response"}},
        }
    )
    session._messages.put(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn_7", "status": "completed"}},
        }
    )
    deltas: list[str] = []

    result = codex_app_server._run_conversation_turn(
        session=session,  # type: ignore[arg-type]
        thread_id="thread_7",
        model="gpt-5.5",
        cwd=Path("/tmp/board-only"),
        user_prompt="hello",
        image_urls=["data:image/png;base64,YQ=="],
        deadline_monotonic=time.monotonic() + 5,
        on_delta=deltas.append,
        is_cancelled=None,
        reasoning_effort="xhigh",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert result.thread_id == "thread_7"
    assert result.turn_id == "turn_7"
    assert result.final_response == "final response"
    assert deltas == ["partial"]
    params = session.writes[0]["params"]
    assert params["cwd"] == "/tmp/board-only"
    assert params["approvalPolicy"] == "never"
    assert params["effort"] == "xhigh"
    assert params["serviceTier"] == "priority"
    assert params["input"][1] == {
        "type": "image",
        "url": "data:image/png;base64,YQ==",
        "detail": "original",
    }
    assert "sandboxPolicy" not in params


def test_conversation_turn_separates_work_activity_from_final_answer() -> None:
    class FakeSession:
        _next_id = 9

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()

        def _write(self, _payload: dict) -> None:
            return None

        def _answer_server_request(self, message: dict) -> None:
            raise AssertionError(message)

    session = FakeSession()
    for message in [
        {"id": 9, "result": {"turn": {"id": "turn_activity"}}},
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "reasoning_1", "type": "reasoning", "summary": [], "content": []},
            },
        },
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "reasoning_1",
                "delta": "检查板书结构",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "reasoning_1",
                    "type": "reasoning",
                    "summary": ["检查板书结构"],
                    "content": ["private reasoning must not be persisted"],
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "commentary_1", "type": "agentMessage", "phase": "commentary", "text": ""},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "commentary_1",
                "delta": "正在读取当前板书",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "commentary_1",
                    "type": "agentMessage",
                    "phase": "commentary",
                    "text": "正在读取当前板书",
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "command_1",
                    "type": "commandExecution",
                    "command": "sed -n '1,80p' board.md",
                    "cwd": "/tmp/board-only",
                    "status": "inProgress",
                },
            },
        },
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "command_1",
                "delta": "# Existing board\n",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "command_1",
                    "type": "commandExecution",
                    "command": "sed -n '1,80p' board.md",
                    "cwd": "/tmp/board-only",
                    "status": "completed",
                    "aggregatedOutput": "# Existing board\n",
                    "exitCode": 0,
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "final_1", "type": "agentMessage", "phase": "final_answer", "text": ""},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "final_1",
                "delta": "最终回复",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "final_1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "最终回复",
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn_activity", "status": "completed"}},
        },
    ]:
        session._messages.put(message)

    final_deltas: list[str] = []
    activity_updates: list[AgentActivityEvent] = []
    result = codex_app_server._run_conversation_turn(
        session=session,  # type: ignore[arg-type]
        thread_id="thread_activity",
        model="gpt-5.5",
        cwd=Path("/tmp/board-only"),
        user_prompt="Explain the board",
        image_urls=[],
        deadline_monotonic=time.monotonic() + 5,
        on_delta=final_deltas.append,
        on_activity=activity_updates.append,
        is_cancelled=None,
    )

    assert result.final_response == "最终回复"
    assert final_deltas == ["最终回复"]
    assert [event.id for event in result.activity] == ["reasoning_1", "commentary_1", "command_1"]
    assert result.activity[0].metadata["detail"] == "检查板书结构"
    assert "private reasoning" not in str(result.activity[0].metadata)
    assert result.activity[1].metadata["detail"] == "正在读取当前板书"
    assert result.activity[2].metadata["detail"] == "# Existing board\n"
    assert all(event.status == "completed" for event in result.activity)
    assert len(activity_updates) > len(result.activity)


def test_runtime_settings_distinguish_inherited_and_standard_speed() -> None:
    inherited = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=False,
        include_effort=True,
    )
    standard = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=True,
        include_effort=True,
    )

    assert inherited == {}
    assert standard == {"serviceTier": None}


def test_existing_codex_thread_is_forked_before_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            return _thread_result("thread_fork", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_fork", "turn_fork", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="recovery prompt",
        developer_instructions="board only",
        thread_id="thread_base",
        last_turn_id="turn_base",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert session.requests[0][0] == "thread/fork"
    assert session.requests[0][1]["threadId"] == "thread_base"
    assert session.requests[0][1]["lastTurnId"] == "turn_base"
    assert session.requests[0][1]["ephemeral"] is False
    assert session.requests[0][1]["serviceTier"] == "priority"
    assert observed_prompts == ["normal prompt"]
    assert result.thread_id == "thread_fork"
    assert result.parent_thread_id == "thread_base"
    assert result.replaced_stale_thread_id is None


def test_stale_codex_thread_starts_fresh_with_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            if method == "thread/fork":
                raise CodexAppServerError("thread not found")
            return _thread_result("thread_recovered", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_recovered", "turn_recovered", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="conversation recovery prompt",
        developer_instructions="board only",
        thread_id="thread_missing",
        service_tier=None,
        service_tier_is_set=True,
    )

    assert [method for method, _params in session.requests] == ["thread/fork", "thread/start"]
    assert all(params["serviceTier"] is None for _method, params in session.requests)
    assert observed_prompts == ["conversation recovery prompt"]
    assert result.thread_id == "thread_recovered"
    assert result.parent_thread_id == "thread_missing"
    assert result.replaced_stale_thread_id == "thread_missing"


def test_non_stale_fork_error_is_not_retried_as_a_new_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.methods: list[str] = []

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

        def request(self, method: str, _params: dict, **_kwargs) -> dict:
            self.methods.append(method)
            raise CodexAppServerError("authentication failed")

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )

    with pytest.raises(CodexAppServerError, match="authentication failed"):
        codex_app_server.run_codex_thread_turn(
            user_id=TEST_USER_ID,
            model="gpt-5.5",
            cwd=tmp_path,
            user_prompt="normal prompt",
            fallback_user_prompt="recovery prompt",
            developer_instructions="board only",
            thread_id="thread_base",
        )

    assert session.methods == ["thread/fork"]


def test_deepseek_selection_uses_shared_provider_for_an_authorized_board_edit(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="# Existing\n\nOriginal content.")
    calls: list[object] = []

    class FakeDeepSeekAdapter:
        def parse_structured(self, **kwargs):
            schema = kwargs["schema"]
            calls.append(schema)
            if schema is codex_chat._StructuredExistingBoardTurn:
                parsed = schema(
                    chatbot_message="I updated the requested paragraph.",
                    board_markdown="# Existing\n\nUpdated content.",
                )
            else:
                parsed = schema(
                    suggestions=["Explain the change", "Show one concrete example"]
                )
            return SimpleNamespace(output_parsed=parsed, activity=[])

    adapter = FakeDeepSeekAdapter()
    monkeypatch.setattr(
        codex_chat,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: adapter,
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Replace the paragraph in the board.",
            interaction_mode="direct_edit",
            text_model={"provider": "deepseek", "model": "deepseek-v4-flash"},
        ),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    metadata = current_head_commit(saved_lesson).metadata
    assert response.board_document_operation_status == "succeeded"
    assert "Updated content." in saved_lesson.board_document.content_text
    assert metadata["ai_provider"] == "deepseek"
    assert metadata["ai_model"] == "deepseek-v4-flash"
    assert codex_chat._StructuredExistingBoardTurn in calls


def test_chat_normalizes_a_legacy_codex_backend_request_to_pi(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="# Existing\n\nOriginal content.")
    observed_activity: list[AgentActivityEvent] = []
    monkeypatch.setattr(
        codex_chat,
        "_text_model_selection",
        PRODUCTION_TEXT_MODEL_SELECTION,
    )

    class FakePiAdapter:
        def parse_structured(self, **kwargs):
            schema = kwargs["schema"]
            if schema is codex_chat._StructuredExistingBoardTurn:
                activity = AgentActivityEvent(
                    turn_id="piturn_live",
                    stage="build_context",
                    label="OpenClass 正在推理",
                    status="running",
                    role="OpenClass",
                    metadata={"kind": "reasoning"},
                )
                kwargs["on_activity"](activity)
                parsed = schema(
                    chatbot_message="Here is the explanation.",
                    board_markdown="# Existing\n\nOriginal content.",
                )
            else:
                parsed = schema(suggestions=["Continue"])
            return SimpleNamespace(output_parsed=parsed, activity=[])

    monkeypatch.setattr(
        codex_chat,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakePiAdapter(),
    )

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Explain this paragraph.",
            text_model={
                "agent_backend": "codex",
                "provider": "openai_codex",
                "model": "gpt-5.5",
            },
        ),
        user_id=TEST_USER_ID,
        on_agent_activity=observed_activity.append,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    metadata = current_head_commit(saved_lesson).metadata
    assert response.chatbot_message == "Here is the explanation."
    assert response.board_document_operation_status == "none"
    assert metadata["agent_backend"] == "pi"
    assert metadata["assistant_message_source"] == "pi"
    assert metadata["ai_provider"] == "openai_codex"
    assert [event.label for event in observed_activity] == ["OpenClass 正在推理"]

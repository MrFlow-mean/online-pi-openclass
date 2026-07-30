from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import (
    AIModelSelection,
    BoardExplanationDirective,
    ChatRequest,
    SelectionRef,
)
from app.services import workspace_state
from app.services.board_segment_index import build_board_segment_index
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.existing_board.mutation_planner import MutationPlannerModelDraft
from app.services.existing_board.task_manager import BoardTaskManagerDraft
from app.services.existing_board.workflow import (
    ExistingBoardWorkflowError,
    process_existing_board_workflow,
)
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "existing_board_workflow_user"
FULL_BOARD_SENTINEL = "FULL_BOARD_SENTINEL_MUST_STAY_OUTSIDE_MODEL_PROMPTS"


def _model() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model="gpt-live-1-codex",
        access_method="chatgpt_subscription",
    )


def _draft(**updates: object) -> BoardTaskManagerDraft:
    payload: dict[str, object] = {
        "action": "explain",
        "target_hint": "Target",
        "location_kind": "target_range",
        "question_or_topic": "Explain the resolved target",
        "special_interaction_requirements": "none",
        "extent": "section",
        "destination": "current_lesson",
        "topic_relation": "current_document",
        "relation_to_active": "none",
        "missing_items": [],
        "reason": "The task fields are complete.",
    }
    payload.update(updates)
    return BoardTaskManagerDraft.model_validate(payload)


def _mutation_draft(**updates: object) -> MutationPlannerModelDraft:
    payload: dict[str, object] = {
        "operations": [
            {
                "operation_id": "edit_target",
                "action": "edit",
                "binding": {"kind": "target_range", "position": "replace"},
                "content_markdown": "Rewritten paragraph.",
            }
        ],
        "extent": "paragraph",
        "destination": "current_lesson",
        "topic_relation": "current_document",
        "requires_confirmation": False,
        "reason": "The bounded edit is ready.",
    }
    payload.update(updates)
    return MutationPlannerModelDraft.model_validate(payload)


class RecordingAdapter:
    def __init__(
        self,
        draft: BoardTaskManagerDraft,
        *,
        directive_status: str = "approved",
        mutation_draft: MutationPlannerModelDraft | None = None,
    ) -> None:
        self.draft = draft
        self.directive_status = directive_status
        self.mutation_draft = mutation_draft
        self.parse_calls: list[dict[str, object]] = []
        self.text_calls: list[dict[str, object]] = []

    def parse_structured(self, **kwargs):
        self.parse_calls.append(kwargs)
        if kwargs["schema"] is BoardTaskManagerDraft:
            parsed = self.draft
        elif kwargs["schema"] is MutationPlannerModelDraft:
            if self.mutation_draft is None:
                raise AssertionError("A mutation draft was not configured")
            parsed = self.mutation_draft
        elif kwargs["schema"] is BoardExplanationDirective:
            payload = json.loads(str(kwargs["user_prompt"]))
            excerpt = payload["resolved_target"]["excerpt"]
            parsed = (
                BoardExplanationDirective(
                    status="approved",
                    target_summary="resolved target",
                    target_excerpt=excerpt,
                    teaching_instruction="explain only the authorized excerpt",
                    constraints=["stay inside the directive"],
                )
                if self.directive_status == "approved"
                else BoardExplanationDirective(
                    status=self.directive_status,
                    clarification_question="model generated boundary clarification",
                    reason="the Board Manager needs a narrower boundary",
                )
            )
        else:
            raise AssertionError(f"Unexpected schema: {kwargs['schema']}")
        return SimpleNamespace(output_parsed=parsed, activity=[])

    def complete_text(self, **kwargs):
        self.text_calls.append(kwargs)
        payload = json.loads(str(kwargs["user_prompt"]))
        mode = payload["response_mode"]
        messages = {
            "task_clarification": "model generated one clarification question",
            "future_action_status": "model generated pending-stage status",
            "confirmation_required": "model generated confirmation request",
            "confirmation_declined": "model generated cancellation acknowledgement",
            "mutation_completed": "model generated mutation completion",
            "approved_bounded_explanation": "model generated bounded explanation",
            "directive_status_only": "model generated directive status",
        }
        return SimpleNamespace(output_text=messages[mode], activity=[])


def _selection_for_text(lesson, text: str) -> SelectionRef:
    segment = next(
        item
        for item in build_board_segment_index(lesson.board_document).segments
        if item.text == text
    )
    return SelectionRef(
        kind="board",
        location_kind="target_range",
        lesson_id=lesson.id,
        source_commit_id=current_head_commit(lesson).id,
        document_id=lesson.board_document.id,
        segment_id=segment.segment_id,
        heading_path=list(segment.heading_path),
        excerpt=text,
        text_hash=segment.text_hash,
    )


@pytest.fixture
def workflow_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Existing board workflow")
    lesson.board_document = build_document(
        title=lesson.board_document.title,
        content_text=(
            "# Target\n\nAuthorized paragraph.\n\n"
            f"## Other\n\n{FULL_BOARD_SENTINEL}"
        ),
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.history_graph.commits[0].snapshot = lesson.board_document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return store, lesson


def test_ambiguous_target_generates_one_model_question_and_persists_collecting_runtime(
    workflow_store,
) -> None:
    store, seeded = workflow_store
    workspace = store.load_for_user(TEST_USER_ID)
    _package, lesson = workspace_state.find_lesson_package(workspace, seeded.id)
    lesson.board_document = build_document(
        title=lesson.board_document.title,
        content_text=(
            "# Duplicate\n\nFirst candidate.\n\n"
            "# Duplicate\n\nSecond candidate.\n\n"
            f"# Other\n\n{FULL_BOARD_SENTINEL}"
        ),
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.history_graph.commits[0].snapshot = lesson.board_document
    store.save_for_user(TEST_USER_ID, workspace)
    adapter = RecordingAdapter(_draft(target_hint="Duplicate"))

    response = process_existing_board_workflow(
        seeded.id,
        ChatRequest(message="current request"),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert response.board_task_phase == "collecting"
    assert response.needs_clarification is True
    assert response.chatbot_message == "model generated one clarification question"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.location_status == "ambiguous"
    assert len(response.active_board_task_sheet.target_candidates) == 2
    assert len(adapter.parse_calls) == 1
    assert len(adapter.text_calls) == 1
    assert json.loads(str(adapter.text_calls[0]["user_prompt"]))["response_mode"] == (
        "task_clarification"
    )
    assert FULL_BOARD_SENTINEL not in str(adapter.parse_calls[0]["user_prompt"])
    assert FULL_BOARD_SENTINEL not in str(adapter.text_calls[0]["user_prompt"])
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, seeded.id)
    head = current_head_commit(saved)
    assert head.metadata["board_task_phase"] == "collecting"
    assert head.metadata["board_task_route"] == "clarify_location"
    assert head.runtime_snapshot is not None
    assert head.runtime_snapshot.board_task_requirements is not None
    assert head.runtime_snapshot.board_task_requirements.task_id == (
        response.board_task_sheet.task_id
    )


def test_approved_explanation_persists_ready_then_consumed_without_document_change(
    workflow_store,
) -> None:
    store, lesson = workflow_store
    before_text = lesson.board_document.content_text
    adapter = RecordingAdapter(_draft())

    response = process_existing_board_workflow(
        lesson.id,
        ChatRequest(message="current request"),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert response.chatbot_message == "model generated bounded explanation"
    assert response.board_task_phase == "consumed"
    assert response.active_board_task_sheet is None
    assert response.board_document_operation_status == "none"
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    workflow_commits = saved.history_graph.commits[-2:]
    assert [commit.metadata["board_task_phase"] for commit in workflow_commits] == [
        "ready",
        "consumed",
    ]
    ready, consumed = workflow_commits
    assert ready.runtime_snapshot is not None
    assert ready.runtime_snapshot.board_task_requirements is not None
    assert consumed.runtime_snapshot is not None
    assert consumed.runtime_snapshot.board_task_requirements is None
    assert consumed.metadata["board_task_run_id"] == ready.metadata["board_task_run_id"]
    assert consumed.metadata["board_task_version_id"] == ready.metadata["board_task_version_id"]
    assert consumed.metadata["board_task_route"] == "explain"
    assert consumed.metadata["board_task_decision"]["action"] == "explain"
    assert consumed.metadata["resolved_focus"]["excerpt"] == "Target"
    assert consumed.metadata["board_explanation_directive"]["status"] == "approved"
    assert consumed.metadata["decision_trace"]["document_changed"] is False
    assert [item["role"] for item in ready.metadata["role_executions"]] == [
        "task_manager",
        "focus_resolver",
    ]
    assert [item["role"] for item in consumed.metadata["role_executions"]] == [
        "task_manager",
        "focus_resolver",
        "board_manager",
        "chatbot",
    ]
    assert consumed.metadata["role_executions"][-1]["role"] == "chatbot"
    assert saved.board_document.content_text == before_text
    assert FULL_BOARD_SENTINEL not in str(adapter.parse_calls[0]["user_prompt"])
    assert FULL_BOARD_SENTINEL not in str(adapter.parse_calls[1]["user_prompt"])
    assert FULL_BOARD_SENTINEL not in str(adapter.text_calls[0]["user_prompt"])


@pytest.mark.parametrize(
    ("action", "extent", "expected_phase", "expected_mode"),
    [
        ("delete", "paragraph", "awaiting_confirmation", "confirmation_required"),
    ],
)
def test_non_explain_actions_persist_pending_state_with_zero_document_mutation(
    workflow_store,
    action: str,
    extent: str,
    expected_phase: str,
    expected_mode: str,
) -> None:
    store, lesson = workflow_store
    before_text = lesson.board_document.content_text
    adapter = RecordingAdapter(_draft(action=action, extent=extent))

    response = process_existing_board_workflow(
        lesson.id,
        ChatRequest(message="current request"),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert response.board_task_phase == expected_phase
    assert response.active_board_task_sheet is not None
    assert response.board_document_operation_status == "none"
    assert json.loads(str(adapter.text_calls[0]["user_prompt"]))["response_mode"] == expected_mode
    assert len(adapter.parse_calls) == 1
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert saved.board_document.content_text == before_text
    assert current_head_commit(saved).metadata["document_changed"] is False
    assert current_head_commit(saved).metadata["board_task_route"] == (
        "await_write_confirmation" if expected_phase == "awaiting_confirmation" else action
    )


def test_bounded_edit_and_write_execute_atomically_in_one_history_version(
    workflow_store,
) -> None:
    store, lesson = workflow_store
    commit_count_before = len(lesson.history_graph.commits)
    adapter = RecordingAdapter(
        _draft(action="edit", target_hint="Authorized paragraph.", extent="paragraph"),
        mutation_draft=_mutation_draft(
            operations=[
                {
                    "operation_id": "edit_target",
                    "action": "edit",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "Rewritten paragraph.",
                },
                {
                    "operation_id": "write_example",
                    "action": "write",
                    "binding": {"kind": "insertion_anchor", "position": "after"},
                    "content_markdown": "Example paragraph.",
                },
            ]
        ),
    )

    response = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="current request",
            selection=_selection_for_text(lesson, "Authorized paragraph."),
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert response.chatbot_message == "model generated mutation completion"
    assert response.board_task_phase == "consumed"
    assert response.active_board_task_sheet is None
    assert response.board_document_operation_status == "succeeded"
    assert response.decision_trace is not None
    assert response.decision_trace.document_changed is True
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert len(saved.history_graph.commits) == commit_count_before + 1
    assert saved.board_document.content_text == (
        "# Target\n\nRewritten paragraph.\n\nExample paragraph.\n\n"
        f"## Other\n\n{FULL_BOARD_SENTINEL}"
    )
    head = current_head_commit(saved)
    assert head.metadata["document_changed"] is True
    assert head.metadata["board_mutation_plan"]["operations"][0]["action"] == "edit"
    assert head.metadata["board_mutation_plan"]["operations"][1]["action"] == "write"
    assert head.metadata["board_mutation_audit"]["document_changed"] is True
    assert head.runtime_snapshot is not None
    assert head.runtime_snapshot.board_task_requirements is None
    assert [call["schema"] for call in adapter.parse_calls] == [
        BoardTaskManagerDraft,
        MutationPlannerModelDraft,
    ]
    assert all(FULL_BOARD_SENTINEL not in str(call["user_prompt"]) for call in adapter.parse_calls)


def test_confirmed_delete_revalidates_and_mutates_only_the_frozen_target(
    workflow_store,
) -> None:
    store, lesson = workflow_store
    adapter = RecordingAdapter(
        _draft(
            action="delete",
            target_hint="Authorized paragraph.",
            extent="paragraph",
        ),
        mutation_draft=_mutation_draft(
            operations=[
                {
                    "operation_id": "delete_target",
                    "action": "delete",
                    "binding": {"kind": "target_range", "position": "replace"},
                    "content_markdown": "",
                }
            ],
            requires_confirmation=True,
        ),
    )
    first = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="current request",
            selection=_selection_for_text(lesson, "Authorized paragraph."),
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert first.board_task_phase == "awaiting_confirmation"
    assert first.active_board_task_sheet is not None
    assert first.active_board_task_sheet.confirmation_status == "awaiting"
    before_confirmation = store.load_for_user(TEST_USER_ID)
    _package, waiting_lesson = workspace_state.find_lesson_package(
        before_confirmation,
        lesson.id,
    )
    assert "Authorized paragraph." in waiting_lesson.board_document.content_text

    confirmed = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="confirm visible operation",
            board_task_confirmation="confirm",
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert confirmed.board_task_phase == "consumed"
    assert confirmed.active_board_task_sheet is None
    assert confirmed.board_document_operation_status == "succeeded"
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert "Authorized paragraph." not in saved.board_document.content_text
    assert FULL_BOARD_SENTINEL in saved.board_document.content_text
    head = current_head_commit(saved)
    assert head.metadata["board_mutation_audit"]["applied_operation_ids"] == [
        "delete_target"
    ]
    assert head.metadata["role_executions"][0]["role"] == "confirmation_gate"
    assert [call["schema"] for call in adapter.parse_calls] == [
        BoardTaskManagerDraft,
        MutationPlannerModelDraft,
    ]


def test_declined_dangerous_task_archives_with_zero_document_change(
    workflow_store,
) -> None:
    store, lesson = workflow_store
    before_text = lesson.board_document.content_text
    adapter = RecordingAdapter(
        _draft(
            action="delete",
            target_hint="Authorized paragraph.",
            extent="paragraph",
        )
    )
    process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="current request",
            selection=_selection_for_text(lesson, "Authorized paragraph."),
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    declined = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="decline visible operation",
            board_task_confirmation="decline",
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert declined.chatbot_message == "model generated cancellation acknowledgement"
    assert declined.board_task_phase == "archived"
    assert declined.active_board_task_sheet is None
    assert declined.board_document_operation_status == "none"
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert saved.board_document.content_text == before_text
    head = current_head_commit(saved)
    assert head.metadata["document_changed"] is False
    assert head.runtime_snapshot is not None
    assert head.runtime_snapshot.board_task_requirements is None
    assert [call["schema"] for call in adapter.parse_calls] == [
        BoardTaskManagerDraft
    ]


def test_board_manager_clarification_keeps_the_task_active_for_refinement(
    workflow_store,
) -> None:
    store, lesson = workflow_store
    adapter = RecordingAdapter(_draft(), directive_status="needs_clarification")

    response = process_existing_board_workflow(
        lesson.id,
        ChatRequest(message="current request"),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    assert response.board_task_phase == "collecting"
    assert response.needs_clarification is True
    assert response.active_board_task_sheet is not None
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    head = current_head_commit(saved)
    assert head.metadata["board_explanation_directive"]["status"] == "needs_clarification"
    assert head.runtime_snapshot is not None
    assert head.runtime_snapshot.board_task_requirements is not None


def test_expected_head_conflict_fails_closed_without_persisting_workflow_commit(
    monkeypatch: pytest.MonkeyPatch,
    workflow_store,
) -> None:
    store, lesson = workflow_store
    before_head = current_head_commit(
        workspace_state.find_lesson_package(
            store.load_for_user(TEST_USER_ID), lesson.id
        )[1]
    ).id
    adapter = RecordingAdapter(
        _draft(
            action="edit",
            target_hint="Authorized paragraph.",
            extent="paragraph",
        ),
        mutation_draft=_mutation_draft(),
    )
    monkeypatch.setattr(
        workspace_state,
        "save_lesson_for_user_if_head",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(ExistingBoardWorkflowError, match="changed"):
        process_existing_board_workflow(
            lesson.id,
            ChatRequest(
                message="current request",
                selection=_selection_for_text(lesson, "Authorized paragraph."),
            ),
            user_id=TEST_USER_ID,
            adapter=adapter,
            selected_model=_model(),
        )

    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert current_head_commit(saved).id == before_head
    assert saved.board_task_requirements is None

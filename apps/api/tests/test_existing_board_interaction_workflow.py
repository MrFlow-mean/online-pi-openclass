from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import AIModelSelection, ChatRequest, SelectionRef
from app.services import workspace_state
from app.services.board_segment_index import build_board_segment_index
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.existing_board.interaction_runtime import InteractionRouteModelDraft
from app.services.existing_board.interaction_session import InteractionRule
from app.services.existing_board.interaction_workflow import (
    ExistingBoardInteractionReroute,
)
from app.services.existing_board.task_manager import BoardTaskManagerDraft
from app.services.existing_board.workflow import process_existing_board_workflow
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "interaction_workflow_user"
FULL_BOARD_SENTINEL = "INTERACTION_WORKFLOW_FULL_BOARD_SENTINEL"


def _model() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model="gpt-live-1-codex",
        access_method="chatgpt_subscription",
    )


def _task_draft() -> BoardTaskManagerDraft:
    return BoardTaskManagerDraft(
        action="interact",
        target_hint="Approved interaction target.",
        location_kind="target_range",
        question_or_topic="Run the requested bounded interaction",
        special_interaction_requirements="alternate turns under the learner rule",
        extent="paragraph",
        destination="current_lesson",
        topic_relation="current_document",
        relation_to_active="none",
        missing_items=[],
        reason="The interaction task is complete.",
    )


def _rule() -> InteractionRule:
    return InteractionRule(
        rule_text="alternate turns under the learner rule",
        interaction_goal="complete the bounded interaction",
        compliant_input_description="the learner performs the current rule turn",
        assistant_behavior_instruction="perform only the next assistant rule turn",
    )


def _route(route: str) -> InteractionRouteModelDraft:
    return InteractionRouteModelDraft(
        route=route,
        reason=f"route {route}",
        progress_note=f"progress {route}",
        correction_note=("correct within the active rule" if route == "rule_violation" else ""),
    )


class RecordingAdapter:
    def __init__(self, routes: list[str], replies: list[str]) -> None:
        self.routes = list(routes)
        self.replies = list(replies)
        self.parse_calls: list[dict[str, object]] = []
        self.text_calls: list[dict[str, object]] = []

    def parse_structured(self, **kwargs):
        self.parse_calls.append(kwargs)
        schema = kwargs["schema"]
        if schema is BoardTaskManagerDraft:
            parsed = _task_draft()
        elif schema is InteractionRule:
            parsed = _rule()
        elif schema is InteractionRouteModelDraft:
            parsed = _route(self.routes.pop(0))
        else:
            raise AssertionError(f"Unexpected schema: {schema}")
        return SimpleNamespace(output_parsed=parsed, activity=[])

    def complete_text(self, **kwargs):
        self.text_calls.append(kwargs)
        return SimpleNamespace(output_text=self.replies.pop(0), activity=[])


@pytest.fixture
def interaction_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Interaction workflow")
    lesson.board_document = build_document(
        title=lesson.board_document.title,
        content_text=(
            "# Interaction\n\nApproved interaction target.\n\n"
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


def _selection(lesson) -> SelectionRef:
    segment = next(
        item
        for item in build_board_segment_index(lesson.board_document).segments
        if item.text == "Approved interaction target."
    )
    return SelectionRef(
        kind="board",
        location_kind="target_range",
        lesson_id=lesson.id,
        source_commit_id=current_head_commit(lesson).id,
        document_id=lesson.board_document.id,
        segment_id=segment.segment_id,
        excerpt=segment.text,
        text_hash=segment.text_hash,
    )


def test_interaction_starts_restores_after_refresh_and_exits_without_board_change(
    interaction_store,
) -> None:
    store, lesson = interaction_store
    before_text = lesson.board_document.content_text
    adapter = RecordingAdapter(
        ["continue_rule", "rule_violation", "exit_rule"],
        ["first interaction reply", "correction reply", "exit reply"],
    )
    first = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="start the interaction",
            input_event_id="interaction_event_1",
            selection=_selection(lesson),
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )
    assert first.board_task_phase == "ready"
    assert first.active_board_task_sheet is not None
    first_session = first.active_board_task_sheet.interaction_session
    assert first_session is not None
    assert first_session["turn_count"] == 1

    restored_workspace = store.load_for_user(TEST_USER_ID)
    _package, restored_lesson = workspace_state.find_lesson_package(
        restored_workspace,
        lesson.id,
    )
    second = process_existing_board_workflow(
        restored_lesson.id,
        ChatRequest(
            message="input outside the current rule",
            input_event_id="interaction_event_2",
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )
    assert second.active_board_task_sheet is not None
    second_session = second.active_board_task_sheet.interaction_session
    assert second_session is not None
    assert second_session["turn_count"] == 2
    assert second_session["progress"]["rule_violation_count"] == 1

    exited = process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="end this interaction",
            input_event_id="interaction_event_3",
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )
    assert exited.board_task_phase == "consumed"
    assert exited.active_board_task_sheet is None
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    assert saved.board_document.content_text == before_text
    assert current_head_commit(saved).runtime_snapshot is not None
    assert current_head_commit(saved).runtime_snapshot.board_task_requirements is None
    assert [call["schema"] for call in adapter.parse_calls] == [
        BoardTaskManagerDraft,
        InteractionRule,
        InteractionRouteModelDraft,
        InteractionRouteModelDraft,
        InteractionRouteModelDraft,
    ]
    prompts = [
        *(str(call["user_prompt"]) for call in adapter.parse_calls),
        *(str(call["user_prompt"]) for call in adapter.text_calls),
    ]
    assert all(FULL_BOARD_SENTINEL not in prompt for prompt in prompts)


def test_new_task_persists_replaced_session_before_requesting_one_reroute(
    interaction_store,
) -> None:
    store, lesson = interaction_store
    before_text = lesson.board_document.content_text
    adapter = RecordingAdapter(
        ["continue_rule", "new_task"],
        ["first interaction reply"],
    )
    process_existing_board_workflow(
        lesson.id,
        ChatRequest(
            message="start the interaction",
            input_event_id="interaction_event_start",
            selection=_selection(lesson),
        ),
        user_id=TEST_USER_ID,
        adapter=adapter,
        selected_model=_model(),
    )

    with pytest.raises(ExistingBoardInteractionReroute) as raised:
        process_existing_board_workflow(
            lesson.id,
            ChatRequest(
                message="a separate new task",
                input_event_id="interaction_event_new_task",
            ),
            user_id=TEST_USER_ID,
            adapter=adapter,
            selected_model=_model(),
        )

    assert raised.value.request.message == "a separate new task"
    assert raised.value.dispatch_key.endswith(":interaction_event_new_task")
    persisted = store.load_for_user(TEST_USER_ID)
    _package, saved = workspace_state.find_lesson_package(persisted, lesson.id)
    head = current_head_commit(saved)
    assert saved.board_document.content_text == before_text
    assert head.metadata["interaction_route"] == "new_task"
    assert head.metadata["interaction_session"]["current_state"] == "replaced"
    assert head.runtime_snapshot is not None
    assert head.runtime_snapshot.board_task_requirements is None
    assert json.dumps(head.metadata, ensure_ascii=False).count(
        "interaction_event_new_task"
    ) >= 1

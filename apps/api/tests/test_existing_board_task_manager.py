from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.models import AIModelSelection, ConversationTurn, SelectionRef
from app.services.existing_board import task_manager
from pydantic import ValidationError


def _selected_model() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model="gpt-live-1-codex",
        access_method="chatgpt_subscription",
    )


def _draft(**updates) -> task_manager.BoardTaskManagerDraft:
    payload = {
        "action": "explain",
        "target_hint": "The referenced section identity",
        "location_kind": "target_range",
        "question_or_topic": "Explain the idea in simpler terms",
        "special_interaction_requirements": "none",
        "extent": "section",
        "destination": "current_lesson",
        "topic_relation": "current_document",
        "relation_to_active": "continue",
        "missing_items": [],
        "reason": "The user supplied a complete explanation task.",
    }
    payload.update(updates)
    return task_manager.BoardTaskManagerDraft.model_validate(payload)


def test_task_manager_uses_selected_model_and_only_the_redacted_input_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_model = _selected_model()
    captured: dict[str, object] = {}
    conversations = [
        ConversationTurn(role="user", content=f"turn {index}") for index in range(14)
    ]
    reference = SelectionRef(
        kind="board",
        excerpt="SECRET_SELECTED_EXCERPT",
        before_text="SECRET_BEFORE_TEXT",
        after_text="SECRET_AFTER_TEXT",
        lesson_id="lesson_1",
        document_id="document_1",
        segment_id="segment_1",
        heading_path=["Unit", "Section"],
        text_hash="hash_1",
    )
    manager_input = task_manager.build_task_manager_input(
        message="Please explain the part I referenced.",
        conversation=conversations,
        explicit_controls=task_manager.BoardTaskExplicitControls(action="explain"),
        references=[reference],
        active_task=task_manager.ActiveBoardTaskSummary(
            action="explain",
            location_kind="target_range",
            target_hint="Existing location hint",
            question_or_topic="Existing question",
            special_interaction_requirements="none",
        ),
    )

    class FakeAdapter:
        def parse_structured(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=_draft(), activity=[])

    def fake_resolve(selection, *, user_id):
        assert selection == selected_model
        assert user_id == "user_1"
        return selected_model

    def fake_build(selection, *, owner_user_id):
        assert selection == selected_model
        assert owner_user_id == "user_1"
        return FakeAdapter()

    monkeypatch.setattr(task_manager, "resolve_text_model_selection", fake_resolve)
    monkeypatch.setattr(task_manager, "build_ai_execution_adapter", fake_build)

    result = task_manager.manage_existing_board_task(
        manager_input,
        text_model=selected_model,
        user_id="user_1",
    )

    prompt = json.loads(str(captured["user_prompt"]))
    assert set(prompt) == {
        "message",
        "conversation",
        "explicit_controls",
        "references",
        "active_task",
    }
    assert len(prompt["conversation"]) == 12
    assert prompt["conversation"][0]["content"] == "turn 2"
    assert prompt["references"][0]["segment_id"] == "segment_1"
    assert "excerpt" not in prompt["references"][0]
    assert "before_text" not in prompt["references"][0]
    assert "after_text" not in prompt["references"][0]
    assert "SECRET_SELECTED_EXCERPT" not in str(prompt)
    assert "SECRET_BEFORE_TEXT" not in str(prompt)
    assert "SECRET_AFTER_TEXT" not in str(prompt)
    assert result.selected_model == selected_model
    assert result.decision.action == "explain"
    assert result.decision.completeness == 100
    assert result.decision.execution_allowed is True
    assert result.decision.requires_confirmation is False


def test_active_task_summary_rejects_target_excerpt() -> None:
    with pytest.raises(ValidationError):
        task_manager.ActiveBoardTaskSummary.model_validate(
            {
                "action": "edit",
                "location_kind": "target_range",
                "target_hint": "Known location",
                "question_or_topic": "Make it clearer",
                "special_interaction_requirements": "none",
                "target_excerpt": "BOARD_BODY_MUST_NOT_ENTER_THE_MANAGER",
            }
        )


def test_delete_of_whole_board_requires_confirmation_and_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed=_draft(
                    action="delete",
                    extent="whole_board",
                    question_or_topic="Remove the selected board content",
                ),
                activity=[],
            )

    monkeypatch.setattr(task_manager, "resolve_text_model_selection", lambda *_args, **_kwargs: _selected_model())
    monkeypatch.setattr(task_manager, "build_ai_execution_adapter", lambda *_args, **_kwargs: FakeAdapter())

    result = task_manager.manage_existing_board_task(
        task_manager.build_task_manager_input(message="Delete it."),
        text_model=_selected_model(),
        user_id="user_1",
    )

    assert result.decision.requires_confirmation is True
    assert result.decision.confirmation_reasons == ["delete", "whole_board"]
    assert result.decision.execution_allowed is False


def test_independent_topic_in_new_lesson_is_complete_but_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed=_draft(
                    action="write",
                    target_hint="",
                    location_kind="unresolved",
                    question_or_topic="Create a lesson for the independent topic",
                    extent="article",
                    destination="new_lesson",
                    topic_relation="independent",
                    relation_to_active="new_task",
                    missing_items=["target"],
                ),
                activity=[],
            )

    monkeypatch.setattr(
        task_manager,
        "resolve_text_model_selection",
        lambda *_args, **_kwargs: _selected_model(),
    )
    monkeypatch.setattr(
        task_manager,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )

    result = task_manager.manage_existing_board_task(
        task_manager.build_task_manager_input(
            message="Create a new lesson for this independent topic."
        ),
        text_model=_selected_model(),
        user_id="user_1",
    )

    assert result.decision.missing_items == []
    assert result.decision.completeness == 100
    assert result.decision.requires_confirmation is True
    assert result.decision.confirmation_reasons == ["new_lesson"]
    assert result.decision.execution_allowed is False


def test_whole_board_write_is_complete_without_segment_target_but_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed=_draft(
                    action="write",
                    target_hint="",
                    location_kind="unresolved",
                    question_or_topic="Replace the board with a complete new version",
                    extent="whole_board",
                    relation_to_active="new_task",
                    missing_items=["target"],
                ),
                activity=[],
            )

    monkeypatch.setattr(
        task_manager,
        "resolve_text_model_selection",
        lambda *_args, **_kwargs: _selected_model(),
    )
    monkeypatch.setattr(
        task_manager,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )

    result = task_manager.manage_existing_board_task(
        task_manager.build_task_manager_input(message="Rewrite the whole board."),
        text_model=_selected_model(),
        user_id="user_1",
    )

    assert result.decision.missing_items == []
    assert result.decision.completeness == 100
    assert result.decision.requires_confirmation is True
    assert result.decision.confirmation_reasons == ["whole_board"]
    assert result.decision.execution_allowed is False


def test_current_lesson_write_without_target_remains_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed=_draft(
                    action="write",
                    target_hint="",
                    location_kind="unresolved",
                    question_or_topic="Add another example",
                    extent="section",
                    destination="current_lesson",
                    relation_to_active="new_task",
                    missing_items=[],
                ),
                activity=[],
            )

    monkeypatch.setattr(
        task_manager,
        "resolve_text_model_selection",
        lambda *_args, **_kwargs: _selected_model(),
    )
    monkeypatch.setattr(
        task_manager,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )

    result = task_manager.manage_existing_board_task(
        task_manager.build_task_manager_input(message="Add another example."),
        text_model=_selected_model(),
        user_id="user_1",
    )

    assert result.decision.missing_items == ["target"]
    assert result.decision.completeness < 100
    assert result.decision.requires_confirmation is False
    assert result.decision.execution_allowed is False


def test_unresolved_task_reports_missing_items_and_never_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed=_draft(
                    action="unresolved",
                    target_hint="",
                    location_kind="unresolved",
                    question_or_topic="",
                    destination="unresolved",
                    topic_relation="unresolved",
                    relation_to_active="unresolved",
                    extent="unresolved",
                    missing_items=["action", "target", "question_or_topic"],
                ),
                activity=[],
            )

    monkeypatch.setattr(task_manager, "resolve_text_model_selection", lambda *_args, **_kwargs: _selected_model())
    monkeypatch.setattr(task_manager, "build_ai_execution_adapter", lambda *_args, **_kwargs: FakeAdapter())

    result = task_manager.manage_existing_board_task(
        task_manager.build_task_manager_input(message="Do something here."),
        text_model=_selected_model(),
        user_id="user_1",
    )

    assert result.decision.action == "unresolved"
    assert set(result.decision.missing_items) >= {
        "action",
        "target",
        "question_or_topic",
        "extent",
        "destination",
        "topic_relation",
        "relation_to_active",
    }
    assert result.decision.completeness < 100
    assert result.decision.execution_allowed is False
    assert not hasattr(result.decision, "clarification_question")

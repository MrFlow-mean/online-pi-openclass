from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from app.models import (
    AIModelSelection,
    BoardDecision,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    DecisionTrace,
    LearningClarificationStatus,
    SelectionRef,
    TurnDecision,
)
from app.services import chat_service, chat_turn_gate, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "user_turn_gate"
BOARD_SENTINEL = "BOARD_SENTINEL_MUST_NOT_REACH_THE_GATE"


@pytest.fixture(autouse=True)
def reset_idempotency_state():
    chat_service._clear_idempotency_state_for_tests()
    yield
    chat_service._clear_idempotency_state_for_tests()


@pytest.fixture
def turn_gate_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    return store


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Turn gate lesson")
    lesson.board_document = build_document(
        title=lesson.board_document.title,
        document_id=lesson.board_document.id,
        content_text=f"# Existing board\n\n{BOARD_SENTINEL}",
    )
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    lesson.learning_requirements = build_requirements("Preserved learning requirement")
    lesson.board_task_requirements = BoardTaskRequirementSheet(
        target_hint="Preserved target",
        requested_action="explain",
        question_or_topic="Preserved board task",
    )
    commit_operations(
        lesson,
        operations=[],
        label="Seed active workflow state",
        message="Seed active workflow state for the turn-gate test.",
        metadata={
            "active_requirement_sheet_after": lesson.learning_requirements.model_dump(
                mode="json"
            ),
            "requirement_phase": "collecting",
            "active_board_task_sheet_after": lesson.board_task_requirements.model_dump(
                mode="json"
            ),
            "board_task_phase": "collecting",
        },
    )
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def _neutral_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="",
        reason="",
        missing_items=[],
        can_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
    )


def _workflow_response(lesson_id: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    return ChatResponse(
        chatbot_message="Existing learning workflow ran.",
        decision_trace=DecisionTrace(
            intent_signals=["board_task:explain"],
            matched_rules=["existing_board_bounded_workflow"],
            selected_action="learning_need",
            target_resolver="FocusResolver",
            role_executed="chatbot",
            board_access="bounded_board_role",
            requirement_effect="updated",
            document_changed=False,
            reason="Existing-board workflow test result.",
        ),
        learning_requirement_sheet=build_requirements(lesson.title),
        learning_clarification=_neutral_clarification(),
        board_decision=BoardDecision(action="no_change", reason="Test workflow"),
        course_package=workspace_state.package_view_for_lesson(
            workspace,
            package,
            lesson.id,
        ),
    )


def _gate_result(request: ChatRequest, intent: str, reply: str):
    envelope = chat_turn_gate.build_turn_envelope(
        request,
        lesson_id="lesson_gate",
        selected_model=AIModelSelection(
            provider="openai_codex",
            model="test-model",
            access_method="chatgpt_subscription",
        ),
    )

    class FakeAdapter:
        def complete_text(self, **kwargs):
            assert BOARD_SENTINEL not in kwargs["user_prompt"]
            assert "selected secret text" not in kwargs["user_prompt"]
            if kwargs.get("on_text_delta") is not None:
                kwargs["on_text_delta"](reply)
            return SimpleNamespace(output_text=reply, activity=[])

    decision = TurnDecision(intent=intent, reason=f"Test decision: {intent}")
    return chat_turn_gate.TurnGateResult(
        envelope=envelope,
        decision=decision,
        trace=DecisionTrace(
            intent_signals=["model_classification"],
            matched_rules=["unified_turn_gate"],
            selected_action=intent,
            role_executed="turn_decision",
            board_access="forbidden" if intent != "learning_need" else "state_check_only",
            requirement_effect="preserved" if intent != "learning_need" else "eligible",
            document_changed=False,
            reason=decision.reason,
        ),
        adapter=FakeAdapter(),
    )


def test_turn_envelope_freezes_the_transport_contract() -> None:
    request = ChatRequest(
        message="Please handle this.",
        session_id="session_1",
        turn_id="turn_1",
        input_event_id="event_1",
        channel="realtime",
        input_kind="voice",
        provider_reference="provider_event_1",
        interaction_mode="direct_edit",
        selection=SelectionRef(kind="board", excerpt="selected secret text"),
    )

    envelope = chat_turn_gate.build_turn_envelope(
        request,
        lesson_id="lesson_1",
        selected_model=AIModelSelection(
            provider="openai_codex",
            model="test-model",
            access_method="chatgpt_subscription",
        ),
    )
    payload = envelope.model_dump(mode="json")

    assert payload["lesson_id"] == "lesson_1"
    assert payload["session_id"] == "session_1"
    assert payload["turn_id"] == "turn_1"
    assert payload["input_event_id"] == "event_1"
    assert payload["channel"] == "realtime"
    assert payload["input_kind"] == "voice"
    assert payload["provider_reference"] == "provider_event_1"
    assert payload["selected_model"]["model"] == "test-model"
    assert payload["explicit_action"] == "direct_edit"
    assert payload["message"] == request.message
    assert payload["has_selection"] is True
    assert payload["selection_kind"] == "board"
    assert payload["references"][0]["excerpt"] == "selected secret text"
    request.selection.excerpt = "mutated after envelope creation"
    assert envelope.references[0].excerpt == "selected secret text"


def test_model_turn_gate_runs_without_workspace_or_selection_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ChatRequest(
        message="I want to study a broad topic.",
        selection=SelectionRef(kind="board", excerpt="selected secret text"),
    )
    captured: dict[str, object] = {}

    class FakeAdapter:
        def parse_structured(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=TurnDecision(
                    intent="learning_need",
                    reason="The learner confirmed a learning goal.",
                ),
                activity=[],
            )

    monkeypatch.setattr(
        chat_turn_gate,
        "resolve_text_model_selection",
        lambda *_args, **_kwargs: AIModelSelection(
            provider="openai_codex",
            model="test-model",
            access_method="chatgpt_subscription",
        ),
    )
    monkeypatch.setattr(
        chat_turn_gate,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        workspace_state,
        "load_workspace_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TurnDecision must run before workspace access")
        ),
    )

    result = chat_turn_gate.evaluate_turn_gate(
        request,
        lesson_id="lesson_1",
        user_id=TEST_USER_ID,
    )

    assert result.decision.intent == "learning_need"
    assert result.trace.board_access == "state_check_only"
    routing_payload = json.loads(str(captured["user_prompt"]))
    assert routing_payload["reference_count"] == 1
    assert routing_payload["reference_kinds"] == ["board"]
    assert "references" not in routing_payload
    assert "selected secret text" not in str(routing_payload)
    assert "broad topic" in str(routing_payload)


def test_explicit_document_control_cannot_be_downgraded_by_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_called = False

    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            nonlocal parse_called
            parse_called = True
            raise AssertionError("Explicit controls must not be reclassified")

    monkeypatch.setattr(
        chat_turn_gate,
        "resolve_text_model_selection",
        lambda *_args, **_kwargs: AIModelSelection(
            provider="openai_codex",
            model="test-model",
            access_method="chatgpt_subscription",
        ),
    )
    monkeypatch.setattr(
        chat_turn_gate,
        "build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )

    result = chat_turn_gate.evaluate_turn_gate(
        ChatRequest(message="Do it.", interaction_mode="direct_edit"),
        lesson_id="lesson_1",
        user_id=TEST_USER_ID,
    )

    assert parse_called is False
    assert result.decision.intent == "learning_need"
    assert result.trace.intent_signals == ["direct_edit_mode"]


@pytest.mark.parametrize(
    ("intent", "message", "reply"),
    [
        ("ordinary_chat", "How has your day been?", "A natural conversational reply."),
        ("unclear", "I might want to do something useful.", "Which direction do you mean?"),
    ],
)
def test_non_learning_turn_short_circuits_before_workspace_and_preserves_active_state(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
    intent: str,
    message: str,
    reply: str,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    request = ChatRequest(
        message=message,
        selection=SelectionRef(kind="board", excerpt="selected secret text"),
    )
    workspace_reads: list[str] = []
    real_load = workspace_state.load_workspace_for_user

    def tracked_load(user_id: str):
        workspace_reads.append(user_id)
        return real_load(user_id)

    def fake_gate(*_args, **_kwargs):
        assert workspace_reads == []
        return _gate_result(request, intent, reply)

    def unexpected_learning_workflow(*_args, **_kwargs):
        raise AssertionError("non-learning turns must not enter the board workflow")

    def unexpected_title(*_args, **_kwargs):
        raise AssertionError("non-learning turns must not trigger an automatic lesson title")

    monkeypatch.setattr(workspace_state, "load_workspace_for_user", tracked_load)
    monkeypatch.setattr(chat_service, "evaluate_turn_gate", fake_gate)
    monkeypatch.setattr(
        chat_service,
        "process_codex_chat_on_lesson",
        unexpected_learning_workflow,
    )
    monkeypatch.setattr(chat_service, "maybe_generate_lesson_title", unexpected_title)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        request,
        user_id=TEST_USER_ID,
        on_requirement_update=lambda _payload: (_ for _ in ()).throw(
            AssertionError("non-learning turns must not update requirement state")
        ),
    )

    assert response.chatbot_message == reply
    assert response.turn_decision is not None
    assert response.turn_decision.intent == intent
    assert response.decision_trace is not None
    assert response.decision_trace.board_access == "forbidden"
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.theme == "Preserved learning requirement"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.question_or_topic == "Preserved board task"
    saved = turn_gate_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved.board_document.content_text.endswith(BOARD_SENTINEL)
    commit = current_head_commit(saved)
    assert commit.runtime_snapshot is not None
    assert commit.runtime_snapshot.learning_requirements == lesson.learning_requirements
    assert commit.runtime_snapshot.board_task_requirements == lesson.board_task_requirements
    assert commit.metadata["turn_decision"]["intent"] == intent
    assert commit.metadata["decision_trace"]["board_access"] == "forbidden"
    assert commit.metadata["requirement_changed"] is False
    assert commit.metadata["board_task_changed"] is False
    assert "active_requirement_sheet_after" not in commit.metadata
    assert "active_board_task_sheet_after" not in commit.metadata
    assert "board_state_before" not in commit.metadata


def test_learning_need_is_the_only_route_into_existing_workflow(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    request = ChatRequest(message="Explain the selected concept.")
    workflow_calls: list[str] = []
    title_calls: list[str] = []

    monkeypatch.setattr(
        chat_service,
        "evaluate_turn_gate",
        lambda *_args, **_kwargs: _gate_result(
            request,
            "learning_need",
            "unused",
        ),
    )

    def fake_existing_workflow(
        lesson_id,
        _request,
        *,
        user_id,
        adapter,
        selected_model,
        **_kwargs,
    ):
        assert user_id == TEST_USER_ID
        assert adapter is not None
        assert selected_model.model == "test-model"
        workflow_calls.append(lesson_id)
        return _workflow_response(lesson_id)

    def unexpected_legacy_workflow(*_args, **_kwargs):
        raise AssertionError("A normal non-empty board task must use the bounded workflow")

    def fake_title(lesson_id, _request, response, *, user_id):
        assert user_id == TEST_USER_ID
        title_calls.append(lesson_id)
        return response

    monkeypatch.setattr(
        chat_service,
        "process_existing_board_workflow",
        fake_existing_workflow,
    )
    monkeypatch.setattr(
        chat_service,
        "process_codex_chat_on_lesson",
        unexpected_legacy_workflow,
    )
    monkeypatch.setattr(chat_service, "maybe_generate_lesson_title", fake_title)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        request,
        user_id=TEST_USER_ID,
    )

    assert workflow_calls == [lesson.id]
    assert title_calls == [lesson.id]
    assert response.turn_decision is not None
    assert response.turn_decision.intent == "learning_need"
    assert response.decision_trace is not None
    assert response.decision_trace.board_access == "bounded_board_role"
    assert response.decision_trace.role_executed == "chatbot"
    assert response.decision_trace.intent_signals == [
        "model_classification",
        "board_task:explain",
    ]


def test_same_input_event_is_concurrently_executed_once_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    request = ChatRequest(
        message="A single delivered event.",
        session_id="session_once",
        turn_id="turn_once",
        input_event_id="event_once",
    )
    owner_started = Event()
    release_owner = Event()
    duplicate_execution = Event()
    calls = 0

    def fake_gate(gate_request, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            duplicate_execution.set()
        owner_started.set()
        assert release_owner.wait(timeout=2)
        return _gate_result(gate_request, "ordinary_chat", "Executed once.")

    monkeypatch.setattr(chat_service, "evaluate_turn_gate", fake_gate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            chat_service.process_chat_on_lesson,
            lesson.id,
            request,
            user_id=TEST_USER_ID,
        )
        assert owner_started.wait(timeout=2)
        second = executor.submit(
            chat_service.process_chat_on_lesson,
            lesson.id,
            request,
            user_id=TEST_USER_ID,
        )
        assert duplicate_execution.wait(timeout=0.1) is False
        release_owner.set()
        first_response = first.result(timeout=2)
        second_response = second.result(timeout=2)

    assert calls == 1
    assert first_response == second_response
    saved = turn_gate_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved)
    assert commit.metadata["chat_idempotency_key"] == "session_once:event_once"
    assert commit.metadata["chat_session_id"] == "session_once"
    assert commit.metadata["chat_input_event_id"] == "event_once"
    assert commit.metadata["chat_turn_id"] == "turn_once"


def test_same_text_in_different_input_events_is_not_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    calls: list[str] = []

    def fake_gate(gate_request, **_kwargs):
        calls.append(gate_request.input_event_id or "")
        return _gate_result(gate_request, "ordinary_chat", "Fresh event reply.")

    monkeypatch.setattr(chat_service, "evaluate_turn_gate", fake_gate)

    for event_id in ("event_a", "event_b"):
        chat_service.process_chat_on_lesson(
            lesson.id,
            ChatRequest(
                message="Identical text.",
                session_id="session_same_text",
                turn_id=f"turn_{event_id}",
                input_event_id=event_id,
            ),
            user_id=TEST_USER_ID,
        )

    assert calls == ["event_a", "event_b"]


def test_reused_input_event_rejects_a_different_request(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    calls = 0

    def fake_gate(gate_request, **_kwargs):
        nonlocal calls
        calls += 1
        return _gate_result(gate_request, "ordinary_chat", "Original reply.")

    monkeypatch.setattr(chat_service, "evaluate_turn_gate", fake_gate)
    identity = {
        "session_id": "session_collision",
        "turn_id": "turn_collision",
        "input_event_id": "event_collision",
    }
    chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Original request.", **identity),
        user_id=TEST_USER_ID,
    )

    with pytest.raises(ValueError, match="reused for a different request"):
        chat_service.process_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Conflicting request.", **identity),
            user_id=TEST_USER_ID,
        )

    assert calls == 1


def test_failed_input_event_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    turn_gate_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(turn_gate_store)
    request = ChatRequest(
        message="Retry this event.",
        session_id="session_retry",
        input_event_id="event_retry",
    )
    calls = 0

    def flaky_gate(gate_request, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return _gate_result(gate_request, "ordinary_chat", "Retry succeeded.")

    monkeypatch.setattr(chat_service, "evaluate_turn_gate", flaky_gate)

    with pytest.raises(RuntimeError, match="temporary failure"):
        chat_service.process_chat_on_lesson(
            lesson.id,
            request,
            user_id=TEST_USER_ID,
        )

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        request,
        user_id=TEST_USER_ID,
    )

    assert calls == 2
    assert response.chatbot_message == "Retry succeeded."

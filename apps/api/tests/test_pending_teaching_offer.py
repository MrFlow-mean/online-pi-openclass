from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.models import (
    AIModelSelection,
    BoardDecision,
    ChatRequest,
    ChatResponse,
    DecisionTrace,
    LearningClarificationStatus,
    TurnDecision,
)
from app.services import chat_service, chat_turn_gate, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.pending_teaching_offer import (
    PendingTeachingOfferDecision,
    build_pending_teaching_offer,
)
from app.services.rich_document import build_document

TEST_USER_ID = "user_pending_teaching_offer"
BOARD_SENTINEL = "BOARD_BODY_MUST_NOT_REACH_PENDING_OFFER_DECISION"
INVITATION = "The new board is ready. Would you like to start from the beginning?"


@pytest.fixture(autouse=True)
def reset_idempotency_state():
    chat_service._clear_idempotency_state_for_tests()
    yield
    chat_service._clear_idempotency_state_for_tests()


@pytest.fixture
def pending_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    return store


def _seed_generation_handoff(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Pending teaching lesson")
    lesson.board_document = build_document(
        title=lesson.board_document.title,
        document_id=lesson.board_document.id,
        content_text=f"# Generated board\n\n{BOARD_SENTINEL}",
    )
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    source_generation_commit_id = current_head_commit(lesson).id
    offer = build_pending_teaching_offer(
        invitation=INVITATION,
        source_generation_commit_id=source_generation_commit_id,
        requirement_run_id="requirement_run_1",
        requirement_version_id="requirement_version_1",
    )
    commit_operations(
        lesson,
        operations=[],
        label="Board generation handoff",
        message="The Chatbot offered to begin teaching.",
        metadata={
            "kind": "board_generation_handoff",
            "assistant_message": INVITATION,
            "pending_teaching_offer": offer.model_dump(mode="json"),
            "pending_teaching_offer_transition": "created",
            "document_changed": False,
        },
    )
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson.id, offer


class _FakeAdapter:
    def __init__(self, actions: list[str], *, reply: str = "Natural reply.") -> None:
        self.actions = actions
        self.reply = reply
        self.decision_payloads: list[dict[str, object]] = []
        self.completion_prompts: list[dict[str, object]] = []
        self.classifier_finished = False

    def parse_structured(self, **kwargs):
        assert kwargs["schema"] is PendingTeachingOfferDecision
        assert BOARD_SENTINEL not in kwargs["user_prompt"]
        payload = json.loads(kwargs["user_prompt"])
        assert set(payload) == {
            "current_message",
            "recent_conversation",
            "pending_invitation",
            "response_contract",
        }
        assert payload["pending_invitation"] == INVITATION
        self.decision_payloads.append(payload)
        self.classifier_finished = True
        return SimpleNamespace(
            output_parsed=PendingTeachingOfferDecision(
                action=self.actions.pop(0),
                reason="The current input has an unambiguous relation to the invitation.",
            ),
            activity=[],
        )

    def complete_text(self, **kwargs):
        assert self.classifier_finished is True
        assert BOARD_SENTINEL not in kwargs["user_prompt"]
        self.completion_prompts.append(json.loads(kwargs["user_prompt"]))
        if kwargs.get("on_text_delta") is not None:
            kwargs["on_text_delta"](self.reply)
        return SimpleNamespace(output_text=self.reply, activity=[])


def _gate(
    request: ChatRequest,
    *,
    lesson_id: str,
    adapter: _FakeAdapter,
    intent: str,
) -> chat_turn_gate.TurnGateResult:
    envelope = chat_turn_gate.build_turn_envelope(
        request,
        lesson_id=lesson_id,
        selected_model=AIModelSelection(
            provider="openai_codex",
            model="test-model",
            access_method="chatgpt_subscription",
        ),
    )
    decision = TurnDecision(intent=intent, reason=f"Router selected {intent}.")
    return chat_turn_gate.TurnGateResult(
        envelope=envelope,
        decision=decision,
        trace=DecisionTrace(
            intent_signals=["model_classification"],
            matched_rules=["unified_model_turn_gate"],
            selected_action=intent,
            role_executed="turn_decision",
            board_access=(
                "state_check_only" if intent == "learning_need" else "forbidden"
            ),
            requirement_effect=(
                "eligible" if intent == "learning_need" else "preserved"
            ),
            reason=decision.reason,
        ),
        adapter=adapter,
    )


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


def _response_for_lesson(lesson_id: str, *, message: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    return ChatResponse(
        chatbot_message=message,
        learning_requirement_sheet=build_requirements(lesson.title),
        learning_clarification=_neutral_clarification(),
        board_decision=BoardDecision(action="no_change", reason="Test result."),
        course_package=workspace_state.package_view_for_lesson(
            workspace,
            package,
            lesson.id,
        ),
    )


def _commit_test_transition(
    lesson_id: str,
    *,
    kind: str,
    message: str,
) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    branch_name = lesson.history_graph.current_branch
    base_commit_id = current_head_commit(lesson).id
    commit_operations(
        lesson,
        operations=[],
        label="Pending teaching test transition",
        message="Persist the successful downstream transition.",
        metadata={"kind": kind, "document_changed": False},
    )
    assert workspace_state.save_lesson_for_user_if_head(
        TEST_USER_ID,
        lesson,
        expected_branch_name=branch_name,
        expected_head_commit_id=base_commit_id,
    )
    return _response_for_lesson(lesson_id, message=message)


def _install_gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lesson_id: str,
    adapter_ref: list[_FakeAdapter],
    intent_ref: list[str],
) -> None:
    monkeypatch.setattr(
        chat_service,
        "evaluate_turn_gate",
        lambda request, **_kwargs: _gate(
            request,
            lesson_id=lesson_id,
            adapter=adapter_ref[0],
            intent=intent_ref[0],
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "maybe_generate_lesson_title",
        lambda _lesson_id, _request, response, *, user_id: response,
    )


def test_generation_handoff_accepts_yes_as_explicit_restart_from_section_zero(
    monkeypatch: pytest.MonkeyPatch,
    pending_store: SqliteCourseStore,
) -> None:
    lesson_id, offer = _seed_generation_handoff(pending_store)
    adapter = _FakeAdapter(["accept"])
    _install_gate(
        monkeypatch,
        lesson_id=lesson_id,
        adapter_ref=[adapter],
        intent_ref=["ordinary_chat"],
    )
    effective_requests: list[ChatRequest] = []
    real_load = workspace_state.load_workspace_for_user

    def tracked_full_load(user_id: str):
        assert adapter.classifier_finished is True
        return real_load(user_id)

    def fake_teaching(selected_lesson_id, request, **_kwargs):
        effective_requests.append(request)
        return _commit_test_transition(
            selected_lesson_id,
            kind="board_section_teaching",
            message="Section zero was taught.",
        )

    monkeypatch.setattr(workspace_state, "load_workspace_for_user", tracked_full_load)
    monkeypatch.setattr(chat_service, "process_codex_chat_on_lesson", fake_teaching)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="Yes, begin."),
        user_id=TEST_USER_ID,
    )

    assert effective_requests[0].teaching_action == "restart"
    assert response.turn_decision is not None
    assert response.turn_decision.continuation == "teaching_sequence"
    assert response.chatbot_message == "Section zero was taught."
    head = current_head_commit(pending_store.load_for_user(TEST_USER_ID).packages[0].lessons[0])
    assert head.metadata["pending_teaching_offer"] is None
    assert head.metadata["pending_teaching_offer_id"] == offer.offer_id
    assert head.metadata["pending_teaching_offer_transition"] == "accept"


def test_unrelated_turn_preserves_offer_across_refresh_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
    pending_store: SqliteCourseStore,
) -> None:
    lesson_id, offer = _seed_generation_handoff(pending_store)
    adapter_ref = [_FakeAdapter(["unrelated"], reply="A fresh ordinary reply.")]
    intent_ref = ["ordinary_chat"]
    _install_gate(
        monkeypatch,
        lesson_id=lesson_id,
        adapter_ref=adapter_ref,
        intent_ref=intent_ref,
    )

    first = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="An unrelated aside."),
        user_id=TEST_USER_ID,
    )

    assert first.chatbot_message == "A fresh ordinary reply."
    first_head = current_head_commit(
        pending_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    )
    assert first_head.metadata["pending_teaching_offer"]["offer_id"] == offer.offer_id
    assert first_head.metadata["pending_teaching_offer_transition"] == "unrelated"

    chat_service._clear_idempotency_state_for_tests()
    adapter_ref[0] = _FakeAdapter(["accept"])
    taught_requests: list[ChatRequest] = []

    def fake_teaching(selected_lesson_id, request, **_kwargs):
        taught_requests.append(request)
        return _commit_test_transition(
            selected_lesson_id,
            kind="board_section_teaching",
            message="Teaching resumed from section zero.",
        )

    monkeypatch.setattr(chat_service, "process_codex_chat_on_lesson", fake_teaching)
    second = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="Now I accept the invitation."),
        user_id=TEST_USER_ID,
    )

    assert taught_requests[0].teaching_action == "restart"
    assert second.chatbot_message == "Teaching resumed from section zero."
    second_head = current_head_commit(
        pending_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    )
    assert second_head.metadata["pending_teaching_offer"] is None
    assert second_head.metadata["pending_teaching_offer_transition"] == "accept"


def test_decline_uses_chatbot_natural_reply_and_clears_offer(
    monkeypatch: pytest.MonkeyPatch,
    pending_store: SqliteCourseStore,
) -> None:
    lesson_id, _offer = _seed_generation_handoff(pending_store)
    adapter = _FakeAdapter(["decline"], reply="A context-specific decline response.")
    _install_gate(
        monkeypatch,
        lesson_id=lesson_id,
        adapter_ref=[adapter],
        intent_ref=["learning_need"],
    )
    monkeypatch.setattr(
        chat_service,
        "process_codex_chat_on_lesson",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("A declined invitation must not start teaching")
        ),
    )

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="No, not now."),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "A context-specific decline response."
    assert adapter.completion_prompts[0]["conversation"][-1] == {
        "role": "assistant",
        "content": INVITATION,
    }
    head = current_head_commit(pending_store.load_for_user(TEST_USER_ID).packages[0].lessons[0])
    assert head.metadata["pending_teaching_offer"] is None
    assert head.metadata["pending_teaching_offer_transition"] == "decline"


def test_new_task_clears_offer_and_reruns_original_input_without_rewriting_it(
    monkeypatch: pytest.MonkeyPatch,
    pending_store: SqliteCourseStore,
) -> None:
    lesson_id, _offer = _seed_generation_handoff(pending_store)
    adapter = _FakeAdapter(["new_task"])
    _install_gate(
        monkeypatch,
        lesson_id=lesson_id,
        adapter_ref=[adapter],
        intent_ref=["learning_need"],
    )
    original_request = ChatRequest(message="Please perform a separate learning task.")
    workflow_requests: list[ChatRequest] = []

    def fake_workflow(selected_lesson_id, request, **_kwargs):
        workflow_requests.append(request)
        return _commit_test_transition(
            selected_lesson_id,
            kind="board_task_requirement_refinement",
            message="The separate task entered the normal workflow.",
        )

    monkeypatch.setattr(chat_service, "process_existing_board_workflow", fake_workflow)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        original_request,
        user_id=TEST_USER_ID,
    )

    assert workflow_requests == [original_request]
    assert response.turn_decision is not None
    assert response.turn_decision.relation_to_active == "new_task"
    assert response.chatbot_message == "The separate task entered the normal workflow."
    head = current_head_commit(pending_store.load_for_user(TEST_USER_ID).packages[0].lessons[0])
    assert head.metadata["pending_teaching_offer"] is None
    assert head.metadata["pending_teaching_offer_transition"] == "new_task"


def test_pending_offer_decision_failure_is_fail_closed_without_teaching(
    monkeypatch: pytest.MonkeyPatch,
    pending_store: SqliteCourseStore,
) -> None:
    lesson_id, offer = _seed_generation_handoff(pending_store)
    adapter = _FakeAdapter([])

    def fail_decision(**_kwargs):
        adapter.classifier_finished = True
        raise RuntimeError("decision unavailable")

    monkeypatch.setattr(adapter, "parse_structured", fail_decision)
    _install_gate(
        monkeypatch,
        lesson_id=lesson_id,
        adapter_ref=[adapter],
        intent_ref=["ordinary_chat"],
    )
    monkeypatch.setattr(
        chat_service,
        "process_codex_chat_on_lesson",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("A failed pending decision must not start teaching")
        ),
    )

    before = workspace_state.load_lesson_runtime_state_for_user(
        TEST_USER_ID,
        lesson_id,
    )
    with pytest.raises(RuntimeError, match="decision unavailable"):
        chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="An ambiguous response."),
            user_id=TEST_USER_ID,
        )
    after = workspace_state.load_lesson_runtime_state_for_user(
        TEST_USER_ID,
        lesson_id,
    )

    assert before is not None and after is not None
    assert after.head_commit_id == before.head_commit_id
    assert after.commit_metadata["pending_teaching_offer"]["offer_id"] == offer.offer_id

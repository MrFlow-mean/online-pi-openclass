from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.models import (
    AIModelSelection,
    BoardTaskRequirementSheet,
    CoursePackage,
    LessonRuntimeSnapshot,
    WorkspaceState,
)
from app.services.existing_board import (
    document_destination_workflow as destination_module,
)
from app.services.existing_board.document_destination_workflow import (
    NewLessonArtifact,
    WholeBoardReplacement,
    run_document_destination_workflow,
)
from app.services.existing_board.mutation_plan import board_document_hash
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document, document_to_markdown
from fastapi import HTTPException

BOARD_SENTINEL = "CURRENT_BOARD_FULL_TEXT_MUST_STAY_OUT_OF_NEW_LESSON_PROMPT"
REPLACEMENT_MARKDOWN = "# Replacement\n\nValidated complete replacement."


def _selected_model() -> AIModelSelection:
    return AIModelSelection(
        agent_backend="pi",
        provider="openai_codex",
        model="gpt-live-1-codex",
    )


def _workspace() -> WorkspaceState:
    source = create_empty_lesson("Source lesson")
    source_document = build_document(
        title="Source lesson",
        content_text=f"# Existing board\n\n{BOARD_SENTINEL}",
        document_id=source.board_document.id,
    )
    commit_operations(
        source,
        [],
        label="Existing board",
        message="Create the source fixture.",
        new_document=source_document,
        metadata={"kind": "test_fixture", "document_changed": True},
    )
    package = CoursePackage(
        id="package_destination_fixture",
        title="Destination fixture",
        summary="",
        lessons=[source],
        active_lesson_id=source.id,
        open_lesson_ids=[source.id],
        workspace_tab_order=[source.id],
    )
    return WorkspaceState(packages=[package], active_package_id=package.id)


def _task(
    source_lesson,
    *,
    destination: str,
    extent: str,
    action: str,
    confirmation_status: str = "confirmed",
) -> BoardTaskRequirementSheet:
    task = BoardTaskRequirementSheet(
        location_kind="insertion_anchor" if destination == "new_lesson" else "target_range",
        target_hint="an already confirmed document destination",
        location_status="content_absent" if destination == "new_lesson" else "resolved",
        requested_action=action,
        question_or_topic="create the confirmed complete learning document",
        special_interaction_requirements="none",
        content_extent=extent,
        topic_relation="independent" if destination == "new_lesson" else "current_document",
        document_destination=destination,
        base_commit_id=current_head_commit(source_lesson).id,
        base_document_hash=board_document_hash(source_lesson.board_document),
        missing_items=[],
        progress=100,
        confirmation_status=confirmation_status,
    )
    persisted_task = task.model_copy(
        deep=True,
        update={
            "confirmation_status": (
                "awaiting" if confirmation_status == "confirmed" else confirmation_status
            )
        },
    )
    current_head_commit(source_lesson).runtime_snapshot = LessonRuntimeSnapshot(
        board_task_requirements=persisted_task,
    )
    source_lesson.board_task_requirements = None
    return task


class RecordingAdapter:
    def __init__(self, structured_output: dict[str, object], *, text: str = "Generated status response") -> None:
        self.structured_output = structured_output
        self.text = text
        self.parse_calls: list[dict[str, object]] = []
        self.text_calls: list[dict[str, object]] = []
        self.call_instance_ids: list[int] = []

    def parse_structured(self, **kwargs):
        self.parse_calls.append(kwargs)
        self.call_instance_ids.append(id(self))
        return SimpleNamespace(output_parsed=self.structured_output, activity=[])

    def complete_text(self, **kwargs):
        self.text_calls.append(kwargs)
        self.call_instance_ids.append(id(self))
        return SimpleNamespace(output_text=self.text, activity=[])


class FakePersistence:
    def __init__(self, workspace: WorkspaceState, *, revision: int = 7) -> None:
        self.workspace = workspace.model_copy(deep=True)
        self.revision = revision
        self.workspace_conflict = False
        self.head_conflict = False
        self.workspace_saves = 0
        self.lesson_saves = 0

    def load_with_revision(self, _user_id: str):
        return self.workspace.model_copy(deep=True), self.revision

    def load(self, _user_id: str):
        return self.workspace.model_copy(deep=True)

    def save_workspace(self, _user_id: str, workspace: WorkspaceState, *, expected_revision: int):
        self.workspace_saves += 1
        if self.workspace_conflict or expected_revision != self.revision:
            raise HTTPException(status_code=409, detail="revision conflict")
        self.workspace = workspace.model_copy(deep=True)
        self.revision += 1

    def save_lesson(
        self,
        _user_id: str,
        lesson,
        *,
        expected_branch_name: str,
        expected_head_commit_id: str,
    ) -> bool:
        self.lesson_saves += 1
        if self.head_conflict:
            return False
        package, persisted = destination_module.workspace_state.find_lesson_package(
            self.workspace,
            lesson.id,
        )
        if (
            persisted.history_graph.current_branch != expected_branch_name
            or current_head_commit(persisted).id != expected_head_commit_id
        ):
            return False
        package.lessons = [
            lesson.model_copy(deep=True) if item.id == lesson.id else item
            for item in package.lessons
        ]
        self.revision += 1
        return True


@pytest.fixture
def persistence(monkeypatch) -> FakePersistence:
    fake = FakePersistence(_workspace())
    monkeypatch.setattr(
        destination_module.workspace_state,
        "load_workspace_for_user_with_revision",
        fake.load_with_revision,
    )
    monkeypatch.setattr(
        destination_module.workspace_state,
        "load_workspace_for_user",
        fake.load,
    )
    monkeypatch.setattr(
        destination_module.workspace_state,
        "save_workspace_for_user_if_revision",
        fake.save_workspace,
    )
    monkeypatch.setattr(
        destination_module.workspace_state,
        "save_lesson_for_user_if_head",
        fake.save_lesson,
    )
    return fake


def _source(fake: FakePersistence):
    return fake.workspace.packages[0].lessons[0]


def test_confirmed_new_lesson_is_added_and_source_task_archived_atomically(
    persistence: FakePersistence,
) -> None:
    source = _source(persistence)
    task = _task(
        source,
        destination="new_lesson",
        extent="article",
        action="write",
    )
    persistence.workspace = persistence.workspace.model_copy(deep=True)
    adapter = RecordingAdapter(
        {
            "title": "Generated independent lesson",
            "markdown": "# Generated lesson\n\nComplete generated content.",
        }
    )
    source_head = current_head_commit(source).id
    source_history_count = len(source.history_graph.commits)

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Create the confirmed destination document.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=source_head,
        expected_workspace_revision=7,
    )

    assert result.status == "succeeded"
    assert result.destination == "new_lesson"
    assert result.document_changed is True
    assert result.new_lesson_id
    assert persistence.workspace_saves == 1
    assert persistence.lesson_saves == 0
    persisted_package = persistence.workspace.packages[0]
    assert len(persisted_package.lessons) == 2
    persisted_source = next(item for item in persisted_package.lessons if item.id == source.id)
    generated = next(item for item in persisted_package.lessons if item.id == result.new_lesson_id)
    assert persisted_source.board_task_requirements is None
    assert len(persisted_source.history_graph.commits) == source_history_count + 1
    source_metadata = current_head_commit(persisted_source).metadata
    assert source_metadata["board_task_phase"] == "archived"
    assert source_metadata["user_message"] == "Create the confirmed destination document."
    assert source_metadata["assistant_message"] == "Generated status response"
    assert source_metadata["assistant_message_source"] == "document_destination_workflow"
    assert source_metadata["board_topic_relation"] == "independent"
    assert source_metadata["role_executions"][0]["role"] == "content_planner_editor"
    assert document_to_markdown(generated.board_document) == (
        "# Generated lesson\n\nComplete generated content."
    )
    generated_metadata = current_head_commit(generated).metadata
    assert generated_metadata["board_document_destination"] == "new_lesson"
    assert generated_metadata["board_topic_relation"] == "independent"
    assert generated_metadata["role_executions"][1]["role"] == "chatbot"
    assert adapter.call_instance_ids == [id(adapter), id(adapter)]
    assert adapter.parse_calls[0]["schema"] is NewLessonArtifact
    assert BOARD_SENTINEL not in str(adapter.parse_calls[0]["user_prompt"])
    assert BOARD_SENTINEL not in str(adapter.text_calls[0]["user_prompt"])
    chatbot_payload = json.loads(str(adapter.text_calls[0]["user_prompt"]))
    assert "markdown" not in chatbot_payload
    assert result.audit["input_scope"] == ["safe_board_task_fields"]
    assert result.audit["model"] == "gpt-live-1-codex"


@pytest.mark.parametrize(
    ("destination", "extent", "action"),
    [
        ("new_lesson", "article", "write"),
        ("current_lesson", "whole_board", "edit"),
    ],
)
def test_unconfirmed_high_range_action_is_zero_change_and_uses_no_model(
    persistence: FakePersistence,
    destination: str,
    extent: str,
    action: str,
) -> None:
    source = _source(persistence)
    task = _task(
        source,
        destination=destination,
        extent=extent,
        action=action,
        confirmation_status="awaiting",
    )
    adapter = RecordingAdapter({"title": "unused", "markdown": "unused"})
    before = persistence.workspace.model_dump(mode="json")

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Apply the high-range document action.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=current_head_commit(source).id,
        expected_workspace_revision=7,
    )

    assert result.status == "rejected"
    assert result.reason == "confirmation_required"
    assert result.document_changed is False
    assert adapter.parse_calls == []
    assert adapter.text_calls == []
    assert persistence.workspace.model_dump(mode="json") == before
    assert persistence.workspace_saves == 0
    assert persistence.lesson_saves == 0


def test_new_lesson_workspace_revision_conflict_persists_nothing(
    persistence: FakePersistence,
) -> None:
    source = _source(persistence)
    task = _task(source, destination="new_lesson", extent="article", action="write")
    adapter = RecordingAdapter(
        {"title": "Generated lesson", "markdown": "# Generated\n\nContent."}
    )
    persistence.workspace_conflict = True
    before = persistence.workspace.model_dump(mode="json")

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Create the destination lesson.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=current_head_commit(source).id,
        expected_workspace_revision=7,
    )

    assert result.status == "conflict"
    assert result.reason == "workspace_revision_conflict"
    assert result.document_changed is False
    assert persistence.workspace.model_dump(mode="json") == before
    assert len(persistence.workspace.packages[0].lessons) == 1


def test_confirmed_whole_board_replacement_uses_one_head_cas_commit(
    persistence: FakePersistence,
) -> None:
    source = _source(persistence)
    task = _task(
        source,
        destination="current_lesson",
        extent="whole_board",
        action="edit",
    )
    adapter = RecordingAdapter({"markdown": REPLACEMENT_MARKDOWN})
    source_head = current_head_commit(source).id
    history_count = len(source.history_graph.commits)

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Replace the complete current board.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=source_head,
    )

    assert result.status == "succeeded"
    assert result.destination == "current_lesson"
    assert persistence.lesson_saves == 1
    assert persistence.workspace_saves == 0
    persisted = _source(persistence)
    assert document_to_markdown(persisted.board_document) == REPLACEMENT_MARKDOWN
    assert len(persisted.history_graph.commits) == history_count + 1
    assert persisted.board_task_requirements is None
    metadata = current_head_commit(persisted).metadata
    assert metadata["board_content_extent"] == "whole_board"
    assert metadata["board_topic_relation"] == "current_document"
    assert metadata["board_document_destination"] == "current_lesson"
    assert metadata["user_message"] == "Replace the complete current board."
    assert metadata["assistant_message"] == "Generated status response"
    assert metadata["assistant_message_source"] == "document_destination_workflow"
    assert metadata["role_executions"][0]["role"] == "content_planner_editor"
    assert metadata["input_scope"] == ["safe_board_task_fields", "current_board_full_markdown"]
    assert adapter.parse_calls[0]["schema"] is WholeBoardReplacement
    assert BOARD_SENTINEL in str(adapter.parse_calls[0]["user_prompt"])
    assert BOARD_SENTINEL not in str(adapter.text_calls[0]["user_prompt"])


def test_whole_board_head_conflict_persists_nothing(
    persistence: FakePersistence,
) -> None:
    source = _source(persistence)
    task = _task(
        source,
        destination="current_lesson",
        extent="whole_board",
        action="edit",
    )
    adapter = RecordingAdapter({"markdown": REPLACEMENT_MARKDOWN})
    persistence.head_conflict = True
    before = persistence.workspace.model_dump(mode="json")

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Replace the complete current board.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=current_head_commit(source).id,
    )

    assert result.status == "conflict"
    assert result.reason == "source_head_conflict"
    assert result.document_changed is False
    assert persistence.workspace.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("active_state", "reason"),
    [
        ("missing", "active_board_task_missing"),
        ("tampered", "active_board_task_semantic_mismatch"),
        ("not_awaiting", "active_board_task_not_awaiting_confirmation"),
    ],
)
def test_missing_or_tampered_persisted_active_task_is_zero_model_zero_change(
    persistence: FakePersistence,
    active_state: str,
    reason: str,
) -> None:
    source = _source(persistence)
    task = _task(source, destination="new_lesson", extent="article", action="write")
    runtime = current_head_commit(source).runtime_snapshot
    assert runtime is not None
    if active_state == "missing":
        runtime.board_task_requirements = None
    elif active_state == "tampered":
        assert runtime.board_task_requirements is not None
        runtime.board_task_requirements = runtime.board_task_requirements.model_copy(
            deep=True,
            update={"question_or_topic": "persisted task with different semantics"},
        )
    else:
        assert runtime.board_task_requirements is not None
        runtime.board_task_requirements = runtime.board_task_requirements.model_copy(
            deep=True,
            update={"confirmation_status": "confirmed"},
        )
    adapter = RecordingAdapter(
        {"title": "Must not run", "markdown": "Must not run"}
    )
    before = persistence.workspace.model_dump(mode="json")

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Create a destination lesson.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=current_head_commit(source).id,
        expected_workspace_revision=7,
    )

    assert result.status == "conflict"
    assert result.reason == reason
    assert result.document_changed is False
    assert adapter.parse_calls == []
    assert adapter.text_calls == []
    assert persistence.workspace.model_dump(mode="json") == before
    assert persistence.workspace_saves == 0
    assert persistence.lesson_saves == 0


@pytest.mark.parametrize(
    ("destination", "extent", "action", "output"),
    [
        ("new_lesson", "article", "write", {"title": "", "markdown": ""}),
        ("current_lesson", "whole_board", "edit", {"markdown": "   "}),
    ],
)
def test_empty_model_document_output_is_zero_change(
    persistence: FakePersistence,
    destination: str,
    extent: str,
    action: str,
    output: dict[str, object],
) -> None:
    source = _source(persistence)
    task = _task(source, destination=destination, extent=extent, action=action)
    adapter = RecordingAdapter(output)
    before = persistence.workspace.model_dump(mode="json")

    result = run_document_destination_workflow(
        user_id="user_destination",
        source_lesson_id=source.id,
        user_message="Generate the confirmed destination document.",
        board_task=task,
        adapter=adapter,
        selected_model=_selected_model(),
        expected_source_head_commit_id=current_head_commit(source).id,
        expected_workspace_revision=7,
    )

    assert result.status == "rejected"
    assert result.reason == "empty_model_document"
    assert result.document_changed is False
    assert adapter.text_calls == []
    assert persistence.workspace.model_dump(mode="json") == before
    assert persistence.workspace_saves == 0
    assert persistence.lesson_saves == 0

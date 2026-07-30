from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from app.models import (
    AIModelSelection,
    AgentActivityEvent,
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    BoardTaskRunStatus,
    ChatRequest,
    ChatResponse,
    DecisionTrace,
    LearningClarificationStatus,
    Lesson,
    SelectionRef,
    new_id,
)
from app.services import workspace_state
from app.services.ai_execution_adapter import AIExecutionAdapter
from app.services.existing_board import task_manager
from app.services.existing_board.explanation_workflow import (
    BoardFreeRecentConversation,
    run_existing_board_explanation,
)
from app.services.existing_board.focus_resolver import (
    FocusResolver,
    TargetResolution,
)
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import build_requirements


TASK_CHATBOT_INSTRUCTIONS = """
You are the learner-facing Chatbot for an existing-board task. Use only the
structured task and bounded target candidates in the payload. In
task_clarification mode, generate exactly one context-specific question that
resolves the most important missing field or candidate choice. In pending-stage
modes, generate only the status or confirmation response appropriate to the
structured task. Do not explain lesson content, perform the requested action,
claim a document change, invent board text, or reuse fixed wording.
""".strip()

TaskResponseMode = Literal[
    "task_clarification",
    "future_action_status",
    "confirmation_required",
]


class ExistingBoardWorkflowError(RuntimeError):
    pass


def process_existing_board_workflow(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    adapter: AIExecutionAdapter,
    selected_model: AIModelSelection,
    on_delta: Callable[[str], None] | None = None,
    on_board_task_update: Callable[[dict[str, object]], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if not lesson.board_document.content_text.strip():
        raise ExistingBoardWorkflowError("Existing-board workflow requires a non-empty board")

    branch_name = lesson.history_graph.current_branch
    base_head = current_head_commit(lesson)
    active_task = _restore_active_task(lesson, base_head)
    manager_input = task_manager.build_task_manager_input(
        message=request.message,
        conversation=request.conversation,
        explicit_controls=_explicit_controls(request),
        references=_frozen_references(request),
        active_task=_active_task_summary(active_task),
    )
    manager_response = adapter.parse_structured(
        system_prompt=task_manager.TASK_MANAGER_INSTRUCTIONS,
        user_prompt=json.dumps(
            manager_input.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        schema=task_manager.BoardTaskManagerDraft,
        allow_live_web_search=False,
        on_activity=on_agent_activity,
    )
    draft = task_manager.BoardTaskManagerDraft.model_validate(
        manager_response.output_parsed
    )
    decision = task_manager._finalize_decision(draft)
    activity = list(getattr(manager_response, "activity", []))
    resolution = _resolve_target(lesson, request, decision.target_hint)
    task = _build_task_sheet(
        lesson,
        decision,
        resolution,
        active_task=active_task,
        base_commit_id=base_head.id,
    )
    run_id = _task_run_id(base_head.metadata, active_task, decision.relation_to_active)
    version_id = new_id("boardtaskver")
    phase = _next_phase(task, decision.requires_confirmation)
    roles = [
        _role_execution("task_manager", selected_model, ["message", "recent_conversation", "reference_identity", "active_task_summary"]),
        _role_execution("focus_resolver", None, ["board_segment_index", "target_hint", "frozen_selection_identity"]),
    ]

    if phase == "collecting" or task.requested_action != "explain":
        needs_clarification = phase == "collecting"
        response_mode: TaskResponseMode = "task_clarification"
        if not needs_clarification:
            response_mode = (
                "confirmation_required"
                if phase == "awaiting_confirmation"
                else "future_action_status"
            )
        chatbot_message, chatbot_activity = _generate_task_message(
            adapter,
            task,
            mode=response_mode,
            on_activity=on_agent_activity,
            is_cancelled=is_cancelled,
        )
        activity.extend(chatbot_activity)
        if needs_clarification:
            task.clarification_question = chatbot_message
        roles.append(
            _role_execution("chatbot", selected_model, ["board_task_sheet", "bounded_target_candidates"])
        )
        trace = _decision_trace(decision, task, phase, role="chatbot")
        _persist_task_phase(
            lesson,
            user_id=user_id,
            expected_branch=branch_name,
            expected_head=base_head.id,
            task=task,
            phase=phase,
            run_id=run_id,
            version_id=version_id,
            request=request,
            chatbot_message=chatbot_message,
            selected_model=selected_model,
            decision=decision,
            trace=trace,
            roles=roles,
        )
        _publish_task_update(on_board_task_update, task, run_id, version_id, phase)
        _publish_delta(on_delta, chatbot_message)
        return _build_response(
            lesson_id=lesson_id,
            user_id=user_id,
            task=task,
            active_task=task,
            phase=phase,
            run_id=run_id,
            version_id=version_id,
            chatbot_message=chatbot_message,
            activity=activity,
            decision_reason=decision.reason,
            needs_clarification=needs_clarification,
        )

    assert task.target_location is not None
    ready_trace = _decision_trace(decision, task, "ready", role="focus_resolver")
    ready_roles = list(roles)
    ready_commit_id = _persist_task_phase(
        lesson,
        user_id=user_id,
        expected_branch=branch_name,
        expected_head=base_head.id,
        task=task,
        phase="ready",
        run_id=run_id,
        version_id=version_id,
        request=request,
        chatbot_message="",
        selected_model=selected_model,
        decision=decision,
        trace=ready_trace,
        roles=ready_roles,
    )
    _publish_task_update(on_board_task_update, task, run_id, version_id, "ready")
    explanation = run_existing_board_explanation(
        adapter=adapter,
        board_task=task,
        resolved_focus=task.target_location,
        teaching_requirements=[task.question_or_topic],
        current_user_message=request.message,
        recent_conversation=BoardFreeRecentConversation(
            board_content_included=False,
            turns=[],
        ),
        is_cancelled=is_cancelled,
        on_activity=on_agent_activity,
        on_text_delta=None,
    )
    activity.extend(explanation.activity)
    final_roles = [
        *ready_roles,
        _role_execution("board_manager", selected_model, ["board_task_sheet", "resolved_target_excerpt", "teaching_requirements"]),
        _role_execution("chatbot", selected_model, ["board_explanation_directive", "current_user_message"]),
    ]
    final_phase: BoardTaskRunStatus = (
        "consumed"
        if explanation.substantive_explanation_allowed
        else (
            "collecting"
            if explanation.directive.status == "needs_clarification"
            else "not_executed"
        )
    )
    needs_final_clarification = final_phase == "collecting"
    if needs_final_clarification:
        task.clarification_question = explanation.chatbot_message
    latest_workspace, latest_lesson = _load_expected_lesson(
        lesson_id,
        user_id=user_id,
        branch_name=branch_name,
        head_commit_id=ready_commit_id,
    )
    del latest_workspace
    clear_final_task = final_phase in {"consumed", "not_executed"}
    latest_lesson.board_task_requirements = None if clear_final_task else task
    final_trace = _decision_trace(decision, task, final_phase, role="chatbot")
    _persist_task_phase(
        latest_lesson,
        user_id=user_id,
        expected_branch=branch_name,
        expected_head=ready_commit_id,
        task=task,
        phase=final_phase,
        run_id=run_id,
        version_id=version_id,
        request=request,
        chatbot_message=explanation.chatbot_message,
        selected_model=selected_model,
        decision=decision,
        trace=final_trace,
        roles=final_roles,
        directive=explanation.directive.model_dump(mode="json"),
        clear_active=clear_final_task,
    )
    _publish_task_update(on_board_task_update, task, run_id, version_id, final_phase)
    _publish_delta(on_delta, explanation.chatbot_message)
    return _build_response(
        lesson_id=lesson_id,
        user_id=user_id,
        task=task,
        active_task=(None if clear_final_task else task),
        phase=final_phase,
        run_id=run_id,
        version_id=version_id,
        chatbot_message=explanation.chatbot_message,
        activity=activity,
        decision_reason=decision.reason,
        needs_clarification=needs_final_clarification,
    )


def _restore_active_task(lesson: Lesson, head) -> BoardTaskRequirementSheet | None:
    runtime = head.runtime_snapshot
    active = runtime.board_task_requirements if runtime is not None else None
    lesson.board_task_requirements = active.model_copy(deep=True) if active else None
    return lesson.board_task_requirements


def _explicit_controls(request: ChatRequest) -> task_manager.BoardTaskExplicitControls:
    action = "edit" if request.interaction_mode == "direct_edit" else None
    location_kind = None
    for reference in _frozen_references(request):
        if reference.location_kind in {"target_range", "insertion_anchor"}:
            location_kind = reference.location_kind
            break
    return task_manager.BoardTaskExplicitControls(action=action, location_kind=location_kind)


def _frozen_references(request: ChatRequest) -> list[SelectionRef]:
    references = [*([request.selection] if request.selection is not None else []), *request.selections]
    unique: dict[str, SelectionRef] = {}
    for reference in references:
        key = json.dumps(reference.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        unique.setdefault(key, reference.model_copy(deep=True))
    return list(unique.values())[:8]


def _active_task_summary(
    active: BoardTaskRequirementSheet | None,
) -> task_manager.ActiveBoardTaskSummary | None:
    if active is None:
        return None
    action = "interact" if active.requested_action == "chat" else active.requested_action
    return task_manager.ActiveBoardTaskSummary(
        action=action,
        location_kind=(active.location_kind if active.location_kind != "unspecified" else None),
        target_hint=active.target_hint,
        question_or_topic=active.question_or_topic,
        special_interaction_requirements=active.special_interaction_requirements or "none",
        extent=active.content_extent,
        destination=active.document_destination,
        topic_relation=active.topic_relation,
    )


def _resolve_target(
    lesson: Lesson,
    request: ChatRequest,
    target_hint: str,
) -> TargetResolution:
    resolver = FocusResolver()
    selections = [item for item in _frozen_references(request) if item.kind == "board"]
    if not selections:
        return resolver.resolve(lesson, target_text=target_hint)
    results = [resolver.resolve(lesson, selection=item) for item in selections]
    resolved = [item.focus for item in results if item.status == "resolved" and item.focus]
    unique = {item.segment_id: item for item in resolved}
    if len(results) == len(resolved) and len(unique) == 1:
        return TargetResolution(
            status="resolved",
            machine_reason="resolved_by_selection",
            focus=next(iter(unique.values())),
        )
    candidates: dict[str, BoardFocusRef] = {}
    for result in results:
        for focus in [*([result.focus] if result.focus else []), *result.candidates]:
            candidates.setdefault(focus.segment_id or focus.match_id or focus.excerpt, focus)
    return TargetResolution(
        status="target_not_resolved",
        machine_reason="ambiguous_candidates",
        candidates=list(candidates.values())[:5],
    )


def _build_task_sheet(
    lesson: Lesson,
    decision: task_manager.BoardTaskManagerDecision,
    resolution: TargetResolution,
    *,
    active_task: BoardTaskRequirementSheet | None,
    base_commit_id: str,
) -> BoardTaskRequirementSheet:
    missing = list(dict.fromkeys(decision.missing_items))
    if resolution.status == "resolved":
        missing = [item for item in missing if item != "target"]
    elif "target" not in missing:
        missing.append("target")
    relation_continues = decision.relation_to_active in {"continue", "supplement"}
    task_id = active_task.task_id if active_task is not None and relation_continues else new_id("boardtask")
    action = None if decision.action == "unresolved" else decision.action
    location_kind = (
        decision.location_kind
        if decision.location_kind in {"target_range", "insertion_anchor"}
        else "unspecified"
    )
    candidates = [_safe_focus(item) for item in resolution.candidates[:5]]
    focus = resolution.focus.model_copy(deep=True) if resolution.focus is not None else None
    location_status = (
        "resolved"
        if focus is not None
        else ("ambiguous" if candidates else "missing")
    )
    return BoardTaskRequirementSheet(
        task_id=task_id,
        location_kind=location_kind,
        target_hint=decision.target_hint.strip(),
        target_location=focus,
        location_status=location_status,
        requested_action=action,
        question_or_topic=decision.question_or_topic.strip(),
        special_interaction_requirements=decision.special_interaction_requirements.strip(),
        content_extent=(None if decision.extent == "unresolved" else decision.extent),
        topic_relation=decision.topic_relation,
        document_destination=decision.destination,
        target_candidates=candidates,
        target_resolution_reason=resolution.machine_reason,
        base_commit_id=base_commit_id,
        base_document_hash=_document_hash(lesson),
        missing_items=missing,
        progress=(100 if not missing else min(decision.completeness, 99)),
        confirmation_status=("awaiting" if decision.requires_confirmation else "none"),
    )


def _next_phase(
    task: BoardTaskRequirementSheet,
    requires_confirmation: bool,
) -> BoardTaskRunStatus:
    if task.missing_items or task.location_status != "resolved" or task.requested_action is None:
        return "collecting"
    return "awaiting_confirmation" if requires_confirmation else "ready"


def _generate_task_message(
    adapter: AIExecutionAdapter,
    task: BoardTaskRequirementSheet,
    *,
    mode: TaskResponseMode,
    on_activity: Callable[[AgentActivityEvent], None] | None,
    is_cancelled: Callable[[], bool] | None,
) -> tuple[str, list[AgentActivityEvent]]:
    safe_task = task.model_copy(
        deep=True,
        update={
            "target_location": _safe_focus(task.target_location),
            "target_candidates": [_safe_focus(item) for item in task.target_candidates],
            "mutation_plan": None,
            "interaction_session": None,
        },
    )
    response = adapter.complete_text(
        system_prompt=TASK_CHATBOT_INSTRUCTIONS,
        user_prompt=json.dumps(
            {
                "response_mode": mode,
                "board_task_requirement_sheet": safe_task.model_dump(mode="json"),
                "target_candidates": [
                    item.model_dump(mode="json") for item in safe_task.target_candidates
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_cancelled=is_cancelled,
        on_activity=on_activity,
        on_text_delta=None,
    )
    message = response.output_text.strip()
    if not message:
        raise ExistingBoardWorkflowError("Task Chatbot returned an empty response")
    return message, list(getattr(response, "activity", []))


def _persist_task_phase(
    lesson: Lesson,
    *,
    user_id: str,
    expected_branch: str,
    expected_head: str,
    task: BoardTaskRequirementSheet,
    phase: BoardTaskRunStatus,
    run_id: str,
    version_id: str,
    request: ChatRequest,
    chatbot_message: str,
    selected_model: AIModelSelection,
    decision: task_manager.BoardTaskManagerDecision,
    trace: dict[str, object],
    roles: list[dict[str, object]],
    directive: dict[str, object] | None = None,
    clear_active: bool = False,
) -> str:
    lesson.board_task_requirements = None if clear_active else task.model_copy(deep=True)
    focus = task.target_location.model_dump(mode="json") if task.target_location else None
    document_hash = _document_hash(lesson)
    commit_operations(
        lesson,
        operations=[],
        label=f"Board task {phase}",
        message=f"Recorded board task phase {phase}.",
        new_document=lesson.board_document,
        metadata={
            "kind": ("board_directed_explanation" if phase == "consumed" else "board_task_requirement_refinement"),
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "existing_board_workflow",
            "document_changed": False,
            "document_write_authorized": False,
            "document_hash_before": document_hash,
            "document_hash_after": document_hash,
            "board_task_run_id": run_id,
            "board_task_version_id": version_id,
            "board_task_phase": phase,
            "board_task_route": (
                "clarify_location"
                if phase == "collecting"
                else (
                    "await_write_confirmation"
                    if phase == "awaiting_confirmation"
                    else (task.requested_action or "clarify_location")
                )
            ),
            "board_task_decision": decision.model_dump(mode="json"),
            "board_task_cleared": clear_active,
            "active_board_task_sheet_after": None if clear_active else task.model_dump(mode="json"),
            "resolved_focus": focus,
            "target_candidates": [item.model_dump(mode="json") for item in task.target_candidates],
            "board_explanation_directive": directive,
            "decision_trace": trace,
            "selected_model": selected_model.model_dump(mode="json"),
            "ai_provider": selected_model.provider,
            "ai_model": selected_model.model,
            "agent_backend": selected_model.agent_backend,
            "role_executions": roles,
        },
    )
    if not workspace_state.save_lesson_for_user_if_head(
        user_id,
        lesson,
        expected_branch_name=expected_branch,
        expected_head_commit_id=expected_head,
    ):
        raise ExistingBoardWorkflowError("The lesson changed while the existing-board workflow was working")
    return current_head_commit(lesson).id


def _build_response(
    *,
    lesson_id: str,
    user_id: str,
    task: BoardTaskRequirementSheet,
    active_task: BoardTaskRequirementSheet | None,
    phase: BoardTaskRunStatus,
    run_id: str,
    version_id: str,
    chatbot_message: str,
    activity: list[AgentActivityEvent],
    decision_reason: str,
    needs_clarification: bool,
) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    clarification = _neutral_clarification(chatbot_message if needs_clarification else "")
    return ChatResponse(
        chatbot_message=chatbot_message,
        decision_trace=DecisionTrace(
            intent_signals=[f"board_task:{task.requested_action or 'unresolved'}"],
            matched_rules=["existing_board_bounded_workflow"],
            selected_action="learning_need",
            target_resolver="FocusResolver",
            role_executed="chatbot",
            board_access="bounded_board_role",
            requirement_effect="updated",
            document_changed=False,
            reason=decision_reason,
        ),
        agent_activity=activity,
        learning_requirement_sheet=build_requirements(lesson.title),
        active_requirement_sheet=None,
        learning_clarification=clarification,
        board_task_sheet=task,
        active_board_task_sheet=active_task,
        board_task_run_id=run_id,
        board_task_version_id=version_id,
        board_task_phase=phase,
        board_task_questions=([chatbot_message] if needs_clarification else []),
        board_decision=BoardDecision(action="no_change", reason=decision_reason),
        needs_clarification=needs_clarification,
        clarification_questions=([chatbot_message] if needs_clarification else []),
        requirement_cleared=False,
        board_document_operation_status="none",
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _load_expected_lesson(
    lesson_id: str,
    *,
    user_id: str,
    branch_name: str,
    head_commit_id: str,
):
    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if lesson.history_graph.current_branch != branch_name or current_head_commit(lesson).id != head_commit_id:
        raise ExistingBoardWorkflowError("The lesson changed while the existing-board workflow was working")
    return workspace, lesson


def _task_run_id(
    metadata: dict[str, object],
    active_task: BoardTaskRequirementSheet | None,
    relation: str,
) -> str:
    existing = metadata.get("board_task_run_id")
    if active_task is not None and relation in {"continue", "supplement"} and isinstance(existing, str) and existing:
        return existing
    return new_id("boardtaskrun")


def _safe_focus(focus: BoardFocusRef | None) -> BoardFocusRef | None:
    if focus is None:
        return None
    return focus.model_copy(deep=True, update={"before_text": "", "after_text": ""})


def _decision_trace(
    decision: task_manager.BoardTaskManagerDecision,
    task: BoardTaskRequirementSheet,
    phase: str,
    *,
    role: str,
) -> dict[str, object]:
    return {
        "intent_signals": [f"board_task:{decision.action}"],
        "matched_rules": ["existing_board_bounded_workflow"],
        "selected_action": task.requested_action or "clarify_location",
        "target_resolver": "FocusResolver",
        "sequence_mode": "single_turn",
        "role_executed": role,
        "board_access": "bounded_board_role",
        "requirement_effect": "updated",
        "document_changed": False,
        "board_task_phase": phase,
        "reason": decision.reason,
    }


def _role_execution(
    role: str,
    model: AIModelSelection | None,
    input_scope: list[str],
) -> dict[str, object]:
    return {
        "role": role,
        "model": model.model_dump(mode="json") if model is not None else None,
        "input_scope": input_scope,
        "document_write_allowed": False,
    }


def _neutral_clarification(question: str) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="",
        reason="",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question=question,
        ready_for_board=False,
    )


def _publish_task_update(
    callback: Callable[[dict[str, object]], None] | None,
    task: BoardTaskRequirementSheet,
    run_id: str,
    version_id: str,
    phase: BoardTaskRunStatus,
) -> None:
    if callback is not None:
        callback(
            {
                "board_task_sheet": task.model_dump(mode="json"),
                "active_board_task_sheet": (None if phase in {"consumed", "not_executed"} else task.model_dump(mode="json")),
                "board_task_run_id": run_id,
                "board_task_version_id": version_id,
                "board_task_phase": phase,
            }
        )


def _publish_delta(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None and message:
        callback(message)


def _document_hash(lesson: Lesson) -> str:
    return hashlib.sha256(lesson.board_document.content_text.encode("utf-8")).hexdigest()

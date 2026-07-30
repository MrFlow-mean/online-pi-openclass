from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    BoardContentExtent,
    LearningRequirementSheet,
    new_id,
)
from app.services.ai_logging import (
    ai_log_context,
    ai_usage_logger,
    current_ai_log_context,
)
from app.services.codex_app_server import CodexAppServerTextClient
from app.services.deepseek_api import DeepSeekTextClient
from app.services.pi_agent_runtime import PiTextClient

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
ExecutionValue = TypeVar("ExecutionValue")

_AUDIT_CONTEXT_KEYS = (
    "workflow_run_id",
    "delegation_id",
    "input_event_id",
    "session_id",
    "channel",
    "input_kind",
    "provider_reference",
)


def _audit_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _structured_payload_fields(user_prompt: str) -> list[str]:
    """Return JSON field names without retaining any prompt values."""

    try:
        payload = json.loads(user_prompt)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload)


def _prompt_input_scope(
    *,
    system_prompt: str,
    user_prompt: str,
    image_inputs: list[str] | None,
    allow_live_web_search: bool | None = None,
) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "input_fields": ["system_prompt", "user_prompt", "image_inputs"],
        "structured_payload_fields": _structured_payload_fields(user_prompt),
        "system_prompt_character_count": len(system_prompt),
        "system_prompt_sha256": _audit_digest(system_prompt),
        "user_prompt_character_count": len(user_prompt),
        "user_prompt_sha256": _audit_digest(user_prompt),
        "image_count": len(image_inputs or []),
    }
    if allow_live_web_search is not None:
        scope["input_fields"].append("allow_live_web_search")
        scope["live_web_search_allowed"] = allow_live_web_search
    return scope


def _safe_result_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel) or hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            "value_type": "object",
            "field_names": sorted(str(key) for key in value),
            "field_count": len(value),
        }
    if isinstance(value, (list, tuple, set)):
        return {"value_type": "array", "item_count": len(value)}
    if isinstance(value, str):
        return {
            "value_type": "text",
            "character_count": len(value),
            "sha256": _audit_digest(value),
        }
    return {"value_type": type(value).__name__}


def _schema_type_matches(value: Any, schema_type: object) -> bool:
    return (
        (schema_type == "null" and value is None)
        or (schema_type == "object" and isinstance(value, dict))
        or (schema_type == "array" and isinstance(value, (list, tuple)))
        or (schema_type == "string" and isinstance(value, str))
        or (schema_type == "boolean" and isinstance(value, bool))
        or (
            schema_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (
            schema_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    )


def _resolve_audit_schema(
    schema_node: dict[str, Any],
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    reference = schema_node.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema_node
    resolved: Any = root_schema
    for part in reference[2:].split("/"):
        if not isinstance(resolved, dict):
            return schema_node
        resolved = resolved.get(part.replace("~1", "/").replace("~0", "~"))
    return resolved if isinstance(resolved, dict) else schema_node


def _select_audit_schema_variant(
    schema_node: dict[str, Any],
    root_schema: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    resolved = _resolve_audit_schema(schema_node, root_schema)
    variants = resolved.get("anyOf") or resolved.get("oneOf")
    if not isinstance(variants, list):
        return resolved
    candidates = [
        _resolve_audit_schema(item, root_schema)
        for item in variants
        if isinstance(item, dict)
    ]
    for candidate in candidates:
        enum_values = candidate.get("enum")
        if ("const" in candidate and value == candidate["const"]) or (
            isinstance(enum_values, list) and value in enum_values
        ):
            return candidate
        if _schema_type_matches(value, candidate.get("type")):
            return candidate
    return candidates[0] if candidates else resolved


def _safe_validated_value(
    value: Any,
    schema_node: dict[str, Any],
    root_schema: dict[str, Any],
) -> Any:
    if isinstance(value, BaseModel) or hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    schema_node = _select_audit_schema_variant(schema_node, root_schema, value)
    if isinstance(value, dict):
        properties = schema_node.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        return {
            str(key): _safe_validated_value(
                item,
                property_schemas.get(str(key), {}),
                root_schema,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        item_schema = schema_node.get("items")
        safe_item_schema = item_schema if isinstance(item_schema, dict) else {}
        return [
            _safe_validated_value(item, safe_item_schema, root_schema) for item in value
        ]
    if isinstance(value, str):
        enum_values = schema_node.get("enum")
        if ("const" in schema_node and value == schema_node["const"]) or (
            isinstance(enum_values, list) and value in enum_values
        ):
            return value
        return _safe_result_summary(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return {"value_type": type(value).__name__}


def _safe_validated_result(value: Any, schema: type[BaseModel]) -> Any:
    root_schema = schema.model_json_schema()
    return _safe_validated_value(value, root_schema, root_schema)


def _contract_logical_role(schema: type[BaseModel]) -> str:
    """Map structural response contracts to workflow roles, never prompt wording."""

    contract = schema.__name__
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", contract).lower()
    if contract == "TurnDecision" or "PendingTeachingOffer" in contract:
        return "turn_router"
    if "BlankBoard" in contract and "Decision" in contract:
        return "content_planner"
    if "TaskManager" in contract:
        return "content_planner"
    if "ExplanationDirective" in contract:
        return "board_manager"
    if "Mutation" in contract and any(
        marker in contract for marker in ("Draft", "Plan", "Operation")
    ):
        return "content_planner_editor"
    if "Interaction" in contract:
        return "interaction_session"
    if contract.endswith(("Response", "Reply", "Handoff")):
        return "chatbot"
    return f"structured_role:{normalized}"


def _run_audited_role(
    *,
    logical_role: str,
    provider: str,
    model: str,
    selected_model: dict[str, Any],
    input_scope: dict[str, Any],
    result_contract: dict[str, Any],
    operation: Callable[[], ExecutionValue],
    summarize: Callable[[ExecutionValue], dict[str, Any]],
) -> ExecutionValue:
    context = current_ai_log_context()
    run_id = new_id("logicalrun")
    ownership = {
        key: context[key] for key in _AUDIT_CONTEXT_KEYS if context.get(key) is not None
    }
    metadata = {
        "logical_role": logical_role,
        "selected_model": selected_model,
        "input_scope": input_scope,
        "result_contract": result_contract,
        "ownership": ownership,
    }
    common = {
        "run_id": run_id,
        "parent_run_id": context.get("workflow_run_id") or context.get("trace_id"),
        "provider": provider,
        "model": model,
        "user_id": context.get("user_id"),
        "lesson_id": context.get("lesson_id"),
        "turn_id": context.get("turn_id"),
        "request_kind": "logical_role",
        "metadata": metadata,
    }
    ai_usage_logger.log_model_run_event(
        "started",
        status="running",
        input_data={"input_scope": input_scope},
        **common,
    )
    try:
        with ai_log_context(
            logical_role=logical_role,
            logical_role_run_id=run_id,
            selected_model=selected_model,
            input_scope=input_scope,
            result_contract=result_contract,
        ):
            result = operation()
    except Exception as exc:
        ai_usage_logger.log_model_run_event(
            "failed",
            status="failed",
            error=type(exc).__name__,
            **common,
        )
        raise
    ai_usage_logger.log_model_run_event(
        "completed",
        status="completed",
        output_data={
            "result_contract": result_contract,
            "summary": summarize(result),
        },
        **common,
    )
    return result


@dataclass(frozen=True)
class StructuredExecutionResult:
    output_parsed: Any
    activity: list[AgentActivityEvent] = field(default_factory=list)


@dataclass(frozen=True)
class TextExecutionResult:
    output_text: str
    activity: list[AgentActivityEvent] = field(default_factory=list)


@dataclass(frozen=True)
class BoardGenerationExecutionRequest:
    requirement: LearningRequirementSheet
    teaching_plan: str
    content_extent: BoardContentExtent = "article"
    image_inputs: list[str] = field(default_factory=list)
    visual_manifest: list[dict[str, Any]] = field(default_factory=list)


class BoardGenerationExecutionResult(Protocol):
    thread_id: str
    turn_id: str | None
    final_response: str
    activity: list[AgentActivityEvent]


class AIExecutionAdapter(Protocol):
    """Provider-neutral execution boundary for OpenClass AI roles."""

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        allow_live_web_search: bool = False,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
    ) -> StructuredExecutionResult: ...

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]: ...

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_inputs: list[str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> TextExecutionResult: ...

    def explain_from_directive(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
    ) -> StructuredExecutionResult: ...

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str: ...


BoardRunner = Callable[
    [
        str,
        str,
        LearningRequirementSheet,
        str,
        BoardContentExtent,
        list[str],
        list[dict[str, Any]],
        Callable[[], bool] | None,
        Callable[[AgentActivityEvent], None] | None,
    ],
    tuple[BoardGenerationExecutionResult, str],
]
ImageAnalysisRunner = Callable[
    [
        str,
        str,
        str,
        list[str],
        Callable[[], bool] | None,
        Callable[[AgentActivityEvent], None] | None,
    ],
    str,
]


class CodexAIExecutionAdapter:
    """Codex app-server implementation of the provider-neutral execution contract."""

    def __init__(
        self,
        *,
        owner_user_id: str,
        model: str,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        board_runner: BoardRunner | None = None,
        image_analysis_runner: ImageAnalysisRunner | None = None,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self._board_runner = board_runner
        self._image_analysis_runner = image_analysis_runner

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        allow_live_web_search: bool = False,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
    ) -> StructuredExecutionResult:
        response = CodexAppServerTextClient(self.owner_user_id).parse(
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image_inputs=image_inputs,
            allow_live_web_search=allow_live_web_search,
            on_activity=on_activity,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            service_tier_is_set=self.service_tier is not None,
        )
        return StructuredExecutionResult(
            output_parsed=response.output_parsed,
            activity=list(getattr(response, "activity", [])),
        )

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]:
        if self._board_runner is None:
            raise RuntimeError("This AI adapter has no board-generation runner")
        return self._board_runner(
            self.owner_user_id,
            self.model,
            request.requirement,
            request.teaching_plan,
            request.content_extent,
            request.image_inputs,
            request.visual_manifest,
            is_cancelled,
            on_activity,
        )

    def explain_from_directive(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
    ) -> StructuredExecutionResult:
        return self.parse_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
        )

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str:
        if self._image_analysis_runner is None:
            raise RuntimeError("This AI adapter has no image-analysis runner")
        return self._image_analysis_runner(
            self.owner_user_id,
            self.model,
            prompt,
            image_inputs,
            is_cancelled,
            on_activity,
        )


class _StructuredBoardResponse(BaseModel):
    content_text: str
    chatbot_message: str


@dataclass(frozen=True)
class StructuredBoardGenerationResult:
    thread_id: str
    turn_id: str | None
    final_response: str
    activity: list[AgentActivityEvent] = field(default_factory=list)


STRUCTURED_BOARD_GENERATION_INSTRUCTIONS = """
You are the board-writing capability inside OpenClass. Generate a self-contained learning board
from only the supplied frozen learning requirement, teaching plan, requested content extent, and
verified source evidence. Treat `content_extent` as an authoritative output-scale constraint.
Return the complete board as Markdown in `content_text` and a brief learner-facing completion in
`chatbot_message`. Do not ask questions and do not use HTML. Use fenced code blocks only for real
code. Put display formulas in `$$` delimiters on their own lines. Preserve a semantic Markdown
heading hierarchy and keep sibling sections at the same level.

If `visual_manifest` is present, handle every item exactly once and preserve its order. For a
verified editable table or single-direction linear flow whose essential content is available in
the manifest, recreate it as editable Markdown and then place its `recreation_marker` once on a
standalone line. Otherwise place its `marker` once on a standalone line after the paragraph that
introduces it. Never write both markers for one item and never invent missing visual details.
""".strip()

PI_BOARD_GENERATION_INSTRUCTIONS = """
You are the board-writing capability inside OpenClass. Generate one complete, self-contained
learning board from only the supplied frozen learning requirement, teaching plan, requested content
extent, and verified source evidence. Treat `content_extent` as an authoritative output-scale
constraint. Return only the board Markdown. Do not wrap the document in a JSON object, do not
add a learner-facing completion message, and do not use HTML. Use fenced code blocks only for real
code. Put display formulas in `$$` delimiters on their own lines. Preserve a semantic Markdown
heading hierarchy and keep sibling sections at the same level.

If `visual_manifest` is present, handle every item exactly once and preserve its order. For a
verified editable table or single-direction linear flow whose essential content is available in
the manifest, recreate it as editable Markdown and then place its `recreation_marker` once on a
standalone line. Otherwise place its `marker` once on a standalone line after the paragraph that
introduces it. Never write both markers for one item and never invent missing visual details.
""".strip()


class DeepSeekAIExecutionAdapter:
    """Shared DeepSeek implementation of the provider-neutral execution contract."""

    runtime_label = "DeepSeek"
    turn_id_prefix = "deepseekturn"

    def __init__(self, *, model: str) -> None:
        self.model = model
        self._client = DeepSeekTextClient(model=model)

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        allow_live_web_search: bool = False,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
    ) -> StructuredExecutionResult:
        parsed, activity = self._client.parse(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image_inputs=image_inputs,
        )
        for event in activity:
            if on_activity is not None:
                on_activity(event)
        return StructuredExecutionResult(output_parsed=parsed, activity=activity)

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]:
        if is_cancelled is not None and is_cancelled():
            raise RuntimeError(f"{self.runtime_label} board generation was cancelled")
        response = self.parse_structured(
            system_prompt=STRUCTURED_BOARD_GENERATION_INSTRUCTIONS,
            user_prompt=(
                "Frozen board-generation payload:\n"
                + json.dumps(
                    {
                        "learning_requirement": request.requirement.model_dump(
                            mode="json"
                        ),
                        "teaching_plan": request.teaching_plan,
                        "content_extent": request.content_extent,
                        "visual_manifest": request.visual_manifest,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
            schema=_StructuredBoardResponse,
            image_inputs=request.image_inputs,
        )
        output = _StructuredBoardResponse.model_validate(response.output_parsed)
        if not output.content_text.strip():
            raise RuntimeError(
                f"{self.runtime_label} board generation returned empty content"
            )
        for event in response.activity:
            if on_activity is not None:
                on_activity(event)
        turn_id = (
            response.activity[0].turn_id
            if response.activity
            else new_id(self.turn_id_prefix)
        )
        result = StructuredBoardGenerationResult(
            thread_id=turn_id,
            turn_id=turn_id,
            final_response=output.chatbot_message.strip(),
            activity=response.activity,
        )
        return result, output.content_text

    def explain_from_directive(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
    ) -> StructuredExecutionResult:
        return self.parse_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
        )

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str:
        raise RuntimeError(
            "The selected DeepSeek text model does not accept image inputs"
        )


class PiAIExecutionAdapter(DeepSeekAIExecutionAdapter):
    """Pi implementation with OpenClass retaining workflow and write validation."""

    runtime_label = "Pi"
    turn_id_prefix = "piturn"

    def __init__(
        self,
        *,
        owner_user_id: str,
        provider: str,
        model: str,
        access_method: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.provider = provider
        self.model = model
        self.access_method = access_method
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self._client = PiTextClient(
            owner_user_id=owner_user_id,
            provider=provider,
            model=model,
            access_method=access_method,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
        )

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        allow_live_web_search: bool = False,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
    ) -> StructuredExecutionResult:
        return _run_audited_role(
            logical_role=_contract_logical_role(schema),
            provider=self.provider,
            model=self.model,
            selected_model=self._selected_model_audit(),
            input_scope=_prompt_input_scope(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_inputs=image_inputs,
                allow_live_web_search=allow_live_web_search,
            ),
            result_contract={"kind": "structured", "schema": schema.__name__},
            operation=lambda: self._parse_with_visible_activity(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                image_inputs=image_inputs,
                on_activity=on_activity,
                running_label="OpenClass 正在处理当前步骤",
                completed_label="OpenClass 已完成当前步骤",
                failed_label="OpenClass 当前步骤未完成",
                activity_kind="structured_model_step",
            ),
            summarize=lambda result: {
                "shape": _safe_result_summary(result.output_parsed),
                "validated_result": _safe_validated_result(
                    result.output_parsed,
                    schema,
                ),
            },
        )

    def _selected_model_audit(self) -> dict[str, Any]:
        return {
            "agent_backend": "pi",
            "provider": self.provider,
            "model": self.model,
            "access_method": getattr(self, "access_method", None),
            "reasoning_effort": getattr(self, "reasoning_effort", None),
            "service_tier": getattr(self, "service_tier", None),
        }

    def _parse_with_visible_activity(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
        running_label: str,
        completed_label: str,
        failed_label: str,
        activity_kind: str,
    ) -> StructuredExecutionResult:
        activity_by_id: dict[str, AgentActivityEvent] = {}
        activity_order: list[str] = []

        def publish(event: AgentActivityEvent) -> None:
            if event.id not in activity_by_id:
                activity_order.append(event.id)
            activity_by_id[event.id] = event
            if on_activity is not None:
                on_activity(event)

        lifecycle_event = AgentActivityEvent(
            turn_id=new_id("piworkflow"),
            stage="execute_role",
            label=running_label,
            status="running",
            role="OpenClass",
            metadata={
                "kind": activity_kind,
                "agent_backend": "pi",
                "provider": self.provider,
                "model": self.model,
            },
        )
        publish(lifecycle_event)
        try:
            response = self._client.parse(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                image_inputs=image_inputs,
                on_activity=publish,
            )
        except Exception:
            failed_event = lifecycle_event.model_copy(
                update={"label": failed_label, "status": "failed"}
            )
            publish(failed_event)
            raise
        completed_event = lifecycle_event.model_copy(
            update={"label": completed_label, "status": "completed"}
        )
        publish(completed_event)
        for event in response.activity:
            if event.id not in activity_by_id:
                publish(event)
        return StructuredExecutionResult(
            output_parsed=response.output_parsed,
            activity=[activity_by_id[event_id] for event_id in activity_order],
        )

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_inputs: list[str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> TextExecutionResult:
        return _run_audited_role(
            logical_role="chatbot",
            provider=self.provider,
            model=self.model,
            selected_model=self._selected_model_audit(),
            input_scope=_prompt_input_scope(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_inputs=image_inputs,
            ),
            result_contract={"kind": "text", "schema": "plain_text"},
            operation=lambda: self._complete_text_unlogged(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_inputs=image_inputs,
                is_cancelled=is_cancelled,
                on_activity=on_activity,
                on_text_delta=on_text_delta,
            ),
            summarize=lambda result: _safe_result_summary(result.output_text),
        )

    def _complete_text_unlogged(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_inputs: list[str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> TextExecutionResult:
        response = self._client.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_inputs=image_inputs,
            on_activity=on_activity,
            on_text_delta=on_text_delta,
            is_cancelled=is_cancelled,
        )
        return TextExecutionResult(
            output_text=response.output_text,
            activity=response.activity,
        )

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]:
        if is_cancelled is not None and is_cancelled():
            raise RuntimeError(f"{self.runtime_label} board generation was cancelled")
        input_scope = {
            "input_fields": [
                "learning_requirement",
                "teaching_plan",
                "content_extent",
                "image_inputs",
                "visual_manifest",
            ],
            "learning_requirement_fields": sorted(
                request.requirement.model_dump(mode="json")
            ),
            "teaching_plan_character_count": len(request.teaching_plan),
            "teaching_plan_sha256": _audit_digest(request.teaching_plan),
            "image_count": len(request.image_inputs),
            "visual_manifest_item_count": len(request.visual_manifest),
        }
        return _run_audited_role(
            logical_role="board_writer",
            provider=self.provider,
            model=self.model,
            selected_model=self._selected_model_audit(),
            input_scope=input_scope,
            result_contract={"kind": "document", "schema": "board_markdown"},
            operation=lambda: self._generate_board_unlogged(
                request,
                is_cancelled=is_cancelled,
                on_activity=on_activity,
            ),
            summarize=lambda result: {
                "value_type": "board_generation",
                "document_character_count": len(result[1]),
                "document_sha256": _audit_digest(result[1]),
                "activity_count": len(result[0].activity),
            },
        )

    def _generate_board_unlogged(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]:
        response = self._complete_text_unlogged(
            system_prompt=PI_BOARD_GENERATION_INSTRUCTIONS,
            user_prompt=(
                "Frozen board-generation payload:\n"
                + json.dumps(
                    {
                        "learning_requirement": request.requirement.model_dump(
                            mode="json"
                        ),
                        "teaching_plan": request.teaching_plan,
                        "content_extent": request.content_extent,
                        "visual_manifest": request.visual_manifest,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
            image_inputs=request.image_inputs,
            on_activity=on_activity,
            is_cancelled=is_cancelled,
        )
        content = response.output_text.strip()
        if not content:
            raise RuntimeError(
                f"{self.runtime_label} board generation returned empty content"
            )
        turn_id = (
            response.activity[0].turn_id
            if response.activity
            else new_id(self.turn_id_prefix)
        )
        result = StructuredBoardGenerationResult(
            thread_id=turn_id,
            turn_id=turn_id,
            final_response="",
            activity=response.activity,
        )
        return result, content

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str:
        raise RuntimeError("The selected Pi runtime does not accept image inputs yet")


def build_ai_execution_adapter(
    selection: AIModelSelection,
    *,
    owner_user_id: str,
    board_runner: BoardRunner | None = None,
    image_analysis_runner: ImageAnalysisRunner | None = None,
) -> AIExecutionAdapter:
    del board_runner, image_analysis_runner
    if selection.provider not in {"openai_codex", "deepseek"}:
        raise RuntimeError(f"Unsupported text model provider: {selection.provider}")
    # Runtime selection is server-owned. Cached clients and stored records may
    # still carry agent_backend="codex", but no text task routes back to Codex.
    return PiAIExecutionAdapter(
        owner_user_id=owner_user_id,
        provider=selection.provider,
        model=selection.model,
        access_method=selection.access_method
        or (
            "chatgpt_subscription"
            if selection.provider == "openai_codex"
            else "platform_credits"
        ),
        reasoning_effort=selection.reasoning_effort,
        service_tier=selection.service_tier,
    )

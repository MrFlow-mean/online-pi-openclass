from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

from app.models import AgentActivityEvent, AIModelSelection, LearningRequirementSheet, new_id
from app.services.codex_app_server import CodexAppServerTextClient
from app.services.deepseek_api import DeepSeekTextClient
from app.services.pi_agent_runtime import PiTextClient


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


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
from only the supplied frozen learning requirement, teaching plan, and verified source evidence.
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
learning board from only the supplied frozen learning requirement, teaching plan, and verified
source evidence. Return only the board Markdown. Do not wrap the document in a JSON object, do not
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
                        "learning_requirement": request.requirement.model_dump(mode="json"),
                        "teaching_plan": request.teaching_plan,
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
        raise RuntimeError("The selected DeepSeek text model does not accept image inputs")


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
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._client = PiTextClient(
            owner_user_id=owner_user_id,
            provider=provider,
            model=model,
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
        return self._parse_with_visible_activity(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image_inputs=image_inputs,
            on_activity=on_activity,
            running_label="OpenClass 正在处理当前步骤",
            completed_label="OpenClass 已完成当前步骤",
            failed_label="OpenClass 当前步骤未完成",
            activity_kind="structured_model_step",
        )

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
        response = self.complete_text(
            system_prompt=PI_BOARD_GENERATION_INSTRUCTIONS,
            user_prompt=(
                "Frozen board-generation payload:\n"
                + json.dumps(
                    {
                        "learning_requirement": request.requirement.model_dump(mode="json"),
                        "teaching_plan": request.teaching_plan,
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
        reasoning_effort=selection.reasoning_effort,
        service_tier=selection.service_tier,
    )

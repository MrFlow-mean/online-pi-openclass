from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.models import AgentActivityEvent, new_id
from app.services.ai_logging import ai_usage_logger
from app.services.config import load_root_dotenv
from app.services.structured_output import (
    json_object,
    validation_issues,
    validation_repair_prompt,
)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

CODEX_TEXT_PROXY_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-5.6-sol", "GPT 5.6 Sol · OpenClass Codex"),
    ("gpt-5.6-terra", "GPT 5.6 Terra · OpenClass Codex"),
    ("gpt-5.6-luna", "GPT 5.6 Luna · OpenClass Codex"),
)
CODEX_TEXT_PROXY_MODEL_IDS = frozenset(
    model for model, _label in CODEX_TEXT_PROXY_MODELS
)
CODEX_TEXT_PROXY_REASONING_EFFORTS = (
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
DEFAULT_CODEX_TEXT_PROXY_URL = "http://127.0.0.1:8317/v1"
DEFAULT_CODEX_TEXT_PROXY_KEY_FILE = "/etc/cliproxyapi/api-key"


def _normalized_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {
        "none",
        "null",
        "disabled",
        "false",
        "0",
    }:
        return None
    if normalized.lower().startswith("your_") or normalized.startswith("你的_"):
        return None
    return normalized


def _proxy_base_url() -> str:
    configured = (
        os.getenv("OPENCLASS_CODEX_TEXT_PROXY_URL")
        or os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_URL")
        or DEFAULT_CODEX_TEXT_PROXY_URL
    ).strip()
    normalized = configured.rstrip("/")
    normalized = normalized.removesuffix("/live")
    return normalized or DEFAULT_CODEX_TEXT_PROXY_URL


def _proxy_api_key() -> str | None:
    inline = _normalized_secret(
        os.getenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY")
        or os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY")
    )
    if inline:
        return inline
    configured_path = (
        os.getenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY_FILE")
        or os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE")
        or DEFAULT_CODEX_TEXT_PROXY_KEY_FILE
    ).strip()
    if not configured_path:
        return None
    try:
        return _normalized_secret(
            Path(configured_path).expanduser().read_text(encoding="utf-8")
        )
    except OSError:
        return None


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(1.0, float((os.getenv(name) or str(default)).strip()))
    except ValueError:
        return default


def _positive_int_env(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


@dataclass(frozen=True)
class CodexTextProxyConfig:
    api_key: str | None
    base_url: str
    timeout_seconds: float = 180.0
    max_output_tokens: int | None = None

    @property
    def configured(self) -> bool:
        return self.api_key is not None


def codex_text_proxy_config() -> CodexTextProxyConfig:
    load_root_dotenv()
    return CodexTextProxyConfig(
        api_key=_proxy_api_key(),
        base_url=_proxy_base_url(),
        timeout_seconds=_positive_float_env(
            "OPENCLASS_CODEX_TEXT_PROXY_TIMEOUT_SECONDS",
            180.0,
        ),
        max_output_tokens=_positive_int_env(
            "OPENCLASS_CODEX_TEXT_PROXY_MAX_OUTPUT_TOKENS"
        ),
    )


def codex_text_proxy_user_allowed(user_id: str) -> bool:
    raw_user_ids = os.getenv("OPENCLASS_CODEX_TEXT_PROXY_ALLOWED_USER_IDS")
    if raw_user_ids is None:
        raw_user_ids = os.getenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS") or ""
    allowed_user_ids = {item for item in raw_user_ids.replace(",", " ").split() if item}
    return "*" in allowed_user_ids or user_id in allowed_user_ids


def codex_text_proxy_available_for_user(user_id: str) -> bool:
    return (
        codex_text_proxy_user_allowed(user_id) and codex_text_proxy_config().configured
    )


@dataclass(frozen=True)
class CodexTextProxyStructuredResponse:
    output_parsed: BaseModel
    activity: list[AgentActivityEvent] = field(default_factory=list)


@dataclass(frozen=True)
class CodexTextProxyResponse:
    output_text: str
    activity: list[AgentActivityEvent] = field(default_factory=list)


def _response_output_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    output_text = "".join(parts).strip()
    if not output_text:
        raise RuntimeError("Codex platform proxy returned no text output")
    return output_text


def _schema_name(schema: type[BaseModel]) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", schema.__name__)[:64]
    return normalized or "openclass_response"


class CodexTextProxyClient:
    """OpenAI-compatible platform text client backed by CLIProxyAPI."""

    def __init__(
        self,
        *,
        owner_user_id: str,
        model: str,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        config: CodexTextProxyConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if model not in CODEX_TEXT_PROXY_MODEL_IDS:
            raise RuntimeError(f"Unsupported Codex platform proxy model: {model}")
        if not codex_text_proxy_user_allowed(owner_user_id):
            raise RuntimeError(
                "The current user is not allowed to use the Codex platform proxy"
            )
        self.config = config or codex_text_proxy_config()
        if not self.config.configured:
            raise RuntimeError("Codex platform text proxy is not configured")
        self.owner_user_id = owner_user_id
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self._client = client

    def _input(self, user_prompt: str, image_inputs: list[str] | None) -> Any:
        if not image_inputs:
            return user_prompt
        content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
        for image_url in image_inputs:
            if not image_url.lower().startswith("data:image/"):
                raise RuntimeError(
                    "Codex platform proxy accepts only embedded image inputs"
                )
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": "auto",
                }
            )
        return [{"role": "user", "content": content}]

    def _request(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_inputs: list[str] | None,
        turn_id: str,
        request_kind: str,
        on_activity: Callable[[AgentActivityEvent], None] | None,
        is_cancelled: Callable[[], bool] | None,
        text_format: dict[str, Any] | None = None,
    ) -> str:
        if is_cancelled is not None and is_cancelled():
            raise RuntimeError("Codex platform proxy request was cancelled")
        event = AgentActivityEvent(
            turn_id=turn_id,
            stage="execute_role",
            label="正在通过 OpenClass Codex 代理调用模型",
            status="running",
            role="OpenClass",
            metadata={
                "kind": "model_runtime",
                "agent_backend": "platform_proxy",
                "provider": "openai_codex",
                "model": self.model,
                "transport": "cliproxyapi",
            },
        )
        if on_activity is not None:
            on_activity(event)
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": system_prompt,
            "input": self._input(user_prompt, image_inputs),
        }
        if self.config.max_output_tokens is not None:
            payload["max_output_tokens"] = self.config.max_output_tokens
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if self.service_tier:
            payload["service_tier"] = self.service_tier
        if text_format is not None:
            payload["text"] = {"format": text_format}

        client = self._client or httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
        )
        owns_client = self._client is None
        try:
            response = client.post(
                "responses",
                json=payload,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
            response.raise_for_status()
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise TypeError("Codex platform proxy returned an invalid response")
            output_text = _response_output_text(response_payload)
        except (httpx.HTTPError, TypeError, ValueError, RuntimeError) as error:
            if on_activity is not None:
                on_activity(
                    event.model_copy(
                        update={
                            "label": "OpenClass Codex 代理调用失败",
                            "status": "failed",
                        }
                    )
                )
            ai_usage_logger.log_event(
                "codex_text_proxy_request_failed",
                provider="openai_codex",
                model=self.model,
                turn_id=turn_id,
                request_kind=request_kind,
                error=type(error).__name__,
            )
            raise RuntimeError(
                f"Codex platform proxy request failed: {error}"
            ) from error
        finally:
            if owns_client:
                client.close()
        if is_cancelled is not None and is_cancelled():
            raise RuntimeError("Codex platform proxy request was cancelled")
        if on_activity is not None:
            on_activity(
                event.model_copy(
                    update={
                        "label": "OpenClass Codex 代理已返回结果",
                        "status": "completed",
                    }
                )
            )
        ai_usage_logger.log_event(
            "codex_text_proxy_request_completed",
            provider="openai_codex",
            model=self.model,
            turn_id=turn_id,
            request_kind=request_kind,
            response_id=response_payload.get("id"),
            output_character_count=len(output_text),
            usage=response_payload.get("usage"),
        )
        return output_text

    def parse(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> CodexTextProxyStructuredResponse:
        turn_id = new_id("codexproxyturn")
        activity: list[AgentActivityEvent] = []

        def publish(event: AgentActivityEvent) -> None:
            activity.append(event)
            if on_activity is not None:
                on_activity(event)

        text_format = {
            "type": "json_schema",
            "name": _schema_name(schema),
            "strict": False,
            "schema": schema.model_json_schema(),
        }
        output_text = self._request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_inputs=image_inputs,
            turn_id=turn_id,
            request_kind="structured",
            on_activity=publish,
            is_cancelled=is_cancelled,
            text_format=text_format,
        )
        try:
            parsed = schema.model_validate(json_object(output_text))
        except (TypeError, ValueError) as first_error:
            repaired_text = self._request(
                system_prompt=system_prompt,
                user_prompt=(
                    f"{user_prompt}\n\nPrevious response:\n{output_text}\n\n"
                    f"{validation_repair_prompt(first_error)}"
                ),
                image_inputs=image_inputs,
                turn_id=turn_id,
                request_kind="structured_repair",
                on_activity=publish,
                is_cancelled=is_cancelled,
                text_format=text_format,
            )
            try:
                parsed = schema.model_validate(json_object(repaired_text))
            except (TypeError, ValueError) as repair_error:
                ai_usage_logger.log_event(
                    "codex_text_proxy_structured_response_failed",
                    provider="openai_codex",
                    model=self.model,
                    turn_id=turn_id,
                    initial_validation_issues=validation_issues(first_error),
                    repair_validation_issues=validation_issues(repair_error),
                )
                raise RuntimeError(
                    "Codex platform proxy returned an invalid structured response"
                ) from repair_error
        return CodexTextProxyStructuredResponse(
            output_parsed=parsed,
            activity=activity,
        )

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_inputs: list[str] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> CodexTextProxyResponse:
        turn_id = new_id("codexproxyturn")
        activity: list[AgentActivityEvent] = []

        def publish(event: AgentActivityEvent) -> None:
            activity.append(event)
            if on_activity is not None:
                on_activity(event)

        output_text = self._request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_inputs=image_inputs,
            turn_id=turn_id,
            request_kind="text",
            on_activity=publish,
            is_cancelled=is_cancelled,
        )
        if on_text_delta is not None:
            on_text_delta(output_text)
        return CodexTextProxyResponse(output_text=output_text, activity=activity)

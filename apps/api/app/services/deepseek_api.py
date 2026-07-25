from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
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

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_CURATED_MODELS: tuple[tuple[str, str], ...] = (
    ("deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro"),
)


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
    lowered = normalized.lower()
    if lowered.startswith("your_") or normalized.startswith("你的_"):
        return None
    return normalized


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float
    max_tokens: int | None

    @property
    def configured(self) -> bool:
        return self.api_key is not None


def deepseek_config() -> DeepSeekConfig:
    load_root_dotenv()
    timeout_value = (os.getenv("DEEPSEEK_TIMEOUT_SECONDS") or "120").strip()
    max_tokens_value = (os.getenv("DEEPSEEK_MAX_TOKENS") or "").strip()
    try:
        timeout_seconds = max(1.0, float(timeout_value))
    except ValueError:
        timeout_seconds = 120.0
    try:
        max_tokens = max(1, int(max_tokens_value)) if max_tokens_value else None
    except ValueError:
        max_tokens = None
    return DeepSeekConfig(
        api_key=_normalized_secret(os.getenv("DEEPSEEK_API_KEY")),
        base_url=(os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL).strip()
        or DEEPSEEK_DEFAULT_BASE_URL,
        model=(os.getenv("DEEPSEEK_MODEL") or DEEPSEEK_DEFAULT_MODEL).strip()
        or DEEPSEEK_DEFAULT_MODEL,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )


def deepseek_provider_configured() -> bool:
    return deepseek_config().configured


def _response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise RuntimeError("DeepSeek response did not include choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek response did not include text content")
    return content.strip()


class DeepSeekTextClient:
    """Shared site-level DeepSeek client without per-user quota enforcement."""

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        config: DeepSeekConfig | None = None,
    ) -> None:
        self.config = config or deepseek_config()
        self.model = (model or self.config.model).strip() or self.config.model
        if not self.config.configured:
            raise RuntimeError("DeepSeek is not configured on this server")
        self.client = client or OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )

    def parse(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
    ) -> tuple[StructuredModel, list[AgentActivityEvent]]:
        if image_inputs:
            raise RuntimeError("The selected DeepSeek text model does not accept image inputs")
        turn_id = new_id("deepseekturn")
        schema_payload = schema.model_json_schema()
        schema_text = json.dumps(schema_payload, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "Return one valid JSON object matching this JSON schema. "
                    "Do not wrap it in Markdown and do not add prose outside the JSON.\n"
                    f"JSON schema: {schema_text}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if self.config.max_tokens is not None:
            kwargs["max_tokens"] = self.config.max_tokens
        response = self.client.chat.completions.create(**kwargs)
        output_text = _response_text(response)
        try:
            parsed = schema.model_validate(json_object(output_text))
        except Exception as first_error:
            initial_validation_issues = validation_issues(first_error)
            repair_response = self.client.chat.completions.create(
                **{
                    **kwargs,
                    "messages": [
                        *messages,
                        {"role": "assistant", "content": output_text},
                        {
                            "role": "user",
                            "content": validation_repair_prompt(first_error),
                        },
                    ],
                }
            )
            repaired_text = _response_text(repair_response)
            try:
                parsed = schema.model_validate(json_object(repaired_text))
            except Exception as repair_error:
                ai_usage_logger.log_event(
                    "deepseek_structured_response_failed",
                    provider="deepseek",
                    model=self.model,
                    turn_id=turn_id,
                    initial_validation_issues=initial_validation_issues,
                    repair_validation_issues=validation_issues(repair_error),
                    initial_output_character_count=len(output_text),
                    repair_output_character_count=len(repaired_text),
                )
                raise RuntimeError(
                    "DeepSeek returned an invalid structured response"
                ) from repair_error
            response = repair_response
            output_text = repaired_text
            ai_usage_logger.log_event(
                "deepseek_structured_response_repaired",
                provider="deepseek",
                model=self.model,
                turn_id=turn_id,
                initial_validation_issues=initial_validation_issues,
            )
        usage = getattr(response, "usage", None)
        ai_usage_logger.log_event(
            "deepseek_request_completed",
            provider="deepseek",
            model=self.model,
            turn_id=turn_id,
            response_id=getattr(response, "id", None),
            output_character_count=len(output_text),
            usage=usage,
        )
        activity = [
            AgentActivityEvent(
                turn_id=turn_id,
                stage="execute_role",
                label="DeepSeek completed the model request",
                status="completed",
                role="deepseek",
                metadata={"provider": "deepseek", "model": self.model},
            )
        ]
        return parsed, activity

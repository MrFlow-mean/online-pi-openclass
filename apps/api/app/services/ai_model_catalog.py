from __future__ import annotations

import os
from typing import Any

from app.models import (
    AIModelCatalog,
    AIModelOption,
    AIModelSelection,
    AIReasoningEffortOption,
    AIServiceTierOption,
)
from app.services.codex_app_server import (
    CODEX_DEFAULT_MODELS,
    codex_provider_status,
    list_codex_models,
)
from app.services.deepseek_api import (
    DEEPSEEK_CURATED_MODELS,
    deepseek_config,
)


OPENAI_CODEX_DEFAULT_TEXT_MODEL = "gpt-5.5"
OPENAI_DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
OPENAI_FAST_REALTIME_MODEL = "gpt-realtime-2.1-mini"


def default_text_selection(
    *,
    model: str = OPENAI_CODEX_DEFAULT_TEXT_MODEL,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
) -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model=model,
        access_method="chatgpt_subscription",
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
    )


def default_realtime_selection() -> AIModelSelection:
    return AIModelSelection(
        provider="openai",
        model=(os.getenv("OPENAI_REALTIME_MODEL") or OPENAI_DEFAULT_REALTIME_MODEL).strip(),
        access_method="platform_credits",
    )


def realtime_runtime_enabled() -> bool:
    return (os.getenv("OPENCLASS_REALTIME_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _configured_secret(name: str) -> bool:
    value = (os.getenv(name) or "").strip()
    return bool(
        value
        and value.lower() not in {"none", "null", "disabled", "false", "0"}
        and not value.startswith(("your_", "你的_"))
    )


def _fallback_codex_models() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "model": model,
            "displayName": label.removeprefix("OpenAI Codex "),
        }
        for model, label in CODEX_DEFAULT_MODELS
    )


def _codex_text_models(user_id: str) -> tuple[dict[str, Any], ...]:
    try:
        models = list_codex_models(user_id)
    except Exception:
        return _fallback_codex_models()
    options: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or item.get("id") or "").strip()
        if not model:
            continue
        display_name = str(
            item.get("displayName") or item.get("display_name") or model
        ).strip()
        options.append({**item, "model": model, "displayName": display_name})
    return tuple(options) or _fallback_codex_models()


def _reasoning_efforts(item: dict[str, Any]) -> list[AIReasoningEffortOption]:
    raw_options = item.get("supportedReasoningEfforts")
    if not isinstance(raw_options, list):
        raw_options = item.get("supported_reasoning_efforts")
    options: list[AIReasoningEffortOption] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        effort = str(
            raw_option.get("reasoningEffort")
            or raw_option.get("reasoning_effort")
            or ""
        ).strip()
        if effort:
            options.append(
                AIReasoningEffortOption(
                    reasoning_effort=effort,
                    description=str(raw_option.get("description") or "").strip(),
                )
            )
    return options


def _service_tiers(item: dict[str, Any]) -> list[AIServiceTierOption]:
    raw_options = item.get("serviceTiers")
    if not isinstance(raw_options, list):
        raw_options = item.get("service_tiers")
    options: list[AIServiceTierOption] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        tier_id = str(raw_option.get("id") or "").strip()
        if tier_id:
            options.append(
                AIServiceTierOption(
                    id=tier_id,
                    name=str(raw_option.get("name") or tier_id).strip(),
                    description=str(raw_option.get("description") or "").strip(),
                )
            )
    return options


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _default_model_id(models: list[dict[str, Any]]) -> str:
    configured = (os.getenv("OPENAI_CODEX_MODEL") or "").strip()
    if configured:
        return configured
    for item in models:
        if item.get("isDefault") is True:
            return str(item["model"])
    return OPENAI_CODEX_DEFAULT_TEXT_MODEL


def build_model_catalog(user_id: str) -> AIModelCatalog:
    status = codex_provider_status(user_id, refresh=False)
    shared_deepseek = deepseek_config()
    realtime_default = default_realtime_selection()
    realtime_configured = _configured_secret("OPENAI_API_KEY")
    realtime_enabled = realtime_runtime_enabled() and realtime_configured
    realtime_models = [
        (OPENAI_DEFAULT_REALTIME_MODEL, "OpenAI GPT Realtime 2.1"),
        (OPENAI_FAST_REALTIME_MODEL, "OpenAI GPT Realtime 2.1 Mini"),
    ]
    if not any(model == realtime_default.model for model, _label in realtime_models):
        realtime_models.insert(0, (realtime_default.model, f"OpenAI {realtime_default.model}"))
    models = list(_codex_text_models(user_id))
    default_model_id = _default_model_id(models)
    if not any(item["model"] == default_model_id for item in models):
        models.insert(
            0,
            {"model": default_model_id, "displayName": default_model_id},
        )
    text_options = [
        AIModelOption(
            provider="openai_codex",
            model=str(item["model"]),
            access_method="chatgpt_subscription",
            label=f"OpenAI Codex {item['displayName']}",
            capability="text",
            enabled=status.configured,
            configured=status.configured,
            default=item["model"] == default_model_id,
            default_reasoning_effort=_optional_string(
                item.get("defaultReasoningEffort")
                or item.get("default_reasoning_effort")
            ),
            supported_reasoning_efforts=_reasoning_efforts(item),
            default_service_tier=_optional_string(
                item.get("defaultServiceTier")
                or item.get("default_service_tier")
            ),
            service_tiers=_service_tiers(item),
        )
        for item in models
    ]
    deepseek_models = list(DEEPSEEK_CURATED_MODELS)
    if not any(model == shared_deepseek.model for model, _label in deepseek_models):
        deepseek_models.insert(0, (shared_deepseek.model, f"DeepSeek {shared_deepseek.model}"))
    deepseek_is_default = shared_deepseek.configured and not status.configured
    text_options.extend(
        AIModelOption(
            provider="deepseek",
            model=model,
            access_method="platform_credits",
            label=label,
            capability="text",
            enabled=shared_deepseek.configured,
            configured=shared_deepseek.configured,
            default=deepseek_is_default and model == shared_deepseek.model,
        )
        for model, label in deepseek_models
    )
    codex_default_option = next(option for option in text_options if option.provider == "openai_codex" and option.default)
    if deepseek_is_default:
        codex_default_option.default = False
        text_default = AIModelSelection(
            provider="deepseek",
            model=shared_deepseek.model,
            access_method="platform_credits",
        )
    else:
        text_default = default_text_selection(
            model=codex_default_option.model,
            reasoning_effort=codex_default_option.default_reasoning_effort,
            service_tier=codex_default_option.default_service_tier,
        )
    return AIModelCatalog(
        text=text_options,
        realtime=[
            AIModelOption(
                provider="openai",
                model=model,
                access_method="platform_credits",
                label=label,
                capability="realtime",
                enabled=realtime_enabled,
                configured=realtime_configured,
                default=model == realtime_default.model,
                transport="openai_webrtc",
            )
            for model, label in realtime_models
        ],
        defaults={"text": text_default, "realtime": realtime_default},
    )

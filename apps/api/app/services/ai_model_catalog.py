from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from app.models import (
    AIAgentBackendOption,
    AIModelCatalog,
    AIModelOption,
    AIModelSelection,
    AIReasoningEffortOption,
    AIServiceTierOption,
)
from app.services.deepseek_api import (
    DEEPSEEK_CURATED_MODELS,
    deepseek_config,
)
from app.services.pi_agent_runtime import (
    pi_credentials_available,
    pi_personal_api_configured,
    pi_runtime_available,
)


OPENAI_CODEX_DEFAULT_TEXT_MODEL = "gpt-5.5"
OPENAI_CODEX_REALTIME_MODEL = "gpt-live-1-codex"
OPENAI_DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
OPENAI_FAST_REALTIME_MODEL = "gpt-realtime-2.1-mini"
PI_OPENAI_CODEX_MODELS = (
    ("gpt-5.5", "GPT 5.5"),
    ("gpt-5.4", "GPT 5.4"),
    ("gpt-5.4-mini", "GPT 5.4 Mini"),
    ("gpt-5.3-codex-spark", "GPT 5.3 Codex Spark"),
)
PI_OPENAI_CODEX_REASONING_EFFORTS = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
PI_OPENAI_CODEX_SERVICE_TIERS = (
    {
        "id": "priority",
        "name": "Priority",
        "description": "Faster processing with higher usage.",
    },
)


def _agent_backend_options() -> dict[str, list[AIAgentBackendOption]]:
    codex_option = AIAgentBackendOption(
        id="codex",
        label="Codex Agent",
        description="Codex 后端已停用，仅保留回退适配器。",
        enabled=False,
    )
    pi_available = pi_runtime_available()
    teaching_options = [
        AIAgentBackendOption(
            id="pi",
            label="Pi Agent",
            description=(
                "使用 Pi Agent 运行框架。"
                if pi_available
                else "服务器尚未安装 Pi Agent。"
            ),
            enabled=pi_available,
        ),
        codex_option,
    ]
    return {
        "teaching": teaching_options,
        "source": [
            AIAgentBackendOption(
                id="pi",
                label="Pi Agent",
                description=(
                    "使用 Pi Agent 和 OpenClass 受限文件资料工具。"
                    if pi_available
                    else "服务器尚未安装 Pi Agent。"
                ),
                enabled=pi_available,
            ),
            codex_option.model_copy(),
        ],
    }


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


def resolve_text_model_selection(
    selection: AIModelSelection | None,
    *,
    user_id: str,
    catalog_builder: Callable[[str], AIModelCatalog] | None = None,
) -> AIModelSelection:
    if selection is not None:
        selected_model = selection.model.strip()
        if selection.provider in {"openai_codex", "deepseek"} and selected_model:
            access_method = selection.access_method or (
                "chatgpt_subscription"
                if selection.provider == "openai_codex"
                else "platform_credits"
            )
            return selection.model_copy(
                update={
                    "model": selected_model,
                    "agent_backend": "pi",
                    "access_method": access_method,
                }
            )
        raise RuntimeError(f"Unsupported text model provider: {selection.provider}")
    try:
        default_selection = (catalog_builder or build_model_catalog)(user_id).defaults["text"]
        if isinstance(default_selection, AIModelSelection):
            return default_selection.model_copy(update={"agent_backend": "pi"})
        return AIModelSelection(
            agent_backend="pi",
            provider=getattr(default_selection, "provider", "openai_codex"),
            model=str(
                getattr(
                    default_selection,
                    "model",
                    OPENAI_CODEX_DEFAULT_TEXT_MODEL,
                )
            ),
            access_method=getattr(default_selection, "access_method", None),
        )
    except Exception:
        return default_text_selection(
            model=(
                os.getenv("OPENAI_CODEX_MODEL")
                or OPENAI_CODEX_DEFAULT_TEXT_MODEL
            ).strip()
            or OPENAI_CODEX_DEFAULT_TEXT_MODEL,
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


def codex_realtime_runtime_enabled() -> bool:
    return (os.getenv("OPENCLASS_CODEX_REALTIME_ENABLED") or "").strip().lower() in {
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


def codex_realtime_proxy_configured() -> bool:
    if _configured_secret("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY"):
        return True
    key_file = (os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE") or "").strip()
    return bool(key_file and Path(key_file).is_file())


def _pi_text_models() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "model": model,
            "displayName": label,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort}
                for effort in PI_OPENAI_CODEX_REASONING_EFFORTS
            ],
            "serviceTiers": [dict(tier) for tier in PI_OPENAI_CODEX_SERVICE_TIERS],
        }
        for model, label in PI_OPENAI_CODEX_MODELS
    )


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
    pi_available = pi_runtime_available()
    pi_openai_configured = pi_available and pi_credentials_available(
        owner_user_id=user_id
    )
    personal_deepseek_configured = pi_available and pi_personal_api_configured(
        owner_user_id=user_id,
        provider="deepseek",
    )
    shared_deepseek = deepseek_config()
    realtime_default = default_realtime_selection()
    realtime_configured = _configured_secret("OPENAI_API_KEY")
    realtime_enabled = realtime_runtime_enabled() and realtime_configured
    codex_realtime_exposed = codex_realtime_runtime_enabled()
    codex_realtime_configured = codex_realtime_proxy_configured()
    codex_realtime_enabled = (
        realtime_runtime_enabled()
        and codex_realtime_exposed
        and codex_realtime_configured
    )
    realtime_models = [
        (OPENAI_DEFAULT_REALTIME_MODEL, "OpenAI GPT Realtime 2.1"),
        (OPENAI_FAST_REALTIME_MODEL, "OpenAI GPT Realtime 2.1 Mini"),
    ]
    if not any(model == realtime_default.model for model, _label in realtime_models):
        realtime_models.insert(0, (realtime_default.model, f"OpenAI {realtime_default.model}"))
    models = list(_pi_text_models())
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
            label=str(item["displayName"]),
            capability="text",
            enabled=pi_openai_configured,
            configured=pi_openai_configured,
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
    deepseek_is_default = shared_deepseek.configured and not pi_openai_configured
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
    text_options.extend(
        AIModelOption(
            provider="deepseek",
            model=model,
            access_method="personal_api",
            label=label,
            capability="text",
            enabled=personal_deepseek_configured,
            configured=personal_deepseek_configured,
            default=False,
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
    elif personal_deepseek_configured and not pi_openai_configured:
        text_default = AIModelSelection(
            provider="deepseek",
            model=shared_deepseek.model,
            access_method="personal_api",
        )
        next(
            option
            for option in text_options
            if option.provider == "deepseek"
            and option.model == shared_deepseek.model
            and option.access_method == "personal_api"
        ).default = True
        codex_default_option.default = False
    else:
        text_default = default_text_selection(
            model=codex_default_option.model,
            reasoning_effort=codex_default_option.default_reasoning_effort,
            service_tier=codex_default_option.default_service_tier,
        )
    realtime_options = [
        AIModelOption(
            provider="openai",
            model=model,
            access_method="platform_credits",
            label=label,
            capability="realtime",
            enabled=realtime_enabled,
            configured=realtime_configured,
            default=not codex_realtime_enabled and model == realtime_default.model,
            transport="openai_webrtc",
        )
        for model, label in realtime_models
    ]
    if codex_realtime_exposed:
        realtime_options.insert(
            0,
            AIModelOption(
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                access_method="chatgpt_subscription",
                label="OpenAI Codex Live",
                capability="realtime",
                enabled=codex_realtime_enabled,
                configured=codex_realtime_configured,
                default=codex_realtime_enabled,
                transport="openai_webrtc",
            ),
        )
    realtime_default_selection = (
        AIModelSelection(
            provider="openai_codex",
            model=OPENAI_CODEX_REALTIME_MODEL,
            access_method="chatgpt_subscription",
        )
        if codex_realtime_enabled
        else realtime_default
    )
    return AIModelCatalog(
        text=text_options,
        realtime=realtime_options,
        defaults={"text": text_default, "realtime": realtime_default_selection},
        agent_backends=_agent_backend_options(),
    )

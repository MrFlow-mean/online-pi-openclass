from __future__ import annotations

import re
from pathlib import Path

from app.services.ai_model_catalog import (
    DEFAULT_REALTIME_MODEL_PROVIDERS,
    DEFAULT_TEXT_MODEL_PROVIDERS,
)


ROOT_DIR = Path(__file__).resolve().parents[3]
README = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")


def _assigned_value(document: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=(.*)$", document)
    assert match is not None, f"{name} is missing from .env.example"
    return match.group(1).strip()


def _provider_set(name: str) -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in _assigned_value(ENV_EXAMPLE, name).split(",")
        if item.strip()
    )


def test_self_host_example_exposes_every_default_model_provider() -> None:
    assert _provider_set("OPENCLASS_TEXT_MODEL_PROVIDERS") == DEFAULT_TEXT_MODEL_PROVIDERS
    assert (
        _provider_set("OPENCLASS_REALTIME_MODEL_PROVIDERS")
        == DEFAULT_REALTIME_MODEL_PROVIDERS
    )


def test_readme_model_quickstart_matches_the_runtime_contract() -> None:
    obsolete_text_assignments = (
        "AI_TEXT_PROVIDER",
        "OPENAI_MODEL",
        "OPENAI_PM_MODEL",
        "OPENAI_BOARD_MODEL",
        "OPENAI_CHATBOT_MODEL",
    )
    for name in obsolete_text_assignments:
        assert re.search(rf"(?m)^{re.escape(name)}=", README) is None

    assert "DEEPSEEK_API_KEY=your_deepseek_api_key" in README
    assert "`OPENAI_API_KEY` 当前只用于标准 OpenAI Realtime" in README
    assert "`OPENCLASS_CODEX_APP_SERVER_ENABLED=true`" in README

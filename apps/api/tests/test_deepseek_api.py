from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.services.deepseek_api import DeepSeekConfig, DeepSeekTextClient


class _Reply(BaseModel):
    answer: str


class _Guidance(BaseModel):
    options: list[str] = Field(default_factory=list)


class _ReplyWithGuidance(BaseModel):
    answer: str
    guidance: _Guidance = Field(default_factory=_Guidance)


class _FakeCompletions:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        return SimpleNamespace(
            id=f"response-{len(self.calls)}",
            choices=[SimpleNamespace(message=SimpleNamespace(content=output))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13),
        )


def _config() -> DeepSeekConfig:
    return DeepSeekConfig(
        api_key="server-shared-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=30,
        max_tokens=None,
    )


def test_deepseek_client_uses_json_output_and_validates_the_schema() -> None:
    completions = _FakeCompletions([json.dumps({"answer": "ok"})])
    client = DeepSeekTextClient(
        config=_config(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    parsed, activity = client.parse(
        system_prompt="Answer from the supplied context.",
        user_prompt="Question",
        schema=_Reply,
    )

    assert parsed == _Reply(answer="ok")
    assert completions.calls[0]["model"] == "deepseek-v4-flash"
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in completions.calls[0]["messages"][0]["content"]
    assert activity[0].role == "deepseek"


def test_deepseek_client_repairs_one_invalid_structured_response() -> None:
    completions = _FakeCompletions(
        [
            "not-json",
            json.dumps({"answer": "repaired"}),
        ]
    )
    client = DeepSeekTextClient(
        config=_config(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    parsed, _activity = client.parse(
        system_prompt="Return structured data.",
        user_prompt="Question",
        schema=_Reply,
    )

    assert parsed.answer == "repaired"
    assert len(completions.calls) == 2


def test_deepseek_client_tells_repair_turn_which_field_failed_validation() -> None:
    completions = _FakeCompletions(
        [
            json.dumps({"answer": "ok", "guidance": None}),
            json.dumps({"answer": "ok"}),
        ]
    )
    client = DeepSeekTextClient(
        config=_config(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    parsed, _activity = client.parse(
        system_prompt="Return structured data.",
        user_prompt="Question",
        schema=_ReplyWithGuidance,
    )

    repair_message = completions.calls[1]["messages"][-1]["content"]
    assert parsed.guidance == _Guidance()
    assert "guidance" in repair_message
    assert "valid dictionary" in repair_message
    assert "Do not return null for a non-nullable field" in repair_message


def test_deepseek_client_logs_safe_field_issues_when_repair_still_fails(
    monkeypatch,
) -> None:
    completions = _FakeCompletions(
        [
            json.dumps({"answer": "first", "guidance": None}),
            json.dumps({"answer": "second", "guidance": None}),
        ]
    )
    client = DeepSeekTextClient(
        config=_config(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    logged_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "app.services.deepseek_api.ai_usage_logger.log_event",
        lambda event_type, **payload: logged_events.append((event_type, payload)),
    )

    with pytest.raises(RuntimeError, match="invalid structured response"):
        client.parse(
            system_prompt="Return structured data.",
            user_prompt="Question",
            schema=_ReplyWithGuidance,
        )

    event_type, payload = logged_events[-1]
    serialized_payload = json.dumps(payload)
    assert event_type == "deepseek_structured_response_failed"
    assert payload["initial_validation_issues"][0]["path"] == "guidance"
    assert payload["repair_validation_issues"][0]["path"] == "guidance"
    assert "first" not in serialized_payload
    assert "second" not in serialized_payload


def test_deepseek_text_model_rejects_image_inputs() -> None:
    completions = _FakeCompletions([json.dumps({"answer": "unused"})])
    client = DeepSeekTextClient(
        config=_config(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    try:
        client.parse(
            system_prompt="Return structured data.",
            user_prompt="Question",
            schema=_Reply,
            image_inputs=["data:image/png;base64,AAAA"],
        )
    except RuntimeError as exc:
        assert "does not accept image inputs" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("DeepSeek image inputs should be rejected")

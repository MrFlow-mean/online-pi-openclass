from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.models import AIModelSelection
from app.services import ai_execution_adapter, codex_app_server, pi_agent_runtime
from app.services.billing_service import BillingConfig, BillingService
from app.services.codex_app_server import CodexTurnCancelledError
from app.services.lesson_factory import build_requirements
from app.services.openrouter_provisioning import (
    OpenRouterConfig,
    OpenRouterProvisioningService,
)
from app.services.pi_agent_runtime import PiTextClient


class _Answer(BaseModel):
    answer: str


_real_ensure_pi_openai_codex_auth = pi_agent_runtime.ensure_pi_openai_codex_auth


@pytest.fixture(autouse=True)
def _allow_fake_pi_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        pi_agent_runtime,
        "ensure_pi_openai_codex_auth",
        lambda **_kwargs: True,
    )


def _test_access_token(*, expires: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expires}).encode("utf-8")
    ).decode().rstrip("=")
    return f"{header}.{payload}.signature"


def _write_codex_auth(
    *,
    user_id: str,
    access: str,
    refresh: str,
    account_id: str,
) -> None:
    home = codex_app_server.codex_home_path(user_id)
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access,
                    "refresh_token": refresh,
                    "account_id": account_id,
                },
            }
        ),
        encoding="utf-8",
    )


def test_codex_auth_is_bridged_into_the_matching_pi_user_directory(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    expires = int(time.time()) + 3600
    access = _test_access_token(expires=expires)
    _write_codex_auth(
        user_id="user_a",
        access=access,
        refresh="refresh-a",
        account_id="account-a",
    )

    assert _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=tmp_path / "pi",
    )

    agent_dir = pi_agent_runtime.pi_agent_directory(
        owner_user_id="user_a",
        runtime_root=tmp_path / "pi",
    )
    credential = json.loads((agent_dir / "auth.json").read_text(encoding="utf-8"))[
        "openai-codex"
    ]
    assert credential == {
        "type": "oauth",
        "access": access,
        "refresh": "refresh-a",
        "expires": expires * 1000,
        "accountId": "account-a",
    }
    assert "user_a" not in str(agent_dir)
    assert agent_dir.stat().st_mode & 0o777 == 0o700
    assert (agent_dir / "auth.json").stat().st_mode & 0o777 == 0o600


def test_unchanged_codex_auth_does_not_overwrite_a_pi_token_refresh(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    source_access = _test_access_token(expires=int(time.time()) + 3600)
    _write_codex_auth(
        user_id="user_a",
        access=source_access,
        refresh="source-refresh",
        account_id="account-a",
    )
    runtime_root = tmp_path / "pi"
    assert _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )
    agent_dir = pi_agent_runtime.pi_agent_directory(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )
    auth_path = agent_dir / "auth.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["openai-codex"].update(
        access="pi-refreshed-access",
        refresh="pi-refreshed-refresh",
    )
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    assert _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )
    preserved = json.loads(auth_path.read_text(encoding="utf-8"))["openai-codex"]
    assert preserved["access"] == "pi-refreshed-access"
    assert preserved["refresh"] == "pi-refreshed-refresh"


def test_a_new_codex_login_replaces_the_pi_credential(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    runtime_root = tmp_path / "pi"
    _write_codex_auth(
        user_id="user_a",
        access=_test_access_token(expires=int(time.time()) + 3600),
        refresh="refresh-a",
        account_id="account-a",
    )
    assert _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )
    replacement_access = _test_access_token(expires=int(time.time()) + 7200)
    _write_codex_auth(
        user_id="user_a",
        access=replacement_access,
        refresh="refresh-b",
        account_id="account-b",
    )

    assert _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )

    agent_dir = pi_agent_runtime.pi_agent_directory(
        owner_user_id="user_a",
        runtime_root=runtime_root,
    )
    credential = json.loads((agent_dir / "auth.json").read_text(encoding="utf-8"))[
        "openai-codex"
    ]
    assert credential["access"] == replacement_access
    assert credential["refresh"] == "refresh-b"
    assert credential["accountId"] == "account-b"


def test_invalid_codex_auth_is_not_exposed_to_pi(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    home = codex_app_server.codex_home_path("user_a")
    home.mkdir(parents=True)
    (home / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")

    assert not _real_ensure_pi_openai_codex_auth(
        owner_user_id="user_a",
        runtime_root=tmp_path / "pi",
    )


def test_pi_binary_can_be_configured_outside_path(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "pi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("OPENCLASS_PI_BINARY", str(binary))

    assert pi_agent_runtime.pi_binary_path() == str(binary.resolve())
    assert pi_agent_runtime.pi_runtime_available()


def test_pi_binary_falls_back_to_project_dependency(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_PI_BINARY", raising=False)
    monkeypatch.setattr(pi_agent_runtime.shutil, "which", lambda _name: None)
    bundled = (
        Path(pi_agent_runtime.__file__).resolve().parents[4]
        / "node_modules"
        / ".bin"
        / "pi"
    )
    monkeypatch.setattr(Path, "is_file", lambda path: path == bundled)
    monkeypatch.setattr(
        pi_agent_runtime.os,
        "access",
        lambda path, mode: path == bundled and mode == pi_agent_runtime.os.X_OK,
    )

    assert pi_agent_runtime.pi_binary_path() == str(bundled)


def _pi_stdout(content: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def _pi_stdout_with_usage(content: str, *, cost_usd: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "usage": {
                                "input": 120,
                                "output": 30,
                                "cacheRead": 10,
                                "cacheWrite": 0,
                                "totalTokens": 160,
                                "cost": {"total": cost_usd},
                            },
                        }
                    ],
                }
            ),
        ]
    )


def _pi_error_stdout(message: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "errorMessage": message,
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def _pi_stdout_with_live_reasoning(content: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_start",
                        "contentIndex": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 0,
                        "delta": "private reasoning that must not be persisted",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_end",
                        "contentIndex": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_start",
                        "contentIndex": 1,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 1,
                        "delta": content,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 1,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def test_pi_client_runs_without_tools_or_discovered_resources(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _pi_stdout('{"answer":"ok"}'), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="deepseek",
        model="deepseek-v4-flash",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(
        system_prompt="Answer from verified context.",
        user_prompt="Question",
        schema=_Answer,
    )

    command, kwargs = calls[0]
    assert response.output_parsed == _Answer(answer="ok")
    assert command[:5] == [
        "/test/pi",
        "--provider",
        "deepseek",
        "--model",
        "deepseek-v4-flash",
    ]
    assert "--no-tools" in command
    assert "--no-extensions" in command
    assert "--no-context-files" in command
    assert kwargs["input"] == "Question"
    assert kwargs["env"]["PI_TELEMETRY"] == "0"
    assert kwargs["timeout"] == 600
    assert str(kwargs["env"]["PI_CODING_AGENT_DIR"]).startswith(str(tmp_path))


def test_pi_client_separates_platform_and_personal_provider_credentials(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    pi_agent_runtime.save_pi_personal_api_key(
        owner_user_id="user_test",
        provider="deepseek",
        api_key="sk-personal-route",
        runtime_root=tmp_path,
    )
    observed_auth: list[dict[str, object]] = []

    def run(command, **kwargs):
        auth_path = Path(kwargs["env"]["PI_CODING_AGENT_DIR"]) / "auth.json"
        observed_auth.append(
            json.loads(auth_path.read_text(encoding="utf-8"))
            if auth_path.exists()
            else {}
        )
        return subprocess.CompletedProcess(command, 0, _pi_stdout('{"answer":"ok"}'), "")

    for access_method in ("platform_credits", "personal_api"):
        PiTextClient(
            owner_user_id="user_test",
            provider="deepseek",
            model="deepseek-v4-flash",
            access_method=access_method,
            binary="/test/pi",
            runtime_root=tmp_path,
            process_runner=run,
        ).parse(
            system_prompt="Answer.",
            user_prompt="Question",
            schema=_Answer,
        )

    assert observed_auth[0] == {}
    assert observed_auth[1]["deepseek"] == {
        "type": "api_key",
        "key": "sk-personal-route",
    }


def test_platform_credit_request_reserves_and_charges_reported_pi_cost(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CREDIT_BILLING_ENABLED", "true")
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    config = BillingConfig(
        mode="sandbox",
        client_id="client-id",
        client_secret="client-secret",
        webhook_id="webhook-id",
        currency="USD",
        public_origin="https://openclass.example",
        credit_value_percent=75,
        top_up_amounts_cents=(10000,),
    )
    billing = BillingService(tmp_path / "billing.sqlite3", config=config)
    with billing._transaction() as connection:
        wallet = billing._wallet_row(connection, "user_test")
        connection.execute(
            "UPDATE credit_wallets SET balance_credits = 100 WHERE user_id = ?",
            (wallet["user_id"],),
        )

    response = PiTextClient(
        owner_user_id="user_test",
        provider="deepseek",
        model="deepseek-v4-flash",
        access_method="platform_credits",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            _pi_stdout_with_usage("answer", cost_usd="0.1234"),
            "",
        ),
        billing_service=billing,
    ).complete_text(system_prompt="Answer.", user_prompt="Question")

    assert response.output_text == "answer"
    assert billing.wallet("user_test")["balance_credits"] == 83
    usage_entry = billing.transactions("user_test")[0]
    assert usage_entry["kind"] == "model_usage"
    assert usage_entry["delta_credits"] == -17
    assert usage_entry["metadata"]["total_tokens"] == 160


def test_platform_credit_openrouter_route_injects_only_the_users_private_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("OPENCLASS_CREDIT_BILLING_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_API_KEY", "must-not-reach-pi")
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    billing = BillingService(
        tmp_path / "billing.sqlite3",
        config=BillingConfig(
            mode="sandbox",
            client_id="client-id",
            client_secret="client-secret",
            webhook_id="webhook-id",
            currency="USD",
            public_origin="https://openclass.example",
            credit_value_percent=75,
            top_up_amounts_cents=(10_000,),
        ),
    )
    openrouter = OpenRouterProvisioningService(
        billing,
        config=OpenRouterConfig(
            provisioning_enabled=True,
            management_api_key="management-secret",
            api_origin="https://openrouter.example",
            sync_interval_seconds=1,
            safety_buffer_microusd=25_000_000,
            credentials_dir=tmp_path / "private-openrouter",
            model_map={"deepseek:deepseek-v4-flash": "deepseek/deepseek-chat"},
        ),
    )
    timestamp = "2026-07-27T00:00:00+00:00"
    with billing._transaction() as connection:
        connection.execute(
            """
            INSERT INTO openrouter_user_keys (
                user_id, key_hash, key_label, key_name, target_limit_microusd,
                usage_microusd, status, disabled, created_at, updated_at
            ) VALUES (
                'user_test', 'hash-user', 'user key', 'openclass-user-hash',
                75000000, 0, 'ready', 0, ?, ?
            )
            """,
            (timestamp, timestamp),
        )
    openrouter._write_secret("user_test", "sk-or-v1-private-user")
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["key"] = kwargs["env"].get("OPENROUTER_API_KEY")
        observed["management_key"] = kwargs["env"].get(
            "OPENROUTER_MANAGEMENT_API_KEY"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            _pi_stdout_with_usage("answer", cost_usd="0.25"),
            "",
        )

    response = PiTextClient(
        owner_user_id="user_test",
        provider="deepseek",
        model="deepseek-v4-flash",
        access_method="platform_credits",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
        billing_service=billing,
        openrouter_service=openrouter,
    ).complete_text(system_prompt="Answer.", user_prompt="Question")

    assert response.output_text == "answer"
    command = observed["command"]
    assert isinstance(command, list)
    assert command[command.index("--provider") + 1] == "openrouter"
    assert command[command.index("--model") + 1] == "deepseek/deepseek-chat"
    assert observed["key"] == "sk-or-v1-private-user"
    assert observed["management_key"] is None
    key = openrouter.store.key("user_test")
    assert key is not None
    assert key["usage_microusd"] == 250_000


def test_pi_client_converts_live_json_events_into_public_activity(tmp_path) -> None:
    observed = []

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            _pi_stdout_with_live_reasoning('{"answer":"ok"}'),
            "",
        )

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=observed.append,
    )

    assert response.output_parsed.answer == "ok"
    assert any(event.label == "OpenClass 正在推理" for event in observed)
    assert any(event.label == "OpenClass 已完成推理" for event in observed)
    assert any(event.label == "OpenClass 正在生成结果" for event in observed)
    assert any(event.label == "OpenClass 已校验模型结果" for event in observed)
    assert all("private reasoning" not in str(event.metadata) for event in observed)
    assert all("private reasoning" not in str(event.metadata) for event in response.activity)


def test_pi_client_publishes_activity_before_the_process_finishes(tmp_path) -> None:
    fake_pi = tmp_path / "fake-pi"
    fake_pi.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time

sys.stdin.read()
print(json.dumps({"type": "agent_start"}), flush=True)
time.sleep(0.2)
print(json.dumps({
    "type": "message_update",
    "assistantMessageEvent": {"type": "text_start", "contentIndex": 0},
}), flush=True)
time.sleep(0.2)
print(json.dumps({
    "type": "message_end",
    "message": {"role": "assistant", "content": [{"type": "text", "text": "{\\\"answer\\\":\\\"ok\\\"}"}]},
}), flush=True)
print(json.dumps({"type": "agent_end", "messages": []}), flush=True)
""",
        encoding="utf-8",
    )
    os.chmod(fake_pi, 0o700)
    observed: list[tuple[float, object]] = []

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary=str(fake_pi),
        runtime_root=tmp_path / "runtime",
    ).parse(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=lambda event: observed.append((time.monotonic(), event)),
    )
    finished_at = time.monotonic()

    assert response.output_parsed.answer == "ok"
    assert observed
    assert observed[0][0] < finished_at - 0.25


def test_pi_client_streams_plain_text_deltas_without_waiting_for_completion(tmp_path) -> None:
    fake_pi = tmp_path / "fake-pi-text"
    fake_pi.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time

sys.stdin.read()
print(json.dumps({"type": "agent_start"}), flush=True)
print(json.dumps({
    "type": "message_update",
    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "第一段"},
}), flush=True)
time.sleep(0.25)
print(json.dumps({
    "type": "message_update",
    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "第二段"},
}), flush=True)
print(json.dumps({
    "type": "message_end",
    "message": {"role": "assistant", "content": [{"type": "text", "text": "第一段第二段"}]},
}), flush=True)
print(json.dumps({"type": "agent_end", "messages": []}), flush=True)
""",
        encoding="utf-8",
    )
    os.chmod(fake_pi, 0o700)
    observed: list[tuple[float, str]] = []

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary=str(fake_pi),
        runtime_root=tmp_path / "runtime",
    ).complete_text(
        system_prompt="Answer.",
        user_prompt="Question",
        on_text_delta=lambda delta: observed.append((time.monotonic(), delta)),
    )
    finished_at = time.monotonic()

    assert response.output_text == "第一段第二段"
    assert [delta for _, delta in observed] == ["第一段", "第二段"]
    assert observed[0][0] < finished_at - 0.15


def test_pi_adapter_generates_board_as_direct_markdown(monkeypatch) -> None:
    monkeypatch.setattr(pi_agent_runtime.shutil, "which", lambda _binary: "/test/pi")
    adapter = ai_execution_adapter.PiAIExecutionAdapter(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
    )
    captured: dict[str, object] = {}

    def complete_text(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="# Generated board\n\nA direct Markdown document.",
            activity=[],
        )

    monkeypatch.setattr(adapter._client, "complete_text", complete_text)
    result, content = adapter.generate_board(
        ai_execution_adapter.BoardGenerationExecutionRequest(
            requirement=build_requirements("A general learning topic"),
            teaching_plan="Build a concept-first explanation.",
        ),
        is_cancelled=lambda: False,
        on_activity=None,
    )

    assert content == "# Generated board\n\nA direct Markdown document."
    assert result.final_response == ""
    assert "Return only the board Markdown" in captured["system_prompt"]
    assert "JSON object" in captured["system_prompt"]
    assert captured["is_cancelled"]() is False


def test_pi_client_stages_validated_image_inputs_for_the_cli(tmp_path) -> None:
    captured: dict[str, object] = {}
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"bounded-test-image"
    image_input = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

    def run(command, **kwargs):
        captured["command"] = command
        cwd = kwargs["cwd"]
        image_argument = next(item for item in command if item.startswith("@input-"))
        captured["image_bytes"] = (cwd / image_argument[1:]).read_bytes()
        return subprocess.CompletedProcess(command, 0, _pi_stdout("image understood"), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).complete_text(
        system_prompt="Inspect the image.",
        user_prompt="What is shown?",
        image_inputs=[image_input],
    )

    assert response.output_text == "image understood"
    assert captured["image_bytes"] == png_bytes


def test_pi_client_rejects_an_image_whose_bytes_do_not_match_its_mime(tmp_path) -> None:
    image_input = "data:image/png;base64," + base64.b64encode(b"not a png").decode("ascii")
    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="does not match its declared MIME type"):
        client.complete_text(
            system_prompt="Inspect.",
            user_prompt="Question",
            image_inputs=[image_input],
        )


def test_pi_client_cancels_the_underlying_process_promptly(tmp_path) -> None:
    fake_pi = tmp_path / "fake-pi-cancel"
    fake_pi.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time

sys.stdin.read()
print(json.dumps({"type": "agent_start"}), flush=True)
time.sleep(30)
""",
        encoding="utf-8",
    )
    os.chmod(fake_pi, 0o700)
    cancel_event = threading.Event()
    threading.Timer(0.2, cancel_event.set).start()
    started_at = time.monotonic()

    with pytest.raises(CodexTurnCancelledError):
        PiTextClient(
            owner_user_id="user_test",
            provider="openai_codex",
            model="gpt-5.5",
            binary=str(fake_pi),
            runtime_root=tmp_path / "runtime",
        ).complete_text(
            system_prompt="Answer.",
            user_prompt="Question",
            is_cancelled=cancel_event.is_set,
        )

    assert time.monotonic() - started_at < 2


def test_pi_client_does_not_retry_after_visible_text_was_streamed(tmp_path) -> None:
    calls = 0

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        stdout = "\n".join(
            [
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "text_delta",
                            "contentIndex": 0,
                            "delta": "partial",
                        },
                    }
                ),
                _pi_error_stdout("WebSocket error"),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    )

    with pytest.raises(RuntimeError, match="WebSocket error"):
        client.complete_text(
            system_prompt="Answer.",
            user_prompt="Question",
            on_text_delta=lambda _delta: None,
        )

    assert calls == 1


def test_pi_client_accepts_a_bounded_request_timeout(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS", "420")

    def run(_command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess([], 0, _pi_stdout('{"answer":"ok"}'), "")

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert calls[0]["timeout"] == 420


def test_pi_client_retries_one_transient_websocket_failure(tmp_path) -> None:
    outputs = iter(
        [
            _pi_error_stdout("WebSocket error"),
            _pi_stdout('{"answer":"recovered"}'),
        ]
    )
    calls = 0

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, next(outputs), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert response.output_parsed.answer == "recovered"
    assert calls == 2


def test_pi_client_does_not_retry_a_non_transient_failure(tmp_path) -> None:
    calls = 0

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            _pi_error_stdout("Invalid authentication"),
            "",
        )

    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    )

    try:
        client.parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)
    except RuntimeError as error:
        assert str(error) == "Pi model request failed: Invalid authentication"
    else:  # pragma: no cover - guards retry classification
        raise AssertionError("non-transient Pi failure was accepted")

    assert calls == 1


def test_pi_client_rejects_an_invalid_request_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS", "unbounded")

    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=lambda *_args, **_kwargs: None,
    )

    try:
        client.parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)
    except RuntimeError as exc:
        assert str(exc) == "OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS must be an integer"
    else:  # pragma: no cover - guards configuration validation
        raise AssertionError("invalid Pi request timeout was accepted")


def test_pi_client_uses_an_explicit_operator_agent_directory(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[dict[str, object]] = []
    configured_agent_dir = tmp_path / "configured-agent"
    configured_agent_dir.mkdir()
    monkeypatch.setenv("OPENCLASS_PI_AGENT_DIR", str(configured_agent_dir))

    def run(_command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            [],
            0,
            _pi_stdout('{"answer":"ok"}'),
            "",
        )

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.6-sol",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert calls[0]["env"]["PI_CODING_AGENT_DIR"] == str(configured_agent_dir)


def test_pi_client_maps_codex_provider_and_repairs_invalid_json(tmp_path) -> None:
    outputs = iter([_pi_stdout("not json"), _pi_stdout('{"answer":"fixed"}')])
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, next(outputs), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        reasoning_effort="high",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert response.output_parsed.answer == "fixed"
    assert commands[0][2] == "openai-codex"
    assert commands[0][-2:] == ["--thinking", "high"]


def test_pi_client_applies_a_supported_service_tier(tmp_path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _pi_stdout('{"answer":"ok"}'), "")

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        service_tier="priority",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    command, kwargs = calls[0]
    assert command[command.index("--extension") + 1].endswith(
        "pi_runtime_settings_extension.ts"
    )
    assert kwargs["env"]["OPENCLASS_PI_SERVICE_TIER"] == "priority"


@pytest.mark.parametrize(
    ("provider", "service_tier"),
    [("deepseek", "priority"), ("openai_codex", "unsupported")],
)
def test_pi_client_rejects_an_unsupported_service_tier(
    provider: str,
    service_tier: str,
    tmp_path,
) -> None:
    with pytest.raises(RuntimeError, match="does not support this service tier"):
        PiTextClient(
            owner_user_id="user_test",
            provider=provider,
            model="test-model",
            service_tier=service_tier,
            binary="/test/pi",
            runtime_root=tmp_path,
        )


def test_server_forces_pi_adapter_for_a_legacy_codex_backend_selection(monkeypatch) -> None:
    captured: dict[str, object] = {}
    observed_activity = []

    class FakePiTextClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def parse(self, **kwargs):
            assert observed_activity[0].status == "running"
            return SimpleNamespace(
                output_parsed=kwargs["schema"](answer="through pi"),
                activity=[],
            )

    monkeypatch.setattr(ai_execution_adapter, "PiTextClient", FakePiTextClient)
    adapter = ai_execution_adapter.build_ai_execution_adapter(
        AIModelSelection(
            agent_backend="codex",
            provider="deepseek",
            model="deepseek-v4-flash",
        ),
        owner_user_id="user_test",
    )

    result = adapter.parse_structured(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=observed_activity.append,
    )

    assert captured == {
        "owner_user_id": "user_test",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "access_method": "platform_credits",
        "reasoning_effort": None,
        "service_tier": None,
    }
    assert result.output_parsed.answer == "through pi"
    assert [event.status for event in observed_activity] == ["running", "completed"]
    assert observed_activity[0].id == observed_activity[1].id
    assert result.activity == [observed_activity[1]]

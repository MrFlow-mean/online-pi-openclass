from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.models import CodexAccountView
from app.services import codex_app_server, pi_agent_runtime
from app.services.ai_call_budget import AICallBudget, bind_ai_call_budget


class _Payload(BaseModel):
    value: str = ""


class _NestedPayload(BaseModel):
    note: str = ""


class _StructuredPayload(BaseModel):
    value: str = ""
    nested: _NestedPayload = Field(default_factory=_NestedPayload)


def _source_thread_result(cwd: Path) -> dict:
    return {
        "thread": {"id": "source-thread"},
        "activePermissionProfile": {"id": "openclass_source"},
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [str((cwd / "scratch").resolve())],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


@pytest.fixture
def prepared_source_toolbox(monkeypatch):
    def prepare(
        *,
        cwd: Path,
        source_path: Path,
        scratch_path: Path,
        inspection_scope: str = "source",
    ) -> Path:
        del source_path, scratch_path, inspection_scope
        toolbox = cwd / "toolbox"
        (toolbox / "bin").mkdir(parents=True)
        return toolbox

    monkeypatch.setattr(
        codex_app_server.source_document_toolchain,
        "prepare_source_document_toolbox",
        prepare,
    )


def test_managed_codex_session_inherits_active_absolute_deadline(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Session:
        def __init__(self, *, user_id, timeout_seconds, deadline_monotonic) -> None:
            captured["user_id"] = user_id
            captured["timeout_seconds"] = timeout_seconds
            captured["deadline_monotonic"] = deadline_monotonic

        def close(self) -> None:
            captured["closed"] = 1

    monkeypatch.setattr(codex_app_server, "CodexAppServerSession", _Session)
    budget = AICallBudget(
        deadline_monotonic=time.monotonic() + 5,
        max_output_tokens=512,
        max_output_chars=4096,
    )

    with bind_ai_call_budget(budget):
        with codex_app_server._managed_session(user_id="user_a", timeout_seconds=30):
            pass

    assert captured["user_id"] == "user_a"
    assert captured["deadline_monotonic"] == budget.deadline_monotonic
    assert 0 < captured["timeout_seconds"] <= 5
    assert captured["closed"] == 1


def test_structured_turn_does_not_restart_deadline_after_thread_start(monkeypatch) -> None:
    clock = {"now": 100.0}
    writes: list[dict[str, object]] = []

    class _Session:
        deadline_monotonic = 105.0
        _next_id = 1

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert 0 < timeout_seconds <= 5
            clock["now"] += 5.1
            return {
                "thread": {"id": "thread-id"},
                "activePermissionProfile": {"id": "openclass_chat"},
                "sandbox": {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            }

        def _write(self, payload):
            writes.append(payload)

    monkeypatch.setattr(codex_app_server.time, "monotonic", lambda: clock["now"])

    with pytest.raises(codex_app_server.CodexAppServerError, match="Timed out"):
        codex_app_server._run_structured_turn(
            session=_Session(),
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
        )

    assert writes == []


@pytest.mark.parametrize("service_tier", ["priority", None])
def test_structured_turn_sends_provider_strict_output_schema(
    service_tier: str | None,
) -> None:
    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.writes: list[dict[str, object]] = []
            self.thread_params: dict[str, object] = {}
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": '{"value":"ok","nested":{"note":""}}',
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert timeout_seconds > 0
            self.thread_params = params
            return {
                "thread": {"id": "thread-id"},
                "activePermissionProfile": {"id": "openclass_chat"},
                "sandbox": {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            }

        def _write(self, payload):
            self.writes.append(payload)

        def _answer_server_request(self, message):
            raise AssertionError(message)

    session = _Session()

    codex_app_server._run_structured_turn(
        session=session,  # type: ignore[arg-type]
        model="gpt-5.5",
        system_prompt="system",
        user_prompt="user",
        schema=_StructuredPayload,
        image_inputs=["data:image/png;base64,AAAA"],
        allow_live_web_search=True,
        reasoning_effort="high",
        service_tier=service_tier,
        service_tier_is_set=True,
    )

    output_schema = session.writes[0]["params"]["outputSchema"]
    assert output_schema["additionalProperties"] is False
    assert output_schema["required"] == ["value", "nested"]
    nested_schema = output_schema["$defs"]["_NestedPayload"]
    assert nested_schema["additionalProperties"] is False
    assert nested_schema["required"] == ["note"]
    turn_params = session.writes[0]["params"]
    assert turn_params["effort"] == "high"
    assert turn_params["serviceTier"] == service_tier
    assert "sandboxPolicy" not in turn_params
    assert turn_params["input"] == [
        {"type": "text", "text": "user"},
        {"type": "image", "url": "data:image/png;base64,AAAA", "detail": "original"},
    ]
    assert session.thread_params["config"] == {
        "default_permissions": "openclass_chat",
        "web_search": "live",
    }
    assert "effort" not in session.thread_params
    assert session.thread_params["serviceTier"] == service_tier
    assert "built-in web search" in session.thread_params["developerInstructions"]
    assert "Role instructions:\nsystem" in session.thread_params["developerInstructions"]


def test_structured_turn_forwards_a_custom_model_provider() -> None:
    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.thread_params: dict[str, object] = {}
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": '{"value":"ok"}',
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert timeout_seconds > 0
            self.thread_params = params
            return {
                "thread": {"id": "thread-id"},
                "activePermissionProfile": {"id": "openclass_chat"},
                "sandbox": {"type": "readOnly", "networkAccess": False},
            }

        def _write(self, _payload):
            return None

        def _answer_server_request(self, message):
            raise AssertionError(message)

    session = _Session()

    codex_app_server._run_structured_turn(
        session=session,  # type: ignore[arg-type]
        provider="deepseek",
        model="deepseek-v4-pro",
        system_prompt="system",
        user_prompt="user",
        schema=_Payload,
    )

    assert session.thread_params["modelProvider"] == "deepseek"


def test_custom_model_provider_does_not_require_chatgpt_sign_in(monkeypatch) -> None:
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: pytest.fail("custom providers must not require ChatGPT status"),
    )

    codex_app_server._require_codex_model_access("user_a", "deepseek")


def test_codex_command_defines_an_isolated_source_permission_profile() -> None:
    rendered = "\n".join(
        codex_app_server._codex_app_server_command("/usr/local/bin/codex")
    )

    assert (
        'permissions.openclass_source.filesystem={":minimal"="read",'
        '":workspace_roots"={"."="read","scratch"="write"}}'
    ) in rendered
    assert "permissions.openclass_source.network.enabled=false" in rendered


def test_effective_source_permission_config_must_be_exact() -> None:
    valid = {
        "config": {
            "permissions": {
                "openclass_source": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                        ":workspace_roots": {".": "read", "scratch": "write"},
                    },
                    "network": {"enabled": False, "domains": None},
                }
            }
        }
    }
    codex_app_server._validate_effective_source_permission_config(valid)

    for source_profile in (
        {
            "filesystem": {
                ":minimal": "read",
                ":workspace_roots": {".": "write", "scratch": "write"},
            },
            "network": {"enabled": False},
        },
        {
            "filesystem": {
                ":minimal": "read",
                ":workspace_roots": {".": "read", "scratch": "write"},
            },
            "network": {"enabled": True},
        },
        {
            "filesystem": {
                ":minimal": "read",
                ":workspace_roots": {".": "read", "scratch": "write"},
            },
            "network": {"enabled": False, "domains": ["example.com"]},
        },
    ):
        invalid = {
            "config": {"permissions": {"openclass_source": source_profile}}
        }
        with pytest.raises(
            codex_app_server.CodexAppServerError,
            match="exact isolated Source Codex profile",
        ):
            codex_app_server._validate_effective_source_permission_config(invalid)


def test_source_thread_permission_response_rejects_any_broader_access(
    tmp_path: Path,
) -> None:
    safe = _source_thread_result(tmp_path)
    codex_app_server._validate_source_thread_permission_response(safe, cwd=tmp_path)

    unsafe_results = []
    for key, value in (
        ("type", "dangerFullAccess"),
        ("networkAccess", True),
        ("writableRoots", [str(tmp_path.resolve())]),
        ("excludeTmpdirEnvVar", False),
        ("excludeSlashTmp", False),
    ):
        unsafe_results.append(
            {**safe, "sandbox": {**safe["sandbox"], key: value}}
        )
    unsafe_results.append(
        {**safe, "activePermissionProfile": {"id": "openclass_board"}}
    )

    for unsafe in unsafe_results:
        with pytest.raises(
            codex_app_server.CodexAppServerError,
            match="exact source-file sandbox",
        ):
            codex_app_server._validate_source_thread_permission_response(
                unsafe,
                cwd=tmp_path,
            )


def test_source_structured_turn_stages_an_independent_read_only_copy(
    prepared_source_toolbox,
    tmp_path: Path,
) -> None:
    del prepared_source_toolbox
    source_path = tmp_path / "private original name.PDF"
    source_bytes = b"%PDF-1.7\nsource bytes\x00\xff"
    source_path.write_bytes(source_bytes)
    captured: dict[str, object] = {}

    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()

        def validate_source_permission_config(self, cwd: Path) -> None:
            captured["validated_cwd"] = cwd

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert timeout_seconds > 0
            cwd = Path(params["cwd"])
            staged_path = cwd / "source.pdf"
            captured["cwd"] = cwd
            captured["staged_path"] = staged_path
            captured["staged_bytes"] = staged_path.read_bytes()
            captured["same_file"] = staged_path.samefile(source_path)
            captured["is_symlink"] = staged_path.is_symlink()
            captured["source_mode"] = staged_path.stat().st_mode & 0o777
            captured["thread_params"] = params
            assert sorted(path.name for path in cwd.iterdir()) == [
                "scratch",
                "source.pdf",
                "toolbox",
            ]
            return _source_thread_result(cwd)

        def _write(self, payload):
            captured["turn_payload"] = payload
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": '{"value":"catalog","nested":{"note":""}}',
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def _answer_server_request(self, message):
            raise AssertionError(message)

    output_text, _usage, _activity, source_sha256, source_turn_count = (
        codex_app_server._run_source_file_structured_turn(
            session=_Session(),  # type: ignore[arg-type]
            source_path=source_path,
            model="gpt-5.5",
            system_prompt="return a directory",
            user_prompt="inspect the source",
            schema=_StructuredPayload,
            inspection_scope="directory_only",
        )
    )

    assert output_text.startswith('{"value":"catalog"')
    assert source_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert source_turn_count == 1
    assert captured["staged_bytes"] == source_bytes
    assert captured["same_file"] is False
    assert captured["is_symlink"] is False
    assert captured["source_mode"] & 0o222 == 0
    assert captured["validated_cwd"] == captured["cwd"]
    assert Path(captured["cwd"]).exists() is False
    thread_params = captured["thread_params"]
    assert thread_params["config"]["default_permissions"] == "openclass_source"
    assert thread_params["config"]["web_search"] == "disabled"
    assert "source.pdf" in thread_params["developerInstructions"]
    assert "scratch" in thread_params["developerInstructions"]
    assert "prefer one bounded pdftotext extraction" in thread_params["developerInstructions"]
    assert "do not assume that Python" in thread_params["developerInstructions"]
    assert "Directory-only hard boundary" in thread_params["developerInstructions"]
    assert "never scan the entire source" in thread_params["developerInstructions"]
    assert source_path.name not in thread_params["developerInstructions"]
    turn_payload = captured["turn_payload"]
    assert turn_payload["params"]["outputSchema"]["additionalProperties"] is False


def test_source_catalog_artifact_is_returned_only_after_receipt_and_schema_validation(
    prepared_source_toolbox,
    tmp_path: Path,
) -> None:
    del prepared_source_toolbox
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"source")
    catalog_text = '{"value":"complete catalog"}'
    captured: dict[str, object] = {}

    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.cwd: Path | None = None
            self.write_count = 0

        def validate_source_permission_config(self, cwd: Path) -> None:
            self.cwd = cwd

        def request(self, method, params, *, timeout_seconds):
            assert self.cwd is not None
            captured["thread_params"] = params
            return _source_thread_result(self.cwd)

        def _write(self, payload):
            assert self.cwd is not None
            self.write_count += 1
            captured["turn_payload"] = payload
            artifact = self.cwd / "scratch" / "catalog.json"
            artifact.write_text(catalog_text, encoding="utf-8")
            receipt = {
                "artifact_path": "scratch/catalog.json",
                "sha256": hashlib.sha256(catalog_text.encode("utf-8")).hexdigest(),
                "byte_count": len(catalog_text.encode("utf-8")),
            }
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": json.dumps(receipt),
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def _answer_server_request(self, message):
            raise AssertionError(message)

    session = _Session()
    output_text, _usage, _activity, source_sha256, source_turn_count = (
        codex_app_server._run_source_file_structured_turn(
            session=session,  # type: ignore[arg-type]
            source_path=source_path,
            model="gpt-5.5",
            system_prompt="produce the catalog artifact",
            user_prompt="inspect the source",
            schema=_Payload,
            output_artifact_path=codex_app_server.CODEX_SOURCE_CATALOG_ARTIFACT,
        )
    )

    assert output_text == catalog_text
    assert source_sha256 == hashlib.sha256(b"source").hexdigest()
    assert source_turn_count == 1
    assert session.write_count == 1
    turn_schema = captured["turn_payload"]["params"]["outputSchema"]
    assert set(turn_schema["properties"]) == {"artifact_path", "sha256", "byte_count"}
    instructions = captured["thread_params"]["developerInstructions"]
    assert "scratch/catalog.json" in instructions
    assert "Artifact JSON schema" in instructions
    turn_input = captured["turn_payload"]["params"]["input"][0]["text"]
    assert turn_input == "inspect the source"


def test_source_catalog_artifact_rejects_a_false_receipt(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    artifact = scratch / "catalog.json"
    artifact.write_text('{"value":"catalog"}', encoding="utf-8")
    staged = tmp_path / "source.pdf"
    staged.write_bytes(b"source")
    receipt = json.dumps(
        {
            "artifact_path": "scratch/catalog.json",
            "sha256": "0" * 64,
            "byte_count": artifact.stat().st_size,
        }
    )

    with pytest.raises(codex_app_server.CodexAppServerError, match="wrong SHA-256"):
        codex_app_server._read_source_catalog_artifact(
            scratch_path=scratch,
            staged_path=staged,
            receipt_text=receipt,
            schema=_Payload,
        )


def test_source_catalog_artifact_is_opened_nonblocking_and_without_link_following(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    artifact = scratch / "catalog.json"
    artifact_text = '{"value":"catalog"}'
    artifact.write_text(artifact_text, encoding="utf-8")
    staged = tmp_path / "source.pdf"
    staged.write_bytes(b"source")
    receipt = json.dumps(
        {
            "artifact_path": "scratch/catalog.json",
            "sha256": hashlib.sha256(artifact_text.encode("utf-8")).hexdigest(),
            "byte_count": len(artifact_text.encode("utf-8")),
        }
    )
    captured: dict[str, int] = {}
    real_open = codex_app_server.os.open

    def tracking_open(path, flags, *args, **kwargs):
        captured["flags"] = flags
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(codex_app_server.os, "open", tracking_open)

    output = codex_app_server._read_source_catalog_artifact(
        scratch_path=scratch,
        staged_path=staged,
        receipt_text=receipt,
        schema=_Payload,
    )

    assert output == artifact_text
    assert captured["flags"] & codex_app_server.os.O_NONBLOCK
    if hasattr(codex_app_server.os, "O_NOFOLLOW"):
        assert captured["flags"] & codex_app_server.os.O_NOFOLLOW


def test_source_tool_path_uses_only_the_workspace_toolbox_and_system_tools(
    tmp_path: Path,
) -> None:
    toolbox = tmp_path / "toolbox"
    toolbox_bin = toolbox / "bin"
    toolbox_bin.mkdir(parents=True)

    source_path = codex_app_server._source_document_tool_path(toolbox).split(":")

    assert source_path[0] == str(toolbox_bin)
    assert "/usr/bin" in source_path
    assert all("codex-runtimes" not in entry for entry in source_path)


def test_structured_workspace_turn_returns_validator_error_to_the_same_codex_thread(
    tmp_path: Path,
) -> None:
    captured_turns: list[dict] = []

    class _Session:
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            return {"thread": {"id": "thread_validation"}}

        def _write(self, payload):
            captured_turns.append(payload)
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": '{"value":"candidate","nested":{"note":""}}',
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def _answer_server_request(self, message):
            raise AssertionError(message)

    attempts = 0

    def validate(response: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("A verified child range falls outside its parent range.")
        return response.replace("candidate", "validated")

    output, _usage, _activity = codex_app_server._run_structured_workspace_turn(
        session=_Session(),  # type: ignore[arg-type]
        cwd=tmp_path,
        model="gpt-5.5",
        user_prompt="investigate",
        schema=_StructuredPayload,
        image_inputs=None,
        deadline_monotonic=time.monotonic() + 5,
        config={},
        service_name="test",
        developer_instructions="test",
        validate_permission=lambda _result: None,
        on_activity=None,
        reasoning_effort="low",
        service_tier=None,
        service_tier_is_set=False,
        response_validator=validate,
    )

    assert output.startswith('{"value":"validated"')
    assert attempts == 2
    assert len(captured_turns) == 2
    feedback = captured_turns[1]["params"]["input"][0]["text"]
    assert "outside its parent range" in feedback
    assert "do not terminate" in feedback


@pytest.mark.parametrize("tamper_target", ["staged", "original"])
def test_source_structured_turn_detects_file_mutation(
    prepared_source_toolbox,
    tmp_path: Path,
    tamper_target: str,
) -> None:
    del prepared_source_toolbox
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"original")

    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.cwd: Path | None = None

        def validate_source_permission_config(self, cwd: Path) -> None:
            pass

        def request(self, method, params, *, timeout_seconds):
            self.cwd = Path(params["cwd"])
            return _source_thread_result(self.cwd)

        def _write(self, payload):
            assert self.cwd is not None
            target = self.cwd / "source.pdf" if tamper_target == "staged" else source_path
            target.chmod(0o600)
            target.write_bytes(b"tampered")
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {"type": "agentMessage", "text": '{"value":"x"}'},
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def _answer_server_request(self, message):
            raise AssertionError(message)

    with pytest.raises(
        codex_app_server.CodexAppServerError,
        match="source-file integrity",
    ):
        codex_app_server._run_source_file_structured_turn(
            session=_Session(),  # type: ignore[arg-type]
            source_path=source_path,
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
        )


def test_source_structured_turn_fails_before_model_execution_when_pdf_tools_are_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-1.7\nfixture")

    def reject_toolbox(**_kwargs):
        raise codex_app_server.source_document_toolchain.SourceDocumentToolchainError(
            "PDF toolchain unavailable"
        )

    monkeypatch.setattr(
        codex_app_server.source_document_toolchain,
        "prepare_source_document_toolbox",
        reject_toolbox,
    )

    class _Session:
        deadline_monotonic = time.monotonic() + 5

        def validate_source_permission_config(self, _cwd: Path) -> None:
            pytest.fail("permission validation must not run without the PDF toolchain")

    with pytest.raises(
        codex_app_server.CodexAppServerError,
        match="PDF toolchain unavailable",
    ):
        codex_app_server._run_source_file_structured_turn(
            session=_Session(),  # type: ignore[arg-type]
            source_path=source_path,
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
        )


def test_source_structured_turn_rejects_a_symbolic_link(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    source_path = tmp_path / "linked.pdf"
    source_path.symlink_to(original)

    with pytest.raises(
        codex_app_server.CodexAppServerError,
        match="symbolic-link",
    ):
        codex_app_server._run_source_file_structured_turn(
            session=SimpleNamespace(deadline_monotonic=time.monotonic() + 5),
            source_path=source_path,
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
        )


def test_text_client_parse_source_file_validates_schema(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "book.epub"
    source_path.write_bytes(b"epub")
    captured: dict[str, object] = {}

    class _Managed:
        def __enter__(self):
            return object()

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    def managed_session(**kwargs):
        captured["managed_session"] = kwargs
        return _Managed()

    monkeypatch.setattr(codex_app_server, "_managed_session", managed_session)

    def run_source(**kwargs):
        captured.update(kwargs)
        return '{"value":"ok"}', None, [], "c" * 64, 1

    monkeypatch.setattr(
        codex_app_server,
        "_run_source_file_structured_turn",
        run_source,
    )

    result = codex_app_server.CodexAppServerTextClient("user_a").parse_source_file(
        source_path=source_path,
        model="gpt-5.5",
        system_prompt="system",
        user_prompt="user",
        schema=_Payload,
    )

    assert result.output_parsed.value == "ok"
    assert result.source_sha256 == "c" * 64
    assert result.source_turn_count == 1
    assert captured["source_path"] == source_path
    managed_kwargs = captured["managed_session"]
    assert managed_kwargs["timeout_seconds"] > codex_app_server.CODEX_APP_SERVER_TIMEOUT_SECONDS


def test_codex_home_is_isolated_per_openclass_user(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))

    first = codex_app_server.codex_home_path("user_a")
    second = codex_app_server.codex_home_path("user_b")

    assert first != second
    assert first.parent == second.parent == (tmp_path / "codex" / "accounts")
    assert "user_a" not in str(first)
    assert "user_b" not in str(second)


def test_copy_codex_auth_preserves_existing_target_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    source_home = codex_app_server.codex_home_path("guest_a")
    target_home = codex_app_server.codex_home_path("user_a")
    source_home.mkdir(parents=True)
    target_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"verified": true}', encoding="utf-8")
    (target_home / "state_5.sqlite").write_text("existing runtime", encoding="utf-8")

    codex_app_server.copy_codex_auth("guest_a", "user_a")

    assert (target_home / "auth.json").read_text(encoding="utf-8") == '{"verified": true}'
    assert (target_home / "state_5.sqlite").read_text(encoding="utf-8") == "existing runtime"
    assert (target_home / "auth.json").stat().st_mode & 0o777 == 0o600


def test_remove_codex_auth_preserves_source_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("OPENCLASS_PI_RUNTIME_ROOT", str(tmp_path / "pi"))
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source_home = codex_app_server.codex_home_path("guest_a")
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"refresh": "credential"}', encoding="utf-8")
    (source_home / "state_5.sqlite").write_text("guest runtime", encoding="utf-8")
    pi_home = pi_agent_runtime.pi_agent_directory(
        owner_user_id="guest_a",
        runtime_root=tmp_path / "pi",
    )
    (pi_home / "auth.json").write_text(
        '{"openai-codex": {"type": "oauth"}, "other": {"type": "api_key"}}',
        encoding="utf-8",
    )
    (pi_home / pi_agent_runtime.PI_CODEX_AUTH_FINGERPRINT_FILE).write_text(
        "fingerprint",
        encoding="utf-8",
    )

    codex_app_server.remove_codex_auth("guest_a")

    assert (source_home / "auth.json").exists() is False
    assert (source_home / "state_5.sqlite").read_text(encoding="utf-8") == "guest runtime"
    pi_auth = json.loads((pi_home / "auth.json").read_text(encoding="utf-8"))
    assert "openai-codex" not in pi_auth
    assert "other" in pi_auth
    assert not (pi_home / pi_agent_runtime.PI_CODEX_AUTH_FINGERPRINT_FILE).exists()


def test_codex_logout_removes_the_matching_pi_credential(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_PI_RUNTIME_ROOT", str(tmp_path / "pi"))
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)
    requests: list[tuple[str, dict, int]] = []

    class Session:
        def request(self, method, params, *, timeout_seconds):
            requests.append((method, params, timeout_seconds))

    @contextmanager
    def managed_session(**_kwargs):
        yield Session()

    monkeypatch.setattr(codex_app_server, "_managed_session", managed_session)
    pi_home = pi_agent_runtime.pi_agent_directory(
        owner_user_id="user_a",
        runtime_root=tmp_path / "pi",
    )
    (pi_home / "auth.json").write_text(
        '{"openai-codex": {"type": "oauth"}}',
        encoding="utf-8",
    )

    codex_app_server.logout_codex("user_a")

    assert requests == [("account/logout", {}, 30)]
    assert json.loads((pi_home / "auth.json").read_text(encoding="utf-8")) == {}


def test_codex_status_cache_is_isolated_per_openclass_user(monkeypatch) -> None:
    reads: list[str] = []
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            reads.append(user_id)
            or CodexAccountView(
                type="chatgpt",
                email=f"{user_id}@example.test",
            ),
            False,
        ),
    )
    with codex_app_server._status_cache_lock:
        codex_app_server._cached_status.clear()

    first = codex_app_server.codex_provider_status("user_a")
    second = codex_app_server.codex_provider_status("user_b")
    cached_first = codex_app_server.codex_provider_status("user_a")

    assert reads == ["user_a", "user_b"]
    assert first.account is not None
    assert second.account is not None
    assert cached_first.account is not None
    assert first.account.email == cached_first.account.email == "user_a@example.test"
    assert second.account.email == "user_b@example.test"
    with codex_app_server._status_cache_lock:
        codex_app_server._cached_status.clear()


def test_login_completion_with_null_login_id_is_owned_and_refreshes_account(monkeypatch) -> None:
    class _Session:
        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    session._messages.put(
        {
            "method": "account/login/completed",
            "params": {"loginId": None, "success": True},
        }
    )
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            CodexAccountView(type="chatgpt", email=f"{user_id}@example.test"),
            False,
        ),
    )

    codex_app_server._watch_login_attempt(attempt)

    assert attempt.status == "succeeded"
    assert attempt.account is not None
    assert attempt.account.email == "user_a@example.test"
    assert session.closed is True


def test_login_watcher_does_not_read_account_before_login_completion(monkeypatch) -> None:
    class _Messages:
        def __init__(self) -> None:
            self.items: list[dict] = []

        def get(self, timeout: float) -> dict:
            if self.items:
                return self.items.pop(0)
            raise queue.Empty

    class _Session:
        def __init__(self) -> None:
            self._messages = _Messages()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(codex_app_server, "CODEX_LOGIN_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda *_args, **_kwargs: pytest.fail("account/read ran before account/updated"),
    )

    codex_app_server._watch_login_attempt(attempt)

    assert attempt.status == "expired"
    assert attempt.error == "Codex login timed out"
    assert session.closed is True


def test_login_attempt_cannot_be_read_or_cancelled_by_another_user() -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_owned",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=None,
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        with pytest.raises(codex_app_server.CodexAppServerError, match="Unknown"):
            codex_app_server.codex_login_status(attempt.login_id, "user_b")
        with pytest.raises(codex_app_server.CodexAppServerError, match="Unknown"):
            codex_app_server.cancel_codex_login(attempt.login_id, "user_b")
        assert codex_app_server.cancel_codex_login(attempt.login_id, "user_a").status == "cancelled"
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_cancelled_login_cannot_be_overwritten_by_late_success(monkeypatch) -> None:
    account_read_started = threading.Event()
    release_account_read = threading.Event()

    class _Session:
        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    session._messages.put(
        {
            "method": "account/login/completed",
            "params": {"loginId": "login_cancel_race", "success": True},
        }
    )
    session._messages.put(
        {"method": "account/updated", "params": {"authMode": "chatgpt"}}
    )
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_cancel_race",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )

    def delayed_account_read(*_args, **_kwargs):
        account_read_started.set()
        assert release_account_read.wait(timeout=1)
        return CodexAccountView(type="chatgpt", email="user_a@example.test"), False

    monkeypatch.setattr(codex_app_server, "_read_account", delayed_account_read)
    watcher = threading.Thread(
        target=codex_app_server._watch_login_attempt,
        args=(attempt,),
        daemon=True,
    )
    attempt.thread = watcher
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        watcher.start()
        assert account_read_started.wait(timeout=1)
        cancelled = codex_app_server.cancel_codex_login(attempt.login_id, "user_a")
        release_account_read.set()
        watcher.join(timeout=1)

        assert cancelled.status == "cancelled"
        assert attempt.status == "cancelled"
        assert attempt.account is None
        assert watcher.is_alive() is False
    finally:
        release_account_read.set()
        watcher.join(timeout=1)
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_login_start_rejects_a_second_pending_attempt_for_same_user(monkeypatch) -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_pending",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=None,
    )
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()
    try:
        with pytest.raises(codex_app_server.CodexLoginRateLimitError, match="already in progress"):
            codex_app_server.start_codex_device_login("user_a")
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)
            codex_app_server._login_start_events.clear()
            codex_app_server._login_starting_users.clear()


def test_platform_login_claim_rejects_superseded_account(monkeypatch) -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="account-a@example.test"),
        completed_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            CodexAccountView(type="chatgpt", email="account-b@example.test"),
            False,
        ),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        with pytest.raises(codex_app_server.CodexAppServerError, match="no longer matches"):
            codex_app_server.claim_completed_codex_platform_login(attempt.login_id, "guest_a")

        assert attempt.status == "failed"
        assert attempt.completion_state == "consumed"
        assert "superseded" in (attempt.error or "")
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_platform_login_claim_is_consumed_after_matching_account(monkeypatch) -> None:
    account = CodexAccountView(type="chatgpt", email="account-a@example.test")
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=account,
        completed_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (account, False),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        claimed = codex_app_server.claim_completed_codex_platform_login(attempt.login_id, "guest_a")
        codex_app_server.complete_codex_platform_login_claim(attempt.login_id, "guest_a")

        assert claimed.email == "account-a@example.test"
        assert attempt.completion_state == "consumed"
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_unconsumed_platform_login_blocks_replacement_until_consumed() -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="account-a@example.test"),
        completed_at=datetime.now(timezone.utc),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()
    try:
        with pytest.raises(codex_app_server.CodexLoginRateLimitError, match="already in progress"):
            codex_app_server._reserve_login_start("guest_a", "platform")

        attempt.completion_state = "consumed"
        codex_app_server._reserve_login_start("guest_a", "platform")
        assert "guest_a" in codex_app_server._login_starting_users
    finally:
        codex_app_server._release_login_start("guest_a")
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)
            codex_app_server._login_start_events.clear()
            codex_app_server._login_starting_users.clear()


def test_login_start_cleans_up_when_watcher_thread_cannot_start(monkeypatch) -> None:
    class _Session:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        def request(self, method: str, _params: dict, **_kwargs) -> dict:
            assert method == "account/login/start"
            return {
                "loginId": "login_thread_failure",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }

        def close(self) -> None:
            self.closed = True

    class _Thread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread unavailable")

    session = _Session()
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "CodexAppServerSession", lambda **_kwargs: session)
    monkeypatch.setattr(codex_app_server.threading, "Thread", _Thread)
    with codex_app_server._login_lock:
        codex_app_server._login_attempts.clear()
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()

    with pytest.raises(RuntimeError, match="thread unavailable"):
        codex_app_server.start_codex_device_login("user_a")

    with codex_app_server._login_lock:
        assert "login_thread_failure" not in codex_app_server._login_attempts
        assert "user_a" not in codex_app_server._login_starting_users
        codex_app_server._login_start_events.clear()
    assert session.closed is True


def test_prune_login_state_removes_old_terminal_attempt_data() -> None:
    completed_at = datetime.now(timezone.utc) - timedelta(
        seconds=codex_app_server.CODEX_LOGIN_ATTEMPT_RETENTION_SECONDS + 1
    )
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_old",
        verification_url="https://example.test/device",
        user_code="SENSITIVE-CODE",
        expires_at=completed_at,
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="user_a@example.test"),
        completed_at=completed_at,
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._prune_login_state_locked(
            now_monotonic=time.monotonic(),
            now_utc=datetime.now(timezone.utc),
        )
        retained = attempt.login_id in codex_app_server._login_attempts

    assert retained is False

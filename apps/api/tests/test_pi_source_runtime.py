from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from app.services.pi_source_runtime import PI_SOURCE_TOOLS, PiSourceTextClient


class _Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    complete: bool
    nodes: list[str]


def _run_with_artifacts(payloads: list[dict[str, object]]):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    return calls, run


def test_pi_source_client_exposes_only_openclass_source_tools(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("Contents\nOne\nTwo\n", encoding="utf-8")
    calls, runner = _run_with_artifacts([{"complete": True, "nodes": ["One", "Two"]}])

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=runner,
    ).parse_source_file(
        source_path=source,
        provider="openai_codex",
        model="gpt-test",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="directory_only",
    )

    command, kwargs = calls[0]
    assert response.output_parsed == _Catalog(complete=True, nodes=["One", "Two"])
    assert command[:5] == ["/test/pi", "--provider", "openai-codex", "--model", "gpt-test"]
    assert "--no-builtin-tools" in command
    assert "--no-tools" not in command
    assert command[command.index("--tools") + 1] == ",".join(PI_SOURCE_TOOLS)
    assert command[command.index("--extension") + 1].endswith("pi_source_agent_extension.ts")
    assert kwargs["env"]["OPENCLASS_PI_SOURCE_FILE"] == "source.txt"
    assert kwargs["env"]["OPENCLASS_PI_SOURCE_SCRATCH"] == "scratch"
    assert kwargs["env"]["OPENCLASS_PI_SOURCE_INSPECTION_SCOPE"] == "directory_only"
    assert response.source_turn_count == 1
    assert response.activity[0].metadata["source_tool_policy"] == (
        "openclass_read_only_directory_tools"
    )


def test_pi_source_client_retries_a_mechanically_rejected_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.md"
    source.write_text("# First\n# Second\n", encoding="utf-8")
    calls, runner = _run_with_artifacts(
        [
            {"complete": True, "nodes": ["wrong"]},
            {"complete": True, "nodes": ["First", "Second"]},
        ]
    )

    def validate(payload: object) -> None:
        if isinstance(payload, dict) and payload.get("nodes") == ["wrong"]:
            raise RuntimeError("directory entries do not match the source")

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=runner,
    ).parse_source_file(
        source_path=source,
        provider="deepseek",
        model="deepseek-test",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="directory_only",
        artifact_validator=validate,
    )

    assert len(calls) == 2
    assert "mechanical validator rejected" in str(calls[1][1]["input"])
    assert response.output_parsed.nodes == ["First", "Second"]
    assert response.source_turn_count == 2


def test_pi_source_client_fails_closed_when_no_artifact_is_written(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("One\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(RuntimeError, match="failed OpenClass validation"):
        PiSourceTextClient(
            "user_test",
            binary="/test/pi",
            runtime_root=tmp_path / "runtime",
            process_runner=run,
        ).parse_source_file(
            source_path=source,
            provider="openai_codex",
            model="gpt-test",
            system_prompt="Build a directory.",
            user_prompt="Inspect the source.",
            schema=_Catalog,
            output_artifact_path="scratch/catalog.json",
            inspection_scope="directory_only",
        )

    assert len(calls) == 3


def test_pi_source_client_accepts_an_atomically_written_artifact_at_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("One\n", encoding="utf-8")

    def run(command, **kwargs):
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": ["One"]}),
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(command, timeout=60, output=b"")

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse_source_file(
        source_path=source,
        provider="openai_codex",
        model="gpt-test",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="directory_only",
    )

    assert response.output_parsed.nodes == ["One"]


def test_pi_source_client_retries_a_transient_provider_disconnect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("One\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def run(command, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                command,
                1,
                _error_stdout("WebSocket error"),
                "",
            )
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": ["One"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse_source_file(
        source_path=source,
        provider="openai_codex",
        model="gpt-test",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="directory_only",
    )

    assert len(calls) == 2
    assert "Resume the existing checkpoint" in str(calls[1]["input"])
    assert response.output_parsed.nodes == ["One"]


def _error_stdout(message: str) -> str:
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "errorMessage": message,
            },
        }
    )

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from app.services import pi_agent_runtime, pi_source_runtime
from app.services.pi_source_runtime import (
    PI_SOURCE_PLATFORM_PROVIDER,
    PI_SOURCE_PLATFORM_PROXY_KEY_ENV,
    PI_SOURCE_TOOLS,
    PiSourceTextClient,
)
from app.services.source_codex_catalog import AgentCatalogV2


class _Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    complete: bool
    nodes: list[str]


@pytest.fixture(autouse=True)
def _allow_fake_pi_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        pi_source_runtime,
        "ensure_pi_openai_codex_auth",
        lambda **_kwargs: True,
    )


def test_pi_source_runtime_defaults_beside_the_configured_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    persistent_dir = tmp_path / "persistent"
    monkeypatch.delenv("OPENCLASS_PI_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv(
        "OPENCLASS_DATABASE_PATH",
        str(persistent_dir / "openclass.sqlite3"),
    )

    expected_runtime_root = persistent_dir / "pi-runtime"

    assert pi_agent_runtime.pi_runtime_root() == expected_runtime_root
    assert PiSourceTextClient(
        "user_test",
        binary="/test/pi",
    ).runtime_root == expected_runtime_root


def test_startup_cleanup_removes_orphan_source_workspaces(tmp_path: Path) -> None:
    workspace = tmp_path / "source-workspaces" / "source-turn-orphan"
    workspace.mkdir(parents=True)
    (workspace / "source-task-manifest.json").write_text(
        json.dumps({"source_id": "deleted", "run_id": "old", "pid": None}),
        encoding="utf-8",
    )

    removed = pi_source_runtime.cleanup_orphan_source_workspaces(runtime_root=tmp_path)

    assert removed == 1
    assert not workspace.exists()


def test_catalog_v3_command_cannot_request_arbitrary_pdf_search(tmp_path: Path) -> None:
    client = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
    )

    command = client._command(
        provider="openai_codex",
        model="gpt-test",
        reasoning_effort=None,
        system_prompt="bounded directory task",
        inspection_scope="catalog_v3",
    )

    allowed_tools = command[command.index("--tools") + 1].split(",")
    assert "pdf_search" not in allowed_tools
    assert "pdf_toc_candidates" in allowed_tools
    assert "epub_navigation" in allowed_tools
    assert "source_range_preview" in allowed_tools
    assert "pdf_p_calculate" in allowed_tools


def test_pi_runner_returns_immediately_after_atomic_snapshot_receipt(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    script = """
import hashlib, json, pathlib, sys, time
scratch = pathlib.Path(sys.argv[1])
artifact = json.dumps({"schema_version": "agent_catalog_v3", "nodes": []}).encode()
(scratch / "catalog.json").write_bytes(artifact)
receipt = {
    "artifact_path": "scratch/catalog.json",
    "sha256": hashlib.sha256(artifact).hexdigest(),
    "byte_count": len(artifact),
}
(scratch / "catalog-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
time.sleep(10)
"""
    started = time.monotonic()

    result = pi_source_runtime._run_pi_until_snapshot(
        [sys.executable, "-c", script, str(scratch)],
        input_text="",
        cwd=tmp_path,
        env={},
        timeout_seconds=5,
        scratch_path=scratch,
    )

    assert result.returncode == 0
    assert time.monotonic() - started < 2


def test_catalog_activity_monitor_reports_live_tool_and_checkpoint_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pi_source_runtime, "PI_SOURCE_PROGRESS_POLL_SECONDS", 0.01)
    monkeypatch.setattr(pi_source_runtime, "PI_SOURCE_PROGRESS_HEARTBEAT_SECONDS", 0.02)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "catalog-header.json").write_text(
        json.dumps({"schema_version": "agent_catalog_v2", "revision": 0}),
        encoding="utf-8",
    )
    (scratch / "catalog-nodes.json").write_text("[]", encoding="utf-8")
    events = []
    monitor = pi_source_runtime._SourceCatalogActivityMonitor(
        turn_id="turn_test",
        scratch_path=scratch,
        provider="openai_codex",
        model="gpt-test",
        on_activity=events.append,
    )

    monitor.start()
    (scratch / "catalog-header.json").write_text(
        json.dumps(
            {
                "schema_version": "agent_catalog_v2",
                "revision": 1,
                "phase": "range_mapping",
                "directory_page_ranges": [{"start": 7, "end": 11}],
                "tool_activity": [
                    {"tool": "pdf_text", "first_page": 7, "last_page": 11},
                ],
            }
        ),
        encoding="utf-8",
    )
    (scratch / "catalog-nodes.json").write_text(
        json.dumps(
            [
                {
                    "key": "chapter.1",
                    "number": "1",
                    "title": "First",
                    "mapping_status": "verified",
                    "source_range": {"kind": "pdf_pages", "start": 20, "end": 30},
                },
                {"key": "chapter.2", "number": "2", "title": "Second"},
            ]
        ),
        encoding="utf-8",
    )
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not any(
        event.metadata.get("tool") == "pdf_text" for event in events
    ):
        time.sleep(0.01)
    monitor.stop()

    tool_event = next(event for event in events if event.metadata.get("tool") == "pdf_text")
    assert tool_event.status == "completed"
    assert tool_event.metadata["command"] == "pdftotext -f 7 -l 11 source.pdf -"
    progress_events = [
        event for event in events if event.metadata.get("kind") == "sourceCatalogProgress"
    ]
    assert progress_events
    live_progress = progress_events[-1].metadata["source_progress"]
    assert live_progress["determinate"] is True
    assert live_progress["completed_tool_actions"] == 1
    assert live_progress["catalog_node_count"] == 2
    assert live_progress["verified_range_count"] == 1
    assert live_progress["last_node"] == "2 Second"
    assert live_progress["directory_page_ranges"] == [{"start": 7, "end": 11}]
    assert live_progress["heartbeat_at"]
    assert live_progress["snapshot_revision"] == 1


def test_pi_source_client_requires_connected_openai_codex_account(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(
        pi_source_runtime,
        "ensure_pi_openai_codex_auth",
        lambda **_kwargs: False,
    )
    source = tmp_path / "source.txt"
    source.write_text("Contents\nOne\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="has not connected a ChatGPT account"):
        PiSourceTextClient(
            "user_test",
            binary="/test/pi",
            runtime_root=tmp_path / "runtime",
        ).parse_source_file(
            source_path=source,
            provider="openai_codex",
            model="gpt-test",
            system_prompt="Build a directory.",
            user_prompt="Inspect the source.",
            schema=_Catalog,
            access_method="chatgpt_subscription",
            output_artifact_path="scratch/catalog.json",
            inspection_scope="directory_only",
        )


def test_pi_source_client_uses_ephemeral_platform_proxy_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(
        pi_source_runtime,
        "ensure_pi_openai_codex_auth",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("platform credits must not read ChatGPT credentials")
        ),
    )
    monkeypatch.setattr(
        pi_source_runtime,
        "codex_text_proxy_user_allowed",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        pi_source_runtime,
        "codex_text_proxy_config",
        lambda: type(
            "_ProxyConfig",
            (),
            {
                "configured": True,
                "api_key": "platform-secret",
                "base_url": "https://proxy.example/v1",
            },
        )(),
    )
    source = tmp_path / "source.txt"
    source.write_text("Contents\nOne\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        agent_dir = Path(kwargs["env"]["PI_CODING_AGENT_DIR"])
        observed["agent_dir"] = agent_dir
        observed["models"] = json.loads(
            (agent_dir / "models.json").read_text(encoding="utf-8")
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
        access_method="platform_credits",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="directory_only",
    )

    command = observed["command"]
    environment = observed["environment"]
    models = observed["models"]
    assert isinstance(command, list)
    assert isinstance(environment, dict)
    assert isinstance(models, dict)
    assert command[:5] == [
        "/test/pi",
        "--provider",
        PI_SOURCE_PLATFORM_PROVIDER,
        "--model",
        "gpt-test",
    ]
    assert environment[PI_SOURCE_PLATFORM_PROXY_KEY_ENV] == "platform-secret"
    provider_config = models["providers"][PI_SOURCE_PLATFORM_PROVIDER]
    assert provider_config["baseUrl"] == "https://proxy.example/v1"
    assert provider_config["api"] == "openai-responses"
    assert provider_config["apiKey"] == f"${PI_SOURCE_PLATFORM_PROXY_KEY_ENV}"
    assert provider_config["models"][0]["contextWindow"] == 128_000
    assert provider_config["models"][0]["maxTokens"] == 32_000
    assert "platform-secret" not in json.dumps(models)
    assert not Path(observed["agent_dir"]).exists()
    assert response.output_parsed.nodes == ["One"]


def test_pi_source_catalog_v2_keeps_advisory_prompt_with_platform_credits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_source_runtime, "codex_text_proxy_user_allowed", lambda _user_id: True)
    monkeypatch.setattr(
        pi_source_runtime,
        "codex_text_proxy_config",
        lambda: type(
            "_ProxyConfig",
            (),
            {
                "configured": True,
                "api_key": "platform-secret",
                "base_url": "https://proxy.example/v1",
            },
        )(),
    )
    source = tmp_path / "source.txt"
    source.write_text("Contents\nOne\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed["command"] = command
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": "agent_catalog_v2",
                    "work_state": "working",
                    "summary": "First usable snapshot.",
                    "next_plan": "Inspect authored navigation.",
                    "stop_reason": "",
                    "nodes": [],
                }
            ),
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
        access_method="platform_credits",
        system_prompt="Recover the finest genuine directory.",
        user_prompt="Continue autonomously.",
        schema=AgentCatalogV2,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="catalog_v2",
    )

    command = observed["command"]
    assert isinstance(command, list)
    system_prompt = command[command.index("--system-prompt") + 1]
    assert "Revise the workspace with catalog_apply" in system_prompt
    assert "Publishing is incremental and never attests completeness" in system_prompt
    assert "Save nodes progressively with catalog_append" not in system_prompt
    assert response.output_parsed.work_state == "working"


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


def test_pi_source_client_supports_isolated_repository_archive_inspection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "repository.zip"
    source.write_bytes(b"frozen repository archive")
    calls: list[tuple[list[str], dict[str, object]]] = []
    readable_paths: list[str] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        readable_path = Path(kwargs["cwd"]) / kwargs["env"]["OPENCLASS_PI_REPOSITORY_READABLE_PATHS"]
        readable_paths.extend(json.loads(readable_path.read_text(encoding="utf-8")))
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": ["Architecture"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=runner,
    ).parse_source_file(
        source_path=source,
        provider="openai_codex",
        model="gpt-test",
        system_prompt="Build a repository learning structure with verified evidence.",
        user_prompt="Inspect the frozen repository linked by the user.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="repository",
        archive_prefix="owner-repo-commit",
        repository_readable_paths=["README.md", "src/main.py"],
    )

    command, kwargs = calls[0]
    system_prompt = command[command.index("--system-prompt") + 1]
    assert kwargs["env"]["OPENCLASS_PI_SOURCE_INSPECTION_SCOPE"] == "repository"
    assert kwargs["env"]["OPENCLASS_PI_SOURCE_ARCHIVE_PREFIX"] == "owner-repo-commit"
    assert readable_paths == [
        "README.md",
        "src/main.py",
    ]
    assert "repository archive" in system_prompt
    assert "learning structure" in system_prompt
    assert response.activity[0].metadata["source_tool_policy"] == (
        "openclass_read_only_repository_tools"
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


def test_pi_source_client_retries_an_exit_143_without_losing_the_checkpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.md"
    source.write_text("# Architecture\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        scratch = Path(kwargs["cwd"]) / "scratch"
        if len(calls) == 1:
            (scratch / "catalog-header.json").write_text("null", encoding="utf-8")
            (scratch / "catalog-nodes.json").write_text("[]", encoding="utf-8")
            return subprocess.CompletedProcess(command, 143, "", "")
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": ["Architecture"]}),
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
        provider="deepseek",
        model="deepseek-test",
        system_prompt="Build a learning structure.",
        user_prompt="Inspect the repository.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="repository",
        archive_prefix="owner-repo-commit",
        repository_readable_paths=["README.md"],
    )

    assert len(calls) == 2
    assert "previous provider attempt ended before submission" in str(
        calls[1][1]["input"]
    )
    assert "resume the existing checkpoint" in str(calls[1][1]["input"])
    assert response.output_parsed.nodes == ["Architecture"]
    assert response.source_turn_count == 2


def test_pi_source_client_resumes_after_incomplete_source_catalog_rejection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    from pypdf import PdfWriter

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    with source.open("wb") as stream:
        writer.write(stream)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        scratch = Path(kwargs["cwd"]) / "scratch"
        if len(calls) == 1:
            (scratch / "catalog-header.json").write_text("null", encoding="utf-8")
            (scratch / "catalog-nodes.json").write_text(
                json.dumps(["First"]),
                encoding="utf-8",
            )
            nodes = ["First"]
        else:
            assert (scratch / "catalog-header.json").exists()
            assert (scratch / "catalog-nodes.json").exists()
            nodes = ["First", "Second"]
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": nodes}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    def validate(payload: object) -> None:
        if isinstance(payload, dict) and len(payload.get("nodes", [])) < 2:
            raise RuntimeError(
                "The catalog is incomplete: it contains 1 nodes, but authored navigation "
                "exposes at least 2 authored navigation entries."
            )

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse_source_file(
        source_path=source,
        provider="deepseek",
        model="deepseek-test",
        reasoning_effort="low",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="source",
        artifact_validator=validate,
    )

    assert len(calls) == 2
    assert "valid partial checkpoint" in str(calls[1][1]["input"])
    assert "resume the existing checkpoint" in str(calls[1][1]["input"])
    assert "at most 20" in str(calls[1][1]["input"])
    system_prompt = calls[0][0][calls[0][0].index("--system-prompt") + 1]
    assert "pdf_navigation with start_index equal" in system_prompt
    assert "limit 20" in system_prompt
    assert "open_ancestor_chain" in system_prompt
    assert calls[1][0][calls[1][0].index("--thinking") + 1] == "low"
    assert response.output_parsed.nodes == ["First", "Second"]


def test_pi_source_client_rolls_back_only_the_latest_invalid_source_batch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("First\nSecond\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        scratch = Path(kwargs["cwd"]) / "scratch"
        if len(calls) == 1:
            nodes = ["First"]
        elif len(calls) == 2:
            assert json.loads(
                (scratch / "catalog-nodes.json").read_text(encoding="utf-8")
            ) == ["First"]
            nodes = ["First", " Second "]
        else:
            assert json.loads(
                (scratch / "catalog-nodes.json").read_text(encoding="utf-8")
            ) == ["First"]
            nodes = ["First", "Second"]
        (scratch / "catalog-header.json").write_text("null", encoding="utf-8")
        (scratch / "catalog-nodes.json").write_text(
            json.dumps(nodes),
            encoding="utf-8",
        )
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": nodes}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    def validate(payload: object) -> None:
        nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        if any(isinstance(node, str) and node != node.strip() for node in nodes):
            raise RuntimeError("A directory source locator contains leading or trailing whitespace.")
        if len(nodes) < 2:
            raise RuntimeError(
                "The catalog is incomplete: it contains 1 nodes, but authored navigation "
                "exposes at least 2 authored navigation entries."
            )

    response = PiSourceTextClient(
        "user_test",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse_source_file(
        source_path=source,
        provider="deepseek",
        model="deepseek-test",
        system_prompt="Build a directory.",
        user_prompt="Inspect the source.",
        schema=_Catalog,
        output_artifact_path="scratch/catalog.json",
        inspection_scope="source",
        artifact_validator=validate,
    )

    assert len(calls) == 3
    assert "latest appended batch was rolled back" in str(calls[2][1]["input"])
    assert response.output_parsed.nodes == ["First", "Second"]


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


def test_pi_source_client_keeps_a_partial_source_checkpoint_after_three_timeouts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    source = tmp_path / "source.txt"
    source.write_text("One\nTwo\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def run(command, **kwargs):
        calls.append(kwargs)
        scratch = Path(kwargs["cwd"]) / "scratch"
        (scratch / "catalog-header.json").write_text("null", encoding="utf-8")
        (scratch / "catalog-nodes.json").write_text(
            json.dumps(["One"]),
            encoding="utf-8",
        )
        if len(calls) <= 3:
            raise subprocess.TimeoutExpired(command, timeout=60, output=b"")
        (scratch / "catalog.json").write_text(
            json.dumps({"complete": True, "nodes": ["One", "Two"]}),
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
        inspection_scope="source",
    )

    assert len(calls) == 4
    assert "valid partial checkpoint" in str(calls[3]["input"])
    assert response.output_parsed.nodes == ["One", "Two"]


@pytest.mark.parametrize("provider_error", ["WebSocket error", "stream_read_error"])
def test_pi_source_client_retries_a_transient_provider_disconnect(
    monkeypatch,
    tmp_path: Path,
    provider_error: str,
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
                _error_stdout(provider_error),
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

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from app.models import AgentActivityEvent, new_id, now_iso
from app.services.ai_logging import ai_usage_logger
from app.services.config import DATA_DIR, load_root_dotenv
from app.services.codex_app_server import CodexTurnCancelledError
from app.services.structured_output import (
    json_object,
    validation_issues,
    validation_repair_prompt,
)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
PiProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
PI_REQUEST_TIMEOUT_SECONDS = 10 * 60
PI_TRANSIENT_RETRY_ATTEMPTS = 1
PI_TRANSIENT_ERROR_MARKERS = (
    "websocket error",
    "connection reset",
    "connection closed",
    "connection lost",
    "network error",
)
PI_OPENAI_CODEX_SERVICE_TIERS = {"priority"}
PI_ACTIVITY_EMIT_CHARACTER_INTERVAL = 120
PI_MAX_IMAGE_INPUTS = 8
PI_MAX_IMAGE_INPUT_BYTES = 20 * 1024 * 1024
PI_MAX_TOTAL_IMAGE_INPUT_BYTES = 40 * 1024 * 1024
PI_PROCESS_POLL_SECONDS = 0.1
PI_PROCESS_TERMINATE_GRACE_SECONDS = 1.0


_DATA_IMAGE_PATTERN = re.compile(
    r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PiStructuredResponse:
    output_parsed: BaseModel
    activity: list[AgentActivityEvent]


@dataclass(frozen=True)
class PiTextResponse:
    output_text: str
    activity: list[AgentActivityEvent]


class _PiActivityRecorder:
    """Convert Pi's JSON event stream into public, durable activity updates.

    Pi can emit private reasoning text token by token. OpenClass deliberately
    exposes truthful lifecycle and volume updates without persisting that raw
    private reasoning.
    """

    def __init__(
        self,
        *,
        turn_id: str,
        request_id: str,
        provider: str,
        model: str,
        callback: Callable[[AgentActivityEvent], None] | None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> None:
        self.turn_id = turn_id
        self.request_id = request_id
        self.provider = provider
        self.model = model
        self.callback = callback
        self.on_text_delta = on_text_delta
        self._events: dict[str, AgentActivityEvent] = {}
        self._order: list[str] = []
        self._character_counts: dict[str, int] = {}
        self._last_emitted_counts: dict[str, int] = {}
        self.started_at = time.monotonic()
        self.first_event_at: float | None = None
        self.first_text_at: float | None = None

    @property
    def events(self) -> list[AgentActivityEvent]:
        return [self._events[event_id] for event_id in self._order]

    def _event_id(self, kind: str, content_index: object = 0) -> str:
        return f"{self.request_id}:{kind}:{content_index}"

    def _publish(
        self,
        *,
        event_id: str,
        label: str,
        status: str,
        kind: str,
        detail: str,
        force: bool = True,
    ) -> None:
        existing = self._events.get(event_id)
        event = AgentActivityEvent(
            id=event_id,
            turn_id=self.turn_id,
            stage="build_context" if kind == "reasoning" else "execute_role",
            label=label,
            status=status,
            role="OpenClass",
            metadata={
                "kind": kind,
                "detail": detail,
                "agent_backend": "pi",
                "provider": self.provider,
                "model": self.model,
            },
            created_at=existing.created_at if existing is not None else now_iso(),
        )
        if existing is None:
            self._order.append(event_id)
        self._events[event_id] = event
        if self.callback is None:
            return
        character_count = self._character_counts.get(event_id, 0)
        last_emitted = self._last_emitted_counts.get(event_id, 0)
        if not force and character_count - last_emitted < PI_ACTIVITY_EMIT_CHARACTER_INTERVAL:
            return
        self._last_emitted_counts[event_id] = character_count
        try:
            self.callback(event)
        except Exception:
            logger.exception("Failed to publish live Pi activity")

    def observe_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if self.first_event_at is None:
            self.first_event_at = time.monotonic()
        event_type = str(payload.get("type") or "")
        runtime_id = self._event_id("runtime")
        if event_type == "agent_start":
            self._publish(
                event_id=runtime_id,
                label="OpenClass 已连接模型",
                status="running",
                kind="model_runtime",
                detail=f"正在使用 {self.provider} / {self.model} 处理当前步骤。",
            )
            return
        if event_type == "agent_end":
            self._publish(
                event_id=runtime_id,
                label="OpenClass 已完成模型运行",
                status="completed",
                kind="model_runtime",
                detail=f"{self.provider} / {self.model} 已返回本步骤结果。",
            )
            return
        if event_type != "message_update":
            return
        update = payload.get("assistantMessageEvent")
        if not isinstance(update, dict):
            return
        update_type = str(update.get("type") or "")
        content_index = update.get("contentIndex", 0)
        if update_type.startswith("thinking_"):
            self._observe_content_update(
                update=update,
                update_type=update_type,
                event_id=self._event_id("reasoning", content_index),
                kind="reasoning",
                running_label="OpenClass 正在推理",
                completed_label="OpenClass 已完成推理",
                noun="推理",
            )
        elif update_type.startswith("text_"):
            self._observe_content_update(
                update=update,
                update_type=update_type,
                event_id=self._event_id("output", content_index),
                kind="model_output",
                running_label="OpenClass 正在生成结果",
                completed_label="OpenClass 已生成模型结果",
                noun="结果",
            )

    def _observe_content_update(
        self,
        *,
        update: dict[str, Any],
        update_type: str,
        event_id: str,
        kind: str,
        running_label: str,
        completed_label: str,
        noun: str,
    ) -> None:
        if update_type.endswith("_delta"):
            delta = update.get("delta")
            if isinstance(delta, str):
                self._character_counts[event_id] = (
                    self._character_counts.get(event_id, 0) + len(delta)
                )
                if kind == "model_output" and delta:
                    if self.first_text_at is None:
                        self.first_text_at = time.monotonic()
                    if self.on_text_delta is not None:
                        self.on_text_delta(delta)
        character_count = self._character_counts.get(event_id, 0)
        if update_type.endswith("_end"):
            detail = f"模型{noun}阶段已完成"
            if character_count:
                detail += f"，共接收 {character_count} 个字符"
            self._publish(
                event_id=event_id,
                label=completed_label,
                status="completed",
                kind=kind,
                detail=f"{detail}。",
            )
            return
        privacy_note = (
            "；逐字私有思维不写入聊天记录"
            if kind == "reasoning"
            else ""
        )
        detail = f"模型正在生成{noun}"
        if character_count:
            detail += f"，已接收 {character_count} 个字符"
        self._publish(
            event_id=event_id,
            label=running_label,
            status="running",
            kind=kind,
            detail=f"{detail}{privacy_note}。",
            force=not update_type.endswith("_delta"),
        )

    def fail(self, detail: str) -> None:
        self._publish(
            event_id=self._event_id("runtime"),
            label="OpenClass 模型运行未完成",
            status="failed",
            kind="model_runtime",
            detail=detail,
        )

    def timing_payload(self) -> dict[str, int | None]:
        completed_at = time.monotonic()
        return {
            "first_event_ms": (
                round((self.first_event_at - self.started_at) * 1000)
                if self.first_event_at is not None
                else None
            ),
            "first_text_delta_ms": (
                round((self.first_text_at - self.started_at) * 1000)
                if self.first_text_at is not None
                else None
            ),
            "completed_ms": round((completed_at - self.started_at) * 1000),
        }


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=PI_PROCESS_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _valid_image_signature(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if mime_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    return False


def _stage_image_inputs(image_inputs: list[str], *, workspace: Path) -> list[Path]:
    if len(image_inputs) > PI_MAX_IMAGE_INPUTS:
        raise RuntimeError(f"Pi accepts at most {PI_MAX_IMAGE_INPUTS} image inputs per request")
    staged: list[Path] = []
    total_bytes = 0
    for index, image_input in enumerate(image_inputs, start=1):
        match = _DATA_IMAGE_PATTERN.fullmatch(image_input.strip())
        if match is None:
            raise RuntimeError("Pi image inputs must be bounded base64 data URLs")
        mime_type = match.group(1).lower()
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("Pi image input contains invalid base64 data") from exc
        if not content or len(content) > PI_MAX_IMAGE_INPUT_BYTES:
            raise RuntimeError("Pi image input exceeds the configured size limit")
        total_bytes += len(content)
        if total_bytes > PI_MAX_TOTAL_IMAGE_INPUT_BYTES:
            raise RuntimeError("Pi image inputs exceed the configured total size limit")
        if not _valid_image_signature(mime_type, content):
            raise RuntimeError("Pi image input does not match its declared MIME type")
        image_path = workspace / f"input-{index}{_IMAGE_SUFFIXES[mime_type]}"
        image_path.write_bytes(content)
        image_path.chmod(0o600)
        staged.append(image_path)
    return staged


def _run_streaming_pi_process(
    command: list[str],
    *,
    input_text: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    recorder: _PiActivityRecorder,
    is_cancelled: Callable[[], bool] | None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            recorder.observe_line(line)

    def read_stderr() -> None:
        assert process.stderr is not None
        stderr_lines.extend(process.stderr)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    assert process.stdin is not None
    try:
        process.stdin.write(input_text)
        process.stdin.close()
    except BrokenPipeError:
        pass
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if is_cancelled is not None and is_cancelled():
            _terminate_process(process)
            stdout_thread.join()
            stderr_thread.join()
            recorder.fail("用户已停止当前模型请求。")
            raise CodexTurnCancelledError("Pi turn was cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            stdout_thread.join()
            stderr_thread.join()
            recorder.fail(f"模型请求在 {timeout} 秒后超时。")
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output="".join(stdout_lines),
                stderr="".join(stderr_lines),
            )
        try:
            process.wait(timeout=min(PI_PROCESS_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            continue
    returncode = int(process.returncode or 0)
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        command,
        returncode,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )


def pi_runtime_available() -> bool:
    return shutil.which("pi") is not None


def pi_credentials_available(
    *, owner_user_id: str, runtime_root: Path | None = None
) -> bool:
    """Report whether the selected Pi account directory has usable auth state."""
    configured_agent_dir = (os.getenv("OPENCLASS_PI_AGENT_DIR") or "").strip()
    if configured_agent_dir:
        agent_dir = Path(configured_agent_dir).expanduser().resolve()
    else:
        root = runtime_root or DATA_DIR / "pi-runtime"
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]
        agent_dir = root / "agents" / owner_key
    auth_path = agent_dir / "auth.json"
    try:
        return auth_path.is_file() and auth_path.stat().st_size > 2
    except OSError:
        return False


def pi_agent_directory(*, owner_user_id: str, runtime_root: Path) -> Path:
    owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]
    configured_agent_dir = (os.getenv("OPENCLASS_PI_AGENT_DIR") or "").strip()
    if configured_agent_dir:
        agent_dir = Path(configured_agent_dir).expanduser().resolve()
        if not agent_dir.is_dir():
            raise RuntimeError("The configured Pi agent directory does not exist")
        return agent_dir
    agent_dir = runtime_root / "agents" / owner_key
    agent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return agent_dir


def _pi_provider(provider: str) -> str:
    return "openai-codex" if provider == "openai_codex" else provider.replace("_", "-")


def _runtime_settings_extension_path() -> Path:
    return Path(__file__).with_name("pi_runtime_settings_extension.ts").resolve()


def _validated_service_tier(provider: str, service_tier: str | None) -> str | None:
    normalized = str(service_tier or "").strip()
    if not normalized:
        return None
    if (
        _pi_provider(provider) != "openai-codex"
        or normalized not in PI_OPENAI_CODEX_SERVICE_TIERS
    ):
        raise RuntimeError("The selected Pi model does not support this service tier")
    return normalized


def _pi_request_timeout_seconds() -> int:
    raw = (os.getenv("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return PI_REQUEST_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise RuntimeError("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS must be an integer") from exc
    if not 30 <= timeout <= 30 * 60:
        raise RuntimeError(
            "OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS must be between 30 and 1800 seconds"
        )
    return timeout


def _assistant_text(stdout: str) -> str:
    final_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        error_message = message.get("errorMessage")
        if isinstance(error_message, str) and error_message.strip():
            raise RuntimeError(f"Pi model request failed: {error_message.strip()}")
        content = message.get("content")
        if isinstance(content, str):
            final_text = content.strip()
            continue
        if not isinstance(content, list):
            continue
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if text_parts:
            final_text = "".join(text_parts).strip()
    if not final_text:
        raise RuntimeError("Pi completed without an assistant response")
    return final_text


def _is_transient_pi_error(error: RuntimeError) -> bool:
    message = str(error).casefold()
    return any(marker in message for marker in PI_TRANSIENT_ERROR_MARKERS)


class PiTextClient:
    """Tool-free Pi runtime used behind OpenClass workflow validation."""

    def __init__(
        self,
        *,
        owner_user_id: str,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        binary: str | None = None,
        runtime_root: Path | None = None,
        process_runner: PiProcessRunner | None = None,
    ) -> None:
        resolved_binary = binary or shutil.which("pi")
        if not resolved_binary:
            raise RuntimeError("Pi is not installed on this server")
        self.owner_user_id = owner_user_id
        self.provider = provider
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = _validated_service_tier(provider, service_tier)
        self.binary = resolved_binary
        self.runtime_root = runtime_root or DATA_DIR / "pi-runtime"
        self._process_runner = process_runner

    def _command(self, *, system_prompt: str, image_paths: list[Path] | None = None) -> list[str]:
        command = [
            self.binary,
            "--provider",
            _pi_provider(self.provider),
            "--model",
            self.model,
            "--mode",
            "json",
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--system-prompt",
            system_prompt,
        ]
        if self.reasoning_effort:
            command.extend(["--thinking", self.reasoning_effort])
        if self.service_tier:
            command.extend(["--extension", str(_runtime_settings_extension_path())])
        command.extend(f"@{path.name}" for path in image_paths or [])
        return command

    def _run_once(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        turn_id: str,
        request_id: str,
        on_activity: Callable[[AgentActivityEvent], None] | None,
        on_text_delta: Callable[[str], None] | None,
        image_inputs: list[str] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> str:
        load_root_dotenv()
        agent_dir = pi_agent_directory(
            owner_user_id=self.owner_user_id,
            runtime_root=self.runtime_root,
        )
        workspace_root = self.runtime_root / "workspaces"
        workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = os.environ.copy()
        environment.update(
            {
                "PI_CODING_AGENT_DIR": str(agent_dir),
                "PI_OFFLINE": "1",
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_TELEMETRY": "0",
            }
        )
        if self.service_tier:
            environment["OPENCLASS_PI_SERVICE_TIER"] = self.service_tier
        with tempfile.TemporaryDirectory(prefix="turn-", dir=workspace_root) as temporary:
            temporary_path = Path(temporary)
            image_paths = _stage_image_inputs(image_inputs or [], workspace=temporary_path)
            timeout_seconds = _pi_request_timeout_seconds()
            recorder = _PiActivityRecorder(
                turn_id=turn_id,
                request_id=request_id,
                provider=self.provider,
                model=self.model,
                callback=on_activity,
                on_text_delta=on_text_delta,
            )
            try:
                command = self._command(system_prompt=system_prompt, image_paths=image_paths)
                if self._process_runner is None:
                    result = _run_streaming_pi_process(
                        command,
                        input_text=user_prompt,
                        cwd=temporary_path,
                        env=environment,
                        timeout=timeout_seconds,
                        recorder=recorder,
                        is_cancelled=is_cancelled,
                    )
                else:
                    result = self._process_runner(
                        command,
                        input=user_prompt,
                        text=True,
                        capture_output=True,
                        cwd=temporary_path,
                        env=environment,
                        timeout=timeout_seconds,
                        check=False,
                    )
                    for line in result.stdout.splitlines():
                        recorder.observe_line(line)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Pi model request timed out after {timeout_seconds} seconds"
                ) from exc
            finally:
                ai_usage_logger.log_event(
                    "pi_request_timing",
                    provider=self.provider,
                    model=self.model,
                    request_id=request_id,
                    **recorder.timing_payload(),
                )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[-600:]
            recorder.fail("模型进程返回失败状态。")
            raise RuntimeError(
                "Pi model request failed"
                + (f": {detail}" if detail else f" with exit code {result.returncode}")
            )
        try:
            return _assistant_text(result.stdout)
        except RuntimeError:
            recorder.fail("模型没有返回可用的助手结果。")
            raise

    def _run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        turn_id: str,
        request_kind: str,
        on_activity: Callable[[AgentActivityEvent], None] | None,
        on_text_delta: Callable[[str], None] | None = None,
        image_inputs: list[str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        for attempt in range(PI_TRANSIENT_RETRY_ATTEMPTS + 1):
            attempt_emitted_text = False

            def publish_text_delta(delta: str) -> None:
                nonlocal attempt_emitted_text
                attempt_emitted_text = True
                if on_text_delta is not None:
                    on_text_delta(delta)

            try:
                return self._run_once(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    turn_id=turn_id,
                    request_id=f"{turn_id}:{request_kind}:{attempt + 1}",
                    on_activity=on_activity,
                    on_text_delta=publish_text_delta if on_text_delta is not None else None,
                    image_inputs=image_inputs,
                    is_cancelled=is_cancelled,
                )
            except RuntimeError as error:
                if (
                    attempt_emitted_text
                    or attempt >= PI_TRANSIENT_RETRY_ATTEMPTS
                    or not _is_transient_pi_error(error)
                ):
                    raise
                if on_activity is not None:
                    on_activity(
                        AgentActivityEvent(
                            turn_id=turn_id,
                            stage="execute_role",
                            label="模型连接中断，正在重试",
                            status="running",
                            role="OpenClass",
                            metadata={
                                "kind": "model_retry",
                                "detail": f"正在进行第 {attempt + 2} 次模型请求。",
                                "agent_backend": "pi",
                                "provider": self.provider,
                                "model": self.model,
                            },
                        )
                    )
                ai_usage_logger.log_event(
                    "pi_transient_request_retry",
                    provider=self.provider,
                    model=self.model,
                    attempt=attempt + 1,
                    error=str(error),
                )
        raise RuntimeError("Pi model request failed after a transient retry")

    def parse(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
        on_activity: Callable[[AgentActivityEvent], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PiStructuredResponse:
        turn_id = new_id("piturn")
        activity_by_id: dict[str, AgentActivityEvent] = {}
        activity_order: list[str] = []

        def record_activity(event: AgentActivityEvent) -> None:
            if event.id not in activity_by_id:
                activity_order.append(event.id)
            activity_by_id[event.id] = event
            if on_activity is not None:
                on_activity(event)

        def current_activity() -> list[AgentActivityEvent]:
            return [activity_by_id[event_id] for event_id in activity_order]

        schema_text = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        structured_system_prompt = (
            f"{system_prompt}\n\n"
            "Return one valid JSON object matching this JSON schema. "
            "Do not wrap it in Markdown and do not add prose outside the JSON.\n"
            f"JSON schema: {schema_text}"
        )
        output_text = self._run(
            system_prompt=structured_system_prompt,
            user_prompt=user_prompt,
            turn_id=turn_id,
            request_kind="primary",
            on_activity=record_activity,
            image_inputs=image_inputs,
            is_cancelled=is_cancelled,
        )
        validation_event = AgentActivityEvent(
            turn_id=turn_id,
            stage="verify",
            label="OpenClass 正在校验模型结果",
            status="running",
            role="OpenClass",
            metadata={
                "kind": "structured_validation",
                "detail": f"正在按照 {schema.__name__} 的结构要求检查结果。",
                "agent_backend": "pi",
                "provider": self.provider,
                "model": self.model,
            },
        )
        record_activity(validation_event)
        try:
            parsed = schema.model_validate(json_object(output_text))
        except Exception as first_error:
            record_activity(
                validation_event.model_copy(
                    update={
                        "label": "模型结果需要结构修复",
                        "status": "blocked",
                        "metadata": {
                            **validation_event.metadata,
                            "detail": "首次结果未满足结构要求，正在请求模型修复。",
                        },
                    }
                )
            )
            initial_issues = validation_issues(first_error)
            repaired_text = self._run(
                system_prompt=structured_system_prompt,
                user_prompt=(
                    f"{user_prompt}\n\nPrevious response:\n{output_text}\n\n"
                    f"{validation_repair_prompt(first_error)}"
                ),
                turn_id=turn_id,
                request_kind="repair",
                on_activity=record_activity,
                image_inputs=image_inputs,
                is_cancelled=is_cancelled,
            )
            try:
                parsed = schema.model_validate(json_object(repaired_text))
            except Exception as repair_error:
                ai_usage_logger.log_event(
                    "pi_structured_response_failed",
                    provider=self.provider,
                    model=self.model,
                    turn_id=turn_id,
                    initial_validation_issues=initial_issues,
                    repair_validation_issues=validation_issues(repair_error),
                )
                raise RuntimeError("Pi returned an invalid structured response") from repair_error
            output_text = repaired_text
            ai_usage_logger.log_event(
                "pi_structured_response_repaired",
                provider=self.provider,
                model=self.model,
                turn_id=turn_id,
                initial_validation_issues=initial_issues,
            )
        record_activity(
            validation_event.model_copy(
                update={
                    "label": "OpenClass 已校验模型结果",
                    "status": "completed",
                    "metadata": {
                        **validation_event.metadata,
                        "detail": "模型结果已通过结构校验。",
                    },
                }
            )
        )
        ai_usage_logger.log_event(
            "pi_request_completed",
            provider=self.provider,
            model=self.model,
            turn_id=turn_id,
            output_character_count=len(output_text),
        )
        return PiStructuredResponse(
            output_parsed=parsed,
            activity=current_activity(),
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
    ) -> PiTextResponse:
        turn_id = new_id("piturn")
        activity_by_id: dict[str, AgentActivityEvent] = {}
        activity_order: list[str] = []

        def record_activity(event: AgentActivityEvent) -> None:
            if event.id not in activity_by_id:
                activity_order.append(event.id)
            activity_by_id[event.id] = event
            if on_activity is not None:
                on_activity(event)

        output_text = self._run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            turn_id=turn_id,
            request_kind="text",
            on_activity=record_activity,
            on_text_delta=on_text_delta,
            image_inputs=image_inputs,
            is_cancelled=is_cancelled,
        )
        ai_usage_logger.log_event(
            "pi_text_request_completed",
            provider=self.provider,
            model=self.model,
            turn_id=turn_id,
            output_character_count=len(output_text),
        )
        return PiTextResponse(
            output_text=output_text,
            activity=[activity_by_id[event_id] for event_id in activity_order],
        )

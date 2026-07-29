from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from fastapi import WebSocketDisconnect

from app.models import UserView
from app.routers import chat
from app.services import codex_live_sideband, pi_agent_runtime
from app.services.ai_logging import AIUsageLogger, ai_log_context
from app.services.codex_live_sideband import CodexLiveSession
from app.services.codex_live_task_lifecycle import CodexLiveTaskCoordinator
from app.services.pi_agent_runtime import PiTextClient


def _pi_stdout(content: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 0,
                        "delta": "private reasoning must stay private",
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


def test_model_run_history_records_inputs_outputs_and_redacts_secrets(
    monkeypatch,
    tmp_path,
) -> None:
    logger = AIUsageLogger(tmp_path / "ai-usage.jsonl")
    monkeypatch.setattr(pi_agent_runtime, "ai_usage_logger", logger)
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    monkeypatch.setattr(pi_agent_runtime, "ensure_pi_openai_codex_auth", lambda **_kwargs: True)

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, _pi_stdout("完整结果"), "")

    with ai_log_context(lesson_id="lesson_a", authorization="must-not-leak"):
        response = PiTextClient(
            owner_user_id="user_a",
            provider="openai_codex",
            model="gpt-5.5",
            binary="/test/pi",
            runtime_root=tmp_path / "runtime",
            process_runner=run,
        ).complete_text(
            system_prompt="system input",
            user_prompt="user input",
        )

    assert response.output_text == "完整结果"
    history = logger.read_lesson_events(lesson_id="lesson_a")
    model_events = [
        item["payload"]
        for item in history["events"]
        if item["event_type"] == "model_run_event"
    ]
    assert [item["sequence_no"] for item in model_events] == list(
        range(1, len(model_events) + 1)
    )
    assert model_events[0]["input"] == {
        "system_prompt": "system input",
        "user_prompt": "user input",
        "image_count": 0,
    }
    assert any(item["event"] == "output_delta" and item["delta"] == "完整结果" for item in model_events)
    assert model_events[-1]["event"] == "completed"
    assert model_events[-1]["output"] == {"text": "完整结果"}
    serialized = json.dumps(history, ensure_ascii=False)
    assert "must-not-leak" not in serialized
    assert "private reasoning must stay private" not in serialized
    assert "[REDACTED]" in serialized


def test_lesson_history_is_scoped_and_supports_forward_cursor(tmp_path) -> None:
    logger = AIUsageLogger(tmp_path / "ai-usage.jsonl")
    with ai_log_context(lesson_id="lesson_a"):
        first = logger.log_event("first", value=1)
    with ai_log_context(lesson_id="lesson_b"):
        logger.log_event("other", value=2)
    with ai_log_context(lesson_id="lesson_a"):
        second = logger.log_event("second", value=3)

    latest = logger.read_lesson_events(lesson_id="lesson_a", limit=10)
    assert [item["id"] for item in latest["events"]] == [first["id"], second["id"]]
    forward = logger.read_lesson_events(
        lesson_id="lesson_a",
        after_event_id=first["id"],
    )
    assert [item["id"] for item in forward["events"]] == [second["id"]]
    assert forward["cursor_found"] is True


def test_model_run_history_endpoint_checks_lesson_ownership(monkeypatch) -> None:
    user = UserView(
        id="user_a",
        email="a@example.com",
        role="user",
        created_at="2026-07-29T00:00:00+00:00",
    )
    workspace = object()
    monkeypatch.setattr(chat.workspace_state, "load_workspace_for_user", lambda user_id: workspace)
    checked: list[tuple[object, str]] = []
    monkeypatch.setattr(
        chat.workspace_state,
        "find_lesson_package",
        lambda value, lesson_id: checked.append((value, lesson_id)),
    )
    monkeypatch.setattr(
        chat.ai_usage_logger,
        "read_lesson_events",
        lambda **_kwargs: {"events": [], "next_cursor": None, "truncated": False},
    )

    result = chat.model_run_history("lesson_a", limit=20, after=None, user=user)

    assert checked == [(workspace, "lesson_a")]
    assert result["lesson_id"] == "lesson_a"


def test_codex_live_typed_input_is_queued_with_a_correlated_audit_event(
    monkeypatch,
    tmp_path,
) -> None:
    logger = AIUsageLogger(tmp_path / "ai-usage.jsonl")
    monkeypatch.setattr(codex_live_sideband, "ai_usage_logger", logger)
    session = CodexLiveSession(
        call_id="call_a",
        lesson_id="lesson_a",
        user_id="user_a",
        client_session_id="realtime_a",
        transport_session_id="transport_a",
        selection=None,
        created_at=0,
    )

    class Socket:
        calls = 0

        async def receive_json(self):
            self.calls += 1
            if self.calls == 1:
                return {"type": "input_text", "text": "继续这个任务"}
            raise WebSocketDisconnect()

        async def send_json(self, _payload):
            return None

    async def exercise():
        coordinator = CodexLiveTaskCoordinator()
        with pytest.raises(WebSocketDisconnect):
            await codex_live_sideband._handle_client_messages(  # noqa: SLF001
                Socket(),
                object(),
                session,
                asyncio.Lock(),
                asyncio.Lock(),
                coordinator,
            )
        return coordinator.queue.get_nowait()

    queued_task = asyncio.run(exercise())
    assert queued_task.prompt == "继续这个任务"
    assert queued_task.provider_delegation is False
    history = logger.read_lesson_events(lesson_id="lesson_a")
    queued = history["events"][-1]["payload"]
    assert queued["run_id"] == queued_task.delegation_id
    assert queued["parent_run_id"] == "realtime_a"
    assert queued["event"] == "queued"
    assert queued["input"] == {"text": "继续这个任务"}

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect

from app.models import SelectionRef, new_id
from app.services.ai_logging import ai_usage_logger
from app.services.ai_model_catalog import OPENAI_CODEX_REALTIME_MODEL
from app.services.codex_live_task_lifecycle import (
    CodexLiveTask,
    CodexLiveTaskCoordinator,
    TaskAction,
    TaskDecision,
)
from app.services.openai_realtime import (
    _codex_realtime_proxy_api_key,
    _codex_realtime_proxy_url,
)
from app.services.realtime_tool_bridge import execute_realtime_delegation


_CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SESSION_TTL_SECONDS = 60 * 60
_APPEND_MAX_BYTES = 500


@dataclass
class CodexLiveSession:
    call_id: str
    lesson_id: str
    user_id: str
    client_session_id: str
    transport_session_id: str
    selection: SelectionRef | None
    created_at: float
    claimed: bool = False


_sessions: dict[str, CodexLiveSession] = {}
_sessions_lock = threading.Lock()


def register_codex_live_session(
    *,
    call_id: str,
    lesson_id: str,
    user_id: str,
    client_session_id: str,
    transport_session_id: str,
    selection: SelectionRef | None,
) -> None:
    if not _CALL_ID_PATTERN.fullmatch(call_id):
        raise ValueError("Invalid Codex Live call ID")
    now = time.monotonic()
    with _sessions_lock:
        expired = [
            stored_call_id
            for stored_call_id, session in _sessions.items()
            if now - session.created_at > _SESSION_TTL_SECONDS
        ]
        for stored_call_id in expired:
            _sessions.pop(stored_call_id, None)
        _sessions[call_id] = CodexLiveSession(
            call_id=call_id,
            lesson_id=lesson_id,
            user_id=user_id,
            client_session_id=client_session_id,
            transport_session_id=transport_session_id,
            selection=selection,
            created_at=now,
        )


def claim_codex_live_session(
    *,
    call_id: str,
    lesson_id: str,
    user_id: str,
    client_session_id: str,
) -> CodexLiveSession | None:
    with _sessions_lock:
        session = _sessions.get(call_id)
        if session is None:
            return None
        if time.monotonic() - session.created_at > _SESSION_TTL_SECONDS:
            _sessions.pop(call_id, None)
            return None
        if (
            session.lesson_id != lesson_id
            or session.user_id != user_id
            or session.client_session_id != client_session_id
            or session.claimed
        ):
            return None
        session.claimed = True
        return session


def release_codex_live_session(call_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(call_id, None)


def _snapshot_selection(selection: SelectionRef | None) -> SelectionRef | None:
    return selection.model_copy(deep=True) if selection is not None else None


def codex_live_sideband_url(call_id: str) -> str:
    if not _CALL_ID_PATTERN.fullmatch(call_id):
        raise ValueError("Invalid Codex Live call ID")
    parsed = urlparse(_codex_realtime_proxy_url())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/{call_id}"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def chunk_codex_live_text(text: str) -> list[str]:
    if len(text.encode("utf-8")) <= _APPEND_MAX_BYTES:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in text:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > _APPEND_MAX_BYTES:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += character_bytes
    if current:
        chunks.append("".join(current))
    return chunks


def parse_codex_live_event(payload: str) -> dict[str, Any] | None:
    try:
        event = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        return None
    event_type = event["type"]
    if event_type == "session.started":
        return {"kind": "session_started"}
    if event_type in {"input_transcript.added", "output_transcript.added"}:
        item = event.get("item")
        text = item.get("text") if isinstance(item, dict) else None
        if not isinstance(text, str):
            return None
        return {
            "kind": "transcript_delta",
            "role": "user" if event_type.startswith("input_") else "assistant",
            "text": text,
        }
    if event_type == "turn.done":
        turn = event.get("turn")
        if not isinstance(turn, dict):
            return None
        role = turn.get("role")
        transcript = turn.get("transcript")
        if role not in {"user", "assistant"} or not isinstance(transcript, str):
            return None
        return {"kind": "transcript_done", "role": role, "text": transcript}
    if event_type == "delegation.created":
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "delegation" or item.get("target") != "client":
            return None
        delegation_id = item.get("id")
        content = item.get("content")
        if not isinstance(delegation_id, str) or not isinstance(content, list):
            return None
        prompt = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "input_text"
            and isinstance(part.get("text"), str)
        )
        return {
            "kind": "delegation",
            "id": delegation_id,
            "prompt": prompt,
            "turn_id": _nonempty_identifier(event.get("turn_id"))
            or _nonempty_identifier(item.get("turn_id")),
            "input_event_id": _nonempty_identifier(event.get("input_event_id"))
            or _nonempty_identifier(item.get("input_event_id")),
            "provider_reference": _nonempty_identifier(event.get("id"))
            or delegation_id,
        }
    if event_type == "error":
        error = event.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(error, str):
            message = error
        elif isinstance(event.get("message"), str):
            message = event["message"]
        else:
            message = "Codex Live sideband error"
        return {"kind": "error", "message": message}
    return {"kind": "ignored", "event_type": event_type}


def _nonempty_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _send_context(
    upstream: ClientConnection,
    send_lock: asyncio.Lock,
    *,
    text: str,
    delegation_id: str | None = None,
    channel: str | None = None,
) -> None:
    event_type = "delegation.context.append" if delegation_id else "session.context.append"
    for chunk in chunk_codex_live_text(text):
        event: dict[str, Any] = {
            "type": event_type,
            "content": [{"type": "input_text", "text": chunk}],
        }
        if delegation_id:
            event["delegation_item_id"] = delegation_id
        if channel:
            event["channel"] = channel
        elif delegation_id:
            event["channel"] = "speakable"
        async with send_lock:
            await upstream.send(json.dumps(event, ensure_ascii=False))


async def _send_client_event(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    payload: dict[str, Any],
) -> None:
    async with send_lock:
        await websocket.send_json(payload)


async def _send_queue_snapshot(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    coordinator: CodexLiveTaskCoordinator,
) -> None:
    await _send_client_event(
        websocket,
        send_lock,
        {"type": "codex_live.queue.updated", **coordinator.snapshot()},
    )


def _log_task_decision(
    session: CodexLiveSession,
    decision: TaskDecision,
    *,
    source: str,
) -> None:
    task = decision.task
    ai_usage_logger.log_model_run_event(
        decision.kind,
        run_id=task.workflow_run_id,
        parent_run_id=session.client_session_id,
        provider="openai_codex",
        model=OPENAI_CODEX_REALTIME_MODEL,
        status=task.status,
        user_id=session.user_id,
        lesson_id=session.lesson_id,
        turn_id=task.turn_id,
        request_kind="provider_delegation" if task.provider_delegation else "typed_delegation",
        input_data={"text": task.prompt},
        metadata={
            "call_id": session.call_id,
            "queue_source": source,
            "task_action": task.action,
            "duplicate_of": decision.duplicate_of,
            "queue_position": decision.queue_position,
            "delegation_id": task.delegation_id,
            "input_event_id": task.input_event_id,
            "provider_reference": task.provider_reference,
        },
    )


async def _publish_task_decision(
    websocket: WebSocket,
    client_send_lock: asyncio.Lock,
    upstream: ClientConnection,
    upstream_send_lock: asyncio.Lock,
    session: CodexLiveSession,
    coordinator: CodexLiveTaskCoordinator,
    decision: TaskDecision,
    *,
    source: str,
) -> None:
    _log_task_decision(session, decision, source=source)
    task = decision.task
    if decision.kind == "queued":
        await _send_client_event(
            websocket,
            client_send_lock,
            {
                "type": "codex_live.workflow.queued",
                "delegation_id": task.delegation_id,
                "text": task.prompt,
                "position": decision.queue_position,
                **coordinator.snapshot(),
            },
        )
    elif decision.kind == "pending":
        await _send_client_event(
            websocket,
            client_send_lock,
            {
                "type": "codex_live.workflow.input_pending",
                "delegation_id": task.delegation_id,
                "text": task.prompt,
                "actions": ["supplement", "replace", "queue", "chat", "dismiss"],
                **coordinator.snapshot(),
            },
        )
    elif decision.kind == "duplicate":
        await _send_client_event(
            websocket,
            client_send_lock,
            {
                "type": "codex_live.workflow.duplicate",
                "delegation_id": task.delegation_id,
                "duplicate_of": decision.duplicate_of,
                **coordinator.snapshot(),
            },
        )
    elif decision.kind == "chat":
        await _send_context(
            upstream,
            upstream_send_lock,
            text=task.prompt,
            channel="speakable",
        )
        await _send_client_event(
            websocket,
            client_send_lock,
            {
                "type": "codex_live.workflow.dismissed",
                "delegation_id": task.delegation_id,
                "reason": "ordinary_chat",
                **coordinator.snapshot(),
            },
        )
    else:
        await _send_client_event(
            websocket,
            client_send_lock,
            {
                "type": "codex_live.workflow.dismissed",
                "delegation_id": task.delegation_id,
                "reason": "user_dismissed",
                **coordinator.snapshot(),
            },
        )
    await _send_queue_snapshot(websocket, client_send_lock, coordinator)


async def _handle_client_messages(
    websocket: WebSocket,
    upstream: ClientConnection,
    session: CodexLiveSession,
    upstream_send_lock: asyncio.Lock,
    client_send_lock: asyncio.Lock,
    coordinator: CodexLiveTaskCoordinator,
) -> None:
    while True:
        payload = await websocket.receive_json()
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "selection.update":
            raw_selection = payload.get("selection")
            try:
                session.selection = SelectionRef.model_validate(raw_selection) if raw_selection else None
            except ValueError:
                await _send_client_event(
                    websocket,
                    client_send_lock,
                    {"type": "codex_live.selection.error"},
                )
        elif payload.get("type") == "input_text":
            text = str(payload.get("text") or "").strip()
            if text:
                raw_action = str(payload.get("task_action") or "auto")
                action: TaskAction = (
                    raw_action
                    if raw_action in {"auto", "queue", "supplement", "replace", "chat", "dismiss"}
                    else "auto"
                )
                decision = await coordinator.submit(
                    CodexLiveTask(
                        delegation_id=(
                            _nonempty_identifier(payload.get("delegation_id"))
                            or new_id("typed_delegation")
                        ),
                        prompt=text,
                        provider_delegation=False,
                        turn_id=(
                            _nonempty_identifier(payload.get("turn_id"))
                            or new_id("realtime_turn")
                        ),
                        workflow_run_id=new_id("workflow_run"),
                        input_event_id=(
                            _nonempty_identifier(payload.get("input_event_id"))
                            or new_id("realtime_input")
                        ),
                        input_kind=(
                            payload["input_kind"]
                            if payload.get("input_kind") in {"typed", "voice"}
                            else "typed"
                        ),
                        provider_reference=_nonempty_identifier(
                            payload.get("provider_reference")
                        ),
                        selection=_snapshot_selection(session.selection),
                        action=action,
                    )
                )
                await _publish_task_decision(
                    websocket,
                    client_send_lock,
                    upstream,
                    upstream_send_lock,
                    session,
                    coordinator,
                    decision,
                    source="client",
                )
        elif payload.get("type") == "delegation.resolve":
            delegation_id = str(payload.get("delegation_id") or "")
            raw_action = str(payload.get("action") or "")
            if raw_action not in {"queue", "supplement", "replace", "chat", "dismiss"}:
                continue
            decision = await coordinator.resolve(delegation_id, raw_action)
            if decision is not None:
                await _publish_task_decision(
                    websocket,
                    client_send_lock,
                    upstream,
                    upstream_send_lock,
                    session,
                    coordinator,
                    decision,
                    source="client_resolution",
                )


async def _handle_delegations(
    websocket: WebSocket,
    upstream: ClientConnection,
    session: CodexLiveSession,
    upstream_send_lock: asyncio.Lock,
    client_send_lock: asyncio.Lock,
    coordinator: CodexLiveTaskCoordinator,
) -> None:
    while True:
        task = await coordinator.queue.get()
        cancel_event = await coordinator.begin(task)
        await _send_queue_snapshot(websocket, client_send_lock, coordinator)
        try:
            ai_usage_logger.log_model_run_event(
                "started",
                run_id=task.workflow_run_id,
                parent_run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="running",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                turn_id=task.turn_id,
                request_kind="provider_delegation" if task.provider_delegation else "typed_delegation",
                input_data={"text": task.prompt},
                metadata={
                    "call_id": session.call_id,
                    "task_action": task.action,
                    "delegation_id": task.delegation_id,
                    "input_event_id": task.input_event_id,
                },
            )
            await _send_client_event(
                websocket,
                client_send_lock,
                {
                    "type": "codex_live.workflow.started",
                    "delegation_id": task.delegation_id,
                    **coordinator.snapshot(),
                },
            )
            loop = asyncio.get_running_loop()
            pending_client_events = []

            def publish_progress(event) -> None:
                ai_usage_logger.log_model_run_event(
                    "workflow_progress",
                    run_id=task.workflow_run_id,
                    parent_run_id=session.client_session_id,
                    provider="openai_codex",
                    model=OPENAI_CODEX_REALTIME_MODEL,
                    status=event.status,
                    user_id=session.user_id,
                    lesson_id=session.lesson_id,
                    turn_id=task.turn_id,
                    request_kind="provider_delegation" if task.provider_delegation else "typed_delegation",
                    metadata={
                        "activity": event.model_dump(mode="json"),
                        "delegation_id": task.delegation_id,
                        "input_event_id": task.input_event_id,
                    },
                )
                pending_client_events.append(asyncio.run_coroutine_threadsafe(
                    _send_client_event(
                        websocket,
                        client_send_lock,
                        {
                            "type": "codex_live.workflow.progress",
                            "delegation_id": task.delegation_id,
                            "activity": event.model_dump(mode="json"),
                        },
                    ),
                    loop,
                ))

            def publish_delta(delta: str) -> None:
                pending_client_events.append(asyncio.run_coroutine_threadsafe(
                    _send_client_event(
                        websocket,
                        client_send_lock,
                        {
                            "type": "codex_live.workflow.output_delta",
                            "delegation_id": task.delegation_id,
                            "delta": delta,
                        },
                    ),
                    loop,
                ))

            result = await asyncio.to_thread(
                execute_realtime_delegation,
                lesson_id=session.lesson_id,
                user_id=session.user_id,
                message=task.prompt,
                client_session_id=session.client_session_id,
                delegation_id=task.delegation_id,
                turn_id=task.turn_id,
                workflow_run_id=task.workflow_run_id,
                input_event_id=task.input_event_id,
                input_kind=task.input_kind,
                provider_reference=task.provider_reference,
                selection=task.selection,
                on_delta=publish_delta,
                on_agent_activity=publish_progress,
                is_cancelled=cancel_event.is_set,
            )
            if pending_client_events:
                await asyncio.gather(
                    *(asyncio.wrap_future(event) for event in pending_client_events),
                    return_exceptions=True,
                )
            if cancel_event.is_set():
                await coordinator.finish(task, "cancelled")
                await _send_client_event(
                    websocket,
                    client_send_lock,
                    {
                        "type": "codex_live.workflow.cancelled",
                        "delegation_id": task.delegation_id,
                        **coordinator.snapshot(),
                    },
                )
                continue
            await _send_client_event(
                websocket,
                client_send_lock,
                {
                    "type": "codex_live.workflow.result",
                    "delegation_id": task.delegation_id,
                    "result": result.model_dump(mode="json"),
                },
            )
            ai_usage_logger.log_model_run_event(
                "completed" if result.status == "ok" else "failed",
                run_id=task.workflow_run_id,
                parent_run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="completed" if result.status == "ok" else "failed",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                turn_id=task.turn_id,
                request_kind="provider_delegation" if task.provider_delegation else "typed_delegation",
                output_data={
                    "status": result.status,
                    "model_output": result.model_output,
                    "resolved_focus": (
                        result.resolved_focus.model_dump(mode="json")
                        if result.resolved_focus is not None
                        else None
                    ),
                },
                error=(
                    str(result.model_output.get("message") or "Codex Live delegation failed")
                    if result.status != "ok"
                    else None
                ),
                metadata={
                    "call_id": session.call_id,
                    "delegation_id": task.delegation_id,
                    "input_event_id": task.input_event_id,
                },
            )
            await coordinator.finish(task, "completed" if result.status == "ok" else "failed")
            response_text = str(result.model_output.get("chatbot_message") or "").strip()
            if result.status == "ok" and response_text:
                await _send_context(
                    upstream,
                    upstream_send_lock,
                    text=response_text,
                    delegation_id=task.delegation_id if task.provider_delegation else None,
                    channel="speakable",
                )
            else:
                await _send_client_event(
                    websocket,
                    client_send_lock,
                    {
                        "type": "codex_live.workflow.error",
                        "delegation_id": task.delegation_id,
                        "message": str(result.model_output.get("message") or "Chatbot 工作流未返回可朗读内容"),
                    },
                )
        finally:
            if task.status == "running":
                await coordinator.finish(task, "failed")
            coordinator.queue.task_done()
            await _send_queue_snapshot(websocket, client_send_lock, coordinator)


async def _handle_upstream_messages(
    websocket: WebSocket,
    upstream: ClientConnection,
    session: CodexLiveSession,
    upstream_send_lock: asyncio.Lock,
    client_send_lock: asyncio.Lock,
    coordinator: CodexLiveTaskCoordinator,
) -> None:
    async for raw_payload in upstream:
        payload = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload
        event = parse_codex_live_event(payload)
        if not event:
            continue
        kind = event["kind"]
        if kind == "transcript_delta":
            ai_usage_logger.log_model_run_event(
                "transcript_delta",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="running",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                delta=event["text"],
                metadata={"role": event["role"], "call_id": session.call_id},
            )
            await _send_client_event(
                websocket,
                client_send_lock,
                {
                    "type": "codex_live.transcript.delta",
                    "role": event["role"],
                    "text": event["text"],
                },
            )
        elif kind == "transcript_done":
            ai_usage_logger.log_model_run_event(
                "transcript_completed",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="completed",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                output_data={"role": event["role"], "text": event["text"]},
                metadata={"call_id": session.call_id},
            )
            await _send_client_event(
                websocket,
                client_send_lock,
                {
                    "type": "codex_live.transcript.done",
                    "role": event["role"],
                    "text": event["text"],
                },
            )
        elif kind == "delegation":
            decision = await coordinator.submit(
                CodexLiveTask(
                    delegation_id=event["id"],
                    prompt=event["prompt"],
                    provider_delegation=True,
                    turn_id=event.get("turn_id") or new_id("realtime_turn"),
                    workflow_run_id=new_id("workflow_run"),
                    input_event_id=event.get("input_event_id") or event["id"],
                    input_kind="voice",
                    provider_reference=event.get("provider_reference") or event["id"],
                    selection=_snapshot_selection(session.selection),
                )
            )
            await _publish_task_decision(
                websocket,
                client_send_lock,
                upstream,
                upstream_send_lock,
                session,
                coordinator,
                decision,
                source="provider",
            )
        elif kind == "error":
            ai_usage_logger.log_model_run_event(
                "provider_error",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="failed",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                error=event["message"],
                metadata={"call_id": session.call_id},
            )
            await _send_client_event(
                websocket,
                client_send_lock,
                {"type": "codex_live.error", "message": event["message"]},
            )
        elif kind == "session_started":
            ai_usage_logger.log_model_run_event(
                "provider_started",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="running",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                metadata={"call_id": session.call_id},
            )
        elif kind == "ignored":
            ai_usage_logger.log_model_run_event(
                "provider_event",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="running",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                metadata={"call_id": session.call_id, "event_type": event["event_type"]},
            )


async def bridge_codex_live_sideband(websocket: WebSocket, session: CodexLiveSession) -> None:
    proxy_api_key = _codex_realtime_proxy_api_key()
    if not proxy_api_key:
        await websocket.send_json({"type": "codex_live.error", "message": "Codex Live 代理凭据未配置"})
        return
    headers = {
        "Authorization": f"Bearer {proxy_api_key}",
        "OpenAI-Alpha": "quicksilver=v2",
        "Originator": "OpenClass",
        "Session-Id": session.client_session_id,
        "X-Session-Id": session.transport_session_id,
        "Thread-Id": session.transport_session_id,
    }
    try:
        async with connect(
            codex_live_sideband_url(session.call_id),
            additional_headers=headers,
            open_timeout=20,
            max_size=None,
        ) as upstream:
            upstream_send_lock = asyncio.Lock()
            client_send_lock = asyncio.Lock()
            coordinator = CodexLiveTaskCoordinator()
            client_task = asyncio.create_task(
                _handle_client_messages(
                    websocket,
                    upstream,
                    session,
                    upstream_send_lock,
                    client_send_lock,
                    coordinator,
                )
            )
            delegation_task = asyncio.create_task(
                _handle_delegations(
                    websocket,
                    upstream,
                    session,
                    upstream_send_lock,
                    client_send_lock,
                    coordinator,
                )
            )
            upstream_task = asyncio.create_task(
                _handle_upstream_messages(
                    websocket,
                    upstream,
                    session,
                    upstream_send_lock,
                    client_send_lock,
                    coordinator,
                )
            )
            await _send_client_event(
                websocket,
                client_send_lock,
                {"type": "codex_live.ready", **coordinator.snapshot()},
            )
            ai_usage_logger.log_model_run_event(
                "ready",
                run_id=session.client_session_id,
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                status="running",
                user_id=session.user_id,
                lesson_id=session.lesson_id,
                metadata={"call_id": session.call_id},
            )
            try:
                done, _pending = await asyncio.wait(
                    {client_task, delegation_task, upstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    await task
            finally:
                client_task.cancel()
                delegation_task.cancel()
                upstream_task.cancel()
                await asyncio.gather(
                    client_task,
                    delegation_task,
                    upstream_task,
                    return_exceptions=True,
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        ai_usage_logger.log_event(
            "codex_live_sideband_error",
            lesson_id=session.lesson_id,
            client_session_id=session.client_session_id,
            call_id=session.call_id,
            error=str(exc),
        )
        try:
            await websocket.send_json({"type": "codex_live.error", "message": str(exc)})
        except Exception:
            pass
    finally:
        ai_usage_logger.log_model_run_event(
            "closed",
            run_id=session.client_session_id,
            provider="openai_codex",
            model=OPENAI_CODEX_REALTIME_MODEL,
            status="completed",
            user_id=session.user_id,
            lesson_id=session.lesson_id,
            metadata={"call_id": session.call_id},
        )
        release_codex_live_session(session.call_id)

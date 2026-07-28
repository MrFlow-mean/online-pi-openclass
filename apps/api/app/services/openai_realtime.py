from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.models import (
    AIModelSelection,
    CommitRecord,
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeTranscriptLogRequest,
    new_id,
    now_iso,
)
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_model_catalog import (
    OPENAI_CODEX_REALTIME_MODEL,
    OPENAI_DEFAULT_REALTIME_MODEL,
    codex_realtime_runtime_enabled,
    codex_realtime_proxy_configured,
    codex_realtime_user_allowed,
    default_realtime_selection,
    realtime_runtime_enabled,
)
from app.services.config import load_root_dotenv
from app.services.history import current_head_commit, snapshot_lesson_runtime
from app.services.realtime_tool_bridge import realtime_tool_schemas


load_root_dotenv()

OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com/v1"
OPENAI_CODEX_REALTIME_PROXY_URL = "http://127.0.0.1:8317/v1/live"


class RealtimeServiceError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RealtimeSessionConfig:
    provider: str
    model: str
    voice: str
    tools_enabled: bool
    client_session_id: str
    session_payload: dict[str, Any]


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith(("your_", "你的_")):
        return None
    return normalized


def _openai_api_key() -> str | None:
    return _normalize_optional_secret(os.getenv("OPENAI_API_KEY"))


def _codex_realtime_proxy_api_key() -> str | None:
    configured = _normalize_optional_secret(
        os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY")
    )
    if configured:
        return configured
    path = (os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE") or "").strip()
    if not path:
        return None
    try:
        return _normalize_optional_secret(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def _codex_realtime_proxy_url() -> str:
    return (
        (os.getenv("OPENCLASS_CODEX_REALTIME_PROXY_URL") or "").strip()
        or OPENAI_CODEX_REALTIME_PROXY_URL
    ).rstrip("/")


def _openai_realtime_base_url() -> str:
    return os.getenv("OPENAI_REALTIME_BASE_URL", os.getenv("OPENAI_BASE_URL", OPENAI_OFFICIAL_BASE_URL)).rstrip("/")


def _realtime_reasoning_effort() -> str:
    value = (os.getenv("OPENAI_REALTIME_REASONING_EFFORT") or "low").strip().lower()
    return value if value in {"low", "medium", "high"} else "low"


def _compact_text(value: str | None, *, limit: int = 500) -> str:
    normalized = " ".join((value or "").split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}..."


def _hashed_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def _call_id_from_location(value: str | None) -> str | None:
    return value.rstrip("/").split("/")[-1] if value else None


def _realtime_instructions(*, lesson_title: str, latest_assistant_message: str | None) -> str:
    return (
        "You are the realtime voice and text form of the same OpenClass Chatbot shown in the left conversation panel.\n"
        "Generate every learner-facing sentence from the current conversation and tool results; do not use scripted subject templates.\n"
        "Never assume or invent board content. Before discussing, explaining, quoting, locating, or role-playing from the board, "
        "call read_board_context and stay within the returned bounded content.\n"
        "For a requested board location, use mode=target. For one or more active selections, use mode=current_selection. "
        "Its result may contain an ordered references array. Use all numbered references together and keep their identities distinct; "
        "a later reference never replaces an earlier reference. "
        "Use mode=outline when the location is ambiguous, then read the chosen target.\n"
        "When the learner defines an interaction rule, preserve that rule across turns. For alternating-role practice, produce only "
        "the next required role turn unless correction or clarification is necessary. This is a general content-shape rule, not a subject rule.\n"
        "Use run_chatbot_workflow for document edits, board generation, durable teaching progress, clarification workflows, or deeper orchestration.\n"
        "Tool results are authoritative. Present them naturally without exposing internal tool names. Do not claim an edit or location succeeded "
        "unless the corresponding tool result says it succeeded.\n"
        "Keep spoken responses focused and conversational. The learner may interrupt at any time.\n"
        f"Current lesson title: {lesson_title}\n"
        f"Most recent visible Chatbot reply: {_compact_text(latest_assistant_message) or 'none'}"
    )


def _select_realtime_model(raw: AIModelSelection | None) -> AIModelSelection:
    selected = raw or default_realtime_selection()
    if selected.provider == "openai":
        return selected
    if (
        selected.provider == "openai_codex"
        and selected.model == OPENAI_CODEX_REALTIME_MODEL
        and selected.access_method == "platform_credits"
    ):
        return selected
    raise RealtimeServiceError(400, "当前实时语音入口不支持所选模型。")


def build_openai_realtime_session_config(
    *,
    lesson_title: str,
    request: RealtimeConnectRequest,
) -> RealtimeSessionConfig:
    selected = _select_realtime_model(request.realtime_model)
    model = selected.model or (
        OPENAI_CODEX_REALTIME_MODEL
        if selected.provider == "openai_codex"
        else os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL)
    )
    voice = (os.getenv("OPENAI_REALTIME_VOICE") or "marin").strip()
    client_session_id = request.client_session_id or new_id("realtime")
    tools_enabled = (
        selected.provider == "openai"
        and _env_truthy("OPENCLASS_REALTIME_TOOLS_ENABLED", default=True)
    )
    session_payload: dict[str, Any] = {
        "type": "realtime",
        "model": model,
        "instructions": _realtime_instructions(
            lesson_title=lesson_title,
            latest_assistant_message=request.latest_assistant_message,
        ),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {
                    "model": os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
                },
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "create_response": tools_enabled,
                    "interrupt_response": tools_enabled,
                },
            },
            "output": {"voice": voice},
        },
        "reasoning": {"effort": _realtime_reasoning_effort()},
    }
    if selected.provider == "openai_codex":
        session_payload = {
            "model": model,
            "instructions": session_payload["instructions"],
        }
    if tools_enabled:
        session_payload["tools"] = realtime_tool_schemas()
        session_payload["tool_choice"] = "auto"
    return RealtimeSessionConfig(
        provider=selected.provider,
        model=model,
        voice=voice,
        tools_enabled=tools_enabled,
        client_session_id=client_session_id,
        session_payload=session_payload,
    )


def connect_openai_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    *,
    user_id: str,
) -> RealtimeConnectResponse:
    if not realtime_runtime_enabled():
        raise RealtimeServiceError(410, "实时语音后端未启用；请设置 OPENCLASS_REALTIME_ENABLED=true。")
    if not request.offer_sdp.strip():
        raise RealtimeServiceError(400, "Missing WebRTC offer SDP.")

    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    config = build_openai_realtime_session_config(lesson_title=lesson.title, request=request)
    try:
        with httpx.Client(timeout=30) as client:
            if config.provider == "openai_codex":
                if not codex_realtime_runtime_enabled():
                    raise RealtimeServiceError(
                        410,
                        "Codex 实时语音未启用；请设置 OPENCLASS_CODEX_REALTIME_ENABLED=true。",
                    )
                if not codex_realtime_user_allowed(user_id):
                    raise RealtimeServiceError(
                        403,
                        "当前账户未获授权使用 Codex 实时语音。",
                    )
                proxy_api_key = _codex_realtime_proxy_api_key()
                if not codex_realtime_proxy_configured() or not proxy_api_key:
                    raise RealtimeServiceError(
                        503,
                        "Codex 实时语音代理凭据未配置。",
                    )
                transport_session_id = new_id("codex_live")
                headers = {
                    "Authorization": f"Bearer {proxy_api_key}",
                    "OpenAI-Alpha": "quicksilver=v2",
                    "Originator": "OpenClass",
                    "Session-Id": config.client_session_id,
                    "X-Session-Id": transport_session_id,
                    "Thread-Id": transport_session_id,
                }
                response = client.post(
                    _codex_realtime_proxy_url(),
                    headers=headers,
                    json={
                        "sdp": request.offer_sdp,
                        "session": config.session_payload,
                    },
                )
            else:
                api_key = _openai_api_key()
                if not api_key:
                    raise RealtimeServiceError(
                        503,
                        "OpenAI Realtime 需要在后端配置 OPENAI_API_KEY。",
                    )
                files = {
                    "sdp": (None, request.offer_sdp, "application/sdp"),
                    "session": (
                        None,
                        json.dumps(config.session_payload, ensure_ascii=False),
                        "application/json",
                    ),
                }
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "OpenAI-Safety-Identifier": _hashed_user_id(user_id),
                }
                response = client.post(
                    f"{_openai_realtime_base_url()}/realtime/calls",
                    headers=headers,
                    files=files,
                )
    except httpx.HTTPError as exc:
        raise RealtimeServiceError(502, f"OpenAI Realtime 连接失败：{exc}") from exc
    if response.status_code >= 400:
        raise RealtimeServiceError(response.status_code, response.text or "OpenAI Realtime 连接失败。")

    answer_sdp = response.text
    if "application/json" in response.headers.get("Content-Type", "").lower():
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RealtimeServiceError(502, "Realtime 响应不是有效 JSON。") from exc
        answer_sdp = str(payload.get("sdp") or "") if isinstance(payload, dict) else ""
    if not answer_sdp.strip():
        raise RealtimeServiceError(502, "Realtime 响应缺少 SDP answer。")

    call_id = _call_id_from_location(response.headers.get("Location"))
    ai_usage_logger.log_event(
        "openai_realtime_call_created",
        lesson_id=lesson_id,
        client_session_id=config.client_session_id,
        model=config.model,
        voice=config.voice,
        call_id=call_id,
        tools_enabled=config.tools_enabled,
        provider=config.provider,
    )
    return RealtimeConnectResponse(
        answer_sdp=answer_sdp,
        provider=config.provider,
        model=config.model,
        voice=config.voice,
        call_id=call_id,
        tools_enabled=config.tools_enabled,
        client_session_id=config.client_session_id,
    )


def persist_realtime_transcript_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    *,
    user_id: str,
) -> bool:
    """Persist one learner-visible Realtime message exactly once.

    Returns True when a new history commit is saved, and False when the same
    client event was already persisted.
    """
    if request.role == "tool":
        return False
    transcript = request.transcript.strip()
    if not transcript:
        return False
    client_event_id = request.client_event_id or new_id("realtime_event")

    for _attempt in range(4):
        workspace = workspace_state.load_workspace_for_user(user_id)
        _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
        if any(
            commit.metadata.get("realtime_client_event_id") == client_event_id
            for commit in lesson.history_graph.commits
        ):
            return False

        branch_name = lesson.history_graph.current_branch
        expected_head_commit_id = current_head_commit(lesson).id
        is_user = request.role == "user"
        metadata: dict[str, object] = {
            "kind": "realtime_transcript",
            "document_changed": False,
            "interaction_channel": "realtime",
            "realtime_role": request.role,
            "realtime_client_event_id": client_event_id,
            "realtime_client_session_id": request.client_session_id or "",
            "realtime_turn_id": request.turn_id or "",
            "realtime_transport_event_type": request.transport_event_type,
            "realtime_occurred_at": request.occurred_at.isoformat() if request.occurred_at else "",
        }
        if is_user:
            metadata["user_message"] = transcript
            metadata["interaction_mode"] = "ask"
        else:
            metadata["assistant_message"] = transcript
            metadata["assistant_message_source"] = "realtime"

        metadata.update(
            {
                "history_node_kind": "chat",
                "history_node_title": transcript[:64],
                "history_node_summary": transcript[:160],
            }
        )
        commit = CommitRecord(
            label="Realtime conversation",
            message="Persisted Realtime conversation message",
            branch_name=branch_name,
            parent_ids=[expected_head_commit_id],
            snapshot=lesson.board_document,
            runtime_snapshot=snapshot_lesson_runtime(lesson),
            metadata=metadata,
        )
        if request.occurred_at:
            commit.created_at = request.occurred_at.isoformat()
        if workspace_state.append_non_document_commit_for_user_if_head(
            user_id,
            lesson_id,
            commit,
            expected_branch_name=branch_name,
            expected_head_commit_id=expected_head_commit_id,
            lesson_updated_at=now_iso(),
        ):
            return True

    raise RealtimeServiceError(409, "实时对话保存时课程已连续更新，请重试。")


def log_realtime_transcript_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    *,
    user_id: str,
) -> dict[str, str]:
    workspace = workspace_state.load_workspace_for_user(user_id)
    workspace_state.find_lesson_package(workspace, lesson_id)
    persisted = persist_realtime_transcript_event(lesson_id, request, user_id=user_id)
    direction = "inbound" if request.role == "user" else "outbound"
    if request.role == "tool" or persisted:
        log_ai_interaction_message(
            channel="realtime",
            direction=direction,
            role=request.role,
            transport=request.transport_event_type,
            content=request.transcript,
            metadata={
                "lesson_id": lesson_id,
                "lesson_title": request.lesson_title,
                "client_session_id": request.client_session_id,
                "turn_id": request.turn_id,
                "tool_name": request.tool_name,
                "tool_call_id": request.tool_call_id,
                "tool_status": request.tool_status,
            },
        )
    if request.role == "tool":
        return {"status": "logged"}
    return {"status": "persisted" if persisted else "duplicate"}

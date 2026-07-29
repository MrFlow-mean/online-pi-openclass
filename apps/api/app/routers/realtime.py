from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket

from app.models import (
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeToolCallRequest,
    RealtimeToolCallResponse,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers.auth import current_user, current_websocket_user
from app.services.codex_live_sideband import (
    bridge_codex_live_sideband,
    claim_codex_live_session,
)
from app.services.openai_realtime import (
    RealtimeServiceError,
    connect_openai_realtime_session,
    log_realtime_transcript_event,
)
from app.services.realtime_tool_bridge import execute_realtime_tool


router = APIRouter()


def _realtime_error(exc: RealtimeServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    try:
        return connect_openai_realtime_session(lesson_id, request, user_id=user.id)
    except RealtimeServiceError as exc:
        raise _realtime_error(exc) from exc


@router.post("/api/lessons/{lesson_id}/realtime/tools", response_model=RealtimeToolCallResponse)
def call_realtime_tool(
    lesson_id: str,
    request: RealtimeToolCallRequest,
    user: UserView = Depends(current_user),
) -> RealtimeToolCallResponse:
    return execute_realtime_tool(lesson_id=lesson_id, user_id=user.id, request=request)


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> dict[str, str]:
    try:
        return log_realtime_transcript_event(lesson_id, request, user_id=user.id)
    except RealtimeServiceError as exc:
        raise _realtime_error(exc) from exc


@router.websocket("/api/lessons/{lesson_id}/realtime/codex-sideband/{call_id}")
async def connect_codex_live_sideband(
    websocket: WebSocket,
    lesson_id: str,
    call_id: str,
    client_session_id: str,
) -> None:
    try:
        user = current_websocket_user(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="请先登录")
        return
    session = claim_codex_live_session(
        call_id=call_id,
        lesson_id=lesson_id,
        user_id=user.id,
        client_session_id=client_session_id,
    )
    if session is None:
        await websocket.close(code=4403, reason="Codex Live 会话无效或已过期")
        return
    await websocket.accept()
    await bridge_codex_live_sideband(websocket, session)

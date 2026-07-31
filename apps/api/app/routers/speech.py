from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.models import RealtimeConnectResponse, UserView
from app.routers.auth import current_user
from app.services.openai_realtime import RealtimeServiceError
from app.services.speech_service import (
    SpeechGenerationError,
    SpeechNotConfiguredError,
    connect_live_speech_session,
    get_speech_options,
    synthesize_speech,
)


router = APIRouter(prefix="/api")


class SpeechSynthesisRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4096)
    voice: str | None = Field(default=None, min_length=1, max_length=128)
    speech_rate: int | None = Field(default=None, ge=-50, le=100)


class SpeechVoiceOptionResponse(BaseModel):
    id: str
    label: str
    description: str


class SpeechOptionsResponse(BaseModel):
    provider: str
    model: str
    default_voice: str
    voices: list[SpeechVoiceOptionResponse]
    minimum_speech_rate: int
    maximum_speech_rate: int
    default_speech_rate: int
    delivery: str
    supports_speech_rate: bool
    supports_seek: bool


class LiveSpeechConnectRequest(BaseModel):
    offer_sdp: str = Field(min_length=1)
    client_session_id: str | None = Field(default=None, min_length=1, max_length=160)
    voice: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/speech/options", response_model=SpeechOptionsResponse)
def read_speech_options(
    user: UserView = Depends(current_user),
) -> SpeechOptionsResponse:
    try:
        options = get_speech_options(user_id=user.id)
    except SpeechNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="语音播报服务尚未配置") from exc
    return SpeechOptionsResponse(
        provider=options.provider,
        model=options.model,
        default_voice=options.default_voice,
        voices=[SpeechVoiceOptionResponse(**vars(voice)) for voice in options.voices],
        minimum_speech_rate=options.minimum_speech_rate,
        maximum_speech_rate=options.maximum_speech_rate,
        default_speech_rate=options.default_speech_rate,
        delivery=options.delivery,
        supports_speech_rate=options.supports_speech_rate,
        supports_seek=options.supports_seek,
    )


@router.post(
    "/lessons/{lesson_id}/speech/live/connect",
    response_model=RealtimeConnectResponse,
)
def connect_live_speech(
    lesson_id: str,
    payload: LiveSpeechConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    try:
        return connect_live_speech_session(
            lesson_id,
            offer_sdp=payload.offer_sdp,
            client_session_id=payload.client_session_id,
            voice=payload.voice,
            user_id=user.id,
        )
    except SpeechNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="Codex Live 语音播报尚未配置") from exc
    except RealtimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/speech")
def create_speech(
    payload: SpeechSynthesisRequest,
    _: UserView = Depends(current_user),
) -> Response:
    try:
        audio = synthesize_speech(
            payload.input,
            voice=payload.voice,
            speech_rate=payload.speech_rate,
        )
    except SpeechNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail="语音播报服务尚未配置",
        ) from exc
    except SpeechGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail="语音模型没有成功生成音频",
        ) from exc

    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Speech-Provider": audio.provider,
            "X-Speech-Model": audio.model,
            "X-Speech-Voice": audio.voice,
        },
    )

from __future__ import annotations

import logging
import os

import httpx

from app.services.config import load_root_dotenv
from app.services.speech_service import (
    SpeechAudio,
    SpeechGenerationError,
    SpeechNotConfiguredError,
    SpeechOptions,
    SpeechVoiceOption,
)


logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "marin"
DEFAULT_SPEECH_RATE = 0
MINIMUM_SPEECH_RATE = -50
MAXIMUM_SPEECH_RATE = 100
DEFAULT_TIMEOUT_SECONDS = 30.0
VOICE_IDS = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "marin",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "cedar",
)


def _configured_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _configured_timeout() -> float:
    raw_value = os.getenv("OPENAI_TTS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(120.0, value))


def _auth_headers() -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SpeechNotConfiguredError("OPENAI_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _configured_endpoint() -> str:
    endpoint = os.getenv("OPENAI_TTS_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    return f"{base_url.rstrip('/')}/audio/speech"


def _configured_model() -> str:
    return os.getenv("OPENAI_TTS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _configured_voice() -> str:
    return os.getenv("OPENAI_TTS_VOICE", DEFAULT_VOICE).strip().lower() or DEFAULT_VOICE


def _resolve_voice(requested_voice: str | None) -> str:
    voice = requested_voice.strip().lower() if requested_voice is not None else _configured_voice()
    if voice not in VOICE_IDS:
        raise SpeechGenerationError("Unsupported OpenAI speech voice")
    return voice


def _resolve_speech_rate(requested_speech_rate: int | None) -> tuple[int, float]:
    speech_rate = requested_speech_rate
    if speech_rate is None:
        speech_rate = _configured_integer(
            "OPENAI_TTS_SPEECH_RATE",
            DEFAULT_SPEECH_RATE,
            MINIMUM_SPEECH_RATE,
            MAXIMUM_SPEECH_RATE,
        )
    if not MINIMUM_SPEECH_RATE <= speech_rate <= MAXIMUM_SPEECH_RATE:
        raise SpeechGenerationError("Unsupported OpenAI speech rate")
    return speech_rate, 1.0 + (speech_rate / 100.0)


def get_openai_speech_options() -> SpeechOptions:
    load_root_dotenv()
    _auth_headers()
    default_voice = _configured_voice()
    _resolve_voice(default_voice)
    return SpeechOptions(
        provider="openai",
        model=_configured_model(),
        default_voice=default_voice,
        voices=tuple(
            SpeechVoiceOption(
                id=voice,
                label=voice.title(),
                description="OpenAI built-in voice",
            )
            for voice in VOICE_IDS
        ),
        minimum_speech_rate=MINIMUM_SPEECH_RATE,
        maximum_speech_rate=MAXIMUM_SPEECH_RATE,
        default_speech_rate=_configured_integer(
            "OPENAI_TTS_SPEECH_RATE",
            DEFAULT_SPEECH_RATE,
            MINIMUM_SPEECH_RATE,
            MAXIMUM_SPEECH_RATE,
        ),
    )


def synthesize_openai_speech(
    text: str,
    *,
    voice: str | None = None,
    speech_rate: int | None = None,
) -> SpeechAudio:
    load_root_dotenv()
    resolved_voice = _resolve_voice(voice)
    _, speed = _resolve_speech_rate(speech_rate)
    model = _configured_model()
    payload: dict[str, str | float] = {
        "model": model,
        "voice": resolved_voice,
        "input": text,
        "response_format": "mp3",
        "speed": speed,
    }
    instructions = os.getenv("OPENAI_TTS_INSTRUCTIONS", "").strip()
    if instructions:
        payload["instructions"] = instructions

    try:
        response = httpx.post(
            _configured_endpoint(),
            headers=_auth_headers(),
            json=payload,
            timeout=_configured_timeout(),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("OpenAI speech request failed model=%s voice=%s", model, resolved_voice)
        raise SpeechGenerationError("OpenAI speech request failed") from exc

    if not response.content:
        raise SpeechGenerationError("OpenAI returned empty speech audio")

    logger.info(
        "OpenAI speech generated model=%s voice=%s bytes=%s",
        model,
        resolved_voice,
        len(response.content),
    )
    return SpeechAudio(
        content=response.content,
        media_type="audio/mpeg",
        provider="openai",
        model=model,
        voice=resolved_voice,
    )

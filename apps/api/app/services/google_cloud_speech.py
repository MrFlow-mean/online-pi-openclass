from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from typing import Any

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

DEFAULT_SYNTHESIS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_VOICES_ENDPOINT = "https://texttospeech.googleapis.com/v1/voices"
DEFAULT_VOICE = "cmn-CN-Standard-A"
DEFAULT_SPEECH_RATE = 0
MINIMUM_SPEECH_RATE = -50
MAXIMUM_SPEECH_RATE = 100
DEFAULT_TIMEOUT_SECONDS = 30.0
SUPPORTED_VOICE_PATTERN = re.compile(
    r"^(?P<language>[^-]+-[^-]+)-(?P<tier>Standard|Wavenet)-",
    re.IGNORECASE,
)


def _configured_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _configured_timeout() -> float:
    raw_value = os.getenv("GOOGLE_CLOUD_TTS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(120.0, value))


def _auth_headers(*, include_content_type: bool = False) -> dict[str, str]:
    api_key = os.getenv("GOOGLE_CLOUD_TTS_API_KEY", "").strip()
    access_token = os.getenv("GOOGLE_CLOUD_TTS_ACCESS_TOKEN", "").strip()
    if not api_key and not access_token:
        raise SpeechNotConfiguredError(
            "GOOGLE_CLOUD_TTS_API_KEY or GOOGLE_CLOUD_TTS_ACCESS_TOKEN is not configured"
        )

    headers: dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if api_key:
        headers["X-Goog-Api-Key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {access_token}"
    project_id = os.getenv("GOOGLE_CLOUD_TTS_PROJECT_ID", "").strip()
    if project_id:
        headers["X-Goog-User-Project"] = project_id
    return headers


def _voice_metadata(voice_name: str) -> tuple[str, str]:
    match = SUPPORTED_VOICE_PATTERN.match(voice_name)
    if match is None:
        raise SpeechGenerationError("Unsupported Google Cloud speech voice")
    return match.group("language"), match.group("tier").lower()


def _configured_voice() -> str:
    return os.getenv("GOOGLE_CLOUD_TTS_VOICE", DEFAULT_VOICE).strip() or DEFAULT_VOICE


def _resolve_voice(requested_voice: str | None) -> tuple[str, str, str]:
    voice_name = requested_voice.strip() if requested_voice is not None else _configured_voice()
    language_code, tier = _voice_metadata(voice_name)
    return voice_name, language_code, tier


def _resolve_speech_rate(requested_speech_rate: int | None) -> tuple[int, float]:
    speech_rate = requested_speech_rate
    if speech_rate is None:
        speech_rate = _configured_integer(
            "GOOGLE_CLOUD_TTS_SPEECH_RATE",
            DEFAULT_SPEECH_RATE,
            MINIMUM_SPEECH_RATE,
            MAXIMUM_SPEECH_RATE,
        )
    if not MINIMUM_SPEECH_RATE <= speech_rate <= MAXIMUM_SPEECH_RATE:
        raise SpeechGenerationError("Unsupported Google Cloud speech rate")
    return speech_rate, 1.0 + (speech_rate / 100.0)


def _voice_option(raw_voice: Any) -> SpeechVoiceOption | None:
    if not isinstance(raw_voice, dict):
        return None
    name = str(raw_voice.get("name") or "").strip()
    try:
        language_code, tier = _voice_metadata(name)
    except SpeechGenerationError:
        return None
    gender = str(raw_voice.get("ssmlGender") or "unspecified").strip().lower()
    tier_label = "WaveNet" if tier == "wavenet" else "Standard"
    return SpeechVoiceOption(
        id=name,
        label=name,
        description=f"{language_code} · {gender} · {tier_label}",
    )


def _configured_voice_option(voice_name: str) -> SpeechVoiceOption:
    language_code, tier = _voice_metadata(voice_name)
    tier_label = "WaveNet" if tier == "wavenet" else "Standard"
    return SpeechVoiceOption(
        id=voice_name,
        label=voice_name,
        description=f"{language_code} · configured · {tier_label}",
    )


def _list_voice_options(headers: dict[str, str]) -> tuple[SpeechVoiceOption, ...]:
    endpoint = os.getenv("GOOGLE_CLOUD_TTS_VOICES_ENDPOINT", DEFAULT_VOICES_ENDPOINT).strip()
    endpoint = endpoint or DEFAULT_VOICES_ENDPOINT
    try:
        response = httpx.get(endpoint, headers=headers, timeout=_configured_timeout())
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Google Cloud speech voice discovery failed")
        return ()
    if not isinstance(payload, dict) or not isinstance(payload.get("voices"), list):
        logger.warning("Google Cloud speech voice discovery returned an invalid response")
        return ()
    options = [option for raw_voice in payload["voices"] if (option := _voice_option(raw_voice))]
    return tuple(sorted(options, key=lambda option: option.id.casefold()))


def get_google_cloud_speech_options() -> SpeechOptions:
    load_root_dotenv()
    headers = _auth_headers()
    configured_voice = _configured_voice()
    _voice_metadata(configured_voice)
    voices = _list_voice_options(headers)
    if configured_voice not in {voice.id for voice in voices}:
        voices = (_configured_voice_option(configured_voice), *voices)
    return SpeechOptions(
        provider="google_cloud",
        model="Google Cloud Standard / WaveNet",
        default_voice=configured_voice,
        voices=voices,
        minimum_speech_rate=MINIMUM_SPEECH_RATE,
        maximum_speech_rate=MAXIMUM_SPEECH_RATE,
        default_speech_rate=_configured_integer(
            "GOOGLE_CLOUD_TTS_SPEECH_RATE",
            DEFAULT_SPEECH_RATE,
            MINIMUM_SPEECH_RATE,
            MAXIMUM_SPEECH_RATE,
        ),
    )


def synthesize_google_cloud_speech(
    text: str,
    *,
    voice: str | None = None,
    speech_rate: int | None = None,
) -> SpeechAudio:
    load_root_dotenv()
    resolved_voice, language_code, tier = _resolve_voice(voice)
    _, google_speech_rate = _resolve_speech_rate(speech_rate)
    endpoint = os.getenv("GOOGLE_CLOUD_TTS_ENDPOINT", DEFAULT_SYNTHESIS_ENDPOINT).strip()
    endpoint = endpoint or DEFAULT_SYNTHESIS_ENDPOINT
    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": language_code,
            "name": resolved_voice,
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": google_speech_rate,
        },
    }

    try:
        response = httpx.post(
            endpoint,
            headers=_auth_headers(include_content_type=True),
            json=payload,
            timeout=_configured_timeout(),
        )
        response.raise_for_status()
        response_payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Google Cloud speech request failed voice=%s", resolved_voice)
        raise SpeechGenerationError("Google Cloud speech request failed") from exc

    encoded_audio = response_payload.get("audioContent") if isinstance(response_payload, dict) else None
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise SpeechGenerationError("Google Cloud returned empty speech audio")
    try:
        content = base64.b64decode(encoded_audio, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SpeechGenerationError("Google Cloud returned invalid base64 audio") from exc
    if not content:
        raise SpeechGenerationError("Google Cloud returned empty speech audio")

    logger.info(
        "Google Cloud speech generated tier=%s voice=%s bytes=%s",
        tier,
        resolved_voice,
        len(content),
    )
    return SpeechAudio(
        content=content,
        media_type="audio/mpeg",
        provider="google_cloud",
        model=f"google-cloud-{tier}",
        voice=resolved_voice,
    )

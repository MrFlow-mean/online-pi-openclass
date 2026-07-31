from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from app.services.config import load_root_dotenv


class SpeechNotConfiguredError(RuntimeError):
    pass


class SpeechGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    media_type: str
    provider: str
    model: str
    voice: str


@dataclass(frozen=True)
class SpeechVoiceOption:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class SpeechOptions:
    provider: str
    model: str
    default_voice: str
    voices: tuple[SpeechVoiceOption, ...]
    minimum_speech_rate: int
    maximum_speech_rate: int
    default_speech_rate: int
    delivery: str = "buffered_audio"
    supports_speech_rate: bool = True
    supports_seek: bool = True


SpeechProvider = Callable[[str, str | None, int | None], SpeechAudio]


def _volcengine_provider(text: str, voice: str | None, speech_rate: int | None) -> SpeechAudio:
    from app.services.volcengine_speech import synthesize_volcengine_speech

    return synthesize_volcengine_speech(text, speaker=voice, speech_rate=speech_rate)


def _google_cloud_provider(text: str, voice: str | None, speech_rate: int | None) -> SpeechAudio:
    from app.services.google_cloud_speech import synthesize_google_cloud_speech

    return synthesize_google_cloud_speech(text, voice=voice, speech_rate=speech_rate)


def _openai_provider(text: str, voice: str | None, speech_rate: int | None) -> SpeechAudio:
    from app.services.openai_speech import synthesize_openai_speech

    return synthesize_openai_speech(text, voice=voice, speech_rate=speech_rate)


SPEECH_PROVIDERS: dict[str, SpeechProvider] = {
    "google_cloud": _google_cloud_provider,
    "openai": _openai_provider,
    "volcengine": _volcengine_provider,
}


def _provider_name() -> str:
    configured = os.getenv("OPENCLASS_SPEECH_PROVIDER", "openai_codex").strip().lower()
    return (configured or "openai_codex").replace("-", "_")


def get_speech_options(*, user_id: str | None = None) -> SpeechOptions:
    load_root_dotenv()
    provider_name = _provider_name()
    if provider_name == "openai_codex":
        from app.services.ai_model_catalog import (
            OPENAI_CODEX_REALTIME_MODEL,
            codex_realtime_proxy_configured,
            codex_realtime_runtime_enabled,
            codex_realtime_user_allowed,
        )
        from app.services.openai_realtime import (
            OPENAI_CODEX_REALTIME_VOICES,
            codex_realtime_voice,
        )

        if (
            not codex_realtime_runtime_enabled()
            or not codex_realtime_proxy_configured()
            or (user_id is not None and not codex_realtime_user_allowed(user_id))
        ):
            raise SpeechNotConfiguredError("Codex Live speech is not configured for this user")
        default_voice = codex_realtime_voice()
        return SpeechOptions(
            provider="openai_codex",
            model=OPENAI_CODEX_REALTIME_MODEL,
            default_voice=default_voice,
            voices=tuple(
                SpeechVoiceOption(
                    id=voice,
                    label=voice.title(),
                    description="Codex Live 实时音色",
                )
                for voice in sorted(OPENAI_CODEX_REALTIME_VOICES)
            ),
            minimum_speech_rate=0,
            maximum_speech_rate=0,
            default_speech_rate=0,
            delivery="buffered_live_audio",
            supports_speech_rate=False,
            supports_seek=True,
        )
    if provider_name == "google_cloud":
        from app.services.google_cloud_speech import get_google_cloud_speech_options

        return get_google_cloud_speech_options()
    if provider_name == "openai":
        from app.services.openai_speech import get_openai_speech_options

        return get_openai_speech_options()
    if provider_name == "volcengine":
        from app.services.volcengine_speech import get_volcengine_speech_options

        return get_volcengine_speech_options()
    raise SpeechNotConfiguredError(f"Unsupported speech provider: {provider_name}")


def connect_live_speech_session(
    lesson_id: str,
    *,
    offer_sdp: str,
    client_session_id: str | None,
    voice: str | None,
    user_id: str,
):
    load_root_dotenv()
    if _provider_name() != "openai_codex":
        raise SpeechNotConfiguredError("The configured speech provider does not support live delivery")

    from app.models import AIModelSelection, RealtimeConnectRequest
    from app.services.ai_model_catalog import OPENAI_CODEX_REALTIME_MODEL
    from app.services.openai_realtime import connect_openai_realtime_session

    return connect_openai_realtime_session(
        lesson_id,
        RealtimeConnectRequest(
            offer_sdp=offer_sdp,
            client_session_id=client_session_id,
            realtime_model=AIModelSelection(
                provider="openai_codex",
                model=OPENAI_CODEX_REALTIME_MODEL,
                access_method="platform_credits",
            ),
            voice=voice,
            interaction_mode="announcement",
        ),
        user_id=user_id,
    )


def synthesize_speech(
    text: str,
    *,
    voice: str | None = None,
    speech_rate: int | None = None,
) -> SpeechAudio:
    load_root_dotenv()
    normalized_text = text.strip()
    if not normalized_text:
        raise SpeechGenerationError("Speech input is empty")

    provider_name = _provider_name()
    provider = SPEECH_PROVIDERS.get(provider_name)
    if provider is None:
        raise SpeechNotConfiguredError(f"Unsupported speech provider: {provider_name}")
    return provider(normalized_text, voice, speech_rate)

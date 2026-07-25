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
    configured = os.getenv("OPENCLASS_SPEECH_PROVIDER", "volcengine").strip().lower()
    return (configured or "volcengine").replace("-", "_")


def get_speech_options() -> SpeechOptions:
    load_root_dotenv()
    provider_name = _provider_name()
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

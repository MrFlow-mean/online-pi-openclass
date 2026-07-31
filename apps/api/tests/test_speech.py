from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import RealtimeConnectResponse, UserView
from app.routers import auth as auth_router
from app.routers import speech as speech_router
from app.services.google_cloud_speech import (
    get_google_cloud_speech_options,
    synthesize_google_cloud_speech,
)
from app.services.openai_speech import get_openai_speech_options, synthesize_openai_speech
from app.services.speech_service import (
    SpeechAudio,
    SpeechNotConfiguredError,
    get_speech_options,
    synthesize_speech,
)
from app.services.volcengine_speech import _decode_audio_frames, synthesize_volcengine_speech


TEST_USER = UserView(
    id="user_speech",
    email="speech@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client():
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def test_speech_endpoint_returns_generated_audio(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        speech_router,
        "synthesize_speech",
        lambda text, *, voice=None, speech_rate=None: SpeechAudio(
            content=f"audio:{text}".encode(),
            media_type="audio/mpeg",
            provider="volcengine",
            model="seed-tts-2.0",
            voice=voice or "zh_female_vv_uranus_bigtts",
        ),
    )

    response = api_client.post(
        "/api/speech",
        json={
            "input": "新的聊天回复",
            "voice": "zh_male_dayi_saturn_bigtts",
            "speech_rate": 25,
        },
    )

    assert response.status_code == 200
    assert response.content == b"audio:\xe6\x96\xb0\xe7\x9a\x84\xe8\x81\x8a\xe5\xa4\xa9\xe5\x9b\x9e\xe5\xa4\x8d"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-speech-provider"] == "volcengine"
    assert response.headers["x-speech-model"] == "seed-tts-2.0"
    assert response.headers["x-speech-voice"] == "zh_male_dayi_saturn_bigtts"
    assert response.headers["cache-control"] == "no-store"


def test_speech_endpoint_requires_nonempty_bounded_input(api_client: TestClient) -> None:
    assert api_client.post("/api/speech", json={"input": ""}).status_code == 422
    assert api_client.post("/api/speech", json={"input": "x" * 4097}).status_code == 422
    assert api_client.post("/api/speech", json={"input": "x", "speech_rate": -51}).status_code == 422
    assert api_client.post("/api/speech", json={"input": "x", "speech_rate": 101}).status_code == 422


def test_speech_options_expose_doubao_model_voices_and_rate_range(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_SPEECH_PROVIDER", "volcengine")
    monkeypatch.setenv("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0")
    monkeypatch.setenv("VOLCENGINE_TTS_SPEAKER", "zh_female_vv_uranus_bigtts")
    response = api_client.get("/api/speech/options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "volcengine"
    assert payload["model"] == "seed-tts-2.0"
    assert payload["default_voice"] == "zh_female_vv_uranus_bigtts"
    assert payload["minimum_speech_rate"] == -50
    assert payload["maximum_speech_rate"] == 100
    assert {voice["id"] for voice in payload["voices"]} >= {
        "zh_female_vv_uranus_bigtts",
        "zh_male_dayi_saturn_bigtts",
    }


def test_speech_options_expose_codex_live_realtime_delivery(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_SPEECH_PROVIDER", "openai_codex")
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", TEST_USER.id)
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY", "proxy-key")

    response = api_client.get("/api/speech/options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai_codex"
    assert payload["model"] == "gpt-live-1-codex"
    assert payload["default_voice"] == "cove"
    assert payload["delivery"] == "realtime_audio"
    assert payload["supports_speech_rate"] is False
    assert payload["supports_seek"] is False
    assert {voice["id"] for voice in payload["voices"]} >= {"cove", "ember", "vale"}


def test_live_speech_connect_uses_provider_neutral_endpoint(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def connect(lesson_id: str, **kwargs: object) -> RealtimeConnectResponse:
        captured.update(lesson_id=lesson_id, **kwargs)
        return RealtimeConnectResponse(
            answer_sdp="v=0-answer",
            provider="openai_codex",
            model="gpt-live-1-codex",
            voice="ember",
            client_delegation_enabled=True,
            delegation_websocket_url="/api/lessons/lesson_1/realtime/codex-sideband/rtc_1",
        )

    monkeypatch.setattr(speech_router, "connect_live_speech_session", connect)
    response = api_client.post(
        "/api/lessons/lesson_1/speech/live/connect",
        json={
            "offer_sdp": "v=0-offer",
            "client_session_id": "speech_session_1",
            "voice": "ember",
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "gpt-live-1-codex"
    assert captured == {
        "lesson_id": "lesson_1",
        "offer_sdp": "v=0-offer",
        "client_session_id": "speech_session_1",
        "voice": "ember",
        "user_id": TEST_USER.id,
    }


def test_speech_endpoint_reports_missing_provider_configuration(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_: str, *, voice: str | None = None, speech_rate: int | None = None) -> SpeechAudio:
        raise SpeechNotConfiguredError("missing key")

    monkeypatch.setattr(speech_router, "synthesize_speech", unavailable)

    response = api_client.post("/api/speech", json={"input": "需要播报的内容"})

    assert response.status_code == 503
    assert response.json()["detail"] == "语音播报服务尚未配置"


def test_speech_endpoint_reports_provider_neutral_generation_failure(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(_: str, *, voice: str | None = None, speech_rate: int | None = None) -> SpeechAudio:
        from app.services.speech_service import SpeechGenerationError

        raise SpeechGenerationError("upstream failed")

    monkeypatch.setattr(speech_router, "synthesize_speech", failed)

    response = api_client.post("/api/speech", json={"input": "需要播报的内容"})

    assert response.status_code == 502
    assert response.json()["detail"] == "语音模型没有成功生成音频"


def test_volcengine_chunked_frames_are_joined_in_order() -> None:
    frames = [
        json.dumps({"code": 0, "data": base64.b64encode(b"first").decode()}),
        json.dumps({"code": 0, "data": base64.b64encode(b"second").decode()}),
        json.dumps({"code": 20_000_000, "message": "OK"}),
    ]

    assert _decode_audio_frames(frames) == b"firstsecond"


def test_volcengine_provider_uses_v3_headers_and_doubao_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        headers = {"X-Tt-Logid": "test-log"}

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield json.dumps({"code": 0, "data": base64.b64encode(b"mp3-data").decode()})
            yield json.dumps({"code": 20_000_000, "message": "OK"})

    def fake_stream(method: str, endpoint: str, **kwargs: object) -> FakeResponse:
        captured.update({"method": method, "endpoint": endpoint, **kwargs})
        return FakeResponse()

    monkeypatch.setenv("VOLCENGINE_TTS_API_KEY", "test-api-key")
    monkeypatch.setenv("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0")
    monkeypatch.setattr("app.services.volcengine_speech.httpx.stream", fake_stream)

    audio = synthesize_volcengine_speech(
        "需要播报的内容",
        speaker="zh_male_dayi_saturn_bigtts",
        speech_rate=25,
    )

    headers = captured["headers"]
    payload = captured["json"]
    assert isinstance(headers, dict)
    assert isinstance(payload, dict)
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    assert headers["X-Api-Key"] == "test-api-key"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert headers["X-Api-Request-Id"]
    assert payload["req_params"]["speaker"] == "zh_male_dayi_saturn_bigtts"
    assert payload["req_params"]["audio_params"] == {
        "format": "mp3",
        "sample_rate": 24000,
        "speech_rate": 25,
    }
    assert audio.content == b"mp3-data"
    assert audio.provider == "volcengine"
    assert audio.model == "seed-tts-2.0"


def test_google_cloud_options_expose_standard_and_wavenet_voices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "voices": [
                    {
                        "languageCodes": ["cmn-CN"],
                        "name": "cmn-CN-Standard-A",
                        "ssmlGender": "FEMALE",
                    },
                    {
                        "languageCodes": ["en-US"],
                        "name": "en-US-Wavenet-D",
                        "ssmlGender": "MALE",
                    },
                    {
                        "languageCodes": ["en-US"],
                        "name": "en-US-Neural2-A",
                        "ssmlGender": "FEMALE",
                    },
                ]
            }

    def fake_get(endpoint: str, **kwargs: object) -> FakeResponse:
        captured.update({"endpoint": endpoint, **kwargs})
        return FakeResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_TTS_API_KEY", "test-google-key")
    monkeypatch.setenv("GOOGLE_CLOUD_TTS_VOICE", "cmn-CN-Standard-A")
    monkeypatch.setattr("app.services.google_cloud_speech.httpx.get", fake_get)

    options = get_google_cloud_speech_options()

    assert captured["endpoint"] == "https://texttospeech.googleapis.com/v1/voices"
    assert captured["headers"] == {"X-Goog-Api-Key": "test-google-key"}
    assert options.provider == "google_cloud"
    assert options.model == "Google Cloud Standard / WaveNet"
    assert options.default_voice == "cmn-CN-Standard-A"
    assert [voice.id for voice in options.voices] == [
        "cmn-CN-Standard-A",
        "en-US-Wavenet-D",
    ]


def test_google_cloud_provider_sends_voice_language_rate_and_decodes_mp3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"audioContent": base64.b64encode(b"google-mp3").decode()}

    def fake_post(endpoint: str, **kwargs: object) -> FakeResponse:
        captured.update({"endpoint": endpoint, **kwargs})
        return FakeResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_TTS_API_KEY", "test-google-key")
    monkeypatch.setattr("app.services.google_cloud_speech.httpx.post", fake_post)

    audio = synthesize_google_cloud_speech(
        "需要播报的内容",
        voice="cmn-CN-Wavenet-A",
        speech_rate=25,
    )

    assert captured["endpoint"] == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert captured["headers"] == {
        "Content-Type": "application/json; charset=utf-8",
        "X-Goog-Api-Key": "test-google-key",
    }
    assert captured["json"] == {
        "input": {"text": "需要播报的内容"},
        "voice": {
            "languageCode": "cmn-CN",
            "name": "cmn-CN-Wavenet-A",
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": 1.25,
        },
    }
    assert audio.content == b"google-mp3"
    assert audio.provider == "google_cloud"
    assert audio.model == "google-cloud-wavenet"
    assert audio.voice == "cmn-CN-Wavenet-A"


def test_speech_service_routes_google_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SpeechAudio(
        content=b"audio",
        media_type="audio/mpeg",
        provider="google_cloud",
        model="google-cloud-standard",
        voice="en-US-Standard-A",
    )
    monkeypatch.setenv("OPENCLASS_SPEECH_PROVIDER", "google_cloud")
    monkeypatch.setattr(
        "app.services.google_cloud_speech.synthesize_google_cloud_speech",
        lambda text, *, voice=None, speech_rate=None: expected,
    )
    monkeypatch.setattr(
        "app.services.google_cloud_speech.get_google_cloud_speech_options",
        lambda: "google-options",
    )

    assert synthesize_speech("hello", voice="en-US-Standard-A", speech_rate=0) is expected
    assert get_speech_options() == "google-options"


def test_openai_options_reuse_shared_key_and_expose_builtin_voices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setenv("OPENAI_TTS_VOICE", "cedar")

    options = get_openai_speech_options()

    assert options.provider == "openai"
    assert options.model == "gpt-4o-mini-tts"
    assert options.default_voice == "cedar"
    assert options.minimum_speech_rate == -50
    assert options.maximum_speech_rate == 100
    assert {voice.id for voice in options.voices} >= {"marin", "cedar", "coral"}


def test_openai_provider_sends_model_voice_rate_and_returns_mp3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        content = b"openai-mp3"

        def raise_for_status(self) -> None:
            return None

    def fake_post(endpoint: str, **kwargs: object) -> FakeResponse:
        captured.update({"endpoint": endpoint, **kwargs})
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr("app.services.openai_speech.httpx.post", fake_post)

    audio = synthesize_openai_speech(
        "需要播报的内容",
        voice="marin",
        speech_rate=25,
    )

    assert captured["endpoint"] == "https://api.openai.com/v1/audio/speech"
    assert captured["headers"] == {
        "Authorization": "Bearer test-openai-key",
        "Content-Type": "application/json",
    }
    assert captured["json"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "需要播报的内容",
        "response_format": "mp3",
        "speed": 1.25,
    }
    assert audio.content == b"openai-mp3"
    assert audio.provider == "openai"
    assert audio.model == "gpt-4o-mini-tts"
    assert audio.voice == "marin"


def test_speech_service_routes_openai_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SpeechAudio(
        content=b"audio",
        media_type="audio/mpeg",
        provider="openai",
        model="gpt-4o-mini-tts",
        voice="marin",
    )
    monkeypatch.setenv("OPENCLASS_SPEECH_PROVIDER", "openai")
    monkeypatch.setattr(
        "app.services.openai_speech.synthesize_openai_speech",
        lambda text, *, voice=None, speech_rate=None: expected,
    )
    monkeypatch.setattr(
        "app.services.openai_speech.get_openai_speech_options",
        lambda: "openai-options",
    )

    assert synthesize_speech("hello", voice="marin", speech_rate=0) is expected
    assert get_speech_options() == "openai-options"

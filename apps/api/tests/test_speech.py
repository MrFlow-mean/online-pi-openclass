from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.routers import speech as speech_router
from app.services.speech_service import SpeechAudio, SpeechNotConfiguredError
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


def test_speech_endpoint_reports_missing_provider_configuration(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_: str, *, voice: str | None = None, speech_rate: int | None = None) -> SpeechAudio:
        raise SpeechNotConfiguredError("missing key")

    monkeypatch.setattr(speech_router, "synthesize_speech", unavailable)

    response = api_client.post("/api/speech", json={"input": "需要播报的内容"})

    assert response.status_code == 503
    assert response.json()["detail"] == "语音播报尚未配置 VOLCENGINE_TTS_API_KEY"


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

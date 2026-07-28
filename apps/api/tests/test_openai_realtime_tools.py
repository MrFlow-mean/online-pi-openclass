import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models import (
    RealtimeConnectRequest,
    RealtimeToolCallRequest,
    RealtimeTranscriptLogRequest,
    SelectionRef,
)
from app.services import chat_service, openai_realtime, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.realtime_board_context import read_realtime_board_context
from app.services.realtime_tool_bridge import execute_realtime_tool
from app.services.rich_document import build_document


TEST_USER_ID = "user_realtime_test"


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Realtime 测试页")
    lesson.board_document = build_document(
        title="规则互动板书",
        document_id=lesson.board_document.id,
        content_text=(
            "# 课程内容\n\n"
            "## 第三节 情景对话\n\n"
            "A: Welcome to the library.\n\n"
            "B: Thank you. I need a history book.\n\n"
            "## 第五小节 例题\n\n"
            "例题：已知 x + 2 = 5，求 x。\n\n"
            "解：两边同时减去 2，得到 x = 3。"
        ),
    )
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    return store


def test_realtime_connect_posts_official_webrtc_session_with_tools(monkeypatch, isolated_store) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_REALTIME_TOOLS_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    lesson = _seed_workspace(isolated_store)
    captured = {}

    class _FakeResponse:
        status_code = 201
        text = "answer-sdp"
        headers = {"Location": "/v1/realtime/calls/rtc_test_call"}

    class _FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, files):
            captured.update(url=url, headers=headers, files=files)
            return _FakeResponse()

    monkeypatch.setattr(openai_realtime.httpx, "Client", _FakeClient)
    response = openai_realtime.connect_openai_realtime_session(
        lesson.id,
        RealtimeConnectRequest(offer_sdp="v=0", client_session_id="realtime_test"),
        user_id=TEST_USER_ID,
    )

    assert response.answer_sdp == "answer-sdp"
    assert response.call_id == "rtc_test_call"
    assert response.tools_enabled is True
    assert captured["url"] == "https://api.openai.com/v1/realtime/calls"
    payload = json.loads(captured["files"]["session"][1])
    assert payload["model"] == "gpt-realtime-2.1"
    assert payload["output_modalities"] == ["audio"]
    assert payload["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert payload["reasoning"]["effort"] == "low"
    assert {tool["name"] for tool in payload["tools"]} == {
        "read_board_context",
        "run_chatbot_workflow",
    }
    assert "第三节 情景对话" not in payload["instructions"]


def test_board_context_resolves_heading_range_and_highlight(isolated_store) -> None:
    lesson = _seed_workspace(isolated_store)

    result = read_realtime_board_context(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        arguments={"mode": "target", "target": "第三节 情景对话"},
        selection=None,
    )

    assert result.model_output["status"] == "ok"
    assert "Welcome to the library" in result.model_output["content"]
    assert "例题：已知" not in result.model_output["content"]
    assert result.focus is not None
    assert result.focus.kind == "heading"
    assert result.focus.display_label.endswith("第三节 情景对话")
    assert result.focus.order_end > result.focus.order_start


def test_board_context_uses_validated_current_selection(isolated_store) -> None:
    lesson = _seed_workspace(isolated_store)

    result = read_realtime_board_context(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        arguments={"mode": "current_selection"},
        selection=SelectionRef(
            kind="board",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            excerpt="例题：已知 x + 2 = 5，求 x。",
        ),
    )

    assert result.model_output["status"] == "ok"
    assert "x = 3" in result.model_output["content"]
    assert result.focus is not None
    assert result.focus.confidence == 1.0


def test_read_board_tool_returns_only_bounded_model_output(isolated_store) -> None:
    lesson = _seed_workspace(isolated_store)
    response = execute_realtime_tool(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        request=RealtimeToolCallRequest(
            client_session_id="realtime_session",
            call_id="call_read",
            name="read_board_context",
            arguments={"mode": "target", "target": "第五小节 例题", "max_chars": 1200},
        ),
    )

    assert response.status == "ok"
    assert response.course_package is None
    assert response.resolved_focus is not None
    assert "x + 2 = 5" in response.model_output["content"]


def test_chatbot_tool_reuses_existing_workflow(monkeypatch, isolated_store) -> None:
    lesson = _seed_workspace(isolated_store)
    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    package = workspace_state.package_view_for_lesson(workspace, workspace.packages[0], lesson.id)
    captured = {}

    def _fake_chat(lesson_id, request, *, user_id, commit_metadata=None):
        captured.update(
            lesson_id=lesson_id,
            message=request.message,
            user_id=user_id,
            commit_metadata=commit_metadata,
        )
        return SimpleNamespace(
            chatbot_message="Chatbot 已完成这次编排。",
            needs_clarification=False,
            clarification_questions=[],
            course_package=package,
        )

    monkeypatch.setattr(chat_service, "process_chat_on_lesson", _fake_chat)
    response = execute_realtime_tool(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        request=RealtimeToolCallRequest(
            client_session_id="realtime_session",
            turn_id="turn_test",
            call_id="call_chatbot",
            name="run_chatbot_workflow",
            arguments={"message": "请按当前板书继续。"},
        ),
    )

    assert response.status == "ok"
    assert response.model_output["chatbot_message"] == "Chatbot 已完成这次编排。"
    assert response.course_package is not None
    assert captured == {
        "lesson_id": lesson.id,
        "message": "请按当前板书继续。",
        "user_id": TEST_USER_ID,
        "commit_metadata": {
            "chat_visibility": "hidden",
            "interaction_channel": "realtime_tool",
            "realtime_client_session_id": "realtime_session",
            "realtime_turn_id": "turn_test",
        },
    }


def test_realtime_transcripts_persist_once_in_lesson_history(monkeypatch, isolated_store) -> None:
    lesson = _seed_workspace(isolated_store)
    with sqlite3.connect(isolated_store.path) as connection:
        connection.execute(
            "UPDATE lessons SET board_content_html = ? WHERE id = ?",
            ("legacy-board-html-sentinel", lesson.id),
        )
        raw_board_before = connection.execute(
            "SELECT board_content_json, board_content_html, board_content_text FROM lessons WHERE id = ?",
            (lesson.id,),
        ).fetchone()
    monkeypatch.setattr(openai_realtime, "log_ai_interaction_message", lambda **_kwargs: None)
    occurred_at = datetime(2026, 7, 22, 5, 30, tzinfo=timezone.utc)

    user_request = RealtimeTranscriptLogRequest(
        client_event_id="realtime-event-user",
        client_session_id="realtime-session",
        turn_id="realtime-turn",
        occurred_at=occurred_at,
        role="user",
        transport_event_type="input_audio_transcription.completed",
        transcript="请解释当前这一段。",
    )
    assistant_request = RealtimeTranscriptLogRequest(
        client_event_id="realtime-event-assistant",
        client_session_id="realtime-session",
        turn_id="realtime-turn",
        occurred_at=occurred_at,
        role="assistant",
        transport_event_type="response.output_audio_transcript.done",
        transcript="我会根据当前内容逐步解释。",
    )

    assert openai_realtime.log_realtime_transcript_event(
        lesson.id,
        user_request,
        user_id=TEST_USER_ID,
    ) == {"status": "persisted"}
    assert openai_realtime.log_realtime_transcript_event(
        lesson.id,
        assistant_request,
        user_id=TEST_USER_ID,
    ) == {"status": "persisted"}
    assert openai_realtime.log_realtime_transcript_event(
        lesson.id,
        assistant_request,
        user_id=TEST_USER_ID,
    ) == {"status": "duplicate"}

    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    _package, saved_lesson = workspace_state.find_lesson_package(workspace, lesson.id)
    commits = [
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "realtime_transcript"
    ]
    assert len(commits) == 2
    assert commits[0].metadata["user_message"] == "请解释当前这一段。"
    assert commits[1].metadata["assistant_message"] == "我会根据当前内容逐步解释。"
    assert commits[1].metadata["assistant_message_source"] == "realtime"
    assert commits[1].metadata["realtime_client_event_id"] == "realtime-event-assistant"
    assert commits[1].metadata["realtime_turn_id"] == "realtime-turn"
    assert commits[1].metadata["document_changed"] is False
    assert commits[1].snapshot == saved_lesson.board_document
    with sqlite3.connect(isolated_store.path) as connection:
        raw_board_after = connection.execute(
            "SELECT board_content_json, board_content_html, board_content_text FROM lessons WHERE id = ?",
            (lesson.id,),
        ).fetchone()
        realtime_snapshot_html = connection.execute(
            """
            SELECT snapshot_content_html FROM lesson_commits
            WHERE lesson_id = ? AND json_extract(metadata_json, '$.kind') = 'realtime_transcript'
            ORDER BY sort_order DESC LIMIT 1
            """,
            (lesson.id,),
        ).fetchone()[0]
    assert raw_board_after == raw_board_before
    assert realtime_snapshot_html == "legacy-board-html-sentinel"


def test_realtime_base_url_ignores_blank_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # A blank `.env` entry must not turn the official endpoint into a relative
    # path; every realtime request would then be built against no host at all.
    monkeypatch.setenv("OPENAI_REALTIME_BASE_URL", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")

    assert openai_realtime._openai_realtime_base_url() == openai_realtime.OPENAI_OFFICIAL_BASE_URL

    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1/")
    assert openai_realtime._openai_realtime_base_url() == "https://proxy.example/v1"

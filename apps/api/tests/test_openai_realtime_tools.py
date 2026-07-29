import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models import (
    AIModelSelection,
    RealtimeConnectRequest,
    RealtimeToolCallRequest,
    RealtimeTranscriptLogRequest,
    SelectionRef,
)
from app.services import ai_model_catalog, chat_service, codex_live_sideband, openai_realtime, workspace_state
from app.services.codex_live_task_lifecycle import (
    CodexLiveTask,
    CodexLiveTaskCoordinator,
    task_texts_match,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.realtime_board_context import read_realtime_board_context
from app.services.realtime_tool_bridge import execute_realtime_delegation, execute_realtime_tool
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
    assert payload["type"] == "realtime"
    assert payload["model"] == "gpt-realtime-2.1"
    assert payload["output_modalities"] == ["audio"]
    assert payload["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert payload["audio"]["input"]["turn_detection"]["create_response"] is False
    assert payload["reasoning"]["effort"] == "low"
    assert {tool["name"] for tool in payload["tools"]} == {
        "read_board_context",
        "run_chatbot_workflow",
    }
    assert "tool_choice" not in payload
    workflow_tool = next(tool for tool in payload["tools"] if tool["name"] == "run_chatbot_workflow")
    assert workflow_tool["parameters"]["properties"]["intent"]["enum"] == [
        "ordinary_chat",
        "learning_need",
        "unclear",
    ]
    assert "第三节 情景对话" not in payload["instructions"]


def test_catalog_exposes_codex_live_only_to_allowed_platform_users(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", TEST_USER_ID)
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY", "proxy-api-key")
    monkeypatch.setenv("OPENAI_API_KEY", "disabled")
    monkeypatch.setattr(ai_model_catalog, "pi_runtime_available", lambda: True)
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_personal_api_configured",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    codex_live = [
        option
        for option in catalog.realtime
        if option.provider == "openai_codex"
    ]
    assert len(codex_live) == 1
    assert codex_live[0].model == "gpt-live-1-codex"
    assert codex_live[0].access_method == "platform_credits"
    assert codex_live[0].transport == "openai_webrtc"
    assert codex_live[0].enabled is True
    assert codex_live[0].configured is True
    assert codex_live[0].default is True
    assert catalog.defaults["realtime"] == AIModelSelection(
        provider="openai_codex",
        model="gpt-live-1-codex",
        access_method="platform_credits",
    )

    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )
    disconnected_catalog = ai_model_catalog.build_model_catalog("disconnected_user")
    disconnected_codex_live = next(
        option
        for option in disconnected_catalog.realtime
        if option.provider == "openai_codex"
    )
    assert disconnected_codex_live.enabled is False
    assert disconnected_codex_live.configured is True
    assert disconnected_codex_live.default is False
    assert disconnected_catalog.defaults["realtime"].provider == "openai"

    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "false")
    disabled_catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)
    disabled_codex_live = next(
        option
        for option in disabled_catalog.realtime
        if option.provider == "openai_codex"
    )
    assert disabled_codex_live.enabled is False
    assert disabled_codex_live.configured is True
    assert disabled_codex_live.default is False


def test_realtime_connect_posts_codex_platform_webrtc_session(monkeypatch, isolated_store) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", TEST_USER_ID)
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY", "proxy-api-key")
    monkeypatch.setenv(
        "OPENCLASS_CODEX_REALTIME_PROXY_URL",
        "http://127.0.0.1:8317/v1/live",
    )
    monkeypatch.setenv("OPENCLASS_REALTIME_TOOLS_ENABLED", "true")
    lesson = _seed_workspace(isolated_store)
    captured = {}

    class _FakeResponse:
        status_code = 201
        text = "codex-answer-sdp"
        headers = {
            "Content-Type": "text/plain",
            "Location": "/backend-api/codex/realtime/calls/rtc_codex_call",
        }

        def json(self):
            return json.loads(self.text)

    class _FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return _FakeResponse()

    monkeypatch.setattr(openai_realtime.httpx, "Client", _FakeClient)
    response = openai_realtime.connect_openai_realtime_session(
        lesson.id,
        RealtimeConnectRequest(
            offer_sdp="v=0-codex-offer",
            client_session_id="codex_realtime_test",
            realtime_model=AIModelSelection(
                provider="openai_codex",
                model="gpt-live-1-codex",
                access_method="platform_credits",
            ),
        ),
        user_id=TEST_USER_ID,
    )

    assert response.answer_sdp == "codex-answer-sdp"
    assert response.provider == "openai_codex"
    assert response.model == "gpt-live-1-codex"
    assert response.tools_enabled is False
    assert response.client_delegation_enabled is True
    assert response.call_id == "rtc_codex_call"
    assert response.delegation_websocket_url == (
        f"/api/lessons/{lesson.id}/realtime/codex-sideband/rtc_codex_call"
        "?client_session_id=codex_realtime_test"
    )
    assert captured["url"] == "http://127.0.0.1:8317/v1/live"
    assert captured["headers"]["Authorization"] == "Bearer proxy-api-key"
    assert captured["headers"]["OpenAI-Alpha"] == "quicksilver=v2"
    assert captured["headers"]["Originator"] == "OpenClass"
    assert captured["headers"]["Session-Id"] == "codex_realtime_test"
    assert captured["headers"]["X-Session-Id"] == captured["headers"]["Thread-Id"]
    assert captured["json"]["sdp"] == "v=0-codex-offer"
    assert captured["json"]["session"]["model"] == "gpt-live-1-codex"
    assert "OpenClass Chatbot" in captured["json"]["session"]["instructions"]
    assert captured["json"]["session"]["audio"] == {"output": {"voice": "cove"}}
    assert captured["json"]["session"]["delegation"] == {"type": "client"}
    assert set(captured["json"]["session"]) == {
        "model",
        "instructions",
        "audio",
        "delegation",
    }


def test_codex_live_omits_unsupported_avas_tool_parameters(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_TOOLS_ENABLED", "true")

    config = openai_realtime.build_openai_realtime_session_config(
        lesson_title="Any lesson",
        request=RealtimeConnectRequest(
            offer_sdp="v=0",
            realtime_model=AIModelSelection(
                provider="openai_codex",
                model="gpt-live-1-codex",
                access_method="platform_credits",
            ),
        ),
    )

    assert config.tools_enabled is False
    assert "tools" not in config.session_payload
    assert "tool_choice" not in config.session_payload


def test_codex_live_sideband_wire_parser_and_utf8_chunking(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_URL", "http://127.0.0.1:8317/v1/live")

    assert codex_live_sideband.codex_live_sideband_url("rtc_test") == (
        "ws://127.0.0.1:8317/v1/live/rtc_test"
    )
    assert codex_live_sideband.parse_codex_live_event(
        json.dumps({"type": "input_transcript.added", "item": {"text": "你好"}})
    ) == {"kind": "transcript_delta", "role": "user", "text": "你好"}
    assert codex_live_sideband.parse_codex_live_event(
        json.dumps({"type": "turn.done", "turn": {"role": "assistant", "transcript": "完成"}})
    ) == {"kind": "transcript_done", "role": "assistant", "text": "完成"}
    assert codex_live_sideband.parse_codex_live_event(
        json.dumps(
            {
                "type": "delegation.created",
                "item": {
                    "type": "delegation",
                    "target": "client",
                    "id": "delegation_1",
                    "content": [
                        {"type": "input_text", "text": "读取当前板书，"},
                        {"type": "input_text", "text": "然后补充一段。"},
                    ],
                },
            }
        )
    ) == {
        "kind": "delegation",
        "id": "delegation_1",
        "prompt": "读取当前板书，然后补充一段。",
    }
    chunks = codex_live_sideband.chunk_codex_live_text("你" * 400)
    assert "".join(chunks) == "你" * 400
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 500 for chunk in chunks)


def test_codex_live_sideband_session_is_bound_and_single_claim() -> None:
    codex_live_sideband.register_codex_live_session(
        call_id="rtc_bound_session",
        lesson_id="lesson_bound",
        user_id=TEST_USER_ID,
        client_session_id="client_bound",
        transport_session_id="transport_bound",
        selection=None,
    )
    try:
        assert codex_live_sideband.claim_codex_live_session(
            call_id="rtc_bound_session",
            lesson_id="lesson_bound",
            user_id="another_user",
            client_session_id="client_bound",
        ) is None
        claimed = codex_live_sideband.claim_codex_live_session(
            call_id="rtc_bound_session",
            lesson_id="lesson_bound",
            user_id=TEST_USER_ID,
            client_session_id="client_bound",
        )
        assert claimed is not None
        assert codex_live_sideband.claim_codex_live_session(
            call_id="rtc_bound_session",
            lesson_id="lesson_bound",
            user_id=TEST_USER_ID,
            client_session_id="client_bound",
        ) is None
    finally:
        codex_live_sideband.release_codex_live_session("rtc_bound_session")


def test_codex_live_typed_input_enters_chatbot_queue() -> None:
    class _FakeWebSocket:
        def __init__(self):
            self.calls = 0

        async def receive_json(self):
            self.calls += 1
            if self.calls == 1:
                return {"type": "input_text", "text": "读取当前板书"}
            await asyncio.Future()

        async def send_json(self, _payload):
            return None

    class _FakeUpstream:
        async def send(self, _payload):
            raise AssertionError("typed learner input must enter Chatbot before provider context")

    async def _exercise():
        coordinator = CodexLiveTaskCoordinator()
        session = codex_live_sideband.CodexLiveSession(
            call_id="rtc_typed",
            lesson_id="lesson_typed",
            user_id=TEST_USER_ID,
            client_session_id="client_typed",
            transport_session_id="transport_typed",
            selection=None,
            created_at=0,
        )
        task = asyncio.create_task(
            codex_live_sideband._handle_client_messages(
                _FakeWebSocket(),
                _FakeUpstream(),
                session,
                asyncio.Lock(),
                asyncio.Lock(),
                coordinator,
            )
        )
        queued = await asyncio.wait_for(coordinator.queue.get(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return queued

    queued = asyncio.run(_exercise())
    assert queued.delegation_id.startswith("typed_delegation_")
    assert queued.prompt == "读取当前板书"
    assert queued.provider_delegation is False


def test_codex_live_task_matching_deduplicates_repeated_work_but_not_distinct_actions() -> None:
    assert task_texts_match("读取当前板书，然后补充一段。", "读取当前板书然后补充一段")
    assert not task_texts_match("读取第一段", "读取第二段")
    assert not task_texts_match("解释当前内容", "把当前内容写入板书")


def test_codex_live_running_input_waits_for_explicit_lifecycle_action() -> None:
    async def _exercise():
        coordinator = CodexLiveTaskCoordinator()
        first = CodexLiveTask("task_1", "读取当前板书", True)
        await coordinator.submit(first)
        await coordinator.begin(await coordinator.queue.get())

        pending = await coordinator.submit(CodexLiveTask("task_2", "补充一个例子", True))
        duplicate = await coordinator.submit(CodexLiveTask("task_3", "读取当前板书。", True))
        resolved = await coordinator.resolve("task_2", "queue")
        return coordinator, pending, duplicate, resolved

    coordinator, pending, duplicate, resolved = asyncio.run(_exercise())
    assert pending.kind == "pending"
    assert duplicate.kind == "duplicate"
    assert duplicate.duplicate_of == "task_1"
    assert resolved is not None and resolved.kind == "queued"
    assert coordinator.snapshot() == {
        "running_count": 1,
        "queued_count": 1,
        "pending_count": 0,
        "active_delegation_id": "task_1",
    }


def test_codex_live_replace_cancels_active_and_clears_older_waiting_work() -> None:
    async def _exercise():
        coordinator = CodexLiveTaskCoordinator()
        first = CodexLiveTask("task_1", "处理当前任务", True)
        await coordinator.submit(first)
        active = await coordinator.queue.get()
        cancel_event = await coordinator.begin(active)
        await coordinator.submit(CodexLiveTask("task_2", "稍后处理另一项", True, action="queue"))
        replacement = CodexLiveTask("task_3", "改为处理新任务", True, action="replace")
        decision = await coordinator.submit(replacement)
        next_task = coordinator.queue.get_nowait()
        return cancel_event.is_set(), decision, next_task, coordinator

    cancelled, decision, next_task, coordinator = asyncio.run(_exercise())
    assert cancelled is True
    assert decision.kind == "queued"
    assert next_task.delegation_id == "task_3"
    assert coordinator._tasks["task_2"].status == "dismissed"  # noqa: SLF001


def test_codex_live_typed_result_uses_speakable_session_context() -> None:
    class _FakeUpstream:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

    async def _exercise():
        upstream = _FakeUpstream()
        await codex_live_sideband._send_context(
            upstream,
            asyncio.Lock(),
            text="工作流返回内容",
            channel="speakable",
        )
        return upstream.sent

    sent = asyncio.run(_exercise())
    assert sent == [
        {
            "type": "session.context.append",
            "channel": "speakable",
            "content": [{"type": "input_text", "text": "工作流返回内容"}],
        }
    ]


def test_codex_live_delegation_runs_normal_chatbot_workflow(
    monkeypatch,
    isolated_store,
) -> None:
    lesson = _seed_workspace(isolated_store)
    workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
    package = workspace_state.package_view_for_lesson(workspace, workspace.packages[0], lesson.id)
    captured = {}

    def _fake_chat(lesson_id, request, *, user_id, commit_metadata=None, **_kwargs):
        captured.update(
            lesson_id=lesson_id,
            message=request.message,
            selection=request.selection,
            user_id=user_id,
            commit_metadata=commit_metadata,
        )
        return SimpleNamespace(
            chatbot_message="已根据当前板书完成处理。",
            needs_clarification=False,
            clarification_questions=[],
            course_package=package,
        )

    selection = SelectionRef(
        kind="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        excerpt="第三节 情景对话",
    )
    monkeypatch.setattr(chat_service, "process_chat_on_lesson", _fake_chat)
    response = execute_realtime_delegation(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        message="读取我选中的板书并继续编辑。",
        client_session_id="codex_session",
        delegation_id="delegation_1",
        selection=selection,
    )

    assert response.status == "ok"
    assert response.model_output["route"] == "client_delegation"
    assert response.model_output["chatbot_message"] == "已根据当前板书完成处理。"
    assert response.course_package is not None
    assert captured == {
        "lesson_id": lesson.id,
        "message": "读取我选中的板书并继续编辑。",
        "selection": selection,
        "user_id": TEST_USER_ID,
        "commit_metadata": {
            "chat_visibility": "visible",
            "interaction_channel": "realtime_delegation",
            "realtime_client_session_id": "codex_session",
            "realtime_delegation_id": "delegation_1",
        },
    }


def test_codex_live_falls_back_to_supported_avas_voice(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_VOICE", "marin")

    config = openai_realtime.build_openai_realtime_session_config(
        lesson_title="Any lesson",
        request=RealtimeConnectRequest(
            offer_sdp="v=0",
            realtime_model=AIModelSelection(
                provider="openai_codex",
                model="gpt-live-1-codex",
                access_method="platform_credits",
            ),
        ),
    )

    assert config.voice == "cove"
    assert config.session_payload["audio"] == {"output": {"voice": "cove"}}


def test_realtime_connect_rejects_codex_live_without_proxy_credentials(monkeypatch, isolated_store) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", TEST_USER_ID)
    lesson = _seed_workspace(isolated_store)

    with pytest.raises(openai_realtime.RealtimeServiceError) as exc_info:
        openai_realtime.connect_openai_realtime_session(
            lesson.id,
            RealtimeConnectRequest(
                offer_sdp="v=0",
                realtime_model=AIModelSelection(
                    provider="openai_codex",
                    model="gpt-live-1-codex",
                    access_method="platform_credits",
                ),
            ),
            user_id=TEST_USER_ID,
        )

    assert exc_info.value.status_code == 503
    assert "代理凭据未配置" in exc_info.value.detail


def test_realtime_connect_rejects_codex_live_for_user_outside_allowlist(
    monkeypatch,
    isolated_store,
) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", "another_user")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY", "proxy-api-key")
    lesson = _seed_workspace(isolated_store)

    with pytest.raises(openai_realtime.RealtimeServiceError) as exc_info:
        openai_realtime.connect_openai_realtime_session(
            lesson.id,
            RealtimeConnectRequest(
                offer_sdp="v=0",
                realtime_model=AIModelSelection(
                    provider="openai_codex",
                    model="gpt-live-1-codex",
                    access_method="platform_credits",
                ),
            ),
            user_id=TEST_USER_ID,
        )

    assert exc_info.value.status_code == 403
    assert "未获授权" in exc_info.value.detail


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


@pytest.mark.parametrize("board_content", ["existing content", ""])
def test_explicit_learning_need_checks_board_before_chatbot_workflow(
    monkeypatch,
    isolated_store,
    board_content,
) -> None:
    lesson = _seed_workspace(isolated_store)
    if not board_content:
        lesson.board_document = build_document(
            title=lesson.board_document.title,
            document_id=lesson.board_document.id,
            content_text="",
        )
        workspace = workspace_state.load_workspace_for_user(TEST_USER_ID)
        _package, saved_lesson = workspace_state.find_lesson_package(workspace, lesson.id)
        saved_lesson.board_document = lesson.board_document
        isolated_store.save_for_user(TEST_USER_ID, workspace)
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
            arguments={
                "message": "我们来做轮流角色练习，我说一句你说一句。",
                "intent": "learning_need",
                "reason": "The learner requested a concrete practice activity.",
            },
        ),
    )

    assert response.status == "ok"
    assert response.model_output["route"] == "learning_need"
    assert response.model_output["board_state"] == (
        "board_nonempty" if board_content else "board_blank"
    )
    assert response.model_output["decision_trace"]["selected_action"] == "learning_need"
    assert response.model_output["chatbot_message"] == "Chatbot 已完成这次编排。"
    assert response.course_package is not None
    assert captured == {
        "lesson_id": lesson.id,
        "message": "我们来做轮流角色练习，我说一句你说一句。",
        "user_id": TEST_USER_ID,
        "commit_metadata": {
            "chat_visibility": "hidden",
            "interaction_channel": "realtime_tool",
            "realtime_client_session_id": "realtime_session",
            "realtime_turn_id": "turn_test",
        },
    }


@pytest.mark.parametrize(
    ("intent", "message"),
    [
        ("ordinary_chat", "今天过得怎么样？"),
        ("unclear", "我想提升一下自己。"),
    ],
)
def test_non_learning_realtime_turn_does_not_enter_board_or_chatbot(
    monkeypatch,
    isolated_store,
    intent,
    message,
) -> None:
    lesson = _seed_workspace(isolated_store)

    def _unexpected_chat(*_args, **_kwargs):
        raise AssertionError("non-learning turn must not enter Chatbot workflow")

    monkeypatch.setattr(chat_service, "process_chat_on_lesson", _unexpected_chat)
    response = execute_realtime_tool(
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        request=RealtimeToolCallRequest(
            client_session_id="realtime_session",
            call_id=f"call_{intent}",
            name="run_chatbot_workflow",
            arguments={
                "message": message,
                "intent": intent,
                "reason": "TurnDecision classified the current request.",
            },
        ),
    )

    assert response.status == "ok"
    assert response.course_package is None
    assert response.model_output["route"] == intent
    assert response.model_output["board_state"] == "not_checked"
    assert response.model_output["decision_trace"]["selected_action"] == intent


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

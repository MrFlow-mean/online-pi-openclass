from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import BoardExplanationDirective, ChatRequest
from app.services import codex_chat, workspace_state
from app.services.auto_board_teaching import (
    CHATBOT_EXPLANATION_INSTRUCTIONS,
    _build_teaching_guide,
    continue_board_teaching,
    start_auto_board_teaching,
)
from app.services.board_teaching_turn_decision import BoardTeachingTurnDecision
from app.services.codex_app_server import CodexTurnResult
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "user_auto_board_teaching"
NESTED_BOARD = """# 第一节 会计概述

## 三、会计人员职业道德和会计工作责任

本部分说明职业道德与责任体系。

### （一）会计人员职业道德的内容

第一部分正文。

### （二）中国注册会计师职业道德

第二部分正文。

### （三）会计工作责任

责任体系引言。

### 1. 全面落实单位会计主体责任

第一项正文。

### 2. 明确单位相关人员会计工作责任

第二项正文。

### 3. 强化会计服务机构相关责任

第三项正文。
"""


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Nested board")
    document = build_document(
        title=lesson.board_document.title,
        content_text=NESTED_BOARD,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    document = document.model_copy(
        update={
            "content_text": re.sub(r"^#{1,6}\s+", "", NESTED_BOARD, flags=re.MULTILINE),
        }
    )
    lesson.board_document = document
    lesson.history_graph.commits[0].snapshot = document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


@pytest.fixture
def teaching_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setenv("OPENCLASS_CODEX_WORKSPACE_ROOT", str(tmp_path / "codex-workspaces"))
    monkeypatch.setattr(codex_chat, "delete_codex_thread", lambda _thread_id, **_kwargs: None)
    monkeypatch.setattr(codex_chat, "generate_follow_up_suggestions", lambda **_kwargs: [])
    return store


def test_heading_tree_uses_nested_title_scales_without_repeating_child_content() -> None:
    guide = _build_teaching_guide(
        document_id="document_nested",
        board_title="Nested board",
        board_text=NESTED_BOARD,
        target_heading="三、会计人员职业道德和会计工作责任",
    )

    assert [plan.heading for plan in guide.section_plans] == [
        "（一）会计人员职业道德的内容",
        "（二）中国注册会计师职业道德",
        "（三）会计工作责任",
        "1. 全面落实单位会计主体责任",
        "2. 明确单位相关人员会计工作责任",
        "3. 强化会计服务机构相关责任",
    ]
    parent_plan = guide.section_plans[2]
    first_child_plan = guide.section_plans[3]
    assert "责任体系引言" in parent_plan.board_excerpt
    assert "全面落实单位会计主体责任" not in parent_plan.board_excerpt
    assert first_child_plan.heading_path == [
        "第一节 会计概述",
        "三、会计人员职业道德和会计工作责任",
        "（三）会计工作责任",
        "1. 全面落实单位会计主体责任",
    ]
    assert guide.sequence_mode == "heading_tree_preorder"


def test_natural_ordered_teaching_request_starts_at_target_and_continues_one_title(
    monkeypatch: pytest.MonkeyPatch,
    teaching_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(teaching_store)
    codex_turn_calls: list[str] = []
    streamed_deltas: list[str] = []

    def fake_parse(_self, **kwargs):
        schema = kwargs["schema"]
        payload = json.loads(kwargs["user_prompt"])
        if schema is BoardTeachingTurnDecision:
            message = payload["user_message"]
            parsed = BoardTeachingTurnDecision(
                action="continue" if message == "继续" else "start",
                target_heading=(
                    "" if message == "继续" else "三、会计人员职业道德和会计工作责任"
                ),
                reason="The learner requested ordered title teaching.",
            )
        elif schema is BoardExplanationDirective:
            excerpt = payload["target_excerpt"]
            parsed = BoardExplanationDirective(
                status="approved",
                target_summary=excerpt.splitlines()[0].lstrip("# "),
                target_excerpt=excerpt,
                teaching_instruction="Explain only this title scope.",
                constraints=["Do not include the next title."],
            )
        elif schema is codex_chat._StructuredExistingBoardTurn:
            parsed = schema(
                chatbot_message="Handled by the Pi document route.",
                board_markdown=payload["board_markdown"] + "\n\n## 新增板书小节\n",
            )
        else:
            directive = payload["board_explanation_directive"]
            parsed = schema(
                chatbot_message=f"讲解：{directive['target_summary']}",
                follow_up_suggestions=["继续"],
            )
        return SimpleNamespace(output_parsed=parsed, activity=[])

    def fake_codex_turn(**_kwargs) -> CodexTurnResult:
        codex_turn_calls.append("called")
        return CodexTurnResult(
            thread_id="thread_document_request",
            turn_id="turn_document_request",
            final_response="Handled by the ordinary Codex route.",
        )

    def fake_complete_text(_self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        message = f"讲解：{payload['board_explanation_directive']['target_summary']}"
        if kwargs["on_text_delta"] is not None:
            kwargs["on_text_delta"](message[:3])
            kwargs["on_text_delta"](message[3:])
        return SimpleNamespace(output_text=message, activity=[])

    monkeypatch.setattr(
        "app.services.pi_agent_runtime.PiTextClient.parse",
        fake_parse,
    )
    monkeypatch.setattr(
        "app.services.pi_agent_runtime.PiTextClient.complete_text",
        fake_complete_text,
    )
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_codex_turn)

    started = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="讲解第三部分"),
        user_id=TEST_USER_ID,
        on_delta=streamed_deltas.append,
    )
    assert started.chatbot_message == "讲解：（一）会计人员职业道德的内容"
    assert "".join(streamed_deltas) == started.chatbot_message
    assert started.teaching_progress is not None
    assert started.teaching_progress.section_index == 0
    assert started.teaching_progress.current_heading_path[-1] == "（一）会计人员职业道德的内容"
    assert codex_turn_calls == []

    continued = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续"),
        user_id=TEST_USER_ID,
    )
    assert continued.chatbot_message == "讲解：（二）中国注册会计师职业道德"
    assert continued.teaching_progress is not None
    assert continued.teaching_progress.section_index == 1
    assert codex_turn_calls == []

    mutation = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续生成板书的下一节"),
        user_id=TEST_USER_ID,
    )
    assert mutation.chatbot_message == "Handled by the Pi document route."
    assert codex_turn_calls == []

    saved_lesson = teaching_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    teaching_commits = [
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "board_directed_explanation"
    ]
    assert [commit.metadata["user_message"] for commit in teaching_commits] == [
        "讲解第三部分",
        "继续",
    ]


def test_chatbot_explanation_prompt_uses_a_warm_teacher_opening_without_a_template() -> None:
    assert "warmth, energy, and attentiveness of an excellent teacher" in (
        CHATBOT_EXPLANATION_INSTRUCTIONS
    )
    assert "never reuse a fixed opening template" in CHATBOT_EXPLANATION_INSTRUCTIONS
    assert "For later units" in CHATBOT_EXPLANATION_INSTRUCTIONS
    assert "Never open with a scope disclaimer" in CHATBOT_EXPLANATION_INSTRUCTIONS
    assert "not the complete content" in CHATBOT_EXPLANATION_INSTRUCTIONS
    assert "法语" not in CHATBOT_EXPLANATION_INSTRUCTIONS


def test_teaching_context_marks_only_the_first_unit_as_the_opening(
    teaching_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(teaching_store)

    class RecordingAdapter:
        def __init__(self) -> None:
            self.teaching_contexts: list[dict[str, object]] = []

        def parse_structured(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            excerpt = payload["target_excerpt"]
            parsed = BoardExplanationDirective(
                status="approved",
                target_summary=excerpt.splitlines()[0].lstrip("# "),
                target_excerpt=excerpt,
                teaching_instruction="Explain only this title scope.",
                constraints=["Do not include the next title."],
            )
            return SimpleNamespace(output_parsed=parsed, activity=[])

        def explain_from_directive(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            self.teaching_contexts.append(payload["teaching_context"])
            parsed = kwargs["schema"](
                chatbot_message="A teacher-like explanation.",
                follow_up_suggestions=["Continue"],
            )
            return SimpleNamespace(output_parsed=parsed, activity=[])

    adapter = RecordingAdapter()
    started = start_auto_board_teaching(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        adapter=adapter,
    )
    continued = continue_board_teaching(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        adapter=adapter,
        restart=False,
    )

    assert started.status == "succeeded"
    assert continued.status == "succeeded"
    assert adapter.teaching_contexts == [
        {"is_opening_unit": True, "has_next_unit": True},
        {"is_opening_unit": False, "has_next_unit": True},
    ]

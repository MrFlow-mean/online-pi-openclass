from __future__ import annotations

from typing import Callable

from app.models import AgentActivityEvent, ChatRequest, ChatResponse, ConversationTurn
from app.services.codex_chat import document_ai_edit_request as _document_ai_edit_request
from app.services.codex_chat import process_codex_chat_on_lesson
from app.services.history import bind_commit_metadata
from app.services.lesson_title import maybe_generate_lesson_title


def process_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    on_delta: Callable[[str], None] | None = None,
    on_requirement_update: Callable[[dict[str, object]], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    commit_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    with bind_commit_metadata({**(commit_metadata or {}), **_chat_edit_metadata(request)}):
        response = process_codex_chat_on_lesson(
            lesson_id,
            request,
            user_id=user_id,
            on_delta=on_delta,
            on_requirement_update=on_requirement_update,
            on_agent_activity=on_agent_activity,
            is_cancelled=is_cancelled,
        )
    return maybe_generate_lesson_title(
        lesson_id,
        request,
        response,
        user_id=user_id,
    )


def _chat_edit_metadata(request: ChatRequest) -> dict[str, object]:
    if not request.chat_edit_source_commit_id:
        return {}
    return {
        "chat_edit_source_commit_id": request.chat_edit_source_commit_id,
        "chat_edit_base_commit_id": request.chat_edit_base_commit_id,
        "chat_edit_original_message": request.chat_edit_original_message or "",
    }


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    return _document_ai_edit_request(
        lesson_id,
        instruction,
        selection_text,
        conversation,
        user_id=user_id,
    )

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models import (
    AIProposalLessonMergeSessionRequest,
    CoursePackageView,
    CreateLessonMergeSessionRequest,
    LessonMergeSession,
    LessonMergeSessionView,
    RecomputeLessonMergeSessionRequest,
    SubmitLessonMergeSessionRequest,
    UpdateLessonMergeSessionRequest,
    UserView,
)
from app.routers.auth import current_user
from app.services.lesson_merge import (
    LessonMergeConflictError,
    LessonMergeError,
    LessonMergeStaleError,
    abandon_merge_session,
    create_merge_session,
    merge_session_view,
    submit_merge_session,
    update_merge_session,
)
from app.services.lesson_merge_ai import propose_ai_merge
from app.services.lesson_contribution import (
    LessonContributionError,
    complete_lesson_contribution_merge,
    persist_recomputed_contribution_merge,
)
from app.services.workspace_state import (
    find_lesson_package,
    get_store,
    load_active_merge_session_for_user,
    load_merge_session_for_user,
    load_workspace_for_user,
    load_workspace_for_user_with_revision,
    package_view_for_lesson,
    save_merge_session_for_user,
    save_merge_session_for_user_if_version,
    save_workspace_and_merge_session_for_user_if_revision,
)


router = APIRouter()


def _session_for_lesson(user_id: str, lesson_id: str, session_id: str) -> LessonMergeSession:
    session = load_merge_session_for_user(user_id, session_id)
    if session is None or session.lesson_id != lesson_id:
        raise HTTPException(status_code=404, detail="合并草案不存在")
    return session


def _merge_http_error(exc: LessonMergeError) -> HTTPException:
    if isinstance(exc, (LessonMergeStaleError, LessonMergeConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/api/lessons/{lesson_id}/merge-sessions",
    response_model=LessonMergeSessionView,
)
def create_lesson_merge_session(
    lesson_id: str,
    request: CreateLessonMergeSessionRequest,
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    active = load_active_merge_session_for_user(user.id, lesson_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "这节课已有未结束的合并草案",
                "merge_session_id": active.id,
            },
        )
    try:
        session = create_merge_session(
            lesson,
            owner_user_id=user.id,
            source_branch_name=request.source_branch_name,
            mode=request.mode,
            ai_model=request.text_model,
        )
    except LessonMergeError as exc:
        raise _merge_http_error(exc) from exc
    save_merge_session_for_user(session)
    return merge_session_view(session)


@router.get(
    "/api/lessons/{lesson_id}/merge-sessions/active",
    response_model=LessonMergeSessionView | None,
)
def get_active_lesson_merge_session(
    lesson_id: str,
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView | None:
    workspace = load_workspace_for_user(user.id)
    find_lesson_package(workspace, lesson_id)
    session = load_active_merge_session_for_user(user.id, lesson_id)
    return merge_session_view(session) if session is not None else None


@router.get(
    "/api/lessons/{lesson_id}/merge-sessions/{session_id}",
    response_model=LessonMergeSessionView,
)
def get_lesson_merge_session(
    lesson_id: str,
    session_id: str,
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView:
    return merge_session_view(_session_for_lesson(user.id, lesson_id, session_id))


@router.patch(
    "/api/lessons/{lesson_id}/merge-sessions/{session_id}",
    response_model=LessonMergeSessionView,
)
def update_lesson_merge_session(
    lesson_id: str,
    session_id: str,
    request: UpdateLessonMergeSessionRequest,
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView:
    session = _session_for_lesson(user.id, lesson_id, session_id)
    try:
        update_merge_session(
            session,
            expected_version=request.expected_version,
            resolutions=request.resolutions,
            draft_document=request.draft_document,
            draft_runtime=request.draft_runtime,
        )
    except LessonMergeError as exc:
        raise _merge_http_error(exc) from exc
    save_merge_session_for_user_if_version(session, expected_version=request.expected_version)
    return merge_session_view(session)


@router.delete(
    "/api/lessons/{lesson_id}/merge-sessions/{session_id}",
    response_model=LessonMergeSessionView,
)
def delete_lesson_merge_session(
    lesson_id: str,
    session_id: str,
    expected_version: int = Query(ge=1),
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView:
    session = _session_for_lesson(user.id, lesson_id, session_id)
    if session.version != expected_version:
        raise HTTPException(status_code=409, detail="合并草案已更新，请刷新后重试")
    try:
        abandon_merge_session(session)
    except LessonMergeError as exc:
        raise _merge_http_error(exc) from exc
    save_merge_session_for_user_if_version(session, expected_version=expected_version)
    return merge_session_view(session)


@router.post(
    "/api/lessons/{lesson_id}/merge-sessions/{session_id}/recompute",
    response_model=LessonMergeSessionView,
)
def recompute_lesson_merge_session(
    lesson_id: str,
    session_id: str,
    request: RecomputeLessonMergeSessionRequest,
    user: UserView = Depends(current_user),
) -> LessonMergeSessionView:
    old_session = _session_for_lesson(user.id, lesson_id, session_id)
    if old_session.version != request.expected_version:
        raise HTTPException(status_code=409, detail="合并草案已更新，请刷新后重试")
    workspace, workspace_revision = load_workspace_for_user_with_revision(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    try:
        session = create_merge_session(
            lesson,
            owner_user_id=user.id,
            source_branch_name=old_session.source_branch_name,
            mode=request.mode or old_session.mode,
            ai_model=request.text_model or old_session.ai_model,
            supersedes_session_id=old_session.id,
        )
    except LessonMergeError as exc:
        raise _merge_http_error(exc) from exc
    if old_session.audit.get("lesson_contribution_id"):
        try:
            persist_recomputed_contribution_merge(
                get_store(),
                user=user,
                workspace=workspace,
                expected_workspace_revision=workspace_revision,
                old_session=old_session,
                new_session=session,
            )
        except LessonContributionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        save_merge_session_for_user(session)
    return merge_session_view(session)


@router.post(
    "/api/lessons/{lesson_id}/merge-sessions/{session_id}/submit",
    response_model=CoursePackageView,
)
def submit_lesson_merge_session(
    lesson_id: str,
    session_id: str,
    request: SubmitLessonMergeSessionRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, revision = load_workspace_for_user_with_revision(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    session = _session_for_lesson(user.id, lesson_id, session_id)
    try:
        submit_merge_session(lesson, session, expected_version=request.expected_version)
    except LessonMergeStaleError as exc:
        if session.version > request.expected_version:
            save_merge_session_for_user_if_version(
                session,
                expected_version=request.expected_version,
            )
        raise _merge_http_error(exc) from exc
    except LessonMergeError as exc:
        raise _merge_http_error(exc) from exc
    if session.audit.get("lesson_contribution_id"):
        try:
            complete_lesson_contribution_merge(
                get_store(),
                user=user,
                workspace=workspace,
                expected_workspace_revision=revision,
                session=session,
            )
        except LessonContributionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        save_workspace_and_merge_session_for_user_if_revision(
            user.id,
            workspace,
            session,
            expected_revision=revision,
        )
    return package_view_for_lesson(workspace, package, lesson.id)


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ai_merge_events(
    lesson_id: str,
    session_id: str,
    request: AIProposalLessonMergeSessionRequest,
    *,
    user_id: str,
) -> Iterator[str]:
    events: queue.Queue[tuple[str, object] | None] = queue.Queue()
    cancelled = threading.Event()

    def worker() -> None:
        session: LessonMergeSession | None = None
        try:
            session = _session_for_lesson(user_id, lesson_id, session_id)
            propose_ai_merge(
                session,
                expected_version=request.expected_version,
                is_cancelled=cancelled.is_set,
                on_activity=lambda activity: events.put(
                    ("agent_activity", activity.model_dump(mode="json"))
                ),
            )
            save_merge_session_for_user_if_version(
                session,
                expected_version=request.expected_version,
            )
            events.put(("final", merge_session_view(session).model_dump(mode="json")))
        except Exception as exc:
            if session is not None and session.version > request.expected_version:
                try:
                    get_store().save_merge_session_for_user_if_version(
                        session,
                        expected_version=request.expected_version,
                    )
                except Exception:
                    pass
            events.put(("error", {"message": str(exc)}))
        finally:
            events.put(None)

    thread = threading.Thread(target=worker, name=f"lesson-merge-{session_id}", daemon=True)
    thread.start()
    try:
        while True:
            item = events.get()
            if item is None:
                break
            yield _sse_event(*item)
    finally:
        cancelled.set()


@router.post("/api/lessons/{lesson_id}/merge-sessions/{session_id}/ai-proposal")
def propose_lesson_merge_with_ai(
    lesson_id: str,
    session_id: str,
    request: AIProposalLessonMergeSessionRequest,
    user: UserView = Depends(current_user),
) -> StreamingResponse:
    _session_for_lesson(user.id, lesson_id, session_id)
    return StreamingResponse(
        _ai_merge_events(lesson_id, session_id, request, user_id=user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

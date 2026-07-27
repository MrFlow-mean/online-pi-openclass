from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.models import (
    CreateLessonContributionRequest,
    LessonContributionCommentRequest,
    LessonContributionVersionRequest,
    LessonContributionView,
    UpdateLessonContributionRevisionRequest,
    UserView,
)
from app.routers.auth import current_user
from app.services.lesson_contribution import (
    LessonContributionConflictError,
    LessonContributionError,
    LessonContributionNotFoundError,
    LessonContributionPermissionError,
    add_lesson_contribution_comment,
    close_lesson_contribution,
    contribution_view,
    create_lesson_contribution,
    delete_lesson_contribution_comment,
    edit_lesson_contribution_comment,
    reopen_lesson_contribution,
    return_lesson_contribution_for_changes,
    start_lesson_contribution_merge,
    update_lesson_contribution_revision,
)
from app.services.workspace_state import (
    find_lesson_package,
    get_store,
    load_workspace_for_user,
    load_workspace_for_user_with_revision,
)


router = APIRouter()


def _http_error(exc: LessonContributionError) -> HTTPException:
    if isinstance(exc, LessonContributionNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, LessonContributionPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, LessonContributionConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _view(contribution_id: str, *, user: UserView | None) -> LessonContributionView:
    store = get_store()
    bundle = store.load_lesson_contribution(contribution_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="改进方案不存在。")
    try:
        return contribution_view(store, bundle, viewer=user)
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/lessons/{personal_lesson_id}/contributions",
    response_model=LessonContributionView,
)
def create_contribution(
    personal_lesson_id: str,
    request: CreateLessonContributionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    workspace = load_workspace_for_user(user.id)
    _, personal_lesson = find_lesson_package(workspace, personal_lesson_id)
    try:
        return create_lesson_contribution(
            get_store(),
            user=user,
            personal_lesson=personal_lesson,
            title=request.title,
            description=request.description,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.get("/api/contributions", response_model=list[LessonContributionView])
def list_contributions(
    role: Literal["received", "submitted"] = Query(),
    status: Literal["open", "merge_draft", "merged", "closed"] | None = Query(default=None),
    user: UserView = Depends(current_user),
) -> list[LessonContributionView]:
    store = get_store()
    return [
        contribution_view(store, bundle, viewer=user)
        for bundle in store.list_lesson_contributions(user_id=user.id, role=role, status=status)
    ]


@router.get("/api/public/contributions/{contribution_id}", response_model=LessonContributionView)
def get_public_contribution(contribution_id: str) -> LessonContributionView:
    return _view(contribution_id, user=None)


@router.get("/api/contributions/{contribution_id}", response_model=LessonContributionView)
def get_contribution(
    contribution_id: str,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    return _view(contribution_id, user=user)


@router.post(
    "/api/contributions/{contribution_id}/revisions",
    response_model=LessonContributionView,
)
def update_contribution_revision(
    contribution_id: str,
    request: UpdateLessonContributionRevisionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    bundle = get_store().load_lesson_contribution(contribution_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="改进方案不存在。")
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, bundle[0].contributor_lesson_id)
    try:
        return update_lesson_contribution_revision(
            get_store(),
            contribution_id,
            user=user,
            personal_lesson=lesson,
            expected_version=request.expected_version,
            title=request.title,
            description=request.description,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/contributions/{contribution_id}/comments",
    response_model=LessonContributionView,
)
def add_comment(
    contribution_id: str,
    request: LessonContributionCommentRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return add_lesson_contribution_comment(
            get_store(),
            contribution_id,
            user=user,
            expected_version=request.expected_version,
            body=request.body,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/api/contributions/{contribution_id}/comments/{comment_id}",
    response_model=LessonContributionView,
)
def edit_comment(
    contribution_id: str,
    comment_id: str,
    request: LessonContributionCommentRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return edit_lesson_contribution_comment(
            get_store(),
            contribution_id,
            comment_id,
            user=user,
            expected_version=request.expected_version,
            body=request.body,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/api/contributions/{contribution_id}/comments/{comment_id}",
    response_model=LessonContributionView,
)
def delete_comment(
    contribution_id: str,
    comment_id: str,
    request: LessonContributionVersionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return delete_lesson_contribution_comment(
            get_store(),
            contribution_id,
            comment_id,
            user=user,
            expected_version=request.expected_version,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post("/api/contributions/{contribution_id}/close", response_model=LessonContributionView)
def close_contribution(
    contribution_id: str,
    request: LessonContributionVersionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return close_lesson_contribution(
            get_store(), contribution_id, user=user, expected_version=request.expected_version
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post("/api/contributions/{contribution_id}/reopen", response_model=LessonContributionView)
def reopen_contribution(
    contribution_id: str,
    request: LessonContributionVersionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return reopen_lesson_contribution(
            get_store(), contribution_id, user=user, expected_version=request.expected_version
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/contributions/{contribution_id}/merge/start",
    response_model=LessonContributionView,
)
def start_contribution_merge(
    contribution_id: str,
    request: LessonContributionVersionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    bundle = get_store().load_lesson_contribution(contribution_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="改进方案不存在。")
    workspace, workspace_revision = load_workspace_for_user_with_revision(user.id)
    _, source_lesson = find_lesson_package(workspace, bundle[0].source_lesson_id)
    try:
        return start_lesson_contribution_merge(
            get_store(),
            contribution_id,
            user=user,
            workspace=workspace,
            source_lesson=source_lesson,
            expected_workspace_revision=workspace_revision,
            expected_version=request.expected_version,
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/contributions/{contribution_id}/merge/return",
    response_model=LessonContributionView,
)
def return_contribution_merge(
    contribution_id: str,
    request: LessonContributionVersionRequest,
    user: UserView = Depends(current_user),
) -> LessonContributionView:
    try:
        return return_lesson_contribution_for_changes(
            get_store(), contribution_id, user=user, expected_version=request.expected_version
        )
    except LessonContributionError as exc:
        raise _http_error(exc) from exc

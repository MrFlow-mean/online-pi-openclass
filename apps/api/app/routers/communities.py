from __future__ import annotations

from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import (
    CommunityAcceptedAnswerRequest,
    CommunityAcceptedAnswerView,
    CommunityAnswerCreate,
    CommunityAnswerView,
    CommunityAnswerVoteView,
    CommunityCommentCreate,
    CommunityCommentView,
    CommunityContentUpdate,
    CommunityFollowView,
    CommunityIntegrationView,
    CommunityPostCreate,
    CommunityPostDetail,
    CommunityPostUpdate,
    CommunityPostView,
    CommunitySpaceCreate,
    CommunitySpaceView,
    CommunityVoteRequest,
    CommunityVoteView,
    UserView,
)
from app.routers.auth import current_user
from app.services.community_store import (
    CommunityConflictError,
    CommunityNotFoundError,
    CommunityStore,
    CommunityValidationError,
)
from app.services.community_adapter import community_adapter
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api/community", tags=["community"])
community_store = CommunityStore(DATABASE_PATH)


def _registered_user(user: UserView = Depends(current_user)) -> UserView:
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="登录后才能参与社区")
    return user


def _raise_http_error(exc: ValueError) -> NoReturn:
    if isinstance(exc, CommunityNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, CommunityConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, CommunityValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("/integration", response_model=CommunityIntegrationView)
def get_community_integration() -> CommunityIntegrationView:
    return community_adapter.integration()


@router.get("/spaces", response_model=list[CommunitySpaceView])
def list_community_spaces(
    sort: Literal["active", "new", "popular"] = "active",
) -> list[CommunitySpaceView]:
    return community_store.list_spaces(sort=sort)


@router.post("/spaces", response_model=CommunitySpaceView, status_code=201)
def create_community_space(
    payload: CommunitySpaceCreate,
    user: UserView = Depends(_registered_user),
) -> CommunitySpaceView:
    try:
        return community_store.create_space(payload, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.get("/spaces/{slug}", response_model=CommunitySpaceView)
def get_community_space(slug: str) -> CommunitySpaceView:
    try:
        return community_store.get_space(slug)
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/spaces/{slug}/follow", response_model=CommunityFollowView)
def follow_community_space(
    slug: str,
    user: UserView = Depends(_registered_user),
) -> CommunityFollowView:
    try:
        community_id, follower_count = community_store.set_follow(
            slug,
            user_id=user.id,
            following=True,
        )
        return CommunityFollowView(
            community_id=community_id,
            following=True,
            follower_count=follower_count,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.delete("/spaces/{slug}/follow", response_model=CommunityFollowView)
def unfollow_community_space(
    slug: str,
    user: UserView = Depends(_registered_user),
) -> CommunityFollowView:
    try:
        community_id, follower_count = community_store.set_follow(
            slug,
            user_id=user.id,
            following=False,
        )
        return CommunityFollowView(
            community_id=community_id,
            following=False,
            follower_count=follower_count,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.get("/posts", response_model=list[CommunityPostView])
def list_community_posts(
    community: str = "",
    tag: str = "",
    q: str = "",
    sort: Literal["recent", "hot", "unanswered"] = "recent",
    limit: int = Query(default=50, ge=1, le=100),
    user: UserView = Depends(current_user),
) -> list[CommunityPostView]:
    return community_store.list_posts(
        community_slug=community,
        tag=tag,
        query=q,
        sort=sort,
        viewer_user_id=None if user.role == "guest" else user.id,
        limit=limit,
    )


@router.post("/posts", response_model=CommunityPostView, status_code=201)
def create_community_post(
    payload: CommunityPostCreate,
    user: UserView = Depends(_registered_user),
) -> CommunityPostView:
    try:
        return community_store.create_post(payload, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/posts/{post_id}", response_model=CommunityPostView)
def update_community_post(
    post_id: str,
    payload: CommunityPostUpdate,
    user: UserView = Depends(_registered_user),
) -> CommunityPostView:
    try:
        return community_store.update_post(post_id, payload, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.delete("/posts/{post_id}", status_code=204)
def delete_community_post(
    post_id: str,
    user: UserView = Depends(_registered_user),
) -> None:
    try:
        community_store.delete_post(post_id, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.get("/posts/{post_id}", response_model=CommunityPostDetail)
def get_community_post(
    post_id: str,
    user: UserView = Depends(current_user),
) -> CommunityPostDetail:
    try:
        return community_store.get_post(
            post_id,
            viewer_user_id=None if user.role == "guest" else user.id,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.post(
    "/posts/{post_id}/answers",
    response_model=CommunityAnswerView,
    status_code=201,
)
def create_community_answer(
    post_id: str,
    payload: CommunityAnswerCreate,
    user: UserView = Depends(_registered_user),
) -> CommunityAnswerView:
    try:
        return community_store.add_answer(post_id, body=payload.body, user=user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/answers/{answer_id}", response_model=CommunityAnswerView)
def update_community_answer(
    answer_id: str,
    payload: CommunityContentUpdate,
    user: UserView = Depends(_registered_user),
) -> CommunityAnswerView:
    try:
        return community_store.update_answer(answer_id, body=payload.body, user=user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.delete("/answers/{answer_id}", status_code=204)
def delete_community_answer(
    answer_id: str,
    user: UserView = Depends(_registered_user),
) -> None:
    try:
        community_store.delete_answer(answer_id, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/answers/{answer_id}/vote", response_model=CommunityAnswerVoteView)
def vote_on_community_answer(
    answer_id: str,
    payload: CommunityVoteRequest,
    user: UserView = Depends(_registered_user),
) -> CommunityAnswerVoteView:
    try:
        viewer_vote, vote_score = community_store.set_answer_vote(
            answer_id,
            user_id=user.id,
            value=payload.value,
        )
        return CommunityAnswerVoteView(
            answer_id=answer_id,
            viewer_vote=viewer_vote,
            vote_score=vote_score,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.put(
    "/posts/{post_id}/accepted-answer",
    response_model=CommunityAcceptedAnswerView,
)
def accept_community_answer(
    post_id: str,
    payload: CommunityAcceptedAnswerRequest,
    user: UserView = Depends(_registered_user),
) -> CommunityAcceptedAnswerView:
    try:
        accepted_answer_id = community_store.set_accepted_answer(
            post_id,
            answer_id=payload.answer_id,
            user_id=user.id,
        )
        return CommunityAcceptedAnswerView(
            post_id=post_id,
            accepted_answer_id=accepted_answer_id,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommunityCommentView,
    status_code=201,
)
def create_community_comment(
    post_id: str,
    payload: CommunityCommentCreate,
    user: UserView = Depends(_registered_user),
) -> CommunityCommentView:
    try:
        return community_store.add_comment(
            post_id,
            body=payload.body,
            parent_comment_id=payload.parent_comment_id,
            user=user,
        )
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/comments/{comment_id}", response_model=CommunityCommentView)
def update_community_comment(
    comment_id: str,
    payload: CommunityContentUpdate,
    user: UserView = Depends(_registered_user),
) -> CommunityCommentView:
    try:
        return community_store.update_comment(comment_id, body=payload.body, user=user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_community_comment(
    comment_id: str,
    user: UserView = Depends(_registered_user),
) -> None:
    try:
        community_store.delete_comment(comment_id, user)
    except ValueError as exc:
        _raise_http_error(exc)


@router.put("/posts/{post_id}/vote", response_model=CommunityVoteView)
def vote_on_community_post(
    post_id: str,
    payload: CommunityVoteRequest,
    user: UserView = Depends(_registered_user),
) -> CommunityVoteView:
    try:
        viewer_vote, vote_score = community_store.set_vote(
            post_id,
            user_id=user.id,
            value=payload.value,
        )
        return CommunityVoteView(
            post_id=post_id,
            viewer_vote=viewer_vote,
            vote_score=vote_score,
        )
    except ValueError as exc:
        _raise_http_error(exc)

from __future__ import annotations

from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import (
    CommunityCommentCreate,
    CommunityCommentView,
    CommunityFollowView,
    CommunityPostCreate,
    CommunityPostDetail,
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
) -> list[CommunityPostView]:
    return community_store.list_posts(
        community_slug=community,
        tag=tag,
        query=q,
        sort=sort,
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


@router.get("/posts/{post_id}", response_model=CommunityPostDetail)
def get_community_post(post_id: str) -> CommunityPostDetail:
    try:
        return community_store.get_post(post_id)
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

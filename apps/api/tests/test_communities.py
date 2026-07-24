from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.models import CommunityPostCreate, CommunitySpaceCreate, UserView
from app.routers import communities
from app.services.community_store import (
    CommunityConflictError,
    CommunityNotFoundError,
    CommunityStore,
    CommunityValidationError,
)


def _user(user_id: str = "user_author", *, role: str = "user") -> UserView:
    return UserView(
        id=user_id,
        email=f"{user_id}@example.com",
        role=role,
        display_name=f"Learner {user_id}",
        created_at="2026-07-24T00:00:00+00:00",
    )


def _store(tmp_path: Path) -> CommunityStore:
    return CommunityStore(tmp_path / "openclass.sqlite3")


def _space(store: CommunityStore, *, name: str = "学习方法"):
    return store.create_space(
        CommunitySpaceCreate(name=name, description="交流方法、问题和学习成果"),
        _user(),
    )


def _post(
    store: CommunityStore,
    *,
    community_slug: str,
    title: str = "如何验证自己的理解是否可靠？",
    post_type: str = "question",
    tags: list[str] | None = None,
):
    return store.create_post(
        CommunityPostCreate(
            community_slug=community_slug,
            post_type=post_type,
            title=title,
            body="我希望比较几种可重复使用的验证方法。",
            tags=tags or ["理解检查", "学习方法"],
        ),
        _user(),
    )


def test_space_and_post_creation_are_generic_and_searchable(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store, name="概念理解")

    post = _post(
        store,
        community_slug=space.slug,
        tags=["#理解检查", "理解检查", "概念联系"],
    )

    assert space.slug == "概念理解"
    assert post.community_slug == space.slug
    assert post.tags == ["理解检查", "概念联系"]
    assert [item.id for item in store.list_posts(tag="#理解检查")] == [post.id]
    assert [item.id for item in store.list_posts(query="可重复")] == [post.id]
    assert store.get_space(space.slug).post_count == 1


def test_space_slug_conflicts_are_reported(tmp_path) -> None:
    store = _store(tmp_path)
    _space(store, name="共同学习")

    with pytest.raises(CommunityConflictError, match="已存在"):
        _space(store, name="共同学习")


def test_comments_support_threads_without_cross_post_parents(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store)
    first_post = _post(store, community_slug=space.slug)
    second_post = _post(
        store,
        community_slug=space.slug,
        title="怎样整理一段学习过程？",
        post_type="discussion",
    )
    first_comment = store.add_comment(
        first_post.id,
        body="可以先写出自己的判断依据。",
        parent_comment_id=None,
        user=_user("user_reply"),
    )
    reply = store.add_comment(
        first_post.id,
        body="这样也方便其他人指出依据中的缺口。",
        parent_comment_id=first_comment.id,
        user=_user("user_second_reply"),
    )

    assert reply.parent_comment_id == first_comment.id
    assert store.get_post(first_post.id).post.comment_count == 2

    with pytest.raises(CommunityValidationError, match="不属于当前帖子"):
        store.add_comment(
            second_post.id,
            body="错误的跨帖回复",
            parent_comment_id=first_comment.id,
            user=_user("user_reply"),
        )


def test_votes_and_follows_are_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store)
    post = _post(store, community_slug=space.slug)

    assert store.set_vote(post.id, user_id="user_voter", value=1) == (1, 1)
    assert store.set_vote(post.id, user_id="user_voter", value=1) == (1, 1)
    assert store.set_vote(post.id, user_id="user_voter", value=-1) == (-1, -1)
    assert store.set_vote(post.id, user_id="user_voter", value=0) == (0, 0)
    assert store.set_follow(space.slug, user_id="user_voter", following=True)[1] == 1
    assert store.set_follow(space.slug, user_id="user_voter", following=True)[1] == 1
    assert store.set_follow(space.slug, user_id="user_voter", following=False)[1] == 0


def test_question_answers_support_votes_acceptance_and_reputation(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store)
    question = _post(store, community_slug=space.slug)
    answer = store.add_answer(
        question.id,
        body="先独立复述，再用新例子和反例检查理解边界。",
        user=_user("user_answerer"),
    )

    assert store.get_post(question.id).post.answer_count == 1
    assert store.set_answer_vote(answer.id, user_id="user_voter", value=1) == (1, 1)
    assert store.set_accepted_answer(
        question.id,
        answer_id=answer.id,
        user_id="user_author",
    ) == answer.id

    detail = store.get_post(question.id, viewer_user_id="user_voter")
    assert detail.post.accepted_answer_id == answer.id
    assert detail.answers[0].is_accepted is True
    assert detail.answers[0].viewer_vote == 1
    assert detail.answers[0].author_reputation == 20

    assert store.set_accepted_answer(
        question.id,
        answer_id=None,
        user_id="user_author",
    ) is None
    assert store.get_post(question.id).post.accepted_answer_id is None


def test_answer_permissions_and_question_boundaries_are_enforced(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store)
    question = _post(store, community_slug=space.slug)
    other_question = _post(
        store,
        community_slug=space.slug,
        title="怎样判断一个解释是否足够清楚？",
    )
    discussion = _post(
        store,
        community_slug=space.slug,
        title="分享一次共同整理资料的过程",
        post_type="discussion",
    )
    answer = store.add_answer(
        question.id,
        body="把解释交给不了解背景的人复述，并记录他们卡住的位置。",
        user=_user("user_answerer"),
    )

    with pytest.raises(CommunityValidationError, match="问题帖子"):
        store.add_answer(
            discussion.id,
            body="讨论帖不能添加正式答案。",
            user=_user("user_reply"),
        )
    with pytest.raises(CommunityValidationError, match="自己的答案"):
        store.set_answer_vote(answer.id, user_id="user_answerer", value=1)
    with pytest.raises(CommunityValidationError, match="提问者"):
        store.set_accepted_answer(
            question.id,
            answer_id=answer.id,
            user_id="user_reader",
        )
    with pytest.raises(CommunityValidationError, match="不属于当前问题"):
        store.set_accepted_answer(
            other_question.id,
            answer_id=answer.id,
            user_id="user_author",
        )


def test_feed_supports_hot_and_unanswered_views(tmp_path) -> None:
    store = _store(tmp_path)
    space = _space(store)
    answered = _post(store, community_slug=space.slug)
    unanswered = _post(
        store,
        community_slug=space.slug,
        title="如何把讨论沉淀成学习资料？",
    )
    discussion = _post(
        store,
        community_slug=space.slug,
        title="分享一次协作学习的过程",
        post_type="study_note",
    )
    store.add_answer(
        answered.id,
        body="先说明自己的目标和验证方式。",
        user=_user("user_reply"),
    )
    store.set_vote(discussion.id, user_id="user_voter", value=1)

    assert [post.id for post in store.list_posts(sort="unanswered")] == [unanswered.id]
    assert store.list_posts(sort="hot")[0].id == discussion.id


def test_router_keeps_reads_public_and_requires_registered_writers(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(communities, "community_store", store)
    app = FastAPI()
    app.include_router(communities.router)
    client = TestClient(app)

    assert client.get("/api/community/spaces").status_code == 200

    app.dependency_overrides[communities._registered_user] = _user
    response = client.post(
        "/api/community/spaces",
        json={"name": "公开讨论", "description": "任何主题都可复用的讨论空间"},
    )
    assert response.status_code == 201
    assert client.get("/api/community/spaces/公开讨论").json()["post_count"] == 0

    with pytest.raises(HTTPException) as exc_info:
        communities._registered_user(_user("guest_reader", role="guest"))
    assert exc_info.value.status_code == 403


def test_missing_entities_do_not_create_orphan_interactions(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(CommunityNotFoundError):
        store.get_space("missing")
    with pytest.raises(CommunityNotFoundError):
        store.set_vote("missing", user_id="user_voter", value=1)
    with pytest.raises(CommunityNotFoundError):
        store.set_follow("missing", user_id="user_voter", following=True)

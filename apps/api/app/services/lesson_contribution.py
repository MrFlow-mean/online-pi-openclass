from __future__ import annotations

from app.models import (
    Lesson,
    LessonContribution,
    LessonContributionActor,
    LessonContributionEvent,
    LessonContributionRevision,
    LessonContributionRevisionView,
    LessonContributionView,
    LessonContributionViewerPermissions,
    UserView,
    now_iso,
)
from app.services.course_store import SqliteCourseStore
from app.services.history import current_head_commit, get_commit
from app.services.personal_lesson_copy import (
    PUBLIC_SOURCE_COMMIT_ID_KEY,
    PUBLIC_SOURCE_LESSON_ID_KEY,
)


class LessonContributionError(ValueError):
    pass


class LessonContributionNotFoundError(LessonContributionError):
    pass


class LessonContributionPermissionError(LessonContributionError):
    pass


class LessonContributionConflictError(LessonContributionError):
    pass


def create_lesson_contribution(
    store: SqliteCourseStore,
    *,
    user: UserView,
    personal_lesson: Lesson,
    title: str,
    description: str,
) -> LessonContributionView:
    _require_registered(user)
    source_lesson_id, source_commit_id = _fork_lineage(personal_lesson)
    source_record = store.load_lesson_with_owner(source_lesson_id)
    if source_record is None:
        raise LessonContributionConflictError("来源课程已不存在，无法提交改进方案。")
    source_owner_user_id, source_lesson, source_is_public = source_record
    if source_owner_user_id == user.id:
        raise LessonContributionPermissionError("不能向自己的课程提交改进方案。")
    if not source_is_public:
        raise LessonContributionPermissionError("来源课程当前不是公开课程。")
    if store.find_active_lesson_contribution(
        contributor_user_id=user.id,
        contributor_lesson_id=personal_lesson.id,
        source_lesson_id=source_lesson_id,
    ) is not None:
        raise LessonContributionConflictError("这份个人课程已有未结束的改进方案。")

    try:
        base_commit = get_commit(source_lesson, source_commit_id)
    except ValueError as exc:
        raise LessonContributionConflictError("来源课程的派生基线已不可用。") from exc
    proposed_commit = current_head_commit(personal_lesson)
    if _same_document(base_commit.snapshot, proposed_commit.snapshot):
        raise LessonContributionConflictError("当前个人版本还没有可提交的课程内容变更。")

    actor = _actor_for_user(user, fallback="学习者")
    author = LessonContributionActor(
        user_id=source_owner_user_id,
        display_name="课程作者",
    )
    contribution_id = _new_contribution_id()
    revision = LessonContributionRevision(
        contribution_id=contribution_id,
        revision_number=1,
        source_commit_id=base_commit.id,
        contributor_commit_id=proposed_commit.id,
        base_document=base_commit.snapshot,
        proposed_document=proposed_commit.snapshot,
    )
    contribution = LessonContribution(
        id=contribution_id,
        source_lesson_id=source_lesson.id,
        source_owner_user_id=source_owner_user_id,
        contributor_lesson_id=personal_lesson.id,
        contributor_user_id=user.id,
        source_title=source_lesson.title,
        title=_required_text(title, "改进方案标题"),
        description=description.strip(),
        current_revision_id=revision.id,
        source_author=author,
        contributor=actor,
    )
    opened = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="opened",
        actor=actor,
        metadata={"revision_number": 1},
    )
    store.create_lesson_contribution(contribution, revision, opened)
    return contribution_view(
        store,
        (contribution, revision, [opened]),
        viewer=user,
    )


def update_lesson_contribution_revision(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    personal_lesson: Lesson,
    expected_version: int,
    title: str | None = None,
    description: str | None = None,
) -> LessonContributionView:
    _require_registered(user)
    contribution, current_revision, events = _required_bundle(store, contribution_id)
    if contribution.contributor_user_id != user.id or contribution.contributor_lesson_id != personal_lesson.id:
        raise LessonContributionPermissionError("只有提交者可以更新这份改进方案。")
    if contribution.status != "open":
        raise LessonContributionConflictError("当前改进方案已锁定，不能更新提交版本。")
    _require_version(contribution, expected_version)
    source_lesson_id, source_commit_id = _fork_lineage(personal_lesson)
    if source_lesson_id != contribution.source_lesson_id or source_commit_id != current_revision.source_commit_id:
        raise LessonContributionConflictError("个人课程的派生关系与原提交不一致。")
    proposed_commit = current_head_commit(personal_lesson)
    if proposed_commit.id == current_revision.contributor_commit_id:
        raise LessonContributionConflictError("个人课程没有新的版本可以提交。")

    revision = LessonContributionRevision(
        contribution_id=contribution.id,
        revision_number=contribution.current_revision + 1,
        source_commit_id=current_revision.source_commit_id,
        contributor_commit_id=proposed_commit.id,
        base_document=current_revision.base_document,
        proposed_document=proposed_commit.snapshot,
    )
    contribution.current_revision = revision.revision_number
    contribution.current_revision_id = revision.id
    if title is not None:
        contribution.title = _required_text(title, "改进方案标题")
    if description is not None:
        contribution.description = description.strip()
    previous_version = contribution.version
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="revision_submitted",
        actor=_actor_for_user(user, fallback=contribution.contributor.display_name),
        metadata={"revision_number": revision.revision_number},
    )
    _save(store, contribution, expected_version=previous_version, revision=revision, events=[event])
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def add_lesson_contribution_comment(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    expected_version: int,
    body: str,
) -> LessonContributionView:
    _require_registered(user)
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    _require_version(contribution, expected_version)
    previous_version = contribution.version
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="commented",
        actor=_actor_for_user(user, fallback="OpenClass 用户"),
        body=_required_text(body, "评论"),
    )
    _save(store, contribution, expected_version=previous_version, events=[event])
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def edit_lesson_contribution_comment(
    store: SqliteCourseStore,
    contribution_id: str,
    comment_id: str,
    *,
    user: UserView,
    expected_version: int,
    body: str,
) -> LessonContributionView:
    return _change_comment(
        store,
        contribution_id,
        comment_id,
        user=user,
        expected_version=expected_version,
        kind="comment_edited",
        body=_required_text(body, "评论"),
    )


def delete_lesson_contribution_comment(
    store: SqliteCourseStore,
    contribution_id: str,
    comment_id: str,
    *,
    user: UserView,
    expected_version: int,
) -> LessonContributionView:
    return _change_comment(
        store,
        contribution_id,
        comment_id,
        user=user,
        expected_version=expected_version,
        kind="comment_deleted",
        body="",
    )


def close_lesson_contribution(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    expected_version: int,
) -> LessonContributionView:
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    if user.id not in {contribution.source_owner_user_id, contribution.contributor_user_id}:
        raise LessonContributionPermissionError("只有课程作者或提交者可以关闭改进方案。")
    if contribution.status != "open":
        raise LessonContributionConflictError("当前状态不能直接关闭。")
    _require_version(contribution, expected_version)
    previous_version = contribution.version
    contribution.status = "closed"
    contribution.closed_at = now_iso()
    contribution.updated_at = contribution.closed_at
    contribution.version += 1
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="closed",
        actor=_actor_for_user(user, fallback="参与者"),
    )
    _save(store, contribution, expected_version=previous_version, events=[event])
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def reopen_lesson_contribution(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    expected_version: int,
) -> LessonContributionView:
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    if user.id not in {contribution.source_owner_user_id, contribution.contributor_user_id}:
        raise LessonContributionPermissionError("只有课程作者或提交者可以重新打开改进方案。")
    if contribution.status != "closed":
        raise LessonContributionConflictError("只有已关闭的改进方案可以重新打开。")
    _require_version(contribution, expected_version)
    source_record = store.load_lesson_with_owner(contribution.source_lesson_id)
    if source_record is None or not source_record[2]:
        raise LessonContributionConflictError("来源课程当前不可公开协作。")
    active = store.find_active_lesson_contribution(
        contributor_user_id=contribution.contributor_user_id,
        contributor_lesson_id=contribution.contributor_lesson_id,
        source_lesson_id=contribution.source_lesson_id,
    )
    if active is not None and active[0].id != contribution.id:
        raise LessonContributionConflictError("这份个人课程已有另一个未结束的改进方案。")
    previous_version = contribution.version
    contribution.status = "open"
    contribution.closed_at = None
    contribution.updated_at = now_iso()
    contribution.version += 1
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="reopened",
        actor=_actor_for_user(user, fallback="参与者"),
    )
    _save(store, contribution, expected_version=previous_version, events=[event])
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def contribution_view(
    store: SqliteCourseStore,
    bundle: tuple[LessonContribution, LessonContributionRevision, list[LessonContributionEvent]],
    *,
    viewer: UserView | None,
) -> LessonContributionView:
    contribution, revision, events = bundle
    source_record = store.load_lesson_with_owner(contribution.source_lesson_id)
    source_is_public = bool(source_record and source_record[2])
    participant = bool(
        viewer
        and viewer.id in {contribution.source_owner_user_id, contribution.contributor_user_id}
    )
    if not source_is_public and not participant:
        raise LessonContributionNotFoundError("改进方案不存在或已停止公开。")
    registered = bool(viewer and viewer.role != "guest")
    permissions = LessonContributionViewerPermissions(
        can_comment=registered and (source_is_public or participant),
        can_update=registered and viewer.id == contribution.contributor_user_id and contribution.status == "open",
        can_close=participant and contribution.status == "open",
        can_reopen=participant and contribution.status == "closed" and source_is_public,
        can_start_merge=(
            registered
            and viewer.id == contribution.source_owner_user_id
            and contribution.status == "open"
            and source_is_public
        ),
        can_return_for_changes=(
            registered
            and viewer.id == contribution.source_owner_user_id
            and contribution.status == "merge_draft"
        ),
    )
    return LessonContributionView(
        id=contribution.id,
        source_lesson_id=contribution.source_lesson_id,
        source_title=contribution.source_title,
        title=contribution.title,
        description=contribution.description,
        status=contribution.status,
        version=contribution.version,
        current_revision=contribution.current_revision,
        source_author=contribution.source_author,
        contributor=contribution.contributor,
        revision=LessonContributionRevisionView(
            id=revision.id,
            revision_number=revision.revision_number,
            source_commit_id=revision.source_commit_id,
            base_document=revision.base_document,
            proposed_document=revision.proposed_document,
            created_at=revision.created_at,
        ),
        events=_fold_comment_events(events),
        viewer_permissions=permissions,
        source_is_public=source_is_public,
        merge_session_id=contribution.merge_session_id,
        merged_commit_id=contribution.merged_commit_id,
        created_at=contribution.created_at,
        updated_at=contribution.updated_at,
        closed_at=contribution.closed_at,
    )


def _required_bundle(store: SqliteCourseStore, contribution_id: str):
    bundle = store.load_lesson_contribution(contribution_id)
    if bundle is None:
        raise LessonContributionNotFoundError("改进方案不存在。")
    return bundle


def _required_visible_bundle(store: SqliteCourseStore, contribution_id: str, user: UserView):
    bundle = _required_bundle(store, contribution_id)
    contribution_view(store, bundle, viewer=user)
    return bundle


def _change_comment(
    store: SqliteCourseStore,
    contribution_id: str,
    comment_id: str,
    *,
    user: UserView,
    expected_version: int,
    kind: str,
    body: str,
) -> LessonContributionView:
    _require_registered(user)
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    _require_version(contribution, expected_version)
    comments = {event.id: event for event in events if event.kind == "commented"}
    comment = comments.get(comment_id)
    if comment is None:
        raise LessonContributionNotFoundError("评论不存在。")
    if comment.actor.user_id != user.id:
        raise LessonContributionPermissionError("只能修改或删除自己的评论。")
    deleted = any(
        event.kind == "comment_deleted" and event.metadata.get("comment_id") == comment_id
        for event in events
    )
    if deleted:
        raise LessonContributionConflictError("评论已经删除。")
    previous_version = contribution.version
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind=kind,
        actor=_actor_for_user(user, fallback=comment.actor.display_name),
        body=body,
        metadata={"comment_id": comment_id},
    )
    _save(store, contribution, expected_version=previous_version, events=[event])
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def _fold_comment_events(events: list[LessonContributionEvent]) -> list[LessonContributionEvent]:
    folded: list[LessonContributionEvent] = []
    comment_indexes: dict[str, int] = {}
    for event in events:
        if event.kind == "commented":
            comment_indexes[event.id] = len(folded)
            folded.append(event)
            continue
        if event.kind in {"comment_edited", "comment_deleted"}:
            comment_id = event.metadata.get("comment_id")
            index = comment_indexes.get(comment_id) if isinstance(comment_id, str) else None
            if index is not None:
                original = folded[index]
                folded[index] = original.model_copy(
                    update={
                        "body": event.body if event.kind == "comment_edited" else "",
                        "metadata": {
                            **original.metadata,
                            "edited": event.kind == "comment_edited" or original.metadata.get("edited", False),
                            "deleted": event.kind == "comment_deleted",
                        },
                    }
                )
            continue
        folded.append(event)
    return folded


def _fork_lineage(lesson: Lesson) -> tuple[str, str]:
    for commit in lesson.history_graph.commits:
        lesson_id = commit.metadata.get(PUBLIC_SOURCE_LESSON_ID_KEY)
        commit_id = commit.metadata.get(PUBLIC_SOURCE_COMMIT_ID_KEY)
        if isinstance(lesson_id, str) and lesson_id and isinstance(commit_id, str) and commit_id:
            return lesson_id, commit_id
    raise LessonContributionPermissionError("只有从公开课程派生的个人课程才能提交改进方案。")


def _actor_for_user(user: UserView, *, fallback: str) -> LessonContributionActor:
    return LessonContributionActor(
        user_id=user.id,
        display_name=(user.display_name or "").strip() or fallback,
        avatar_url=user.avatar_url,
    )


def _required_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise LessonContributionConflictError(f"{label}不能为空。")
    return normalized


def _require_registered(user: UserView) -> None:
    if user.role == "guest":
        raise LessonContributionPermissionError("请注册或登录正式账号后参与课程协作。")


def _require_version(contribution: LessonContribution, expected_version: int) -> None:
    if contribution.version != expected_version:
        raise LessonContributionConflictError("改进方案已在其他窗口更新，请刷新后重试。")


def _same_document(left, right) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _save(
    store: SqliteCourseStore,
    contribution: LessonContribution,
    *,
    expected_version: int,
    revision: LessonContributionRevision | None = None,
    events: list[LessonContributionEvent] | None = None,
) -> None:
    if not store.save_lesson_contribution_if_version(
        contribution,
        expected_version=expected_version,
        revision=revision,
        events=events,
    ):
        raise LessonContributionConflictError("改进方案已在其他窗口更新，请刷新后重试。")


def _new_contribution_id() -> str:
    from app.models import new_id

    return new_id("contribution")

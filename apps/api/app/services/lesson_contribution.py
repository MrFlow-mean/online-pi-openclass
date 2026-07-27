from __future__ import annotations

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    Lesson,
    LessonContribution,
    LessonContributionActor,
    LessonContributionEvent,
    LessonContributionRevision,
    LessonContributionRevisionView,
    LessonContributionView,
    LessonContributionViewerPermissions,
    LessonMergeSession,
    UserView,
    WorkspaceState,
    new_id,
    now_iso,
)
from app.services.course_store import SqliteCourseStore
from app.services.history import current_head_commit, get_commit
from app.services.lesson_merge import abandon_merge_session, create_merge_session
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
    if contribution.status not in {"open", "merge_draft"}:
        raise LessonContributionConflictError("当前状态不能关闭。")
    _require_version(contribution, expected_version)
    session = None
    previous_session_version = None
    if contribution.status == "merge_draft":
        if not contribution.merge_session_id:
            raise LessonContributionConflictError("关联的合并草案已不存在。")
        session = store.load_merge_session_for_user(
            contribution.source_owner_user_id,
            contribution.merge_session_id,
        )
        if session is None:
            raise LessonContributionConflictError("关联的合并草案已不存在。")
        previous_session_version = session.version
        abandon_merge_session(session)
    previous_version = contribution.version
    contribution.status = "closed"
    contribution.merge_session_id = None
    contribution.closed_at = now_iso()
    contribution.updated_at = contribution.closed_at
    contribution.version += 1
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="closed",
        actor=_actor_for_user(user, fallback="参与者"),
    )
    if session is not None and previous_session_version is not None:
        saved = store.save_merge_session_and_contribution_if_versions(
            session,
            contribution,
            expected_session_version=previous_session_version,
            expected_contribution_version=previous_version,
            events=[event],
        )
        if not saved:
            raise LessonContributionConflictError("合并草案或改进方案已更新，请刷新后重试。")
    else:
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


def start_lesson_contribution_merge(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    workspace: WorkspaceState,
    source_lesson: Lesson,
    expected_workspace_revision: int,
    expected_version: int,
) -> LessonContributionView:
    _require_registered(user)
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    if contribution.source_owner_user_id != user.id or contribution.source_lesson_id != source_lesson.id:
        raise LessonContributionPermissionError("只有原课程作者可以开始合并。")
    if contribution.status != "open":
        raise LessonContributionConflictError("当前改进方案不能开始新的合并审查。")
    _require_version(contribution, expected_version)
    source_record = store.load_lesson_with_owner(source_lesson.id)
    if source_record is None or source_record[0] != user.id or not source_record[2]:
        raise LessonContributionConflictError("来源课程当前不可公开协作。")
    if store.load_active_merge_session_for_user(user.id, source_lesson.id) is not None:
        raise LessonContributionConflictError("这节课已有未结束的合并草案。")
    try:
        base_commit = get_commit(source_lesson, revision.source_commit_id)
    except ValueError as exc:
        raise LessonContributionConflictError("来源课程的派生基线已不可用。") from exc

    branch_name = f"contribution/{contribution.id.removeprefix('contribution_')[:12]}/r{revision.revision_number}"
    proposed_document = BoardDocument.model_validate(
        revision.proposed_document.model_dump(mode="json")
    ).model_copy(update={"id": new_id("document")})
    synthetic_commit = CommitRecord(
        label=f"贡献方案 #{revision.revision_number}",
        message=f"导入 {contribution.contributor.display_name} 提交的课程改进版本",
        branch_name=branch_name,
        parent_ids=[base_commit.id],
        snapshot=proposed_document,
        runtime_snapshot=base_commit.runtime_snapshot,
        metadata={
            "kind": "lesson_contribution_revision",
            "history_node_kind": "system",
            "history_node_title": "课程改进方案",
            "history_node_summary": contribution.title,
            "lesson_contribution_id": contribution.id,
            "lesson_contribution_revision": revision.revision_number,
            "contributor_user_id": contribution.contributor_user_id,
        },
    )
    source_lesson.history_graph.commits.append(synthetic_commit)
    source_lesson.history_graph.branches[branch_name] = BranchRef(
        name=branch_name,
        head_commit_id=synthetic_commit.id,
        base_commit_id=base_commit.id,
    )
    session = create_merge_session(
        source_lesson,
        owner_user_id=user.id,
        source_branch_name=branch_name,
    )
    session.audit.update(
        {
            "lesson_contribution_id": contribution.id,
            "lesson_contribution_revision": revision.revision_number,
            "contributor_user_id": contribution.contributor_user_id,
        }
    )
    previous_version = contribution.version
    contribution.status = "merge_draft"
    contribution.merge_session_id = session.id
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="merge_started",
        actor=_actor_for_user(user, fallback="课程作者"),
        metadata={
            "revision_number": revision.revision_number,
            "merge_session_id": session.id,
        },
    )
    saved = store.save_workspace_merge_session_and_contribution_if_revision(
        user.id,
        workspace,
        session,
        contribution,
        expected_workspace_revision=expected_workspace_revision,
        expected_contribution_version=previous_version,
        events=[event],
    )
    if not saved:
        raise LessonContributionConflictError("课程或改进方案已更新，请刷新后重试。")
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def return_lesson_contribution_for_changes(
    store: SqliteCourseStore,
    contribution_id: str,
    *,
    user: UserView,
    expected_version: int,
) -> LessonContributionView:
    _require_registered(user)
    contribution, revision, events = _required_visible_bundle(store, contribution_id, user)
    if contribution.source_owner_user_id != user.id:
        raise LessonContributionPermissionError("只有原课程作者可以退回继续修改。")
    if contribution.status != "merge_draft" or not contribution.merge_session_id:
        raise LessonContributionConflictError("当前没有可以退回的合并草案。")
    _require_version(contribution, expected_version)
    session = store.load_merge_session_for_user(user.id, contribution.merge_session_id)
    if session is None:
        raise LessonContributionConflictError("关联的合并草案已不存在。")
    previous_session_version = session.version
    abandon_merge_session(session)
    previous_version = contribution.version
    contribution.status = "open"
    contribution.merge_session_id = None
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="returned_for_changes",
        actor=_actor_for_user(user, fallback="课程作者"),
        metadata={"merge_session_id": session.id},
    )
    saved = store.save_merge_session_and_contribution_if_versions(
        session,
        contribution,
        expected_session_version=previous_session_version,
        expected_contribution_version=previous_version,
        events=[event],
    )
    if not saved:
        raise LessonContributionConflictError("合并草案或改进方案已更新，请刷新后重试。")
    return contribution_view(store, (contribution, revision, [*events, event]), viewer=user)


def persist_recomputed_contribution_merge(
    store: SqliteCourseStore,
    *,
    user: UserView,
    workspace: WorkspaceState,
    expected_workspace_revision: int,
    old_session: LessonMergeSession,
    new_session: LessonMergeSession,
) -> None:
    contribution_id = old_session.audit.get("lesson_contribution_id")
    if not isinstance(contribution_id, str) or not contribution_id:
        raise LessonContributionConflictError("合并草案没有关联改进方案。")
    contribution, _revision, _events = _required_bundle(store, contribution_id)
    if (
        contribution.source_owner_user_id != user.id
        or contribution.status != "merge_draft"
        or contribution.merge_session_id != old_session.id
    ):
        raise LessonContributionConflictError("改进方案与合并草案状态不一致。")
    new_session.audit.update(
        {
            key: old_session.audit[key]
            for key in (
                "lesson_contribution_id",
                "lesson_contribution_revision",
                "contributor_user_id",
            )
            if key in old_session.audit
        }
    )
    previous_version = contribution.version
    contribution.merge_session_id = new_session.id
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="merge_started",
        actor=_actor_for_user(user, fallback="课程作者"),
        metadata={
            "merge_session_id": new_session.id,
            "supersedes_session_id": old_session.id,
            "recomputed": True,
        },
    )
    saved = store.save_workspace_merge_session_and_contribution_if_revision(
        user.id,
        workspace,
        new_session,
        contribution,
        expected_workspace_revision=expected_workspace_revision,
        expected_contribution_version=previous_version,
        events=[event],
        guard_session_id=old_session.id,
        expected_session_version=old_session.version,
    )
    if not saved:
        raise LessonContributionConflictError("课程、合并草案或改进方案已更新，请刷新后重试。")


def complete_lesson_contribution_merge(
    store: SqliteCourseStore,
    *,
    user: UserView,
    workspace: WorkspaceState,
    expected_workspace_revision: int,
    session: LessonMergeSession,
) -> None:
    contribution_id = session.audit.get("lesson_contribution_id")
    if not isinstance(contribution_id, str) or not contribution_id:
        raise LessonContributionConflictError("合并草案没有关联改进方案。")
    contribution, _revision, _events = _required_bundle(store, contribution_id)
    if (
        contribution.source_owner_user_id != user.id
        or contribution.status != "merge_draft"
        or contribution.merge_session_id != session.id
        or session.status != "committed"
        or not session.committed_commit_id
    ):
        raise LessonContributionConflictError("改进方案与合并提交状态不一致。")
    previous_version = contribution.version
    contribution.status = "merged"
    contribution.merged_commit_id = session.committed_commit_id
    contribution.version += 1
    contribution.updated_at = now_iso()
    event = LessonContributionEvent(
        contribution_id=contribution.id,
        kind="merged",
        actor=_actor_for_user(user, fallback="课程作者"),
        metadata={
            "merge_session_id": session.id,
            "merged_commit_id": session.committed_commit_id,
        },
    )
    saved = store.save_workspace_merge_session_and_contribution_if_revision(
        user.id,
        workspace,
        session,
        contribution,
        expected_workspace_revision=expected_workspace_revision,
        expected_contribution_version=previous_version,
        events=[event],
        guard_session_id=session.id,
        expected_session_version=session.version - 1,
    )
    if not saved:
        raise LessonContributionConflictError("课程、合并草案或改进方案已更新，请刷新后重试。")


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
        can_close=participant and contribution.status in {"open", "merge_draft"},
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
        viewer_project_lesson_id=(
            contribution.contributor_lesson_id
            if viewer and viewer.id == contribution.contributor_user_id
            else contribution.source_lesson_id
            if viewer and viewer.id == contribution.source_owner_user_id
            else None
        ),
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
    return new_id("contribution")

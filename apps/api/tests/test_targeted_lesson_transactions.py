import sqlite3

import pytest

from app.models import CourseGraphEdge, CoursePackage, ResourceLibraryItem, WorkspaceState
from app.services.course_store import SqliteCourseStore
from app.services.history import commit_operations, current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document


def _workspace_with_lessons(*titles: str) -> tuple[WorkspaceState, CoursePackage]:
    lessons = [create_empty_lesson(title) for title in titles]
    package = CoursePackage(
        id="course_targeted",
        title="Targeted transactions",
        summary="",
        lessons=lessons,
        open_lesson_ids=[lesson.id for lesson in lessons],
        active_lesson_id=lessons[0].id if lessons else None,
        workspace_tab_order=[lesson.id for lesson in lessons],
    )
    return WorkspaceState(packages=[package], active_package_id=package.id), package


def _fail_full_workspace(*_args, **_kwargs):
    raise AssertionError("targeted transaction must not use full workspace I/O")


def test_targeted_create_close_delete_do_not_read_or_replace_workspace(tmp_path, monkeypatch) -> None:
    store = SqliteCourseStore(tmp_path / "targeted.sqlite3", legacy_json_path=None)
    workspace, package = _workspace_with_lessons("First", "Second")
    store.save_for_user("owner", workspace)
    untouched_lesson_id = package.lessons[1].id
    with sqlite3.connect(store.path) as conn:
        untouched_before = conn.execute(
            "SELECT * FROM lesson_commits WHERE lesson_id = ? ORDER BY sort_order",
            (untouched_lesson_id,),
        ).fetchall()

    monkeypatch.setattr(store, "_read_workspace", _fail_full_workspace)
    monkeypatch.setattr(store, "_replace_workspace", _fail_full_workspace)
    created_lesson = create_empty_lesson("Created")
    created = store.create_lesson_for_user(
        "owner",
        created_lesson,
        target_package_id=package.id,
        branch_from_lesson_id=package.lessons[0].id,
    )
    assert created.created_lesson == created_lesson
    assert created.graph_edge is not None
    assert created.active_lesson_id == created_lesson.id

    closed = store.close_lesson_for_user("owner", created_lesson.id)
    assert closed.active_lesson_id == package.lessons[0].id
    assert created_lesson.id not in closed.workspace_tab_order
    deleted = store.delete_lesson_for_user("owner", created_lesson.id)
    assert deleted.deleted_lesson_id == created_lesson.id

    with sqlite3.connect(store.path) as conn:
        untouched_after = conn.execute(
            "SELECT * FROM lesson_commits WHERE lesson_id = ? ORDER BY sort_order",
            (untouched_lesson_id,),
        ).fetchall()
        assert conn.execute(
            "SELECT count(*) FROM course_graph_edges WHERE source_lesson_id = ? OR target_lesson_id = ?",
            (created_lesson.id, created_lesson.id),
        ).fetchone()[0] == 0
    assert untouched_after == untouched_before


def test_targeted_create_enforces_owner_and_branch_package(tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "ownership.sqlite3", legacy_json_path=None)
    source_workspace, source_package = _workspace_with_lessons("Source")
    target_package = CoursePackage(id="course_other", title="Other", summary="", lessons=[])
    source_workspace.packages.append(target_package)
    store.save_for_user("owner", source_workspace)
    foreign_workspace, foreign_package = _workspace_with_lessons("Foreign")
    foreign_package.id = "course_foreign"
    store.save_for_user("other", foreign_workspace)

    with pytest.raises(ValueError, match="target package"):
        store.create_lesson_for_user(
            "owner",
            create_empty_lesson("Invalid branch"),
            target_package_id=target_package.id,
            branch_from_lesson_id=source_package.lessons[0].id,
        )
    with pytest.raises(KeyError):
        store.create_lesson_for_user(
            "owner",
            create_empty_lesson("Foreign target"),
            target_package_id=foreign_package.id,
            branch_from_lesson_id=None,
        )
    with pytest.raises(KeyError):
        store.close_lesson_for_user("owner", foreign_package.lessons[0].id)


def test_delete_last_lesson_cleans_edges_scoped_resources_and_associations(tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "delete.sqlite3", legacy_json_path=None)
    workspace, package = _workspace_with_lessons("Only")
    lesson = package.lessons[0]
    package.course_graph.append(
        CourseGraphEdge(source_lesson_id=lesson.id, target_lesson_id=lesson.id, relationship="deep_dive")
    )
    package.resources.append(
        ResourceLibraryItem(
            name="Scoped",
            mime_type="text/plain",
            resource_type="document",
            size_bytes=1,
            scope_lesson_id=lesson.id,
        )
    )
    store.save_for_user("owner", workspace)
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO lesson_merge_sessions(
                id, owner_user_id, lesson_id, status, target_branch_name, source_branch_name,
                base_commit_id, target_head_commit_id, source_head_commit_id, version,
                payload_json, created_at, updated_at
            ) VALUES ('merge_delete', 'owner', ?, 'draft', 'main', 'source', 'base', 'target', 'source', 1, '{}', 'now', 'now')
            """,
            (lesson.id,),
        )

    result = store.delete_lesson_for_user("owner", lesson.id)

    assert result.active_lesson_id is None
    assert result.open_lesson_ids == []
    assert result.workspace_tab_order == []
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM lessons WHERE id = ?", (lesson.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM lesson_commits WHERE lesson_id = ?", (lesson.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM resources WHERE scope_lesson_id = ?", (lesson.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM lesson_merge_sessions WHERE lesson_id = ?", (lesson.id,)).fetchone()[0] == 0


def test_targeted_document_save_is_head_guarded_and_preserves_other_history(tmp_path, monkeypatch) -> None:
    store = SqliteCourseStore(tmp_path / "document.sqlite3", legacy_json_path=None)
    workspace, package = _workspace_with_lessons("Edited", "Untouched")
    store.save_for_user("owner", workspace)
    edited_id, untouched_id = (lesson.id for lesson in package.lessons)
    first = store.load_lesson_document_context_for_user("owner", edited_id)
    second = store.load_lesson_document_context_for_user("owner", edited_id)
    assert first is not None and second is not None
    first_head = current_head_commit(first.lesson)
    second_head = current_head_commit(second.lesson)
    with sqlite3.connect(store.path) as conn:
        untouched_before = conn.execute(
            "SELECT * FROM lesson_commits WHERE lesson_id = ? ORDER BY sort_order",
            (untouched_id,),
        ).fetchall()

    for context, text in ((first, "first save"), (second, "stale save")):
        document = build_document(title="Edited", content_text=text)
        commit_operations(
            context.lesson,
            [],
            label="Save",
            message=text,
            new_document=document,
            metadata={"kind": "auto_document_save"},
        )

    monkeypatch.setattr(store, "_read_workspace", _fail_full_workspace)
    monkeypatch.setattr(store, "_replace_workspace", _fail_full_workspace)
    assert store.save_document_for_user_if_head(
        "owner",
        first.lesson,
        expected_branch_name="main",
        expected_head_commit_id=first_head.id,
    ) is not None
    assert store.save_document_for_user_if_head(
        "owner",
        second.lesson,
        expected_branch_name="main",
        expected_head_commit_id=second_head.id,
    ) is None

    reloaded = store.load_lesson_document_context_for_user("owner", edited_id)
    assert reloaded is not None
    assert reloaded.lesson.board_document.content_text == "first save"
    assert len(reloaded.lesson.history_graph.commits) == 2
    with sqlite3.connect(store.path) as conn:
        untouched_after = conn.execute(
            "SELECT * FROM lesson_commits WHERE lesson_id = ? ORDER BY sort_order",
            (untouched_id,),
        ).fetchall()
    assert untouched_after == untouched_before

from __future__ import annotations

from app.models import BoardDocument, PatchOperation
from app.services.board_segment_index import build_board_segment_index
from app.services.history import (
    bind_commit_metadata,
    commit_operations,
    create_branch,
    current_head_commit,
    restore_commit,
    switch_branch,
)
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.rich_document import (
    build_document,
    document_to_markdown,
    export_docx,
    import_docx,
    replace_selection_in_document,
    upgrade_markdown_like_document,
)


def _node_types(node: dict) -> list[str]:
    result = [str(node.get("type") or "")]
    for child in node.get("content", []):
        if isinstance(child, dict):
            result.extend(_node_types(child))
    return result


def test_create_empty_lesson_has_blank_document_and_no_ai_runtime() -> None:
    lesson = create_empty_lesson("Blank document")

    assert lesson.board_document.content_text == ""
    assert current_head_commit(lesson).snapshot.content_text == ""
    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None


def test_branch_switch_and_restore_keep_document_history_only() -> None:
    lesson = create_empty_lesson("History")
    initial_commit_id = current_head_commit(lesson).id
    lesson.history_graph.commits[0].metadata["active_requirement_sheet_after"] = (
        build_requirements(lesson.title).model_dump(mode="json")
    )
    changed = build_document(
        title=lesson.board_document.title,
        content_text="# Changed",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    commit_operations(
        lesson,
        [],
        label="Document update",
        message="Changed the document.",
        new_document=changed,
        metadata={"kind": "manual_document_save"},
    )

    create_branch(lesson, "alternate", initial_commit_id)
    assert lesson.board_document.content_text == ""
    assert lesson.learning_requirements is None
    switch_branch(lesson, "main")
    assert lesson.board_document.content_text == "# Changed"
    restore_commit(lesson, initial_commit_id, "Restore origin")
    assert lesson.board_document.content_text == ""
    assert current_head_commit(lesson).metadata["restored_commit_id"] == initial_commit_id
    assert lesson.learning_requirements is None


def test_commit_metadata_context_and_history_node_classification() -> None:
    lesson = create_empty_lesson("Conversation")

    with bind_commit_metadata({"chat_edit_source_commit_id": "previous"}):
        commit_operations(
            lesson,
            [],
            label="Codex conversation",
            message="Recorded a Codex turn.",
            metadata={
                "kind": "basic_chat",
                "user_message": "Explain this",
                "assistant_message": "Here is the explanation.",
                "assistant_message_source": "codex",
            },
        )

    metadata = current_head_commit(lesson).metadata
    assert metadata["history_node_kind"] == "chat"
    assert metadata["history_node_title"] == "Explain this"
    assert metadata["history_node_summary"] == "Here is the explanation."
    assert metadata["chat_edit_source_commit_id"] == "previous"


def test_unknown_future_commit_kind_with_messages_is_classified_as_chat() -> None:
    lesson = create_empty_lesson("Future conversation")

    commit_operations(
        lesson,
        [],
        label="Future workflow step",
        message="Recorded a future conversation shape.",
        metadata={
            "kind": "future_workflow_step",
            "user_message": "A future user message",
            "assistant_message": "A future assistant reply",
        },
    )

    assert current_head_commit(lesson).metadata["history_node_kind"] == "chat"


def test_document_commit_snapshot_is_isolated_from_later_mutation() -> None:
    lesson = create_empty_lesson("Snapshot")
    document = build_document(title="Snapshot", content_text="# Heading")

    commit_operations(
        lesson,
        [PatchOperation(op="insert_block", content="# Heading")],
        label="Document update",
        message="Added a heading.",
        new_document=document,
        metadata={"kind": "manual_document_save"},
    )
    commit = current_head_commit(lesson)
    lesson.board_document.content_json["content"][0]["type"] = "paragraph"

    assert commit.snapshot.content_json["content"][0]["type"] == "heading"
    assert commit.metadata["history_node_kind"] == "document"


def test_build_document_preserves_markdown_structure_and_math() -> None:
    document = build_document(
        title="Structured",
        content_text=(
            "## Section\n\n"
            "**Key:** value\n\n"
            "- first\n- second\n\n"
            "| Term | Meaning |\n| --- | --- |\n| a | b |\n\n"
            "$$\nE=mc^2\n$$"
        ),
    )
    types = _node_types(document.content_json)

    assert "heading" in types
    assert "bulletList" in types
    assert "table" in types
    assert "blockMath" in types
    assert "<strong>Key:</strong> value" in document.content_html


def test_build_document_preserves_absolute_value_pipes_inside_table_math() -> None:
    document = build_document(
        title="Formula table",
        content_text=(
            "| Concept | Formula |\n"
            "| --- | --- |\n"
            r"| density | \(\rho(x,t)=|\Psi(x,t)|^2\) |" "\n"
            r"| interval | \(P(a<x<b)=\int_a^b|\Psi|^2dx\) |"
        ),
    )

    table = document.content_json["content"][0]
    rows = table["content"]
    assert [len(row["content"]) for row in rows] == [2, 2, 2]
    assert rows[1]["content"][1]["content"][0]["content"] == [
        {"type": "inlineMath", "attrs": {"latex": r"\rho(x,t)=|\Psi(x,t)|^2"}},
    ]
    assert rows[2]["content"][1]["content"][0]["content"] == [
        {"type": "inlineMath", "attrs": {"latex": r"P(a<x<b)=\int_a^b|\Psi|^2dx"}},
    ]
    assert document.content_html.count('data-type="inline-math"') == 2
    assert "\\(" not in document.content_html


def test_build_document_renders_bold_vector_math_without_exposing_delimiters() -> None:
    document = build_document(
        title="Vector notation",
        content_text=(
            "$$\n"
            "\\boldsymbol{x}=(x_1;x_2;\\cdots;x_d)\n"
            "$$\n\n"
            "向量 $$\\boldsymbol{x}$$ 的第 $$i$$ 个分量。"
        ),
    )

    content = document.content_json["content"]
    assert content[0] == {
        "type": "blockMath",
        "attrs": {"latex": r"\boldsymbol{x}=(x_1;x_2;\cdots;x_d)"},
    }
    assert content[1]["content"] == [
        {"type": "text", "text": "向量 "},
        {"type": "inlineMath", "attrs": {"latex": r"\boldsymbol{x}"}},
        {"type": "text", "text": " 的第 "},
        {"type": "inlineMath", "attrs": {"latex": "i"}},
        {"type": "text", "text": " 个分量。"},
    ]
    assert "$$" not in document.content_html
    assert 'data-type="block-math"' in document.content_html
    assert document.content_html.count('data-type="inline-math"') == 2


def test_build_document_reserves_code_blocks_for_code_and_renders_fenced_formulas() -> None:
    document = build_document(
        title="Fenced content",
        content_text=(
            "```plaintext\nF = ma\n```\n\n"
            "```plaintext\n合外力 = 质量 × 加速度\n```\n\n"
            "```python\ndef force(mass, acceleration):\n    return mass * acceleration\n```\n\n"
            "```\nconst force = mass * acceleration;\n```"
        ),
    )
    content = document.content_json["content"]

    assert content[0] == {"type": "blockMath", "attrs": {"latex": "F = ma"}}
    assert content[1]["type"] == "paragraph"
    assert content[1]["content"][0]["text"] == "合外力 = 质量 × 加速度"
    assert content[2]["type"] == "codeBlock"
    assert content[3]["type"] == "codeBlock"
    assert 'data-type="block-math"' in document.content_html
    assert "<pre><code" in document.content_html


def test_upgrade_markdown_like_document_repairs_legacy_non_code_fences() -> None:
    document = BoardDocument(
        title="Legacy fenced content",
        content_text="```plaintext\nF = ma\n```\n\n```plaintext\n关键句\n```",
        content_html="<pre><code>F = ma</code></pre><pre><code>关键句</code></pre>",
        content_json={
            "type": "doc",
            "content": [
                {"type": "codeBlock", "content": [{"type": "text", "text": "F = ma"}]},
                {"type": "codeBlock", "content": [{"type": "text", "text": "关键句"}]},
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(document)

    assert upgraded.content_json["content"][0]["type"] == "blockMath"
    assert upgraded.content_json["content"][1]["type"] == "paragraph"
    assert "<pre><code" not in upgraded.content_html


def test_document_to_markdown_preserves_rich_structure() -> None:
    document = build_document(
        title="Structured",
        content_html=(
            "<h2>Section</h2><p><strong>Key:</strong> value</p>"
            "<ul><li>first</li></ul>"
            "<table><tbody><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></tbody></table>"
        ),
    )

    markdown = document_to_markdown(document)

    assert "## Section" in markdown
    assert "**Key:** value" in markdown
    assert "- first" in markdown
    assert "| A | B |" in markdown


def test_replace_selection_changes_only_exact_target() -> None:
    document = build_document(
        title="Selection",
        content_text="## Section\n\nFirst paragraph.\n\nSecond paragraph.",
    )

    updated = replace_selection_in_document(
        document,
        selection_text="Second paragraph.",
        replacement_text="Revised paragraph.",
    )

    assert "First paragraph." in updated.content_text
    assert "Revised paragraph." in updated.content_text
    assert "Second paragraph." not in updated.content_text
    assert "heading" in _node_types(updated.content_json)


def test_board_segment_index_remains_available_for_document_search() -> None:
    document = build_document(
        title="Index",
        content_text="# Main\n\n## Detail\n\nA searchable paragraph.",
    )

    index = build_board_segment_index(document)

    paragraph = next(segment for segment in index.segments if "searchable" in segment.text)
    assert paragraph.heading_path == ["Main", "Detail"]
    assert any(paragraph.segment_id in chunk.source_segment_ids for chunk in index.chunks)


def test_docx_import_export_roundtrip(tmp_path) -> None:
    document = build_document(title="Document", content_text="# Title\n\nBody")
    export_path = tmp_path / "document.docx"

    export_docx(document, export_path)
    imported = import_docx(export_path, title="Imported")

    assert imported.title == "Imported"
    assert "Title" in imported.content_text
    assert "Body" in imported.content_text

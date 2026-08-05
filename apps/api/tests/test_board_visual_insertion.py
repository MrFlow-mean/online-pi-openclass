from __future__ import annotations

import base64
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.services import board_asset_store as board_asset_store_module
from app.models import (
    SourceIngestionRecord,
    SourceVisualEvidence,
    UserView,
)
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.services.board_asset_store import BoardAssetError, BoardAssetRecord, BoardAssetStore
from app.services.board_visual_insertion import (
    apply_board_insertion_plan,
    build_board_insertion_plan,
    derive_board_visual_placements,
)
from app.services.docx_exporter import export_docx
from app.services.history import commit_operations, restore_commit
from app.services.html_document_export import export_html
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document, html_to_tiptap_doc, rebuild_document_from_content_json
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore
from app.services import workspace_state


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _store(tmp_path) -> BoardAssetStore:
    return BoardAssetStore(tmp_path / "openclass.sqlite3", tmp_path / "board-assets")


def _visual(
    visual_id: str = "visual_1",
    *,
    source_id: str = "source_1",
    chapter_id: str = "chapter_1",
    order_index: int = 1,
    kind: str = "image",
    table_data=None,
) -> dict[str, object]:
    import hashlib

    content_hash = hashlib.sha256(_PNG).hexdigest()
    if kind == "table" and table_data is not None:
        canonical = json.dumps(
            table_data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return {
        "visual_id": visual_id,
        "source_ingestion_id": source_id,
        "source_chapter_id": chapter_id,
        "anchor_status": "verified",
        "order_index": order_index,
        "before_chunk_id": f"{visual_id}_before",
        "after_chunk_id": f"{visual_id}_after",
        "caption": f"Caption {visual_id}",
        "source_title": "Source title",
        "page_no": order_index,
        "kind": kind,
        "mime_type": "image/png",
        "asset_hash": content_hash,
        "table_data": table_data,
    }


def test_plan_orders_frozen_visual_evidence_by_source_order_index() -> None:
    later = SourceVisualEvidence(
        visual_id="visual_a",
        source_ingestion_id="source_1",
        source_chapter_id="chapter_1",
        anchor_status="verified",
        order_index=2,
    )
    earlier = SourceVisualEvidence(
        visual_id="visual_z",
        source_ingestion_id="source_1",
        source_chapter_id="chapter_1",
        anchor_status="verified",
        order_index=1,
    )

    plan = build_board_insertion_plan([later, earlier], nonce="fixed")

    assert [item.visual_id for item in plan.items] == ["visual_z", "visual_a"]
    assert [item.order_index for item in plan.items] == [1, 2]


def test_missing_semantic_placement_is_appended_to_chapter_visual_appendix(
    tmp_path,
) -> None:
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    document = build_document(title="Board", content_text="Verified chapter explanation")

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=_store(tmp_path),
    )

    assert outcome.skipped == []
    assert outcome.applied_visual_ids == ["visual_1"]
    assert outcome.appendix_visual_ids == ["visual_1"]
    assert "本章教学图表" in outcome.document.content_text
    assert any(
        node.get("type") == "resourceVisualBlock"
        for node in outcome.document.content_json.get("content", [])
    )


def test_unplaced_visual_uses_appendix_when_another_visual_has_a_valid_marker(
    tmp_path,
) -> None:
    first = _visual("visual_1")
    second = _visual("visual_2", order_index=2)
    plan = build_board_insertion_plan([first, second], nonce="fixed")
    document = build_document(
        title="Board",
        content_text=f"First explanation\n\n{plan.items[0].marker}\n\nSecond explanation",
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=derive_board_visual_placements(document, plan=plan),
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: (
            first if visual_id == "visual_1" else second,
            _PNG,
        ),
        asset_store=_store(tmp_path),
    )

    assert outcome.skipped == []
    assert outcome.applied_visual_ids == ["visual_1", "visual_2"]
    assert outcome.appendix_visual_ids == ["visual_2"]


def _placement(item, target: str) -> dict[str, str]:
    return {
        "visual_id": item.visual_id,
        "marker": item.marker,
        "target_text_anchor": target,
        "source_before_chunk_id": item.before_chunk_id,
        "source_after_chunk_id": item.after_chunk_id,
        "reason": "The visual documents the adjacent paragraph.",
    }


def test_codex_markdown_derives_backend_owned_visual_placements() -> None:
    plan = build_board_insertion_plan(
        [_visual("visual_1"), _visual("visual_2", order_index=2)],
        nonce="fixed",
    )
    first, second = plan.items
    document = build_document(
        title="Board",
        content_text=(
            f"First unique explanation\n\n{first.marker}\n\n"
            f"Second unique explanation\n\n{second.marker}"
        ),
    )

    placements = derive_board_visual_placements(document, plan=plan)

    assert [placement["visual_id"] for placement in placements] == ["visual_1", "visual_2"]
    assert placements[0]["target_text_anchor"] == "First unique explanation"
    assert placements[1]["target_text_anchor"] == "Second unique explanation"
    assert placements[0]["source_before_chunk_id"] == first.before_chunk_id


def test_codex_marker_after_heading_is_still_materialized() -> None:
    plan = build_board_insertion_plan([_visual()], nonce="fixed")
    document = build_document(
        title="Board",
        content_text=f"# Heading\n\n{plan.items[0].marker}",
    )

    placements = derive_board_visual_placements(document, plan=plan)
    assert placements[0]["target_text_anchor"] == "Heading"


def test_codex_recreation_marker_keeps_editable_markdown_without_loading_original(
    tmp_path,
) -> None:
    visual = _visual(kind="diagram")
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    assert item.recreation_marker
    document = build_document(
        title="Board",
        content_text=(
            "| Stage | Output |\n"
            "|---|---|\n"
            "| Input | Transform |\n\n"
            f"{item.recreation_marker}"
        ),
    )

    placements = derive_board_visual_placements(document, plan=plan)
    assert placements[0]["placement_kind"] == "editable_recreation"
    assert placements[0]["marker"] == item.recreation_marker

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=placements,
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: pytest.fail(
            "an editable recreation must not load or persist the original image"
        ),
        asset_store=_store(tmp_path),
    )

    assert outcome.applied_visual_ids == [item.visual_id]
    assert outcome.recreated_visual_ids == [item.visual_id]
    assert outcome.original_visual_ids == []
    assert outcome.asset_ids == []
    assert [node["type"] for node in outcome.document.content_json["content"]] == [
        "table"
    ]
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text


def test_unstructured_recreation_falls_back_to_verified_original(tmp_path) -> None:
    visual = _visual(kind="diagram")
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=f"A prose summary is not a visual recreation.\n\n{item.recreation_marker}",
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=derive_board_visual_placements(document, plan=plan),
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=_store(tmp_path),
    )

    assert outcome.applied_visual_ids == [item.visual_id]
    assert outcome.recreated_visual_ids == []
    assert outcome.original_visual_ids == [item.visual_id]
    assert len(outcome.asset_ids) == 1
    assert [node["type"] for node in outcome.document.content_json["content"]] == [
        "paragraph",
        "resourceVisualBlock",
    ]


def test_single_direction_linear_flow_is_accepted_as_editable_recreation(tmp_path) -> None:
    visual = _visual(kind="diagram")
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=f"Input → Transform → Output\n\n{item.recreation_marker}",
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=derive_board_visual_placements(document, plan=plan),
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: pytest.fail(
            "a verified linear recreation must not load the original image"
        ),
        asset_store=_store(tmp_path),
    )

    assert outcome.recreated_visual_ids == [item.visual_id]
    assert outcome.original_visual_ids == []
    assert [node["type"] for node in outcome.document.content_json["content"]] == ["paragraph"]


def test_codex_first_marker_wins_when_both_visual_forms_are_present() -> None:
    plan = build_board_insertion_plan([_visual(kind="diagram")], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=(
            f"Editable flow\n\n{item.recreation_marker}\n\n"
            f"Original introduction\n\n{item.marker}"
        ),
    )

    placements = derive_board_visual_placements(document, plan=plan)
    assert placements[0]["marker"] == item.recreation_marker
    assert placements[0]["placement_kind"] == "editable_recreation"


def test_board_asset_store_deduplicates_and_authorizes_by_owner(tmp_path) -> None:
    store = _store(tmp_path)
    source_file = tmp_path / "source-chart.png"
    source_file.write_bytes(_PNG)
    first = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        content=source_file.read_bytes(),
        mime_type="image/png",
        file_name="chart.png",
        source_visual_id="visual_1",
    )
    duplicate = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_b",
        content=_PNG,
        mime_type="image/png",
        file_name="other.png",
        source_visual_id="visual_2",
    )

    assert duplicate.id == first.id
    assert store.get(first.id, "owner_b") is None
    source_file.unlink()
    resolved = store.read_bytes(first.id, "owner_a")
    assert resolved is not None and resolved[1] == _PNG
    reopened = _store(tmp_path).read_bytes(first.id, "owner_a")
    assert reopened is not None and reopened[1] == _PNG

    escaped = BoardAssetRecord(
        **{**first.__dict__, "storage_key": "../outside.png"},
    )
    with pytest.raises(BoardAssetError):
        store.resolve_path(escaped)
    with pytest.raises(BoardAssetError):
        store.put_bytes(
            owner_user_id="owner_a",
            lesson_id="lesson_a",
            content=b"<script>alert(1)</script>",
            mime_type="image/png",
        )


def test_board_asset_store_repairs_same_length_corruption_on_reinsert(tmp_path) -> None:
    store = _store(tmp_path)
    asset = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        content=_PNG,
        mime_type="image/png",
    )
    store.resolve_path(asset).write_bytes(b"x" * len(_PNG))

    repaired = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_b",
        content=_PNG,
        mime_type="image/png",
    )

    assert repaired.id == asset.id
    resolved = store.read_bytes(asset.id, "owner_a")
    assert resolved is not None and resolved[1] == _PNG


def test_permanent_board_asset_survives_source_visual_and_file_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    source_path = tmp_path / "source.png"
    source_path.write_bytes(_PNG)
    source_store = SourceStructureStore(tmp_path / "source.sqlite3")
    source = SourceIngestionRecord(
        id="source_1",
        owner_user_id="owner_a",
        package_id="package_a",
        title="Source",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="image/png",
        size_bytes=len(_PNG),
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    SourceStructureIndexer(store=source_store).rebuild_structure(source)
    visual = source_store.get_structure_view(source=source).visuals[0]
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    board_store = _store(tmp_path / "permanent")
    outcome = apply_board_insertion_plan(
        build_document(title="Board", content_text=f"Source visual\n\n{item.marker}"),
        plan=plan,
        placements=[_placement(item, "Source visual")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: source_store.read_visual_bytes(
            owner_user_id="owner_a",
            package_id="package_a",
            source_id="source_1",
            visual_id=visual_id,
        ),
        asset_store=board_store,
    )
    asset_id = outcome.asset_ids[0]

    source_store.delete_for_source(
        owner_user_id="owner_a",
        package_id="package_a",
        source_id="source_1",
    )
    source_path.unlink()

    assert source_store.get_visual(
        owner_user_id="owner_a",
        package_id="package_a",
        source_id="source_1",
        visual_id=visual.id,
    ) is None
    resolved = board_store.read_bytes(asset_id, "owner_a")
    assert resolved is not None and resolved[1] == _PNG


def test_build_plan_groups_visuals_by_source_and_uses_asset_hash() -> None:
    plan = build_board_insertion_plan(
        [
            _visual("b2", source_id="source_b", order_index=2),
            _visual("a2", source_id="source_a", order_index=2),
            _visual("a1", source_id="source_a", order_index=1),
        ],
        nonce="fixed",
    )

    assert [item.visual_id for item in plan.items] == ["a1", "a2", "b2"]
    assert all(item.content_hash for item in plan.items)
    assert len(set(plan.markers)) == 3


def test_board_asset_store_normalizes_legacy_raster_for_browser_display(tmp_path) -> None:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), color="red").save(buffer, format="BMP")
    store = _store(tmp_path)
    asset = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        content=buffer.getvalue(),
        mime_type="image/bmp",
    )
    resolved = store.read_bytes(asset.id, "owner_a")

    assert asset.mime_type == "image/png"
    assert resolved is not None and resolved[1].startswith(b"\x89PNG\r\n\x1a\n")


def test_board_asset_store_rejects_decompressed_pixel_budget_overflow(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
    monkeypatch.setattr(board_asset_store_module, "MAX_BOARD_ASSET_PIXELS", 100)

    with pytest.raises(BoardAssetError, match="pixel budget"):
        _store(tmp_path).put_bytes(
            owner_user_id="owner_a",
            lesson_id="lesson_a",
            content=buffer.getvalue(),
            mime_type="image/png",
        )


def test_insertion_replaces_unique_standalone_marker_with_permanent_asset(tmp_path) -> None:
    store = _store(tmp_path)
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=f"Unique explanation paragraph\n\n{item.marker}\n\nFollowing paragraph",
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, "Unique explanation paragraph")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=store,
    )

    nodes = outcome.document.content_json["content"]
    assert [node["type"] for node in nodes] == ["paragraph", "resourceVisualBlock", "paragraph"]
    attrs = nodes[1]["attrs"]
    assert attrs["visualId"] == "visual_1"
    assert attrs["sourceIngestionId"] == "source_1"
    assert attrs["pageNo"] == 1
    assert attrs["originalSrc"] == ""
    assert "/api/board-assets/" not in outcome.document.model_dump_json()
    assert "data:image/" not in outcome.document.model_dump_json()
    round_trip_attrs = html_to_tiptap_doc(outcome.document.content_html)["content"][1]["attrs"]
    assert round_trip_attrs["sourceIngestionId"] == "source_1"
    assert round_trip_attrs["sourceChapterId"] == "chapter_1"
    assert round_trip_attrs["pageNo"] == 1
    assert round_trip_attrs["kind"] == "image"
    assert item.marker not in outcome.document.content_text
    assert store.read_bytes(attrs["assetId"], "owner_a") is not None
    assert outcome.applied_visual_ids == ["visual_1"]


def test_invalid_inline_marker_is_removed_without_appending_visual(tmp_path) -> None:
    store = _store(tmp_path)
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=f"Unique explanation {item.marker} remains readable",
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, "Unique explanation")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=store,
    )

    assert outcome.applied_visual_ids == []
    assert outcome.skipped[0]["reason"] == "placement_missing"
    assert item.marker not in outcome.document.content_text
    assert "Unique explanation" in outcome.document.content_text


def test_existing_nested_visual_does_not_block_explicit_marker_insertion(tmp_path) -> None:
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Existing visual"}],
                            },
                            {
                                "type": "resourceVisualBlock",
                                "attrs": {
                                    "visualId": "visual_1",
                                    "assetId": "basset_existing_asset",
                                    "caption": "Existing caption",
                                    "originalSrc": "/api/board-assets/basset_existing_asset/content",
                                },
                            },
                        ],
                    }
                ],
            },
            {"type": "paragraph", "content": [{"type": "text", "text": "Target paragraph"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": item.marker}]},
        ],
    }
    document = rebuild_document_from_content_json(
        build_document(title="Board", content_text="placeholder"),
        content_json,
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, "Target paragraph")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=_store(tmp_path),
    )

    assert outcome.applied_visual_ids == ["visual_1"]
    assert outcome.skipped == []
    visual_nodes = []

    def collect_visuals(value) -> None:
        if isinstance(value, dict):
            if value.get("type") == "resourceVisualBlock":
                visual_nodes.append(value)
            for child in value.get("content", []):
                collect_visuals(child)
        elif isinstance(value, list):
            for child in value:
                collect_visuals(child)

    collect_visuals(outcome.document.content_json)
    assert [node["attrs"]["visualId"] for node in visual_nodes] == ["visual_1", "visual_1"]
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text


def test_same_source_reversed_markers_are_all_materialized(tmp_path) -> None:
    store = _store(tmp_path)
    visuals = [_visual("visual_1", order_index=1), _visual("visual_2", order_index=2)]
    plan = build_board_insertion_plan(visuals, nonce="fixed")
    first, second = plan.items
    document = build_document(
        title="Board",
        content_text=(
            f"Second target\n\n{second.marker}\n\nBridge\n\n"
            f"First target\n\n{first.marker}\n\nEnd"
        ),
    )
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(first, "First target"), _placement(second, "Second target")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: (next(v for v in visuals if v["visual_id"] == visual_id), _PNG),
        asset_store=store,
    )

    assert outcome.applied_visual_ids == ["visual_2", "visual_1"]
    assert outcome.skipped == []
    visual_nodes = [
        node
        for node in outcome.document.content_json["content"]
        if node["type"] == "resourceVisualBlock"
    ]
    assert [node["attrs"]["visualId"] for node in visual_nodes] == ["visual_2", "visual_1"]
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text


@pytest.mark.parametrize(
    "document_visual_order",
    [
        ["visual_1", "visual_3", "visual_2", "visual_4"],
        ["visual_1", "visual_2", "visual_4", "visual_3"],
    ],
)
def test_same_chapter_marker_order_is_materialized_as_written(
    tmp_path,
    document_visual_order: list[str],
) -> None:
    store = _store(tmp_path)
    visuals = [_visual(f"visual_{index}", order_index=index) for index in range(1, 5)]
    plan = build_board_insertion_plan(visuals, nonce="fixed")
    items = {item.visual_id: item for item in plan.items}
    document = build_document(
        title="Board",
        content_text="\n\n".join(
            part
            for visual_id in document_visual_order
            for part in (f"Target {visual_id}", items[visual_id].marker)
        ),
    )

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[
            _placement(item, f"Target {item.visual_id}")
            for item in plan.items
        ],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: (
            next(value for value in visuals if value["visual_id"] == visual_id),
            _PNG,
        ),
        asset_store=store,
    )

    assert outcome.applied_visual_ids == document_visual_order
    assert outcome.skipped == []
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text
    visual_nodes = [
        node
        for node in outcome.document.content_json["content"]
        if node["type"] == "resourceVisualBlock"
    ]
    assert [node["attrs"]["visualId"] for node in visual_nodes] == document_visual_order


def test_visuals_from_different_chapters_follow_written_marker_order(tmp_path) -> None:
    store = _store(tmp_path)
    visuals = [
        _visual("visual_1", chapter_id="chapter_1", order_index=1),
        _visual("visual_2", chapter_id="chapter_2", order_index=2),
    ]
    plan = build_board_insertion_plan(visuals, nonce="fixed")
    first, second = plan.items
    document = build_document(
        title="Board",
        content_text=(
            f"Second chapter target\n\n{second.marker}\n\n"
            f"First chapter target\n\n{first.marker}\n\nEnd"
        ),
    )
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[
            _placement(first, "First chapter target"),
            _placement(second, "Second chapter target"),
        ],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: (
            next(value for value in visuals if value["visual_id"] == visual_id),
            _PNG,
        ),
        asset_store=store,
    )

    assert outcome.applied_visual_ids == ["visual_2", "visual_1"]
    visual_nodes = [
        node
        for node in outcome.document.content_json["content"]
        if node["type"] == "resourceVisualBlock"
    ]
    assert [node["attrs"]["visualId"] for node in visual_nodes] == ["visual_2", "visual_1"]
    assert outcome.skipped == []


@pytest.mark.parametrize(
    ("content_text", "target_anchor", "expected_reason"),
    [
        (
            "Repeated anchor\n\n{marker}\n\nRepeated anchor\n\n{marker}",
            "Repeated anchor",
            "marker_not_unique_and_standalone",
        ),
        (
            "Repeated anchor\n\n{marker}\n\nRepeated anchor",
            "Repeated anchor",
            "target_anchor_not_unique_or_adjacent",
        ),
    ],
)
def test_repeated_marker_or_target_does_not_block_materialization(
    tmp_path,
    content_text: str,
    target_anchor: str,
    expected_reason: str,
) -> None:
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(title="Board", content_text=content_text.format(marker=item.marker))
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, target_anchor)],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=_store(tmp_path),
    )

    assert outcome.applied_visual_ids == ["visual_1"]
    assert outcome.skipped == []
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text


def test_forged_marker_is_stripped_and_never_appended(tmp_path) -> None:
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    forged = "[[OPENCLASS_VISUAL_forged_0000]]"
    document = build_document(title="Board", content_text=f"Target paragraph\n\n{forged}")
    placement = _placement(item, "Target paragraph")
    placement["marker"] = forged
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[placement],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=_store(tmp_path),
    )

    assert outcome.applied_visual_ids == []
    assert {item["reason"] for item in outcome.skipped} == {"placement_missing", "unknown_marker"}
    assert "OPENCLASS_VISUAL" not in outcome.document.content_text


def test_structured_table_marker_becomes_editable_tiptap_table(tmp_path) -> None:
    visual = _visual(
        kind="table",
        table_data={"rows": [["Name", "Value"], ["A", "1"]]},
    )
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(title="Board", content_text=f"Table explanation\n\n{item.marker}")

    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, "Table explanation")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: pytest.fail("table must not request image bytes"),
        asset_store=_store(tmp_path),
    )

    table = outcome.document.content_json["content"][1]
    assert table["type"] == "table"
    assert table["attrs"]["sourceVisualId"] == "visual_1"
    assert table["attrs"]["sourceIngestionId"] == "source_1"
    round_tripped = html_to_tiptap_doc(outcome.document.content_html)
    assert round_tripped["content"][1]["attrs"]["sourceVisualId"] == "visual_1"
    assert "<table" in outcome.document.content_html


def test_docx_and_html_export_resolve_board_asset_and_keep_document_order(tmp_path) -> None:
    store = _store(tmp_path)
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    document = build_document(
        title="Board",
        content_text=f"Before visual\n\n{item.marker}\n\nAfter visual",
    )
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(item, "Before visual")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=store,
    )

    def resolver(asset_id: str):
        resolved = store.read_bytes(asset_id, "owner_a")
        return (resolved[0].mime_type, resolved[1]) if resolved else None

    html_path = export_html(outcome.document, tmp_path / "board.html", asset_resolver=resolver)
    html_content = html_path.read_text(encoding="utf-8")
    assert "data:image/png;base64," in html_content
    assert html_content.count("data:image/png;base64,") == 1
    assert "/api/board-assets/" not in html_content
    assert "data-original-src" not in html_content
    assert html_content.index("Before visual") < html_content.index("<img") < html_content.index("After visual")

    docx_path = export_docx(outcome.document, tmp_path / "board.docx", asset_resolver=resolver)
    with ZipFile(docx_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml")
    assert len(media) == 1
    assert document_xml.index(b"Before visual") < document_xml.index(b"<w:drawing") < document_xml.index(b"After visual")


def test_docx_export_embeds_two_visuals_in_exact_paragraph_order(tmp_path) -> None:
    import hashlib

    from PIL import Image

    image_bytes: dict[str, bytes] = {}
    for visual_id, color in (("visual_1", "red"), ("visual_2", "blue")):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), color=color).save(buffer, format="PNG")
        image_bytes[visual_id] = buffer.getvalue()

    visuals = [
        _visual("visual_1", order_index=1),
        _visual("visual_2", order_index=2),
    ]
    for visual in visuals:
        visual_id = str(visual["visual_id"])
        visual["asset_hash"] = hashlib.sha256(image_bytes[visual_id]).hexdigest()

    plan = build_board_insertion_plan(visuals, nonce="fixed")
    first, second = plan.items
    document = build_document(
        title="Board",
        content_text=f"A\n\n{first.marker}\n\nB\n\n{second.marker}\n\nC",
    )
    visual_by_id = {str(visual["visual_id"]): visual for visual in visuals}
    store = _store(tmp_path)
    outcome = apply_board_insertion_plan(
        document,
        plan=plan,
        placements=[_placement(first, "A"), _placement(second, "B")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda visual_id: (visual_by_id[visual_id], image_bytes[visual_id]),
        asset_store=store,
    )
    assert outcome.applied_visual_ids == ["visual_1", "visual_2"]

    def resolver(asset_id: str):
        resolved = store.read_bytes(asset_id, "owner_a")
        return (resolved[0].mime_type, resolved[1]) if resolved else None

    output = export_docx(outcome.document, tmp_path / "two-visuals.docx", asset_resolver=resolver)
    with ZipFile(output) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml")

    assert len(media) == 2
    assert document_xml.count(b"<w:drawing") == 2
    first_drawing = document_xml.index(b"<w:drawing")
    second_drawing = document_xml.index(b"<w:drawing", first_drawing + 1)
    assert (
        document_xml.index(b"<w:t>A</w:t>")
        < first_drawing
        < document_xml.index(b"<w:t>B</w:t>")
        < second_drawing
        < document_xml.index(b"<w:t>C</w:t>")
    )


def test_html_export_rebuilds_resource_image_from_content_json_after_editor_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    outcome = apply_board_insertion_plan(
        build_document(title="Board", content_text=f"Before visual\n\n{item.marker}"),
        plan=plan,
        placements=[_placement(item, "Before visual")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=store,
    )
    assert "<img" not in outcome.document.content_html
    round_tripped = outcome.document.model_copy(update={"content_html": outcome.document.content_html})

    def resolver(asset_id: str):
        resolved = store.read_bytes(asset_id, "owner_a")
        return (resolved[0].mime_type, resolved[1]) if resolved else None

    html_content = export_html(
        round_tripped,
        tmp_path / "round-tripped-board.html",
        asset_resolver=resolver,
    ).read_text(encoding="utf-8")

    assert "data:image/png;base64," in html_content
    assert '<img src="data:image/png;base64,' in html_content


def test_docx_export_scales_large_resource_image_to_page_width(tmp_path) -> None:
    import hashlib

    from docx import Document as DocxDocument
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (4000, 1000), color="white").save(buffer, format="PNG")
    large_png = buffer.getvalue()
    visual = _visual()
    visual["asset_hash"] = hashlib.sha256(large_png).hexdigest()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    store = _store(tmp_path)
    outcome = apply_board_insertion_plan(
        build_document(title="Board", content_text=f"Wide chart\n\n{item.marker}"),
        plan=plan,
        placements=[_placement(item, "Wide chart")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, large_png),
        asset_store=store,
    )

    def resolver(asset_id: str):
        resolved = store.read_bytes(asset_id, "owner_a")
        return (resolved[0].mime_type, resolved[1]) if resolved else None

    output = export_docx(outcome.document, tmp_path / "wide-chart.docx", asset_resolver=resolver)
    exported = DocxDocument(output)
    available_width = (
        exported.sections[-1].page_width
        - exported.sections[-1].left_margin
        - exported.sections[-1].right_margin
    )

    assert len(exported.inline_shapes) == 1
    assert exported.inline_shapes[0].width <= available_width


def test_docx_export_converts_webp_board_asset_to_embedded_png(tmp_path) -> None:
    import hashlib

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", (80, 60), color=(255, 255, 255, 128)).save(buffer, format="WEBP")
    webp = buffer.getvalue()
    visual = _visual()
    visual["mime_type"] = "image/webp"
    visual["asset_hash"] = hashlib.sha256(webp).hexdigest()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    store = _store(tmp_path)
    outcome = apply_board_insertion_plan(
        build_document(title="Board", content_text=f"Web chart\n\n{item.marker}"),
        plan=plan,
        placements=[_placement(item, "Web chart")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, webp),
        asset_store=store,
    )

    def resolver(asset_id: str):
        resolved = store.read_bytes(asset_id, "owner_a")
        return (resolved[0].mime_type, resolved[1]) if resolved else None

    output = export_docx(outcome.document, tmp_path / "webp-board.docx", asset_resolver=resolver)
    with ZipFile(output) as archive:
        media_names = [name for name in archive.namelist() if name.startswith("word/media/")]
        media_content = archive.read(media_names[0])

    assert len(media_names) == 1
    assert media_names[0].endswith(".png")
    assert media_content.startswith(b"\x89PNG\r\n\x1a\n")


def test_history_restore_keeps_permanent_asset_reference_after_reload(tmp_path) -> None:
    store = _store(tmp_path)
    visual = _visual()
    plan = build_board_insertion_plan([visual], nonce="fixed")
    item = plan.items[0]
    inserted = apply_board_insertion_plan(
        build_document(title="Board", content_text=f"Before visual\n\n{item.marker}"),
        plan=plan,
        placements=[_placement(item, "Before visual")],
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
        asset_store=store,
    )
    lesson = create_empty_lesson("Board")
    commit_operations(
        lesson,
        [],
        label="Visual board",
        message="Persisted visual board",
        new_document=inserted.document,
    )
    visual_commit_id = lesson.history_graph.commits[-1].id
    asset_id = inserted.asset_ids[0]
    persisted_snapshot = lesson.history_graph.commits[-1].snapshot
    persisted_visual = next(
        node for node in persisted_snapshot.content_json["content"] if node["type"] == "resourceVisualBlock"
    )
    assert persisted_visual["attrs"]["assetId"] == asset_id
    assert persisted_visual["attrs"]["originalSrc"] == ""
    assert "/api/board-assets/" not in persisted_snapshot.model_dump_json()
    assert "data:image/" not in persisted_snapshot.model_dump_json()
    commit_operations(
        lesson,
        [],
        label="Later edit",
        message="Removed visual from the latest document",
        new_document=build_document(
            title="Board",
            content_text="Later text only",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
    )
    reloaded = type(lesson).model_validate_json(lesson.model_dump_json())

    restore_commit(reloaded, visual_commit_id, "Restore visual board")

    restored_nodes = reloaded.board_document.content_json["content"]
    restored_visual = next(node for node in restored_nodes if node["type"] == "resourceVisualBlock")
    assert restored_visual["attrs"]["assetId"] == asset_id
    resolved = _store(tmp_path).read_bytes(asset_id, "owner_a")
    assert resolved is not None and resolved[1] == _PNG


def test_board_asset_endpoint_rejects_other_owner(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    store = _store(tmp_path)
    asset = store.put_bytes(
        owner_user_id="owner_a",
        lesson_id="lesson_a",
        content=_PNG,
        mime_type="image/png",
    )
    owner_b = UserView(
        id="owner_b",
        email="owner-b@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(documents_router, "get_board_asset_store", lambda: store)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: owner_b
    try:
        response = TestClient(main_module.app).get(asset.content_url)
    finally:
        main_module.app.dependency_overrides.clear()
    assert response.status_code == 404

    owner_a = owner_b.model_copy(update={"id": "owner_a", "email": "owner-a@example.com"})
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: owner_a
    try:
        response = TestClient(main_module.app).get(asset.content_url)
    finally:
        main_module.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.content == _PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"].startswith("inline;")

    store.resolve_path(asset).write_bytes(b"x" * len(_PNG))
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: owner_a
    try:
        response = TestClient(main_module.app).get(asset.content_url)
    finally:
        main_module.app.dependency_overrides.clear()
    assert response.status_code == 404

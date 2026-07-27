from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.models import EvidenceBundle, RetrievalEvidence
from app.services.history import commit_merge, commit_operations, create_branch, current_head_commit, switch_branch
from app.services.lesson_factory import create_empty_lesson
from app.services.lesson_package_format import (
    RidocAsset,
    RidocFormatError,
    RidocSourcePage,
    build_ridoc_archive,
    read_ridoc,
    write_ridoc,
)
from app.services.rich_document import build_document
from app.services.history import snapshot_lesson_runtime


def _commit(lesson, text: str, *, user: str, assistant: str, selection=None):
    document = build_document(
        title=lesson.board_document.title,
        content_text=text,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    commit_operations(
        lesson,
        [],
        label="Conversation",
        message="Completed a turn",
        new_document=document,
        metadata={
            "kind": "board_document_edit",
            "user_message": user,
            "assistant_message": assistant,
            "assistant_message_source": "codex",
            "selection": selection,
            "verified_source_reference_used": bool(selection),
            "verified_source_bundle_ids": ["bundle_1"] if selection else [],
            "document_changed": True,
            "api_key": "must-not-export",
            "source_path": "/Users/example/private.pdf",
        },
    )
    return current_head_commit(lesson).id


def _lesson_with_merge():
    lesson = create_empty_lesson("Portable lesson")
    base = _commit(
        lesson,
        "# Lesson\n\nBase",
        user="Start",
        assistant="Here is the beginning.",
        selection={
            "kind": "source",
            "source_ingestion_id": "source_1",
            "source_chapter_id": "chapter_1",
            "source_page_range": "10-12",
            "source_uri": "https://example.com/book.pdf?token=secret&lang=zh",
        },
    )
    create_branch(lesson, "source", base)
    source_head = _commit(lesson, "# Lesson\n\nBase\n\nSource", user="Source", assistant="Source branch")
    switch_branch(lesson, "main")
    _commit(lesson, "# Lesson\n\nBase\n\nTarget", user="Target", assistant="Target branch")
    merged = build_document(
        title=lesson.board_document.title,
        content_text="# Lesson\n\nBase\n\nTarget\n\nSource",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    commit_merge(
        lesson,
        source_head_commit_id=source_head,
        new_document=merged,
        runtime_snapshot=snapshot_lesson_runtime(lesson),
        label="Merge source",
        message="Merged source into main",
        metadata={"merge_source_branch": "source"},
    )
    return lesson


def test_ridoc_round_trip_preserves_merge_events_evidence_and_assets(tmp_path) -> None:
    lesson = _lesson_with_merge()
    evidence = EvidenceBundle(
        id="bundle_1",
        owner_user_id="user_private",
        package_id="package_private",
        lesson_id=lesson.id,
        status="confirmed",
        evidence_items=[
            RetrievalEvidence(
                id="evidence_1",
                source_ingestion_id="source_1",
                source_title="Reference",
                chapter_id="chapter_1",
                section_path=["Chapter"],
                page_range="10-12",
                chunk_ids=["chunk_1"],
                excerpt="Frozen evidence",
                expanded_text="Frozen evidence body",
                relevance_score=1,
                reason="Selected by learner",
                token_count=4,
            )
        ],
    )
    archive = build_ridoc_archive(
        lesson,
        evidence_bundles=[evidence],
        assets=[RidocAsset(original_id="asset_1", mime_type="image/png", file_name="figure.png", content=b"png")],
        source_pages=[
            RidocSourcePage(
                source_ingestion_id="source_1",
                source_title="Reference",
                source_file_name="reference.pdf",
                source_mime_type="application/pdf",
                source_content_hash="a" * 64,
                page_number=10,
                mime_type="image/png",
                content=b"rendered-page",
                width=1200,
                height=1600,
                reference_ids=("evidence_1",),
            )
        ],
    )
    target = write_ridoc(archive, tmp_path / "lesson.ridoc")
    restored = read_ridoc(target)

    assert restored.manifest["spec_version"] == "1.0"
    assert restored.graph["current_branch"] == "main"
    assert len(restored.graph["branches"]) == 2
    assert any(len(commit["parent_ids"]) == 2 for commit in restored.graph["commits"])
    assert any(event["type"] == "history.merge" for event in restored.events)
    assert any(event["type"] == "source.reference" for event in restored.events)
    assert restored.evidence["bundles"][0]["id"] == "bundle_1"
    assert "owner_user_id" not in restored.evidence["bundles"][0]
    serialized = json.dumps(restored.graph, ensure_ascii=False)
    assert "must-not-export" not in serialized
    assert "/Users/example" not in serialized
    assert "token=secret" not in serialized
    assert restored.manifest["asset_index"][0]["original_id"] == "asset_1"
    source_page = restored.manifest["source_page_index"][0]
    assert source_page["page_number"] == 10
    assert source_page["page_number_basis"] == "physical_1_based"
    assert source_page["reference_ids"] == ["evidence_1"]
    assert restored.assets[source_page["path"]] == b"rendered-page"
    assert restored.manifest["capabilities"]["cited_source_pages_embedded"] is True


def test_ridoc_rejects_checksum_tampering(tmp_path) -> None:
    target = write_ridoc(build_ridoc_archive(_lesson_with_merge()), tmp_path / "lesson.ridoc")
    tampered = tmp_path / "tampered.ridoc"
    with zipfile.ZipFile(target) as source, zipfile.ZipFile(tampered, "w") as destination:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "manifest.json":
                payload = payload.replace(b'"profile":"learning.lesson"', b'"profile":"learning.other"')
            destination.writestr(info, payload)

    with pytest.raises(RidocFormatError, match="checksum mismatch"):
        read_ridoc(tampered)


def test_ridoc_rejects_path_traversal(tmp_path) -> None:
    target = tmp_path / "unsafe.ridoc"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../manifest.json", "{}")

    with pytest.raises(RidocFormatError, match="unsafe path"):
        read_ridoc(target)


def test_ridoc_rejects_dangling_parent() -> None:
    archive = build_ridoc_archive(_lesson_with_merge())
    archive.graph["commits"][-1]["parent_ids"] = ["missing"]

    with pytest.raises(RidocFormatError, match="dangling parent"):
        write_ridoc(archive, Path("unused.ridoc"))

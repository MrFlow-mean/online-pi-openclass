from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.models import EvidenceBundle, RetrievalEvidence, SourceIngestionRecord
from app.services import lesson_package_source_pages
from app.services.history import commit_operations
from app.services.lesson_factory import create_empty_lesson
from app.services.lesson_package_export import export_lesson_ridoc
from app.services.lesson_package_format import read_ridoc
from app.services.rich_document import build_document


class _EvidenceStore:
    def __init__(self, *, bundle: EvidenceBundle, source: SourceIngestionRecord) -> None:
        self.bundle = bundle
        self.source = source

    def get_bundle(self, *, owner_user_id: str, bundle_id: str):
        if owner_user_id == self.source.owner_user_id and bundle_id == self.bundle.id:
            return self.bundle
        return None

    def get_source(self, *, owner_user_id: str, package_id: str, source_id: str):
        if (
            owner_user_id == self.source.owner_user_id
            and package_id == self.source.package_id
            and source_id == self.source.id
        ):
            return self.source
        return None


class _EmptyAssetStore:
    def references_for_lesson(self, *, owner_user_id: str, lesson_id: str):
        return []


def _write_scanned_pdf(path: Path) -> None:
    image = Image.new("RGB", (600, 800), "white")
    drawing = ImageDraw.Draw(image)
    drawing.text((60, 80), "Scanned source page", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    document = fitz.open()
    first = document.new_page(width=600, height=800)
    first.insert_text((72, 72), "Cover")
    second = document.new_page(width=600, height=800)
    second.insert_image(second.rect, stream=image_bytes)
    document.save(path)
    document.close()


def test_ridoc_embeds_only_referenced_scanned_pdf_page(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "scanned.pdf"
    _write_scanned_pdf(source_path)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = SourceIngestionRecord(
        id="source_scan",
        owner_user_id="user_1",
        package_id="package_1",
        title="Scanned material",
        file_name="scanned.pdf",
        mime_type="application/pdf",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"content_hash": source_hash},
    )
    bundle = EvidenceBundle(
        id="bundle_scan",
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_scan",
        status="confirmed",
        evidence_items=[
            RetrievalEvidence(
                id="evidence_page_2",
                source_ingestion_id=source.id,
                source_title=source.title,
                page_range="2",
                excerpt="Scanned source page",
                metadata={
                    "source_content_hash": source_hash,
                    "source_range": {
                        "kind": "pdf_pages",
                        "start": 2,
                        "end": 2,
                    }
                },
            )
        ],
    )
    lesson = create_empty_lesson("Scanned lesson")
    document = build_document(
        title=lesson.board_document.title,
        content_text="# Scanned lesson\n\nReferenced content",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    commit_operations(
        lesson,
        [],
        label="Use source",
        message="Referenced physical page 2",
        new_document=document,
        metadata={
            "kind": "board_document_edit",
            "selection": {
                "kind": "source",
                "source_ingestion_id": source.id,
                "source_title": source.title,
                "source_page_start": 2,
                "source_page_end": 2,
            },
            "verified_source_reference_used": True,
            "verified_source_bundle_ids": [bundle.id],
        },
    )
    store = _EvidenceStore(bundle=bundle, source=source)
    monkeypatch.setattr(
        lesson_package_source_pages,
        "source_download_path",
        lambda _source: source_path,
    )

    target = export_lesson_ridoc(
        owner_user_id="user_1",
        package_id="package_1",
        lesson=lesson,
        target_path=tmp_path / "scanned.ridoc",
        evidence_store=store,
        asset_store=_EmptyAssetStore(),
    )

    archive = read_ridoc(target)
    assert len(archive.manifest["source_page_index"]) == 1
    page = archive.manifest["source_page_index"][0]
    assert page["source_ingestion_id"] == source.id
    assert page["source_content_hash"] == source_hash
    assert page["page_number"] == 2
    assert "evidence_page_2" in page["reference_ids"]
    assert page["representation"] == "rendered_full_page"
    assert archive.assets[page["path"]].startswith(b"\x89PNG\r\n\x1a\n")
    assert not any("page-000001" in path for path in archive.assets)
    assert archive.manifest["capabilities"]["cited_source_pages_complete"] is True

    with source_path.open("ab") as handle:
        handle.write(b"\nchanged-after-grounding")
    changed_target = export_lesson_ridoc(
        owner_user_id="user_1",
        package_id="package_1",
        lesson=lesson,
        target_path=tmp_path / "scanned-changed.ridoc",
        evidence_store=store,
        asset_store=_EmptyAssetStore(),
    )
    changed_archive = read_ridoc(changed_target)
    assert changed_archive.manifest["source_page_index"] == []
    assert changed_archive.manifest["capabilities"]["cited_source_pages_complete"] is False
    assert any(
        "original file changed" in warning
        for warning in changed_archive.manifest["warnings"]
    )

from __future__ import annotations

import base64
import zipfile
from pathlib import Path
from uuid import uuid4

from reportlab.pdfgen import canvas

from app.models import (
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisualAsset,
)
from app.services import pdf_toc_parser, source_structure_indexer as indexer_module
from app.services.image_ocr import OCRLineLayout, OCRPageLayout, ordered_ocr_lines
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import (
    CURRENT_SOURCE_STRUCTURE_INDEX_VERSION,
    DetectedChapter,
    PageText,
    ParsedSourceDocument,
    SourceStructureIndexer,
    _EpubNavigationItem,
    _canonical_structural_title_from_body,
    _chapters_from_epub_nav,
    _chapter_for_chunk,
    _close_chapter_ranges,
    _detected_pdf_toc_pages,
    _pdf_outline_chapters,
    _pdf_toc_chapters,
    _looks_like_toc_page,
    _parse_toc_line,
    _reanchor_existing_visuals,
    _verify_pdf_toc_nodes,
)
from app.services.source_structure_store import SourceStructureStore
from app.services.source_visual_extraction import (
    CURRENT_SOURCE_VISUAL_INDEX_VERSION,
    SourceVisualExtractionResult,
)


def _source_record(path: Path, *, mime_type: str) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=f"source_{path.stem}",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=path.name,
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(path)},
    )


def test_epub_navigation_builds_verified_stable_chapter_identity(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path)
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(epub_path, mime_type="application/epub+zip")
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    first = indexer.rebuild_structure(record)
    first_view = structure_store.get_structure_view(source=record)
    indexer.rebuild_structure(record)
    second_view = structure_store.get_structure_view(source=record)

    assert first.status == "ready"
    assert first.strategy == "epub_navigation"
    assert [chapter.normalized_number for chapter in first_view.chapters] == ["1", "1.1"]
    assert all(chapter.anchor_status == "verified" for chapter in first_view.chapters)
    assert {tuple(chapter.path): chapter.id for chapter in first_view.chapters} == {
        tuple(chapter.path): chapter.id for chapter in second_view.chapters
    }
    assert len(first_view.visuals) == 1
    assert first_view.visuals[0].kind == "image"
    assert first_view.visuals[0].caption == "A grounded figure"
    assert first_view.visuals[0].chapter_id == first_view.chapters[0].id
    assert first_view.visuals[0].content_hash == second_view.visuals[0].content_hash
    assert first_view.visuals[0].asset_path
    assert "asset_path" not in first_view.visuals[0].model_dump(mode="json")


def test_epub_ncx_preserves_native_parent_child_hierarchy(tmp_path: Path) -> None:
    epub_path = tmp_path / "nested-book.epub"
    _write_nested_ncx_epub(epub_path)
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(epub_path, mime_type="application/epub+zip")
    source_store.save_source(record)

    SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    chapters = structure_store.get_structure_view(source=record).chapters

    assert [chapter.title for chapter in chapters] == [
        "Part I",
        "Overview",
        "Details",
        "Part II",
    ]
    assert [chapter.level for chapter in chapters] == [1, 2, 3, 1]
    assert chapters[1].parent_id == chapters[0].id
    assert chapters[2].parent_id == chapters[1].id
    assert chapters[3].parent_id is None
    assert chapters[2].path == ["Part I", "Overview", "Details"]
    assert chapters[0].source_locator == "epub:OEBPS/cover.xhtml"
    assert chapters[1].source_locator == "epub:OEBPS/overview.xhtml#overview"
    assert chapters[0].body_start_offset == chapters[1].body_start_offset
    assert chapters[0].body_end_offset == chapters[3].body_start_offset


def test_epub_target_file_without_fragment_or_title_remains_unverified() -> None:
    chapters = _chapters_from_epub_nav(
        [
            _EpubNavigationItem(
                target="OEBPS/chapter.xhtml",
                fragment="",
                label="Missing navigation label",
                order=0,
                level=1,
            )
        ],
        [("OEBPS/chapter.xhtml", "Actual body text", [], {})],
        {"OEBPS/chapter.xhtml": 100},
    )

    assert chapters[0].verified is False
    assert chapters[0].start_offset is None
    assert chapters[0].metadata["anchor_source"] == "target_file_only"


def test_epub_fragment_can_ground_a_navigation_label_that_differs_from_body_text() -> None:
    chapters = _chapters_from_epub_nav(
        [
            _EpubNavigationItem(
                target="OEBPS/chapter.xhtml",
                fragment="section-anchor",
                label="Display label",
                order=0,
                level=1,
            )
        ],
        [
            (
                "OEBPS/chapter.xhtml",
                "Actual body text",
                [],
                {"section-anchor": 7},
            )
        ],
        {"OEBPS/chapter.xhtml": 100},
    )

    assert chapters[0].verified is True
    assert chapters[0].start_offset == 107
    assert chapters[0].metadata["anchor_source"] == "epub_fragment"


def test_failed_visual_rebuild_still_persists_new_epub_structure(tmp_path: Path) -> None:
    class FailedVisualExtractor:
        @staticmethod
        def extract(**_kwargs):
            return SourceVisualExtractionResult(
                status="failed",
                warnings=["visual object budget exceeded"],
            )

    epub_path = tmp_path / "nested-book.epub"
    _write_nested_ncx_epub(epub_path)
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    record = _source_record(epub_path, mime_type="application/epub+zip")
    source_store.save_source(record)
    structure_store.save_structure_bundle(
        structure=SourceStructure(
            id="structure_stale",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="ready",
            strategy="epub_navigation",
            visual_index_status="failed",
            visual_index_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            metadata={"structure_index_version": CURRENT_SOURCE_STRUCTURE_INDEX_VERSION},
        ),
        chapters=[
            SourceChapter(
                id="chapter_stale",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                title="Stale chapter",
                body_start_offset=0,
                body_end_offset=13,
                anchor_status="verified",
            )
        ],
        chunks=[
            SourceChunk(
                id="chunk_stale",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                chapter_id="chapter_stale",
                text="Stale chapter",
                start_offset=0,
                end_offset=13,
            )
        ],
    )

    rebuilt = SourceStructureIndexer(
        store=structure_store,
        visual_extractor=FailedVisualExtractor(),
    ).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert rebuilt.status == "ready"
    assert rebuilt.visual_index_status == "failed"
    assert "visual object budget exceeded" in rebuilt.warnings
    assert [chapter.title for chapter in view.chapters] == [
        "Part I",
        "Overview",
        "Details",
        "Part II",
    ]
    assert [chunk.text for chunk in view.chunks] != ["Stale chapter"]


def test_chapter_range_closing_does_not_flatten_navigation_order() -> None:
    chapters = [
        DetectedChapter(title="Parent", level=1, start_offset=100),
        DetectedChapter(title="Child", level=2, start_offset=20),
    ]

    _close_chapter_ranges(chapters, 200)

    assert [chapter.title for chapter in chapters] == ["Parent", "Child"]


def test_plain_text_without_headings_uses_linear_chunks_without_fake_toc(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(text_path, mime_type="text/plain")
    source_store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "linear_only"
    assert structure.has_verified_toc is False
    assert view.chapters == []
    assert view.chunks


def test_bookmarkless_pdf_toc_uses_verified_printed_page_mapping() -> None:
    raw_pages = [
        "封面",
        "目录\n第一章 总论…………1\n第一节 基础概念…………3\n第二章 后续主题…………8",
        "前言",
        "第一章 总论\n本章正文。",
        "正文续页",
        "第一节 基础概念\n本节正文。",
        "正文续页",
        "正文续页",
        "正文续页",
        "正文续页",
        "第二章 后续主题\n后续正文。",
    ]
    pages: list[PageText] = []
    offset = 0
    for page_no, text in enumerate(raw_pages, start=1):
        pages.append(PageText(page_no=page_no, text=text, start_offset=offset, end_offset=offset + len(text)))
        offset += len(text)

    chapters = _pdf_toc_chapters(pages, "".join(raw_pages))

    assert [chapter.title for chapter in chapters] == ["第一章 总论", "第一节 基础概念", "第二章 后续主题"]
    assert [chapter.page_start for chapter in chapters] == [4, 6, 11]
    assert [chapter.level for chapter in chapters] == [1, 2, 1]
    assert all(chapter.verified for chapter in chapters)
    assert all(chapter.metadata["printed_page_offset"] == 3 for chapter in chapters)


def test_spaced_toc_heading_and_private_use_leaders_are_normalized() -> None:
    toc_text = "目\u3000 \u3000录\n第一章 通用主题 １" + "\U001001ba" * 6

    assert _looks_like_toc_page(toc_text) is True
    assert _parse_toc_line("第一章 通用主题 １" + "\U001001ba" * 6) == (
        "第一章 通用主题",
        1,
    )
    marker = pdf_toc_parser.parse_structural_heading("第一节 基础概念")
    assert marker is not None
    assert (marker.kind, marker.level, marker.number) == ("section", 2, "一")
    assert _parse_toc_line("正文中的数值 0\U001001b05") is None


def test_toc_page_detection_stops_before_body_pages() -> None:
    pages = [
        PageText(page_no=1, text="封面"),
        PageText(page_no=2, text="目  录\n第一章 总论……1\n第一节 基础……3\n第二章 后续……8"),
        PageText(page_no=3, text="第三章 进阶……20\n第一节 展开……20\n第二节 深入……25"),
        PageText(page_no=4, text="第一章 总论\n第一节 基础\n这是正文内容。"),
        PageText(page_no=5, text="习题中的第1章引用 3……5\n其他正文"),
    ]

    assert [page.page_no for page in _detected_pdf_toc_pages(pages)] == [2, 3]


def test_layout_toc_splits_columns_before_grouping_rows() -> None:
    layout = OCRPageLayout(
        page_no=2,
        lines=[
            OCRLineLayout("第一章 总论", x=0.10, y=0.90, width=0.24, height=0.02),
            OCRLineLayout("1", x=0.45, y=0.90, width=0.02, height=0.02),
            OCRLineLayout("第一节 基础概念", x=0.15, y=0.80, width=0.26, height=0.02),
            OCRLineLayout("3", x=0.45, y=0.80, width=0.02, height=0.02),
            OCRLineLayout("第二章 后续主题", x=0.55, y=0.90, width=0.26, height=0.02),
            OCRLineLayout("20", x=0.90, y=0.90, width=0.03, height=0.02),
            OCRLineLayout("第一节 继续学习", x=0.60, y=0.80, width=0.24, height=0.02),
            OCRLineLayout("20", x=0.90, y=0.80, width=0.03, height=0.02),
        ],
    )

    rows = pdf_toc_parser._toc_rows([layout])
    pdf_toc_parser._assign_levels(rows)

    assert [row.title for row in rows] == [
        "第一章 总论",
        "第一节 基础概念",
        "第二章 后续主题",
        "第一节 继续学习",
    ]
    assert [row.printed_page for row in rows] == [1, 3, 20, 20]
    assert [row.level for row in rows] == [1, 2, 1, 2]


def test_ocr_page_lines_follow_deterministic_column_reading_order() -> None:
    lines = [
        *[
            OCRLineLayout(f"R{index}", x=0.72, y=1 - index * 0.1)
            for index in range(1, 6)
        ],
        *[
            OCRLineLayout(f"L{index}", x=0.10, y=1 - index * 0.1)
            for index in range(1, 6)
        ],
    ]

    ordered = ordered_ocr_lines(list(reversed(lines)))

    assert [line.text for line in ordered] == [
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
    ]


def test_layout_toc_keeps_same_title_at_chapter_and_section_levels() -> None:
    layout = _toc_layout_page(
        2,
        [
            ("第二章 同名主题", 29, 0.90, 0.20),
            ("第一节 同名主题", 29, 0.80, 0.24),
        ],
    )

    rows = pdf_toc_parser._toc_rows([layout])

    assert [(row.title, row.printed_page) for row in rows] == [
        ("第二章 同名主题", 29),
        ("第一节 同名主题", 29),
    ]


def test_body_heading_canonicalizes_noisy_ocr_title() -> None:
    node = pdf_toc_parser.PdfTocNode(
        title="第四节 正态总体的抽样分布 ⋯149",
        printed_page=149,
        toc_page=4,
        level=2,
        number="四",
    )

    assert _canonical_structural_title_from_body(
        "第四节 正态总体的抽样分布\n本节正文。",
        node,
    ) == "第四节 正态总体的抽样分布"


def test_layout_toc_keeps_a_structural_marker_split_from_its_title() -> None:
    row = pdf_toc_parser._parse_layout_row(
        [
            OCRLineLayout("第五节", x=0.14, y=0.45, width=0.06, height=0.02),
            OCRLineLayout("两个总体下未知参数", x=0.22, y=0.45, width=0.24, height=0.02),
        ],
        toc_page=3,
    )

    assert row is not None
    pdf_toc_parser._assign_levels([row])
    assert (row.title, row.number, row.level) == ("第五节 两个总体下未知参数", "五", 2)


def test_layout_toc_keeps_chapters_and_sections_at_distinct_levels() -> None:
    rows = [
        pdf_toc_parser._RawTocRow("第一章 总论", 1, 3, 0.10, 0.02),
        pdf_toc_parser._RawTocRow("第一节 基础概念", 1, 3, 0.15, 0.02),
    ]

    pdf_toc_parser._assign_levels(rows)

    assert [row.level for row in rows] == [1, 2]
    assert pdf_toc_parser._printed_page_number("⋯（12）") == 12


def test_leading_pdf_toc_probe_extends_past_the_first_probe_batch(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    def fake_extract(_path: Path, *, page_start: int, page_end: int):
        calls.append((page_start, page_end))
        last_page = 8 if page_start == 1 else 13
        nodes = [
            node
            for page_no in range(6, last_page + 1)
            for node in (
                pdf_toc_parser.PdfTocNode(
                    title=f"第{page_no}章 主题",
                    printed_page=page_no,
                    toc_page=page_no,
                    level=1,
                ),
                pdf_toc_parser.PdfTocNode(
                    title=f"第一节 子题 {page_no}",
                    printed_page=page_no,
                    toc_page=page_no,
                    level=2,
                ),
            )
        ]
        return pdf_toc_parser.PdfTocExtraction(nodes=nodes)

    monkeypatch.setattr(pdf_toc_parser, "extract_pdf_toc_from_range", fake_extract)

    extraction = pdf_toc_parser.probe_pdf_toc_from_leading_pages(
        Path("source.pdf"),
        page_count=100,
        max_probe_pages=48,
    )

    assert calls == [(1, 8), (6, 29)]
    assert extraction.toc_page_start == 6
    assert extraction.toc_page_end == 13
    assert sorted({node.toc_page for node in extraction.nodes}) == list(range(6, 14))


def test_layout_toc_nodes_are_verified_by_printed_page_offset() -> None:
    nodes = [
        pdf_toc_parser.PdfTocNode(title="第一章 总论", printed_page=1, toc_page=2, level=1),
        pdf_toc_parser.PdfTocNode(title="第一节 基础概念", printed_page=1, toc_page=2, level=2),
    ]
    pages = [
        PageText(page_no=1, text="封面", start_offset=0),
        PageText(page_no=2, text="目录", start_offset=2),
        PageText(page_no=3, text="第一章 总论\n第一节 基础概念", start_offset=4),
    ]

    offset, support = _verify_pdf_toc_nodes(nodes, pages)

    assert (offset, support) == (2, 2)
    assert [node.physical_page for node in nodes] == [3, 3]
    assert all(node.verified for node in nodes)


def test_layout_toc_infers_a_missing_printed_page_from_body_anchor() -> None:
    nodes = [
        pdf_toc_parser.PdfTocNode(title="第一章 总论", printed_page=1, toc_page=2, level=1),
        pdf_toc_parser.PdfTocNode(title="第一节 基础概念", printed_page=0, toc_page=2, level=2),
        pdf_toc_parser.PdfTocNode(title="第二章 后续主题", printed_page=8, toc_page=2, level=1),
    ]
    pages = [PageText(page_no=page_no, text="", start_offset=page_no) for page_no in range(1, 12)]
    pages[3 - 1].text = "第一章 总论"
    pages[10 - 1].text = "第二章 后续主题"

    offset, support = _verify_pdf_toc_nodes(nodes, pages)

    assert (offset, support) == (2, 2)
    assert nodes[1].printed_page == 1
    assert nodes[1].physical_page == 3
    assert nodes[1].metadata["printed_page_inferred"] is True


def test_failed_rebuild_keeps_last_usable_structure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "stable.md"
    path.write_text("# 1 Foundations\n\nBody.", encoding="utf-8")
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(path, mime_type="text/markdown")
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)
    indexer.rebuild_structure(record)
    before = structure_store.get_structure_view(source=record)
    monkeypatch.setattr(
        indexer,
        "_parse_record",
        lambda _record: (_ for _ in ()).throw(RuntimeError("parse failed")),
    )

    recovered = indexer.rebuild_structure(record)
    after = structure_store.get_structure_view(source=record)

    assert recovered.status == "ready"
    assert recovered.error == "parse failed"
    assert after.chapters == before.chapters
    assert after.chunks == before.chunks


def test_missing_local_source_and_url_remain_distinguishable(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    missing_name = f"missing-{uuid4().hex}.txt"
    missing = SourceIngestionRecord(
        id="source_missing",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=missing_name,
        source_type="local_file",
        file_name=missing_name,
        mime_type="text/plain",
        status="ready",
    )
    url = SourceIngestionRecord(
        id="source_url",
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Example",
        source_type="web_url",
        source_uri="https://example.com/article",
        mime_type="text/html",
        status="ready",
    )
    indexer = SourceStructureIndexer(store=store)

    missing_structure = indexer.rebuild_structure(missing)
    url_structure = indexer.rebuild_structure(url)

    assert missing_structure.metadata["missing_local_source_path"] is True
    assert missing_structure.strategy == "linear_text"
    assert url_structure.strategy == "linear_text"


def test_pdf_outline_merges_with_structured_toc_rows(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "merged.pdf"
    _write_pdf_with_outline(pdf_path)
    monkeypatch.setattr(
        pdf_toc_parser,
        "extract_pdf_pages_layout",
        lambda *_args, **_kwargs: [
            _toc_layout_page(
                1,
                [
                    ("1 Intro", 1, 0.90, 0.20),
                    ("1.1 Details", 1, 0.84, 0.24),
                    ("1.2 More", 2, 0.78, 0.24),
                    ("2 Next", 3, 0.72, 0.20),
                ],
            )
        ],
    )
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(pdf_path, mime_type="application/pdf")
    source_store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.strategy == "pdf_merged_toc"
    assert [chapter.normalized_number for chapter in view.chapters] == ["", "1", "1.1", "1.2", "2"]
    assert all(chapter.anchor_status == "verified" for chapter in view.chapters)
    assert any(visual.kind == "diagram" and visual.page_start == 2 for visual in view.visuals)

    visual_paths = [Path(visual.asset_path) for visual in view.visuals if visual.asset_path]
    structure_store.delete_for_source(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert structure_store.get_structure_view(source=record).visuals == []
    assert all(not path.exists() for path in visual_paths)


def test_nested_pdf_outline_remains_authoritative_without_toc_ocr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "nested-outline.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    pdf.bookmarkPage("chapter-1")
    pdf.addOutlineEntry("1 Intro", "chapter-1", level=0)
    pdf.drawString(72, 720, "1 Intro")
    pdf.showPage()
    pdf.bookmarkPage("section-1-1")
    pdf.addOutlineEntry("1.1 Details", "section-1-1", level=1)
    pdf.drawString(72, 720, "1.1 Details")
    pdf.drawString(72, 690, "Grounded body text.")
    pdf.save()

    def reject_toc_ocr(*_args, **_kwargs):
        raise AssertionError("a nested native outline must not be replaced by TOC OCR")

    monkeypatch.setattr(indexer_module, "extract_pdf_toc", reject_toc_ocr)

    parsed = indexer_module._parse_pdf(pdf_path)

    assert parsed.strategy == "pdf_outline"
    assert [chapter.title for chapter in parsed.chapters] == ["1 Intro", "1.1 Details"]
    assert [chapter.level for chapter in parsed.chapters] == [1, 2]
    assert all(chapter.verified for chapter in parsed.chapters)


def test_deterministic_pdf_directory_is_preserved_without_visual_indexing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "openclass.sqlite3"
    store = SourceStructureStore(database)
    record = SourceIngestionRecord(
        id="source_deterministic_pdf",
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Deterministic PDF",
        source_type="local_file",
        file_name="deterministic.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        status="ready",
    )
    page_text = "1 Intro\nGrounded body text."
    parsed = ParsedSourceDocument(
        text=page_text,
        chapters=[
            DetectedChapter(
                title="1 Intro",
                number="1",
                level=1,
                source_locator="pdf:outline:1",
                start_offset=0,
                end_offset=len(page_text),
                page_start=1,
                page_end=2,
                confidence=0.95,
                verified=True,
                metadata={"source": "pdf_outline"},
            )
        ],
        pages=[
            PageText(
                page_no=1,
                text=page_text,
                start_offset=0,
                end_offset=len(page_text),
                content_start_offset=0,
            )
        ],
        strategy="pdf_outline",
        metadata={"parser": "pdf"},
    )

    class UnexpectedVisualExtractor:
        @staticmethod
        def extract(**_kwargs):
            raise AssertionError("text-first source indexing must skip visual extraction")

    indexer = SourceStructureIndexer(
        store=store,
        visual_extractor=UnexpectedVisualExtractor(),
        visual_index_enabled=False,
    )
    monkeypatch.setattr(indexer, "_parse_record", lambda *_args, **_kwargs: parsed)

    structure = indexer.rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert structure.strategy == "pdf_outline"
    assert structure.visual_index_status == "unsupported"
    assert [chapter.normalized_number for chapter in view.chapters] == ["1"]


def test_large_blank_pdf_ocr_ignores_synthetic_page_markers_and_keeps_all_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "blank-scan.pdf"
    document = canvas.Canvas(str(pdf_path))
    for _ in range(205):
        document.showPage()
    document.save()
    calls: list[dict[str, int]] = []

    def fake_layouts(_path: Path, **kwargs: int) -> list[OCRPageLayout]:
        calls.append(kwargs)
        return [
            OCRPageLayout(
                page_no=1,
                lines=[OCRLineLayout("Recovered scan text", x=0.1, y=0.9)],
            )
        ]

    monkeypatch.setattr(indexer_module, "extract_pdf_pages_layout", fake_layouts)

    parsed = indexer_module._parse_pdf(pdf_path)

    assert calls == [{"page_start": 1, "page_end": 205, "max_pages": 200}]
    assert parsed.metadata["ocr_attempted"] is True
    assert parsed.metadata["ocr"] is True
    assert parsed.metadata["ocr_replaced_page_count"] == 1
    assert len(parsed.pages) == 205
    assert parsed.pages[-1].page_no == 205
    assert parsed.pages[0].text == "Recovered scan text"
    assert "其余页面保留原文字层" in " ".join(parsed.warnings)


def test_unresolved_pdf_outline_destination_is_not_verified_from_toc_text() -> None:
    class OutlineItem:
        title = "6.6.2 Binary heap implementation"

    class Reader:
        outline = [OutlineItem()]

        @staticmethod
        def get_destination_page_number(_item: object) -> int:
            raise ValueError("broken destination")

    pages = [
        PageText(
            page_no=1,
            text="Contents 6.6.2 Binary heap implementation",
            start_offset=0,
            end_offset=43,
        )
    ]

    chapters = _pdf_outline_chapters(
        Reader(),
        pages,
        "Contents 6.6.2 Binary heap implementation",
    )

    assert len(chapters) == 1
    assert chapters[0].verified is False
    assert chapters[0].start_offset is None
    assert chapters[0].page_start is None
    assert chapters[0].metadata["verification"] == "destination_unresolved"


def test_pdf_outline_titles_on_same_page_receive_distinct_body_offsets() -> None:
    class OutlineItem:
        def __init__(self, title: str) -> None:
            self.title = title

    class Reader:
        outline = [OutlineItem("3.4.1 Queue ADT"), OutlineItem("3.4.2 Queue implementation")]

        @staticmethod
        def get_destination_page_number(_item: object) -> int:
            return 0

    page_text = "3.4.1  Queue ADT\nShort body.\n3.4.2 Queue implementation\nLong body."
    page_prefix = "\n\n[Page 90]\n"
    pages = [
        PageText(
            page_no=90,
            text=page_text,
            start_offset=0,
            end_offset=len(page_prefix + page_text),
        )
    ]

    chapters = _pdf_outline_chapters(Reader(), pages, page_prefix + page_text)

    assert [chapter.verified for chapter in chapters] == [True, True]
    assert chapters[0].start_offset == len(page_prefix)
    assert chapters[1].start_offset == len(page_prefix) + page_text.index("3.4.2")
    assert chapters[0].start_offset < chapters[1].start_offset


def test_chunk_uses_physical_pages_instead_of_chapter_page_metadata(tmp_path: Path) -> None:
    path = tmp_path / "physical-pages.txt"
    path.write_text("A" * 200, encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    parsed = ParsedSourceDocument(
        text="A" * 200,
        pages=[
            PageText(page_no=1, text="A" * 100, start_offset=0, end_offset=100),
            PageText(page_no=2, text="A" * 100, start_offset=100, end_offset=200),
        ],
    )
    malformed_chapter = SourceChapter(
        id="chapter_broad",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        title="Broad chapter",
        body_start_offset=0,
        body_end_offset=200,
        page_start=None,
        page_end=None,
        anchor_status="verified",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    chunks = SourceStructureIndexer(store=store)._chunks_for_record(
        record,
        parsed,
        [malformed_chapter],
    )

    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 3

    SourceEvidenceStore(tmp_path / "openclass.sqlite3").save_source(record)
    store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="ready",
        ),
        chapters=[malformed_chapter],
        chunks=chunks,
    )
    evidence = store.page_range_evidence(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        page_start=2,
        page_end=3,
        token_budget=10_000,
    )
    assert evidence
    assert evidence[0].expanded_text == "A" * 200


def test_semantic_visual_reanchor_survives_chapter_id_upgrade() -> None:
    old_chapter = SourceChapter(
        id="sourcechapter_v1_old",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        title="Grounded section",
        path=["Part I", "Grounded section"],
        level=2,
        source_locator="epub:OEBPS/chapter.xhtml#old",
        body_start_offset=0,
        body_end_offset=100,
        anchor_status="verified",
    )
    new_chapter = old_chapter.model_copy(
        update={
            "id": "sourcechapter_v2_new",
            "source_locator": "epub:OEBPS/chapter.xhtml#new",
        }
    )
    chunk = SourceChunk(
        id="sourcechunk_new",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=new_chapter.id,
        source_locator="",
        text="Grounded section body",
        start_offset=0,
        end_offset=23,
    )
    visual = SourceVisualAsset(
        id="sourcevisual_1",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=old_chapter.id,
        source_locator="epub:OEBPS/chapter.xhtml:image:0",
        anchor_status="verified",
    )

    [reanchored] = _reanchor_existing_visuals(
        [visual],
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="pkg_1",
            source_ingestion_id="source_1",
        ),
        chapters=[new_chapter],
        chunks=[chunk],
        previous_chapters=[old_chapter],
    )

    assert reanchored.chapter_id == new_chapter.id
    assert reanchored.before_chunk_id == chunk.id
    assert reanchored.after_chunk_id == chunk.id
    assert reanchored.anchor_status == "verified"
    assert reanchored.metadata["structure_reanchor_method"] == "semantic_chapter"


def test_semantic_visual_reanchor_uses_chapter_number_for_repeated_titles() -> None:
    common = {
        "owner_user_id": "user_1",
        "package_id": "pkg_1",
        "source_ingestion_id": "source_1",
        "title": "Repeated section",
        "path": ["Part I", "Repeated section"],
        "level": 2,
        "source_locator": "epub:OEBPS/shared.xhtml",
        "body_start_offset": 0,
        "body_end_offset": 100,
        "anchor_status": "verified",
    }
    old_first = SourceChapter(
        id="sourcechapter_v1_first",
        number="1",
        normalized_number="1",
        order_index=0,
        **common,
    )
    old_second = SourceChapter(
        id="sourcechapter_v1_second",
        number="2",
        normalized_number="2",
        order_index=1,
        **common,
    )
    new_first = old_first.model_copy(
        update={"id": "sourcechapter_v2_first", "order_index": 1}
    )
    new_second = old_second.model_copy(
        update={"id": "sourcechapter_v2_second", "order_index": 0}
    )
    chunk = SourceChunk(
        id="sourcechunk_second",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=new_second.id,
        source_locator="",
        text="Second repeated section body",
        start_offset=0,
        end_offset=28,
    )
    visual = SourceVisualAsset(
        id="sourcevisual_second",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=old_second.id,
        source_locator="epub:OEBPS/shared.xhtml:image:0",
        anchor_status="verified",
    )

    [reanchored] = _reanchor_existing_visuals(
        [visual],
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="pkg_1",
            source_ingestion_id="source_1",
        ),
        chapters=[new_first, new_second],
        chunks=[chunk],
        previous_chapters=[old_first, old_second],
    )

    assert reanchored.chapter_id == new_second.id
    assert reanchored.before_chunk_id == chunk.id
    assert reanchored.anchor_status == "verified"
    assert reanchored.metadata["structure_reanchor_method"] == "semantic_chapter"


def test_visual_direct_id_cannot_bypass_demoted_chapter_anchor() -> None:
    chapter = SourceChapter(
        id="sourcechapter_demoted",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        title="Unverified section",
        path=["Unverified section"],
        level=1,
        source_locator="epub:OEBPS/chapter.xhtml",
        body_start_offset=0,
        body_end_offset=100,
        anchor_status="unverified",
    )
    visual = SourceVisualAsset(
        id="sourcevisual_1",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=chapter.id,
        source_locator="epub:OEBPS/chapter.xhtml:image:0",
        anchor_status="verified",
    )

    [reanchored] = _reanchor_existing_visuals(
        [visual],
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="pkg_1",
            source_ingestion_id="source_1",
        ),
        chapters=[chapter],
        chunks=[],
        previous_chapters=[chapter],
    )

    assert reanchored.chapter_id is None
    assert reanchored.anchor_status == "unverified"
    assert reanchored.metadata["structure_reanchor_method"] == "unresolved"


def test_semantic_visual_reanchor_uses_occurrence_for_identical_chapters() -> None:
    common = {
        "owner_user_id": "user_1",
        "package_id": "pkg_1",
        "source_ingestion_id": "source_1",
        "title": "Repeated section",
        "path": ["Part I", "Repeated section"],
        "level": 2,
        "source_locator": "epub:OEBPS/shared.xhtml",
        "body_start_offset": 0,
        "body_end_offset": 100,
        "anchor_status": "verified",
    }
    old_first = SourceChapter(
        id="sourcechapter_v1_first",
        order_index=0,
        **common,
    )
    old_second = SourceChapter(
        id="sourcechapter_v1_second",
        order_index=1,
        **common,
    )
    new_first = old_first.model_copy(update={"id": "sourcechapter_v2_first"})
    new_second = old_second.model_copy(update={"id": "sourcechapter_v2_second"})
    chunk = SourceChunk(
        id="sourcechunk_second",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=new_second.id,
        source_locator="",
        text="Second repeated section body",
        start_offset=0,
        end_offset=28,
    )
    visual = SourceVisualAsset(
        id="sourcevisual_second",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id="source_1",
        chapter_id=old_second.id,
        source_locator="epub:OEBPS/shared.xhtml:image:0",
        anchor_status="verified",
    )

    [reanchored] = _reanchor_existing_visuals(
        [visual],
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="pkg_1",
            source_ingestion_id="source_1",
        ),
        chapters=[new_first, new_second],
        chunks=[chunk],
        previous_chapters=[old_first, old_second],
    )

    assert reanchored.chapter_id == new_second.id
    assert reanchored.before_chunk_id == chunk.id
    assert reanchored.anchor_status == "verified"
    assert reanchored.metadata["structure_reanchor_method"] == "semantic_occurrence"


def test_chunk_prefers_nearest_containing_chapter_over_broad_range() -> None:
    chapter_id, _page_start, _page_end = _chapter_for_chunk(
        450,
        550,
        [
            ("chapter_wrong_broad", 0, 1_000, None, None, 3),
            ("chapter_correct_nearby", 400, 600, 5, 7, 2),
        ],
    )

    assert chapter_id == "chapter_correct_nearby"


def test_chunks_stop_at_verified_chapter_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "short-chapters.txt"
    path.write_text("A" * 30 + "B" * 70, encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    parsed = ParsedSourceDocument(text="A" * 30 + "B" * 70)
    chapters = [
        SourceChapter(
            id="chapter_short",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            title="Short chapter",
            body_start_offset=0,
            body_end_offset=30,
            anchor_status="verified",
        ),
        SourceChapter(
            id="chapter_next",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            title="Next chapter",
            body_start_offset=30,
            body_end_offset=100,
            anchor_status="verified",
        ),
    ]

    chunks = SourceStructureIndexer(
        store=SourceStructureStore(tmp_path / "openclass.sqlite3")
    )._chunks_for_record(record, parsed, chapters)

    assert [(chunk.chapter_id, chunk.text) for chunk in chunks] == [
        ("chapter_short", "A" * 30),
        ("chapter_next", "B" * 70),
    ]


def test_legacy_structure_version_is_lazily_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_text("Current body", encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    database = tmp_path / "openclass.sqlite3"
    SourceEvidenceStore(database).save_source(record)
    store = SourceStructureStore(database)
    store.save_structure_bundle(
        structure=SourceStructure(
            id="structure_legacy_text",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="linear_only",
            visual_index_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            metadata={},
        ),
        chapters=[],
        chunks=[
            SourceChunk(
                id="chunk_legacy_text",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                text="Stale body",
                start_offset=0,
                end_offset=10,
            )
        ],
    )

    upgraded = SourceStructureIndexer(store=store).ensure_structure(record)
    view = store.get_structure_view(source=record)

    assert upgraded is not None
    assert (
        upgraded.metadata["structure_index_version"]
        == CURRENT_SOURCE_STRUCTURE_INDEX_VERSION
    )
    assert [chunk.text for chunk in view.chunks] == ["Current body"]


def test_structure_only_upgrade_preserves_current_visual_index(tmp_path: Path) -> None:
    class UnexpectedVisualExtractor:
        @staticmethod
        def extract(**_kwargs):
            raise AssertionError("a structure-only upgrade must not extract visuals again")

    path = tmp_path / "legacy-with-visual.txt"
    path.write_text("Current body", encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    database = tmp_path / "openclass.sqlite3"
    SourceEvidenceStore(database).save_source(record)
    store = SourceStructureStore(database)
    store.save_structure_bundle(
        structure=SourceStructure(
            id="structure_legacy_visual",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="linear_only",
            visual_index_status="ready",
            visual_index_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            metadata={},
        ),
        chapters=[],
        chunks=[
            SourceChunk(
                id="chunk_legacy_visual",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                text="Stale body",
                start_offset=0,
                end_offset=10,
            )
        ],
        visuals=[
            SourceVisualAsset(
                id="visual_preserved",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                structure_id="structure_legacy_visual",
                structure_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
                kind="image",
                anchor_status="verified",
                metadata={"standalone_image": True},
            )
        ],
    )

    upgraded = SourceStructureIndexer(
        store=store,
        visual_extractor=UnexpectedVisualExtractor(),
    ).ensure_structure(record)
    view = store.get_structure_view(source=record)

    assert upgraded is not None
    assert upgraded.metadata["structure_index_version"] == CURRENT_SOURCE_STRUCTURE_INDEX_VERSION
    assert [visual.id for visual in view.visuals] == ["visual_preserved"]
    assert view.visuals[0].metadata["reanchored_after_structure_upgrade"] is True


def _write_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
                <item id="ch11" href="ch11.xhtml" media-type="application/xhtml+xml"/>
                <item id="figure" href="images/figure.png" media-type="image/png"/>
              </manifest>
              <spine><itemref idref="ch1"/><itemref idref="ch11"/></spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html><body><nav epub:type="toc"><ol>
              <li><a href="ch1.xhtml">1 Foundations</a></li>
              <li><a href="ch11.xhtml">1.1 Details</a></li>
            </ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/ch1.xhtml",
            '<html><body><h1>1 Foundations</h1><p>Body.</p><figure><img src="images/figure.png" alt="A grounded figure"/><figcaption>A grounded figure</figcaption></figure></body></html>',
        )
        archive.writestr("OEBPS/ch11.xhtml", "<html><body><h2>1.1 Details</h2><p>Details.</p></body></html>")
        archive.writestr(
            "OEBPS/images/figure.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        )


def _write_nested_ncx_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
              <manifest>
                <item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
                <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
                <item id="overview" href="overview.xhtml" media-type="application/xhtml+xml"/>
                <item id="details" href="details.xhtml" media-type="application/xhtml+xml"/>
                <item id="part2" href="part2.xhtml" media-type="application/xhtml+xml"/>
                <item id="cover-image" href="cover.png" media-type="image/png"/>
              </manifest>
              <spine toc="toc">
                <itemref idref="cover"/><itemref idref="overview"/>
                <itemref idref="details"/><itemref idref="part2"/>
              </spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0"?>
            <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
              <navMap>
                <navPoint id="part1">
                  <navLabel><text>Part I</text></navLabel>
                  <content src="cover.xhtml"/>
                  <navPoint id="overview">
                    <navLabel><text>Overview</text></navLabel>
                    <content src="overview.xhtml#overview"/>
                    <navPoint id="details">
                      <navLabel><text>Details</text></navLabel>
                      <content src="details.xhtml#details"/>
                    </navPoint>
                  </navPoint>
                </navPoint>
                <navPoint id="part2">
                  <navLabel><text>Part II</text></navLabel>
                  <content src="part2.xhtml"/>
                </navPoint>
              </navMap>
            </ncx>""",
        )
        archive.writestr(
            "OEBPS/cover.xhtml",
            '<html><body><img src="cover.png" alt=""/></body></html>',
        )
        archive.writestr(
            "OEBPS/cover.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        )
        for name, title in (
            ("overview.xhtml", "Overview"),
            ("details.xhtml", "Details"),
            ("part2.xhtml", "Part II"),
        ):
            anchor = Path(name).stem
            archive.writestr(
                f"OEBPS/{name}",
                f'<html><body><h1 id="{anchor}">{title}</h1><p>{title} body.</p></body></html>',
            )


def _write_pdf_with_outline(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.bookmarkPage("toc")
    pdf.addOutlineEntry("Contents", "toc", level=0)
    pdf.drawString(72, 720, "Contents")
    pdf.showPage()
    pdf.bookmarkPage("chapter-1")
    pdf.addOutlineEntry("1 Intro", "chapter-1", level=0)
    pdf.drawString(72, 720, "1 Intro")
    pdf.drawString(72, 690, "1.1 Details")
    pdf.rect(72, 420, 260, 180, stroke=1, fill=0)
    pdf.line(82, 440, 300, 570)
    pdf.showPage()
    pdf.drawString(72, 720, "1.2 More")
    pdf.drawString(72, 690, "More section body text.")
    pdf.showPage()
    pdf.bookmarkPage("chapter-2")
    pdf.addOutlineEntry("2 Next", "chapter-2", level=0)
    pdf.drawString(72, 720, "2 Next")
    pdf.drawString(72, 690, "Next section body text.")
    pdf.save()


def _toc_layout_page(
    page_no: int,
    rows: list[tuple[str, int, float, float]],
) -> OCRPageLayout:
    lines: list[OCRLineLayout] = []
    for title, printed_page, y, x in rows:
        lines.append(OCRLineLayout(text=title, x=x, y=y, width=0.30, height=0.02))
        lines.append(OCRLineLayout(text=str(printed_page), x=0.84, y=y, width=0.03, height=0.02))
    return OCRPageLayout(page_no=page_no, lines=lines)

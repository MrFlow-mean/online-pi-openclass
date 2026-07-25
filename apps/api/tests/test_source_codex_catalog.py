from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import (
    AgentActivityEvent,
    AIModelSelection,
    SourceChapter,
    SourceIngestionRecord,
    SourceRange,
)
from app.routers.sources import _parse_catalog_model
from app.services import source_directory_processor as directory_processor_module
from app.services import source_codex_catalog as source_codex_catalog_module
from app.services import source_codex_pdf_mapping as pdf_mapping_module
from app.services.source_codex_catalog import (
    CodexDirectCatalog,
    CodexDirectCatalogEvidence,
    CodexDirectCatalogNode,
    CodexDirectSourceRange,
    SourceCodexCatalogError,
    SourceDirectoryOnlyCatalog,
    SourceDirectoryOnlyNode,
    SourcePdfDirectoryTask,
    SourcePdfPageOffsetAnchor,
    generate_codex_direct_catalog,
    generate_directory_only_catalog,
    materialize_stored_codex_catalog,
)
from app.services.source_codex_pdf_mapping import (
    CodexPdfPageCalibration,
    CodexPdfPrintedPageAnchor,
    PdfNativeOutlineEntry,
    generate_pdf_page_calibration,
    map_pdf_native_outline_ranges,
)
from app.services.source_directory_processor import (
    SourceDirectoryProcessingError,
    SourceDirectoryProcessor,
)
from app.services.source_structure_store import SourceStructureStore


def _node(
    key: str,
    *,
    title: str | None = None,
    parent_key: str | None = None,
    level: int = 1,
    number: str | None = None,
    source_locator: str = "",
    source_range: CodexDirectSourceRange | None = None,
    evidence: list[CodexDirectCatalogEvidence] | None = None,
    mapping_status: str | None = None,
    mapping_reason: str | None = None,
) -> CodexDirectCatalogNode:
    resolved_status = mapping_status or ("verified" if source_range is not None else "unmapped")
    return CodexDirectCatalogNode(
        key=key,
        parent_key=parent_key,
        number=key.removeprefix("n") if number is None else number,
        title=title or f"Title {key}",
        level=level,
        source_locator=source_locator,
        mapping_status=resolved_status,
        mapping_reason=mapping_reason or (
            "Verified directly against the source file."
            if resolved_status == "verified"
            else "No authoritative source range was supplied for this test node."
        ),
        source_range=source_range,
        evidence=evidence or [],
    )


def _catalog(*nodes: CodexDirectCatalogNode) -> CodexDirectCatalog:
    return CodexDirectCatalog(complete=True, nodes=list(nodes))


def _pdf_range(start: int, end: int) -> CodexDirectSourceRange:
    return CodexDirectSourceRange(
        kind="pdf_pages",
        start=start,
        end=end,
        container="",
        start_anchor="",
        end_anchor="",
        display_label=f"PDF pp. {start}-{end}",
    )


def _pdf_evidence(page: int, *, excerpt: str = "Verified physical page") -> list[CodexDirectCatalogEvidence]:
    return [
        CodexDirectCatalogEvidence(
            method="source_codex_visual_page_check",
            source_locator=f"pdf:page:{page}",
            page_start=page,
            page_end=page,
            excerpt=excerpt,
            confidence=0.98,
        )
    ]


class FakeSourceCodexClient:
    def __init__(
        self,
        output_parsed: object,
        *,
        raw_output: str | None = None,
        source_sha256: str = "a" * 64,
        source_turn_count: int = 1,
    ) -> None:
        self.output_parsed = output_parsed
        self.raw_output = (
            output_parsed.model_dump_json()
            if raw_output is None and hasattr(output_parsed, "model_dump_json")
            else raw_output
        )
        self.source_sha256 = source_sha256
        self.source_turn_count = source_turn_count
        self.calls: list[dict[str, object]] = []

    def parse_source_file(self, **kwargs):
        self.calls.append(kwargs)
        validator = kwargs.get("artifact_validator")
        if callable(validator) and hasattr(self.output_parsed, "model_dump"):
            validator(self.output_parsed.model_dump(mode="json"))
        return SimpleNamespace(
            output_parsed=self.output_parsed,
            output_text=self.raw_output,
            usage={"input_tokens": 1, "output_tokens": 1},
            activity=[],
            source_sha256=self.source_sha256,
            source_turn_count=self.source_turn_count,
        )


def _record(path: Path, *, mime_type: str = "application/pdf") -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id="source_direct_catalog",
        owner_user_id="user_direct_catalog",
        package_id="course_direct_catalog",
        title="Direct catalog",
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="parsing",
    )


def _model(
    *,
    provider: str = "openai_codex",
    reasoning_effort: str | None = "low",
) -> AIModelSelection:
    return AIModelSelection(
        provider=provider,
        model="catalog-test-model",
        reasoning_effort=reasoning_effort,
        service_tier="priority",
    )


def _write_pdf(path: Path, *, page_count: int = 60) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=500, height=700)
    with path.open("wb") as stream:
        writer.write(stream)


def _directory_only_catalog(*nodes: SourceDirectoryOnlyNode) -> SourceDirectoryOnlyCatalog:
    return SourceDirectoryOnlyCatalog(
        complete=True,
        pdf=SourcePdfDirectoryTask(
            directory_pages=[2, 3],
            page_offset_p=5,
            anchors=[
                SourcePdfPageOffsetAnchor(pdf_file_page=5, printed_page=1),
                SourcePdfPageOffsetAnchor(pdf_file_page=25, printed_page=21),
                SourcePdfPageOffsetAnchor(pdf_file_page=45, printed_page=41),
            ],
        ),
        nodes=list(nodes),
    )


def _directory_node(
    key: str,
    *,
    title: str,
    directory_page: int,
    printed_page: int | None = None,
    parent_key: str | None = None,
    level: int = 1,
    number: str = "",
) -> SourceDirectoryOnlyNode:
    return SourceDirectoryOnlyNode(
        key=key,
        parent_key=parent_key,
        number=number,
        title=title,
        level=level,
        directory_page=directory_page,
        printed_page=printed_page,
    )


def _generate(
    tmp_path: Path,
    catalog: CodexDirectCatalog,
    *,
    suffix: str = ".pdf",
    mime_type: str = "application/pdf",
    raw_output: str | None = None,
    source_turn_count: int = 1,
    selection: AIModelSelection | None = None,
):
    path = tmp_path / f"source{suffix}"
    path.write_bytes(b"source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        catalog,
        raw_output=raw_output,
        source_sha256=content_hash,
        source_turn_count=source_turn_count,
    )
    result = generate_codex_direct_catalog(
        record=_record(path, mime_type=mime_type),
        source_path=path,
        source_content_hash=content_hash,
        selection=selection or _model(),
        client_factory=lambda _user_id: client,
    )
    return result, client, path, content_hash


def test_source_codex_runs_once_and_materializes_unmapped_hierarchy(tmp_path: Path) -> None:
    result, client, path, content_hash = _generate(
        tmp_path,
        _catalog(
            _node("chapter-1", title="Chapter One", number="1", source_locator="nav:1"),
            _node(
                "section-1-1",
                title="First section",
                number="1.1",
                parent_key="chapter-1",
                level=2,
            ),
        ),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["source_path"] == path
    assert call["provider"] == "openai_codex"
    assert call["reasoning_effort"] == "low"
    assert call["output_artifact_path"] == "scratch/catalog.json"
    assert "body range" in str(call["system_prompt"])
    assert result.turn_count == 1
    assert [chapter.title for chapter in result.chapters] == [
        "Chapter One",
        "First section",
    ]
    assert result.chapters[1].parent_id == result.chapters[0].id
    assert all(chapter.range is None for chapter in result.chapters)
    assert all(chapter.mapping_status == "unmapped" for chapter in result.chapters)
    assert all(chapter.anchor_status == "unverified" for chapter in result.chapters)
    assert all(chapter.source_content_hash == content_hash for chapter in result.chapters)
    assert result.audit_metadata["host_directory_transform"] == (
        "mechanical_materialization_only"
    )
    assert result.audit_metadata["body_text_extracted_by_host"] is False


def test_source_codex_forwards_a_custom_model_provider(tmp_path: Path) -> None:
    _result, client, _path, _content_hash = _generate(
        tmp_path,
        _catalog(_node("chapter-1")),
        selection=_model(provider="deepseek"),
    )

    assert client.calls[0]["provider"] == "deepseek"


def test_source_upload_accepts_any_configured_text_provider() -> None:
    selection = _parse_catalog_model(
        json.dumps({"provider": "deepseek", "model": "deepseek-v4-pro"})
    )

    assert selection is not None
    assert selection.provider == "deepseek"
    assert selection.model == "deepseek-v4-pro"


def test_directory_only_catalog_enforces_the_three_step_contract(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _directory_only_catalog(
        _directory_node(
            "chapter-1",
            title="Chapter One",
            number="1",
            directory_page=2,
            printed_page=1,
        ),
        _directory_node(
            "section-1-1",
            title="First section",
            number="1.1",
            parent_key="chapter-1",
            level=2,
            directory_page=3,
            printed_page=21,
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_directory_only_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(provider="deepseek"),
        client_factory=lambda _user_id: client,
    )

    call = client.calls[0]
    assert call["provider"] == "deepseek"
    assert call["inspection_scope"] == "directory_only"
    assert call["image_inputs"] is None
    assert "strictly forbidden to scan the whole book" in str(call["system_prompt"])
    assert "pdf_file_page - printed_page + 1 = page_offset_p" in str(
        call["system_prompt"]
    )
    assert result.audit_metadata["catalog_task_contract"] == (
        "directory_pages_offset_tree_v1"
    )
    assert result.audit_metadata["pdf_directory_task"] == catalog.pdf.model_dump(
        mode="json"
    )
    assert [chapter.parent_id for chapter in result.chapters] == [
        None,
        result.chapters[0].id,
    ]
    assert all(chapter.range is None for chapter in result.chapters)
    assert all(not chapter.catalog_evidence for chapter in result.chapters)
    assert all(chapter.metadata["body_range_investigated"] is False for chapter in result.chapters)


def test_directory_only_catalog_forces_pi_for_a_legacy_codex_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _directory_only_catalog(
        _directory_node(
            "chapter-1",
            title="Chapter One",
            number="1",
            directory_page=2,
            printed_page=1,
        ),
        _directory_node(
            "chapter-2",
            title="Chapter Two",
            number="2",
            directory_page=3,
            printed_page=21,
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)
    owners: list[str] = []

    def build_client(owner_user_id: str):
        owners.append(owner_user_id)
        return client

    monkeypatch.setattr(source_codex_catalog_module, "PiSourceTextClient", build_client)
    selection = _model().model_copy(update={"agent_backend": "codex"})

    result = generate_directory_only_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=selection,
    )

    assert owners == [_record(path).owner_user_id]
    assert result.audit_metadata["catalog_authority"] == "source_pi"
    assert result.audit_metadata["source_agent_backend"] == "pi"


def test_directory_only_catalog_rejects_an_inexact_p(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _directory_only_catalog(
        _directory_node(
            "chapter-1",
            title="Chapter One",
            directory_page=2,
            printed_page=1,
        ),
        _directory_node(
            "chapter-2",
            title="Chapter Two",
            directory_page=3,
            printed_page=21,
        ),
    )
    assert catalog.pdf is not None
    catalog.pdf.anchors[2] = SourcePdfPageOffsetAnchor(
        pdf_file_page=46,
        printed_page=41,
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    with pytest.raises(SourceCodexCatalogError, match="does not satisfy"):
        generate_directory_only_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_source_codex_materializes_exact_authored_pdf_ranges(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "source.pdf"
    document = fitz.open()
    for _ in range(3):
        document.new_page(width=500, height=700)
    document.save(path)
    document.close()
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _catalog(
        _node(
            "parent",
            title="Parent",
            source_range=_pdf_range(1, 3),
            evidence=_pdf_evidence(1),
        ),
        _node(
            "child",
            title="Child",
            parent_key="parent",
            level=2,
            source_range=_pdf_range(2, 2),
            evidence=_pdf_evidence(2),
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_codex_direct_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (1, 3),
        (2, 2),
    ]
    assert [chapter.page_end for chapter in result.chapters] == [4, 3]
    assert all(chapter.mapping_status == "verified" for chapter in result.chapters)
    assert all(chapter.metadata["source_range_authority"] == "source_pi" for chapter in result.chapters)


def test_source_codex_validator_rejects_a_child_outside_its_authored_parent(
    tmp_path: Path,
) -> None:
    import fitz

    path = tmp_path / "source.pdf"
    document = fitz.open()
    for _ in range(3):
        document.new_page(width=500, height=700)
    document.save(path)
    document.close()
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        _catalog(
            _node("parent", source_range=_pdf_range(1, 2), evidence=_pdf_evidence(1)),
            _node(
                "child",
                parent_key="parent",
                level=2,
                source_range=_pdf_range(2, 3),
                evidence=_pdf_evidence(2),
            ),
        ),
        source_sha256=content_hash,
    )

    with pytest.raises(SourceCodexCatalogError, match="outside.*parent"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_source_codex_validates_and_materializes_epub_spine_ranges(tmp_path: Path) -> None:
    path = tmp_path / "source.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<container><rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "OPS/content.opf",
            """<package><manifest>
            <item id='one' href='one.xhtml'/><item id='two' href='two.xhtml'/>
            </manifest><spine><itemref idref='one'/><itemref idref='two'/></spine></package>""",
        )
        archive.writestr("OPS/one.xhtml", "<html><body><h1 id='a'>A</h1><h2 id='b'>B</h2></body></html>")
        archive.writestr("OPS/two.xhtml", "<html><body><h1 id='c'>C</h1></body></html>")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = [
        CodexDirectCatalogEvidence(
            method="epub_navigation_and_anchor_check",
            source_locator="epub:OPS/one.xhtml#a",
            excerpt="Verified native EPUB navigation anchor.",
            confidence=0.99,
        )
    ]
    catalog = _catalog(
        _node(
            "parent",
            source_locator="epub:OPS/one.xhtml#a",
            source_range=CodexDirectSourceRange(
                kind="epub_spine",
                start=0,
                end=1,
                container="OPS/one.xhtml",
                start_anchor="a",
                end_anchor="c",
                display_label="EPUB spine 0-1",
            ),
            evidence=evidence,
        ),
        _node(
            "child",
            parent_key="parent",
            level=2,
            source_locator="epub:OPS/one.xhtml#b",
            source_range=CodexDirectSourceRange(
                kind="epub_spine",
                start=0,
                end=0,
                container="OPS/one.xhtml",
                start_anchor="b",
                end_anchor="",
                display_label="OPS/one.xhtml#b",
            ),
            evidence=evidence,
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_codex_direct_catalog(
        record=_record(path, mime_type="application/epub+zip"),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [chapter.range.kind for chapter in result.chapters] == [
        "epub_spine",
        "epub_spine",
    ]
    assert all(chapter.mapping_status == "verified" for chapter in result.chapters)


def test_source_codex_forwards_live_activity_callback(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        _catalog(_node("chapter-1")),
        source_sha256=content_hash,
    )
    events: list[AgentActivityEvent] = []

    generate_codex_direct_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        on_activity=events.append,
        client_factory=lambda _user_id: client,
    )

    callback = client.calls[0]["on_activity"]
    assert callable(callback)


def test_pdf_catalog_forwards_bounded_visual_evidence(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "visual-source.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=700)
    page.insert_text((72, 96), "Table of Contents")
    page.insert_text((72, 140), "Chapter 1 ........................ 1")
    scanned_document = fitz.open()
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    scanned_page = scanned_document.new_page(width=500, height=700)
    scanned_page.insert_image(scanned_page.rect, stream=pixmap.tobytes("png"))
    scanned_document.save(path)
    scanned_document.close()
    document.close()
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        _catalog(_node("chapter-1", title="Chapter 1", source_locator="printed-page:1")),
        source_sha256=content_hash,
    )

    result = generate_codex_direct_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    image_inputs = client.calls[0]["image_inputs"]
    assert image_inputs
    assert all(str(value).startswith("data:image/jpeg;base64,") for value in image_inputs)
    assert result.audit_metadata["pdf_catalog_visual_evidence_count"] == len(image_inputs)


def test_scanned_pdf_calibration_uses_visual_footer_evidence(tmp_path: Path) -> None:
    import fitz

    text_document = fitz.open()
    for printed_page in range(1, 7):
        page = text_document.new_page(width=500, height=700)
        page.insert_text((245, 680), str(printed_page), fontsize=16)
    scanned_document = fitz.open()
    for page in text_document:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        scanned_page = scanned_document.new_page(width=500, height=700)
        scanned_page.insert_image(scanned_page.rect, stream=pixmap.tobytes("png"))
    path = tmp_path / "scanned.pdf"
    scanned_document.save(path)
    scanned_document.close()
    text_document.close()
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    calibration = CodexPdfPageCalibration(
        complete=True,
        continuous_arabic_numbering=True,
        printed_page_start=1,
        printed_page_end=6,
        pdf_page_start=1,
        pdf_page_end=6,
        anchors=[
            CodexPdfPrintedPageAnchor(printed_page=1, pdf_page=1),
            CodexPdfPrintedPageAnchor(printed_page=3, pdf_page=3),
            CodexPdfPrintedPageAnchor(printed_page=6, pdf_page=6),
        ],
    )
    client = FakeSourceCodexClient(calibration, source_sha256=content_hash)

    result = generate_pdf_page_calibration(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        required_printed_page_min=1,
        required_printed_page_max=6,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert client.calls[0]["image_inputs"]
    assert result.page_offset == 0
    assert result.audit_metadata["pdf_anchor_verification_method"] == "source_codex_visual_evidence"


def test_native_outline_matches_control_characters_split_numbers_and_reordering(
    monkeypatch,
    tmp_path: Path,
) -> None:
    result, _client, path, _content_hash = _generate(
        tmp_path,
        _catalog(
            _node("prelim", title="Preliminaries", number="0."),
            _node("cover", title="Cover", number=""),
        ),
    )
    monkeypatch.setattr(
        pdf_mapping_module,
        "_read_pdf_native_outline",
        lambda _path: (
            8,
            (
                PdfNativeOutlineEntry(level=1, title="Cover\x00", pdf_page=1),
                PdfNativeOutlineEntry(level=1, title="0. Preliminaries", pdf_page=3),
            ),
        ),
    )

    mapping = map_pdf_native_outline_ranges(result.chapters, source_path=path)

    assert mapping.status == "verified"
    assert mapping.mapped_count == 2
    assert [chapter.range.start for chapter in mapping.chapters] == [3, 1]
    assert mapping.audit_metadata["pdf_native_outline_alignment"] == (
        "unique_title_level_bijection"
    )


@pytest.mark.parametrize(
    "nodes, message",
    [
        (
            [_node("same"), _node("same")],
            "keys must be unique",
        ),
        (
            [_node("child", parent_key="missing", level=2)],
            "parent must appear before",
        ),
        (
            [_node("root"), _node("child", parent_key="root", level=3)],
            "exactly one deeper",
        ),
        (
            [
                _node("root"),
                _node("child", parent_key="root", level=2),
                _node("other"),
                _node("late", parent_key="root", level=2),
            ],
            "parent-consistent preorder",
        ),
        (
            [_node("root", title=" padded")],
            "leading or trailing whitespace",
        ),
    ],
)
def test_source_codex_rejects_invalid_directory_structure(
    tmp_path: Path,
    nodes: list[CodexDirectCatalogNode],
    message: str,
) -> None:
    with pytest.raises(SourceCodexCatalogError, match=message):
        _generate(tmp_path, _catalog(*nodes))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "",
                    "level": 1,
                    "source_locator": "",
                }
            ],
        },
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "Title",
                    "level": "1",
                    "source_locator": "",
                }
            ],
        },
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "Title",
                    "level": 1,
                    "source_locator": "",
                    "unexpected": True,
                }
            ],
        },
    ],
)
def test_source_codex_rejects_invalid_raw_schema(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    raw_output = json.dumps(payload)
    client = FakeSourceCodexClient(
        payload,
        raw_output=raw_output,
        source_sha256=content_hash,
    )

    with pytest.raises(SourceCodexCatalogError, match="invalid auditable"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_source_codex_rejects_duplicate_raw_json_keys(tmp_path: Path) -> None:
    catalog = _catalog(_node("n1"))
    with pytest.raises(SourceCodexCatalogError, match="invalid auditable"):
        _generate(
            tmp_path,
            catalog,
            raw_output=(
                '{"complete":true,"complete":true,"nodes":'
                '[{"key":"n1","parent_key":null,"number":"1",'
                '"title":"Title n1","level":1,"source_locator":""}]}'
            ),
        )


def test_source_codex_rejects_fingerprint_change_and_accepts_validator_feedback_turns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes")
    catalog = _catalog(_node("n1"))
    client = FakeSourceCodexClient(
        catalog,
        source_sha256="b" * 64,
        source_turn_count=1,
    )
    with pytest.raises(SourceCodexCatalogError, match="fingerprint"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            selection=_model(),
            client_factory=lambda _user_id: client,
        )

    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    extra_turn_client = FakeSourceCodexClient(
        catalog,
        source_sha256=content_hash,
        source_turn_count=2,
    )
    result = generate_codex_direct_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: extra_turn_client,
    )

    assert result.turn_count == 2
    assert result.audit_metadata["source_codex_investigation_turn_count"] == 2


def test_source_codex_rejects_empty_complete_catalog(tmp_path: Path) -> None:
    with pytest.raises(SourceCodexCatalogError, match="empty directory"):
        _generate(tmp_path, _catalog())


def test_stored_source_codex_catalog_is_revalidated_before_rematerialization(
    tmp_path: Path,
) -> None:
    result, _client, path, content_hash = _generate(
        tmp_path,
        _catalog(_node("n1", title="Stored", source_locator="printed-page:1")),
    )

    restored = materialize_stored_codex_catalog(
        record=_record(path),
        payload=result.audit_metadata["codex_directory_payload"],
        source_content_hash=content_hash,
        expected_payload_sha256=result.audit_metadata["codex_directory_payload_sha256"],
    )

    assert restored.turn_count == 0
    assert [chapter.title for chapter in restored.chapters] == ["Stored"]
    assert restored.audit_metadata["catalog_authority"] == "source_codex_reused_audit"

    with pytest.raises(SourceCodexCatalogError, match="fingerprint"):
        materialize_stored_codex_catalog(
            record=_record(path),
            payload=result.audit_metadata["codex_directory_payload"],
            source_content_hash=content_hash,
            expected_payload_sha256="0" * 64,
        )


def test_source_codex_rejects_unsupported_or_mismatched_suffix(tmp_path: Path) -> None:
    path = tmp_path / "source.bin"
    path.write_bytes(b"source")
    with pytest.raises(SourceCodexCatalogError, match="not supported"):
        generate_codex_direct_catalog(
            record=_record(path, mime_type="application/octet-stream"),
            source_path=path,
            source_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            selection=_model(),
        )

    stored = tmp_path / "stored.epub"
    stored.write_bytes(b"source")
    mismatched = _record(stored, mime_type="application/pdf").model_copy(
        update={"file_name": "source.pdf"}
    )
    with pytest.raises(SourceCodexCatalogError, match="suffix does not match"):
        generate_codex_direct_catalog(
            record=mismatched,
            source_path=stored,
            source_content_hash=hashlib.sha256(stored.read_bytes()).hexdigest(),
            selection=_model(),
        )


def test_production_processor_publishes_verified_catalog_without_indexes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(
        update={"metadata": {"content_hash": content_hash}}
    )
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "n1",
                title="First",
                source_range=_pdf_range(5, 44),
                evidence=_pdf_evidence(5),
            ),
            _node(
                "n1-1",
                title="Child",
                parent_key="n1",
                level=2,
                source_range=_pdf_range(25, 44),
                evidence=_pdf_evidence(25),
            ),
        ),
        source_sha256=content_hash,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: direct_result,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "extract_directory",
        lambda *_args, **_kwargs: pytest.fail(
            "The production path must not run the host directory extractor"
        ),
    )
    database = tmp_path / "openclass.sqlite3"
    store = SourceStructureStore(database)

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    view = store.get_catalog_view(source=record)

    assert structure.status == "ready"
    assert structure.catalog_version == 1
    assert structure.has_verified_toc is True
    assert structure.quality.level == "fully_verified"
    assert structure.chapter_count == 2
    assert structure.chunk_count == 0
    assert structure.visual_count == 0
    assert structure.metadata["source_chunks_created"] is False
    assert structure.metadata["vector_index_created"] is False
    assert structure.metadata["visual_index_created"] is False
    assert [chapter.title for chapter in view.chapters] == ["First", "Child"]
    assert all(chapter.mapping_status == "verified" for chapter in view.chapters)
    assert [chapter.range.start for chapter in view.chapters if chapter.range] == [5, 25]
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert runs[-1].turn_count == 1
    assert "directory_and_ranges_verified" in runs[-1].stage_history
    assert "validating_directory_ranges" in runs[-1].stage_history
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_visual_assets").fetchone()[0] == 0


def test_production_processor_persists_ranges_and_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "chapter-1",
                title="First",
                source_range=_pdf_range(5, 24),
                evidence=_pdf_evidence(5),
            ),
            _node(
                "chapter-2",
                title="Second",
                source_range=_pdf_range(25, 44),
                evidence=_pdf_evidence(25),
            ),
        ),
        source_sha256=content_hash,
        source_turn_count=2,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: direct_result,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    view = store.get_catalog_view(source=record)
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert structure.has_verified_toc is True
    assert [chapter.range.start for chapter in view.chapters if chapter.range] == [5, 25]
    assert all(chapter.catalog_evidence for chapter in view.chapters)
    assert structure.metadata["catalog_authority"] == "source_pi"
    assert runs[-1].turn_count == 2
    assert "source_codex_investigation" in runs[-1].stage_history
    assert "directory_and_ranges_verified" in runs[-1].stage_history
    assert "validating_directory_ranges" in runs[-1].stage_history


def test_source_pi_directory_tree_publishes_with_body_mapping(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "chapter-1",
                title="First",
                source_range=_pdf_range(5, 24),
                evidence=_pdf_evidence(5),
            ),
            _node(
                "chapter-2",
                title="Second",
                source_range=_pdf_range(25, 44),
                evidence=_pdf_evidence(25),
            ),
        ),
        source_sha256=content_hash,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: direct_result,
    )

    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    view = store.get_catalog_view(source=record)
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert structure.status == "ready"
    assert structure.has_verified_toc is True
    assert all(chapter.mapping_status == "verified" for chapter in view.chapters)
    assert all(chapter.metadata["source_range_mapped"] is True for chapter in view.chapters)
    assert runs[-1].turn_count == 1
    assert "directory_and_ranges_verified" in runs[-1].stage_history


def test_failed_rebuild_preserves_previous_catalog(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(
        update={"metadata": {"content_hash": content_hash}}
    )
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "n1",
                title="Published",
                source_range=_pdf_range(5, 24),
                evidence=_pdf_evidence(5),
            ),
            _node(
                "n2",
                title="Published second",
                source_range=_pdf_range(25, 44),
                evidence=_pdf_evidence(25),
            ),
        ),
        source_sha256=content_hash,
    )
    successful_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: successful_result,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceDirectoryProcessor(store=store)
    first = processor.process(record=record, path=path, catalog_model=_model())

    def fail_catalog(**_kwargs):
        raise SourceCodexCatalogError("single-turn catalog failed")

    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        fail_catalog,
    )
    with pytest.raises(SourceDirectoryProcessingError, match="single-turn"):
        processor.process(record=record, path=path, catalog_model=_model())

    after = store.get_catalog_view(source=record)
    after_structure = store.get_structure(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert after_structure is not None
    assert after_structure.catalog_version == first.catalog_version
    assert [chapter.title for chapter in after.chapters] == [
        "Published",
        "Published second",
    ]

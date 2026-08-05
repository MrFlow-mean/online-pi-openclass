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
    SourceIngestionRecord,
)
from app.routers.sources import _parse_catalog_model
from app.services import source_directory_processor as directory_processor_module
from app.services import source_codex_catalog as source_codex_catalog_module
from app.services import source_codex_pdf_mapping as pdf_mapping_module
from app.services.source_codex_catalog import (
    AgentCatalogDirectoryEvidence,
    AgentCatalogNode,
    AgentCatalogPageRange,
    AgentCatalogPaginationRegime,
    AgentCatalogV3,
    AgentCatalogV3Node,
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
    generate_agent_catalog_turn,
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


def _agent_catalog(
    *nodes: AgentCatalogNode,
    work_state: str = "satisfied",
    summary: str = "usable snapshot",
    next_plan: str = "",
    next_action: str | None = None,
) -> AgentCatalogV3:
    terminal = work_state in {"satisfied", "partial"}
    return AgentCatalogV3(
        schema_version="agent_catalog_v3",
        phase="terminal" if terminal else "directory_discovery",
        directory_status="complete" if terminal else "incomplete",
        index_status="complete" if work_state == "satisfied" else "partial" if work_state == "partial" else "pending",
        work_state=work_state,
        summary=summary,
        next_plan=next_plan,
        next_action=(
            next_action or "inspect one bounded directory gap"
            if work_state == "working"
            else ""
        ),
        stop_reason="Partial catalog." if work_state == "partial" else "",
        completion_reason="Authored navigation exhausted." if terminal else "",
        directory_evidence=(
            [AgentCatalogDirectoryEvidence(kind="native_navigation_exhausted", detail="All authored items were traversed.")]
            if terminal
            else []
        ),
        nodes=[
            node
            if isinstance(node, AgentCatalogV3Node)
            else AgentCatalogV3Node(
                **node.model_dump(mode="python"),
                locator_source=("legacy_range" if node.source_range else "unmapped"),
            )
            for node in nodes
        ],
    )


def _complete_staged_catalog(*nodes: CodexDirectCatalogNode) -> AgentCatalogV3:
    return _agent_catalog(
        *[
            AgentCatalogV3Node(
                **node.model_dump(mode="python"),
                locator_source="native_navigation",
            )
            for node in nodes
        ]
    )


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
        tool_activity: list[dict[str, object]] | None = None,
    ) -> None:
        self.output_parsed = output_parsed
        self.raw_output = (
            output_parsed.model_dump_json()
            if raw_output is None and hasattr(output_parsed, "model_dump_json")
            else raw_output
        )
        self.source_sha256 = source_sha256
        self.source_turn_count = source_turn_count
        self.tool_activity = tool_activity or []
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
            tool_activity=self.tool_activity,
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
    access_method: str | None = None,
    reasoning_effort: str | None = "low",
) -> AIModelSelection:
    return AIModelSelection(
        provider=provider,
        model="catalog-test-model",
        access_method=access_method,
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
    assert call["access_method"] is None
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


def test_source_codex_forwards_platform_credit_access_method(tmp_path: Path) -> None:
    _result, client, _path, _content_hash = _generate(
        tmp_path,
        _catalog(_node("chapter-1")),
        selection=_model(access_method="platform_credits"),
    )

    assert client.calls[0]["access_method"] == "platform_credits"


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


def test_source_codex_rejects_child_range_outside_parent_before_completion(
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

    with pytest.raises(SourceCodexCatalogError, match="child range falls outside"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_source_codex_rejects_catalog_shorter_than_native_pdf_outline(
    tmp_path: Path,
) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "outlined.pdf"
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=500, height=700)
    writer.add_outline_item("First", 0)
    writer.add_outline_item("Second", 1)
    writer.add_outline_item("Third", 2)
    with path.open("wb") as stream:
        writer.write(stream)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "first",
                title="First",
                source_range=_pdf_range(1, 5),
                evidence=_pdf_evidence(1),
            )
        ),
        source_sha256=content_hash,
    )

    with pytest.raises(SourceCodexCatalogError, match="contains 1 nodes.*at least 3"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_pdf_completeness_witness_rejects_numeric_bookmarks_and_prefers_printed_toc(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import pdf_toc_parser
    from pypdf import PdfWriter

    path = tmp_path / "numeric-bookmarks.pdf"
    writer = PdfWriter()
    for _ in range(12):
        writer.add_blank_page(width=500, height=700)
    for page_index in range(8):
        writer.add_outline_item(str(page_index + 1), page_index)
    with path.open("wb") as stream:
        writer.write(stream)

    printed_nodes = [
        pdf_toc_parser.PdfTocNode(
            title=title,
            printed_page=index + 1,
            toc_page=2,
            level=level,
        )
        for index, (title, level) in enumerate(
            [
                ("第一章 总论", 1),
                ("第一节 基础", 2),
                ("第二节 方法", 2),
                ("第二章 应用", 1),
            ]
        )
    ]
    monkeypatch.setattr(
        pdf_toc_parser,
        "probe_pdf_toc_from_leading_pages",
        lambda *_args, **_kwargs: pdf_toc_parser.PdfTocExtraction(
            nodes=printed_nodes,
            toc_page_start=2,
            toc_page_end=3,
        ),
    )

    witness = source_codex_catalog_module._catalog_completeness_witness(path)

    assert witness.source == "PDF printed table of contents"
    assert witness.expected_node_count == 4
    assert witness.sample_titles[-1] == "第二章 应用"


def test_complete_native_pdf_preflight_defers_to_a_richer_printed_toc(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "coarse-outline.pdf"
    writer = PdfWriter()
    for _ in range(20):
        writer.add_blank_page(width=500, height=700)
    writer.add_outline_item("Chapter One", 0)
    writer.add_outline_item("Chapter Two", 10)
    writer.add_outline_item("Appendix", 19)
    with path.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(
        source_codex_catalog_module,
        "_pdf_printed_toc_witness",
        lambda _path: source_codex_catalog_module.SourceCatalogCompletenessWitness(
            source="PDF printed table of contents",
            expected_node_count=12,
            sample_titles=("1.1 Scope", "Appendix A"),
        ),
    )

    result = source_codex_catalog_module._generate_complete_native_pdf_catalog(
        record=_record(path),
        source_path=path,
        source_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        on_activity=None,
    )

    assert result is None


def test_native_pdf_ranges_expand_parents_to_contain_same_page_descendants(
    tmp_path: Path,
) -> None:
    path = tmp_path / "same-page-boundaries.pdf"
    _write_pdf(path, page_count=12)
    catalog = AgentCatalogV3(
        schema_version="agent_catalog_v3",
        phase="terminal",
        directory_status="complete",
        index_status="pending",
        work_state="working",
        nodes=[
            AgentCatalogV3Node(
                key="parent",
                parent_key=None,
                number="1",
                title="Parent",
                level=1,
                native_pdf_page=1,
                locator_source="native_navigation",
            ),
            AgentCatalogV3Node(
                key="child.one",
                parent_key="parent",
                number="1.1",
                title="First child",
                level=2,
                native_pdf_page=2,
                locator_source="native_navigation",
            ),
            AgentCatalogV3Node(
                key="child.two",
                parent_key="parent",
                number="1.2",
                title="Last child",
                level=2,
                native_pdf_page=5,
                locator_source="native_navigation",
            ),
            AgentCatalogV3Node(
                key="next",
                parent_key=None,
                number="2",
                title="Next root on the same page",
                level=1,
                native_pdf_page=5,
                locator_source="native_navigation",
            ),
        ],
    )

    resolved = source_codex_catalog_module._resolve_agent_pdf_ranges(
        catalog,
        source_path=path,
    )

    assert (resolved["parent"][0].start, resolved["parent"][0].end) == (1, 5)
    assert (resolved["child.two"][0].start, resolved["child.two"][0].end) == (5, 5)
    assert (resolved["next"][0].start, resolved["next"][0].end) == (5, 12)


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
    ],
)
def test_source_codex_rejects_invalid_directory_structure(
    tmp_path: Path,
    nodes: list[CodexDirectCatalogNode],
    message: str,
) -> None:
    with pytest.raises(SourceCodexCatalogError, match=message):
        _generate(tmp_path, _catalog(*nodes))


def test_source_codex_rejects_text_padding_before_materialization(
    tmp_path: Path,
) -> None:
    with pytest.raises(SourceCodexCatalogError, match="leading or trailing whitespace"):
        _generate(
            tmp_path,
            _catalog(
                _node(
                    "root",
                    number=" 1 ",
                    title=" Padded title ",
                    source_locator=" printed-page:1 ",
                    mapping_reason=" Located in the source. ",
                )
            ),
        )


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


def test_source_codex_rejects_catalog_below_native_outline_count(
    tmp_path: Path,
) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "source.pdf"
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=500, height=700)
    writer.add_outline_item("Chapter One", 0)
    writer.add_outline_item("Chapter Two", 1)
    writer.add_outline_item("Appendix", 3)
    with path.open("wb") as stream:
        writer.write(stream)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        _catalog(
            _node(
                "whole-book",
                title="Whole book",
                source_range=_pdf_range(1, 4),
                evidence=_pdf_evidence(1),
            )
        ),
        source_sha256=content_hash,
    )

    with pytest.raises(SourceCodexCatalogError, match="contains 1 nodes.*at least 3"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )

    prompt = str(client.calls[0]["user_prompt"])
    assert "3 authored navigation entries" in prompt
    assert "beginning, middle, and end" in prompt


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


def test_agent_catalog_v3_keeps_unmapped_nodes_only_while_a_bounded_gap_remains(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _agent_catalog(
        AgentCatalogNode(
            key="subject.contracts",
            parent_key=None,
            number="I",
            title="Contracts",
            level=1,
        ),
        AgentCatalogNode(
            key="subject.contracts.offer",
            parent_key="subject.contracts",
            number="A",
            title="Offer",
            level=2,
        ),
        work_state="working",
        next_plan="Inspect the next authored contents pages.",
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert result.work_state == "working"
    assert [chapter.mapping_status for chapter in result.chapters] == ["unmapped", "unmapped"]
    assert result.catalog_payload and result.catalog_payload["schema_version"] == "agent_catalog_v3"
    assert client.calls[0]["inspection_scope"] == "catalog_v3"
    prompt = str(client.calls[0]["system_prompt"])
    assert "You own the investigation route" in prompt
    assert "Publish useful directory nodes even before they are citable" in prompt
    assert "Normally inspect native PDF navigation first" in prompt
    assert "pdf_file_page - printed_page + 1 = P" in prompt
    assert "Do not call source_range_preview for a mechanically covered node" in prompt


def test_agent_catalog_v3_materializes_native_navigation_ranges(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _agent_catalog(
        AgentCatalogV3Node(
            key="chapter.1",
            parent_key=None,
            number="1",
            title="First chapter",
            level=1,
            native_pdf_page=5,
            locator_source="native_navigation",
        ),
        AgentCatalogV3Node(
            key="chapter.1.section",
            parent_key="chapter.1",
            number="1.1",
            title="First section",
            level=2,
            native_pdf_page=10,
            locator_source="native_navigation",
        ),
        AgentCatalogV3Node(
            key="chapter.2",
            parent_key=None,
            number="2",
            title="Second chapter",
            level=1,
            native_pdf_page=20,
            locator_source="native_navigation",
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (5, 19),
        (10, 19),
        (20, 60),
    ]
    assert all(chapter.mapping_status == "verified" for chapter in result.chapters)
    assert all(chapter.metadata["locator_source"] == "native_navigation" for chapter in result.chapters)
    assert result.audit_metadata["host_pdf_range_count"] == 3


def test_agent_catalog_v3_preserves_an_agent_authored_pdf_range(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _agent_catalog(
        AgentCatalogV3Node(
            key="chapter.1",
            parent_key=None,
            number="1",
            title="First chapter",
            level=1,
            native_pdf_page=5,
            locator_source="legacy_range",
            mapping_status="verified",
            mapping_reason="The Agent verified the authored range.",
            source_range=_pdf_range(7, 12),
            evidence=_pdf_evidence(7),
        ),
        AgentCatalogV3Node(
            key="chapter.2",
            parent_key=None,
            number="2",
            title="Second chapter",
            level=1,
            native_pdf_page=20,
            locator_source="native_navigation",
        ),
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (7, 12),
        (20, 60),
    ]
    assert result.audit_metadata["host_pdf_range_count"] == 1


def _segmented_p_catalog() -> AgentCatalogV3:
    regimes = [
        AgentCatalogPaginationRegime(
            id="front",
            printed_page_start=1,
            printed_page_end=21,
            page_offset_p=5,
            anchors=[
                SourcePdfPageOffsetAnchor(pdf_file_page=5, printed_page=1),
                SourcePdfPageOffsetAnchor(pdf_file_page=15, printed_page=11),
                SourcePdfPageOffsetAnchor(pdf_file_page=25, printed_page=21),
            ],
        ),
        AgentCatalogPaginationRegime(
            id="restart",
            printed_page_start=1,
            printed_page_end=21,
            page_offset_p=55,
            anchors=[
                SourcePdfPageOffsetAnchor(pdf_file_page=55, printed_page=1),
                SourcePdfPageOffsetAnchor(pdf_file_page=65, printed_page=11),
                SourcePdfPageOffsetAnchor(pdf_file_page=75, printed_page=21),
            ],
        ),
    ]
    nodes = [
        AgentCatalogV3Node(
            key="first",
            parent_key=None,
            number="1",
            title="First",
            level=1,
            directory_page=2,
            printed_page=1,
            pagination_regime_id="front",
            locator_source="printed_directory",
        ),
        AgentCatalogV3Node(
            key="second",
            parent_key=None,
            number="2",
            title="Second",
            level=1,
            directory_page=3,
            printed_page=21,
            pagination_regime_id="front",
            locator_source="printed_directory",
        ),
        AgentCatalogV3Node(
            key="third",
            parent_key=None,
            number="3",
            title="Third",
            level=1,
            directory_page=50,
            printed_page=1,
            pagination_regime_id="restart",
            locator_source="printed_directory",
        ),
        AgentCatalogV3Node(
            key="fourth",
            parent_key=None,
            number="4",
            title="Fourth",
            level=1,
            directory_page=51,
            printed_page=21,
            pagination_regime_id="restart",
            locator_source="printed_directory",
        ),
    ]
    return AgentCatalogV3(
        schema_version="agent_catalog_v3",
        phase="terminal",
        directory_status="complete",
        index_status="complete",
        work_state="satisfied",
        summary="Complete printed directory.",
        completion_reason="Both directory ranges end before body content.",
        directory_evidence=[
            AgentCatalogDirectoryEvidence(kind="printed_directory_start", detail="First directory page verified.", page_start=2, page_end=2),
            AgentCatalogDirectoryEvidence(kind="directory_page_continuity", detail="Both directory ranges are continuous."),
            AgentCatalogDirectoryEvidence(kind="printed_directory_end", detail="Last directory page verified.", page_start=51, page_end=51),
        ],
        directory_page_ranges=[AgentCatalogPageRange(start=2, end=3), AgentCatalogPageRange(start=50, end=51)],
        pagination_regimes=regimes,
        nodes=nodes,
    )


def test_agent_catalog_v3_materializes_verified_segmented_p_regimes(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path, page_count=100)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _segmented_p_catalog().model_copy(
        update={
            "phase": "range_mapping",
            "index_status": "in_progress",
            "work_state": "working",
            "next_plan": "Preview every remaining body range.",
            "next_action": "read the next body pages",
            "completion_reason": "",
        }
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (5, 24),
        (25, 54),
        (55, 74),
        (75, 100),
    ]
    assert all(chapter.mapping_status == "verified" for chapter in result.chapters)
    assert result.work_state == "satisfied"
    assert result.audit_metadata["pdf_ranges_materialized_by_host"] is True
    assert result.audit_metadata["host_pdf_range_count"] == 4
    assert result.catalog_payload is not None
    assert [item["page_offset_p"] for item in result.catalog_payload["pagination_regimes"]] == [5, 55]
    assert all(node["mapping_status"] == "verified" for node in result.catalog_payload["nodes"])


def test_agent_catalog_v3_assigns_one_unambiguous_p_regime_without_body_reads(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path, page_count=60)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = AgentCatalogV3(
        schema_version="agent_catalog_v3",
        phase="range_mapping",
        directory_status="complete",
        index_status="in_progress",
        work_state="working",
        summary="Printed directory and exact P are available.",
        directory_evidence=[
            AgentCatalogDirectoryEvidence(kind="printed_directory_start", detail="Start verified."),
            AgentCatalogDirectoryEvidence(kind="directory_page_continuity", detail="Continuity verified."),
            AgentCatalogDirectoryEvidence(kind="printed_directory_end", detail="End verified."),
        ],
        directory_page_ranges=[AgentCatalogPageRange(start=2, end=3)],
        pagination_regimes=[
            AgentCatalogPaginationRegime(
                id="body",
                printed_page_start=1,
                printed_page_end=41,
                page_offset_p=5,
                anchors=[
                    SourcePdfPageOffsetAnchor(pdf_file_page=5, printed_page=1),
                    SourcePdfPageOffsetAnchor(pdf_file_page=25, printed_page=21),
                    SourcePdfPageOffsetAnchor(pdf_file_page=45, printed_page=41),
                ],
            )
        ],
        nodes=[
            AgentCatalogV3Node(
                key="one",
                parent_key=None,
                number="1",
                title="One",
                level=1,
                directory_page=2,
                printed_page=1,
                locator_source="printed_directory",
            ),
            AgentCatalogV3Node(
                key="two",
                parent_key=None,
                number="2",
                title="Two",
                level=1,
                directory_page=3,
                printed_page=41,
                locator_source="printed_directory",
            ),
        ],
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [(5, 44), (45, 60)]
    assert result.work_state == "satisfied"
    assert result.audit_metadata["host_pdf_range_count"] == 2
    assert result.catalog_payload is not None
    assert {node["pagination_regime_id"] for node in result.catalog_payload["nodes"]} == {"body"}
    assert client.tool_activity == []


def test_first_citable_partial_snapshot_is_published_with_typed_remaining_work(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _agent_catalog(
        AgentCatalogV3Node(
            key="verified",
            parent_key=None,
            number="1",
            title="Verified",
            level=1,
            locator_source="legacy_range",
            mapping_status="verified",
            mapping_reason="Published exact range.",
            source_range=_pdf_range(5, 10),
            evidence=_pdf_evidence(5),
        ),
        AgentCatalogV3Node(
            key="pending",
            parent_key=None,
            number="2",
            title="Pending",
            level=1,
        ),
        work_state="working",
    )

    result = generate_agent_catalog_turn(
        record=_record(path),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: FakeSourceCodexClient(catalog, source_sha256=content_hash),
    )

    assert result.catalog_payload is not None
    assert result.catalog_payload["snapshot_reason"] == "first_citable"
    assert result.catalog_payload["work_state"] == "working"
    assert result.catalog_payload["remaining_work"]
    assert [chapter.mapping_status for chapter in result.chapters] == ["verified", "unmapped"]


def test_two_unchanged_continuation_turns_pause_the_catalog(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    current = _agent_catalog(
        AgentCatalogNode(key="pending", parent_key=None, number="1", title="Pending", level=1),
        work_state="working",
    )
    checkpoint: dict[str, object] | None = None

    for _turn in range(3):
        result = generate_agent_catalog_turn(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            initial_catalog=checkpoint,
            client_factory=lambda _user_id, catalog=current: FakeSourceCodexClient(
                catalog,
                source_sha256=content_hash,
            ),
        )
        assert result.catalog_payload is not None
        checkpoint = result.catalog_payload
        current = AgentCatalogV3.model_validate(checkpoint)

    assert result.work_state == "paused"
    assert result.catalog_payload["no_progress_turns"] == 2
    assert "two consecutive turns" in result.stop_reason


def test_agent_catalog_v3_rejects_inexact_or_overlapping_p_regimes(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path, page_count=100)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    inexact = _segmented_p_catalog()
    inexact.pagination_regimes[1].anchors[2] = SourcePdfPageOffsetAnchor(pdf_file_page=76, printed_page=21)
    with pytest.raises(SourceCodexCatalogError, match="exact P equation"):
        generate_agent_catalog_turn(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: FakeSourceCodexClient(inexact, source_sha256=content_hash),
        )

    overlapping = _segmented_p_catalog()
    overlapping.pagination_regimes[1] = AgentCatalogPaginationRegime(
        id="restart",
        printed_page_start=1,
        printed_page_end=21,
        page_offset_p=20,
        anchors=[
            SourcePdfPageOffsetAnchor(pdf_file_page=20, printed_page=1),
            SourcePdfPageOffsetAnchor(pdf_file_page=30, printed_page=11),
            SourcePdfPageOffsetAnchor(pdf_file_page=40, printed_page=21),
        ],
    )
    with pytest.raises(SourceCodexCatalogError, match="must not overlap"):
        generate_agent_catalog_turn(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: FakeSourceCodexClient(overlapping, source_sha256=content_hash),
        )


def test_agent_catalog_v2_rejects_only_invalid_located_range(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = _agent_catalog(
        AgentCatalogNode(
            key="bad.range",
            parent_key=None,
            number="1",
            title="Invalid located node",
            level=1,
            mapping_status="verified",
            source_range=_pdf_range(1, 999),
        )
    )
    client = FakeSourceCodexClient(catalog, source_sha256=content_hash)

    with pytest.raises(SourceCodexCatalogError, match="outside the physical PDF pages"):
        generate_agent_catalog_turn(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_staged_processor_rebuilds_catalog_with_stable_chapter_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _complete_staged_catalog(
            _node(
                "stable.root",
                number="I",
                title="Complete",
                source_range=_pdf_range(5, 44),
                evidence=_pdf_evidence(5),
            ),
        ),
        source_sha256=content_hash,
    )
    result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    calls = 0

    def run_complete_catalog(**_kwargs):
        nonlocal calls
        calls += 1
        return result

    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
        run_complete_catalog,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    published_ids: list[list[str]] = []
    real_publish = store.publish_catalog

    def capture_publish(**kwargs):
        published_ids.append([chapter.id for chapter in kwargs["chapters"]])
        return real_publish(**kwargs)

    monkeypatch.setattr(store, "publish_catalog", capture_publish)

    processor = SourceDirectoryProcessor(store=store)
    first_structure = processor.process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    structure = processor.process(
        record=record,
        path=path,
        catalog_model=_model(),
        resume_catalog=True,
    )

    assert first_structure.catalog_version == 1
    assert structure.catalog_version == 2
    assert calls == 2
    assert published_ids[0][0] == published_ids[1][0]
    view = store.get_catalog_view(source=record)
    assert view.work_state == "satisfied"
    assert view.directory_status == "complete"
    assert view.index_status == "complete"
    assert view.unresolved_node_count == 0
    assert view.remaining_work_count == 0
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert {run.catalog_version for run in runs} == {1, 2}
    assert all(run.status == "succeeded" for run in runs)
    assert all(run.metadata.get("agent_catalog_payload") is not None for run in runs)


def test_staged_processor_does_not_publish_until_directory_is_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    incomplete_client = FakeSourceCodexClient(
        _agent_catalog(
            AgentCatalogNode(
                key="chapter.1",
                parent_key=None,
                number="1",
                title="First",
                level=1,
            ),
            work_state="working",
            next_action="inspect the remaining authored directory pages",
        ),
        source_sha256=content_hash,
    )
    complete_client = FakeSourceCodexClient(
        _complete_staged_catalog(
            _node(
                "chapter.1",
                number="1",
                title="First",
                source_range=_pdf_range(5, 44),
                evidence=_pdf_evidence(5),
            )
        ),
        source_sha256=content_hash,
    )
    results = [
        generate_agent_catalog_turn(
            record=record,
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: incomplete_client,
        ),
        generate_agent_catalog_turn(
            record=record,
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: complete_client,
        ),
    ]
    calls = 0

    def next_turn(**_kwargs):
        nonlocal calls
        result = results[calls]
        calls += 1
        return result

    monkeypatch.setattr(directory_processor_module, "generate_agent_catalog_turn", next_turn)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )

    assert calls == 2
    assert structure.catalog_version == 1
    assert structure.metadata["directory_status"] == "complete"
    assert store.get_catalog_view(source=record).chapters[0].title == "First"


def test_complete_native_navigation_keeps_agent_nodes_and_materializes_their_ranges(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(60):
        writer.add_blank_page(width=500, height=700)
    first = writer.add_outline_item("第一章 Root", 4)
    writer.add_outline_item("1.1 Child", 9, parent=first)
    writer.add_outline_item("第二章 Next", 19)
    with path.open("wb") as stream:
        writer.write(stream)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _agent_catalog(
            AgentCatalogV3Node(
                key="model.truncated",
                parent_key=None,
                number="第一章",
                title="Root",
                level=1,
                native_pdf_page=5,
                locator_source="native_navigation",
            ),
        ),
        source_sha256=content_hash,
        tool_activity=[{"tool": "pdf_navigation", "item_count": 3}],
    )
    result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    assert result.work_state == "satisfied"
    assert len(result.chapters) == 1
    assert [chapter.title for chapter in result.chapters] == ["Root"]
    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [(5, 60)]


@pytest.mark.parametrize(
    ("nodes", "next_action"),
    [
        ([], "inspect a different bounded directory candidate"),
        (
            [AgentCatalogNode(key="one", parent_key=None, number="1", title="One", level=1)],
            "inspect authored navigation and bounded directory candidates",
        ),
    ],
)
def test_host_derives_structured_work_instead_of_trusting_agent_status_strings(
    tmp_path: Path,
    nodes: list[AgentCatalogNode],
    next_action: str,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _agent_catalog(
            *nodes,
            work_state="working",
            next_action=next_action,
        ),
        source_sha256=content_hash,
    )
    result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    assert result.work_state == "working"
    assert result.next_plan
    assert result.catalog_payload is not None
    assert result.catalog_payload["remaining_work"]
    assert result.stop_reason == ""


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
        _complete_staged_catalog(
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
    direct_result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
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
    assert structure.metadata["directory_status"] == "complete"
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
    assert runs[-1].status == "succeeded"
    assert "agent_snapshot_published" in runs[-1].stage_history
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
        _complete_staged_catalog(
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
    direct_result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
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
    assert runs[-1].status == "succeeded"
    assert "agent_snapshot_published" in runs[-1].stage_history


def test_source_pi_directory_tree_publishes_with_body_mapping(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _complete_staged_catalog(
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
    direct_result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
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
    assert all(chapter.range is not None for chapter in view.chapters)
    assert runs[-1].turn_count == 1
    assert runs[-1].status == "succeeded"
    assert "agent_snapshot_published" in runs[-1].stage_history


def test_failed_rebuild_preserves_previous_catalog(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    _write_pdf(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(
        update={"metadata": {"content_hash": content_hash}}
    )
    client = FakeSourceCodexClient(
        _complete_staged_catalog(
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
    successful_result = generate_agent_catalog_turn(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
        lambda **_kwargs: successful_result,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceDirectoryProcessor(store=store)
    first = processor.process(record=record, path=path, catalog_model=_model())

    def fail_catalog(**_kwargs):
        raise SourceCodexCatalogError("single-turn catalog failed")

    monkeypatch.setattr(
        directory_processor_module,
        "generate_agent_catalog_turn",
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

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.models import SourceChapter, SourceIngestionRecord, SourceStructure
from app.services import source_page_visual_analysis as analysis
from app.services.image_ocr import OCRLineLayout, OCRPageLayout
from reportlab.pdfgen import canvas


class _Result:
    def __init__(self, payload) -> None:
        self.output_parsed = payload
        self.activity = []


class _FakeAdapter:
    supports_source_visual_tools = True

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def parse_structured(self, **kwargs):
        self.calls.append(kwargs)
        return _Result(self.payloads.pop(0))


def _pdf(path: Path, pages: int = 1) -> None:
    document = canvas.Canvas(str(path), pagesize=(600, 800))
    for page_no in range(1, pages + 1):
        document.drawString(80, 700, f"page {page_no}")
        document.rect(120, 280, 360, 300)
        document.showPage()
    document.save()


def _record(path: Path) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id="source_visual_scope",
        owner_user_id="owner_visual_scope",
        package_id="package_visual_scope",
        file_name=path.name,
        title=path.stem,
        mime_type="application/pdf",
        status="ready",
        metadata={"content_hash": "a" * 64},
    )


def _structure(record: SourceIngestionRecord) -> SourceStructure:
    return SourceStructure(
        id="structure_visual_scope",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        status="ready",
        visual_index_version=5,
    )


def _chapter(record: SourceIngestionRecord) -> SourceChapter:
    return SourceChapter(
        id="chapter_visual_scope",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        title="Chapter",
        mapping_status="verified",
    )


def _render_turn(pages: list[int], *, dpi: int = 180) -> dict:
    return {
        "tool_calls": [
            {"tool": "render_source_pages", "pages": pages, "dpi": dpi}
        ],
        "finished": False,
    }


def _crop_turn(page_no: int, bbox: list[float], *, dpi: int = 300) -> dict:
    return {
        "tool_calls": [
            {
                "tool": "crop_source_page",
                "page_no": page_no,
                "bbox": bbox,
                "dpi": dpi,
            }
        ],
        "finished": False,
    }


def _register_turn(
    *,
    crop_ref: str = "crop_0002_01",
    kind: str = "relationship_diagram",
    uncertainty_note: str = "",
    excluded: list[dict] | None = None,
) -> dict:
    return {
        "tool_calls": [
            {
                "tool": "register_board_visual",
                "crop_ref": crop_ref,
                "kind": kind,
                "caption": "Figure 1.1",
                "nearby_anchor": "teaching diagram",
                "teaching_reason": "Shows the relationship required by the explanation.",
                "related_section": "Core concept",
                "uncertainty_note": uncertainty_note,
                "confidence": 0.93,
            }
        ],
        "excluded_candidates": excluded or [],
        "finished": False,
    }


def _finish_turn(*, notes: list[str] | None = None) -> dict:
    return {"tool_calls": [], "notes": notes or [], "finished": True}


def _run(
    *,
    path: Path,
    adapter: _FakeAdapter,
    page_start: int = 1,
    page_end: int = 1,
    on_activity=None,
):
    record = _record(path)
    return analysis.analyze_pdf_visual_scope(
        source=record,
        structure=_structure(record),
        chapter=_chapter(record),
        source_path=path,
        page_start=page_start,
        page_end=page_end,
        scope_cache_key="scope-a",
        model_identity="provider:model-a",
        adapter=adapter,
        on_activity=on_activity,
    )


def test_agent_controls_render_crop_register_and_records_portrait_exclusion(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "teaching-and-portrait.pdf"
    _pdf(path)
    monkeypatch.setattr(
        analysis,
        "extract_pdf_pages_layout",
        lambda *_args, **_kwargs: [
            OCRPageLayout(
                page_no=1,
                lines=[OCRLineLayout(text="Figure 1.1 Teaching relation", x=0.2, y=0.4)],
            )
        ],
    )
    monkeypatch.setattr(
        analysis,
        "persist_source_visual_asset",
        lambda content, *, mime_type: ("sha256/visual.png", "b" * 64),
    )
    monkeypatch.setattr(
        analysis,
        "resolve_source_visual_storage_key",
        lambda _key: tmp_path / "visual.png",
    )
    excluded = [
        {
            "page_no": 1,
            "kind": "portrait",
            "reason": "The author portrait does not explain this chapter concept.",
        }
    ]
    adapter = _FakeAdapter(
        [
            _render_turn([1]),
            _crop_turn(1, [0.15, 0.2, 0.85, 0.72]),
            _register_turn(excluded=excluded),
            _finish_turn(),
        ]
    )
    activity = []

    result = _run(path=path, adapter=adapter, on_activity=activity.append)

    assert [call["schema"] for call in adapter.calls] == [
        analysis.BoardVisualAgentTurn
    ] * 4
    assert adapter.calls[0]["image_inputs"] == []
    assert len(adapter.calls[1]["image_inputs"]) == 1
    assert len(adapter.calls[2]["image_inputs"]) == 1
    assert len(result.visuals) == 1
    assert result.visuals[0].metadata["teaching_reason"]
    assert result.excluded_candidates == excluded
    assert "keyword blacklist" in analysis.BOARD_VISUAL_SELECTION_INSTRUCTIONS
    assert activity[-1].metadata["kind"] == "board_visual_selection_completed"


def test_missing_numbered_figure_does_not_trigger_retry_or_block(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "missing.pdf"
    _pdf(path)
    monkeypatch.setattr(analysis, "extract_pdf_pages_layout", lambda *_args, **_kwargs: [])
    adapter = _FakeAdapter(
        [
            _render_turn([1], dpi=120),
            _finish_turn(notes=["The referenced figure was not visible in this preview."]),
        ]
    )

    result = _run(path=path, adapter=adapter)

    assert len(adapter.calls) == 2
    assert result.visuals == []
    assert result.retry_pages == []
    assert result.missing_references == []
    assert result.warnings == ["The referenced figure was not visible in this preview."]


def test_invalid_crop_is_a_warning_not_a_board_gate(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "invalid-bbox.pdf"
    _pdf(path)
    monkeypatch.setattr(analysis, "extract_pdf_pages_layout", lambda *_args, **_kwargs: [])
    adapter = _FakeAdapter(
        [
            _render_turn([1]),
            _crop_turn(1, [-0.1, 0.1, 0.5, 0.6]),
            _finish_turn(),
        ]
    )

    result = _run(path=path, adapter=adapter)

    assert result.visuals == []
    assert "crop_source_page failed" in result.warnings[0]


def test_agent_can_expand_uncertain_crop_and_preserve_uncertainty_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "uncertain.pdf"
    _pdf(path)
    monkeypatch.setattr(analysis, "extract_pdf_pages_layout", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        analysis,
        "persist_source_visual_asset",
        lambda content, *, mime_type: ("sha256/uncertain.png", "c" * 64),
    )
    monkeypatch.setattr(
        analysis,
        "resolve_source_visual_storage_key",
        lambda _key: tmp_path / "uncertain.png",
    )
    bbox = [0.08, 0.12, 0.92, 0.84]
    adapter = _FakeAdapter(
        [
            _render_turn([1], dpi=100),
            _crop_turn(1, bbox, dpi=300),
            _register_turn(
                uncertainty_note="Wide crop keeps a faint legend and caption."
            ),
            _finish_turn(),
        ]
    )

    result = _run(path=path, adapter=adapter)

    assert result.visuals[0].bbox == bbox
    assert result.visuals[0].metadata["uncertainty_note"].startswith("Wide crop")


def test_agent_chooses_page_order_batch_sizes_resolution_and_reviews(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "nine-pages.pdf"
    _pdf(path, pages=9)
    monkeypatch.setattr(analysis, "extract_pdf_pages_layout", lambda *_args, **_kwargs: [])
    adapter = _FakeAdapter(
        [
            _render_turn([9], dpi=300),
            _render_turn([1, 2, 3, 4], dpi=120),
            _render_turn([5, 6, 7, 8], dpi=180),
            _render_turn([9], dpi=180),
            _finish_turn(),
        ]
    )
    activity = []

    result = _run(
        path=path,
        adapter=adapter,
        page_start=1,
        page_end=9,
        on_activity=activity.append,
    )

    viewed_sets = [
        set(json.loads(call["user_prompt"])["viewed_pages"])
        for call in adapter.calls
    ]
    assert result.page_count == 9
    assert viewed_sets[0] == set()
    assert viewed_sets[-1] == set(range(1, 10))
    assert activity[-1].metadata["viewed_page_count"] == 9
    assert [len(call["image_inputs"]) for call in adapter.calls] == [0, 1, 4, 4, 1]


def test_register_requires_backend_issued_crop_ref(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "invented-ref.pdf"
    _pdf(path)
    monkeypatch.setattr(analysis, "extract_pdf_pages_layout", lambda *_args, **_kwargs: [])
    adapter = _FakeAdapter(
        [
            _render_turn([1]),
            _register_turn(crop_ref="invented"),
            _finish_turn(),
        ]
    )

    result = _run(path=path, adapter=adapter)

    assert result.visuals == []
    assert "unknown crop" in result.warnings[0]


def test_render_and_crop_tools_reject_scope_and_coordinate_violations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "security.pdf"
    _pdf(path, pages=2)

    with pytest.raises(analysis.SourcePageVisualAnalysisError, match="authorized range"):
        analysis.render_source_pages(
            path,
            authorized_page_start=1,
            authorized_page_end=1,
            pages=[2],
        )
    with pytest.raises(analysis.SourcePageVisualAnalysisError, match="coordinates"):
        analysis.crop_source_page(
            path,
            authorized_page_start=1,
            authorized_page_end=2,
            page_no=1,
            bbox=[0.8, 0.2, 0.3, 0.7],
        )

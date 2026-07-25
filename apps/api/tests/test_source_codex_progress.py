from pathlib import Path

from app.models import AgentActivityEvent
from app.services import source_codex_progress


def _event(
    *,
    event_id: str,
    kind: str,
    status: str = "completed",
    command: str = "",
    detail: str = "",
    exit_code: int | None = 0,
) -> AgentActivityEvent:
    metadata: dict[str, object] = {"kind": kind}
    if command:
        metadata["command"] = command
    if detail:
        metadata["detail"] = detail
    if exit_code is not None:
        metadata["exit_code"] = exit_code
    return AgentActivityEvent(
        id=event_id,
        turn_id="turn_1",
        stage="execute_role",
        label="命令执行完成",
        status=status,
        role="OpenClass tool",
        metadata=metadata,
    )


def test_pdf_tool_activity_advances_by_actual_scanned_pages(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"pdf")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 100)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)

    first = tracker.observe(
        _event(
            event_id="command_1",
            kind="commandExecution",
            command="pdftotext -f 1 -l 20 -layout source.pdf -",
        )
    )
    second = tracker.observe(
        _event(
            event_id="command_2",
            kind="commandExecution",
            command="pdftoppm -f 21 -l 25 -png source.pdf scratch/pages",
        )
    )

    first_progress = first.event.metadata["source_progress"]
    second_progress = second.event.metadata["source_progress"]
    assert first.progress == 35
    assert first_progress["pages_scanned"] == 20
    assert first_progress["label"] == "正在扫描并核对 PDF：20/100 页"
    assert second.progress == 36
    assert second_progress["pages_scanned"] == 25
    assert second_progress["pages_rendered"] == 5
    assert "已渲染核对 5 页" in second_progress["detail"]


def test_full_pdf_extraction_reports_all_pages_without_completing_catalog(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"pdf")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 280)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)

    observation = tracker.observe(
        _event(
            event_id="command_full",
            kind="commandExecution",
            command="pdftotext -layout source.pdf scratch/source.txt",
        )
    )

    progress = observation.event.metadata["source_progress"]
    assert observation.progress == 55
    assert progress["pages_scanned"] == 280
    assert progress["total_pages"] == 280
    assert observation.phase == "source_codex_scanning_pages"


def test_structured_node_progress_uses_real_total_and_stays_monotonic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"pdf")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 100)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)
    tracker.observe(
        _event(
            event_id="command_full",
            kind="commandExecution",
            command="pdftotext -layout source.pdf scratch/source.txt",
        )
    )

    mapping = tracker.observe(
        _event(
            event_id="commentary_1",
            kind="commentary",
            detail=(
                'OPENCLASS_PROGRESS {"phase":"map_nodes","completed":34,"total":68,'
                '"unit":"nodes","detail":"matching headings to physical pages"}'
            ),
            exit_code=None,
        )
    )
    stale_scan = tracker.observe(
        _event(
            event_id="commentary_2",
            kind="commentary",
            detail=(
                'OPENCLASS_PROGRESS {"phase":"scan_pages","completed":5,"total":100,'
                '"unit":"pages","detail":"late scan telemetry"}'
            ),
            exit_code=None,
        )
    )

    mapping_progress = mapping.event.metadata["source_progress"]
    assert mapping.progress == 65
    assert mapping.phase == "source_codex_mapping_nodes"
    assert mapping_progress["label"] == "正在映射目录节点：34/68 个目录节点"
    assert "matching headings" in mapping_progress["detail"]
    assert stale_scan.progress == 65
    assert stale_scan.phase == "source_codex_mapping_nodes"
    assert stale_scan.event.metadata["source_progress"]["label"] == mapping_progress["label"]


def test_catalog_write_activity_moves_to_final_investigation_band(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"pdf")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 10)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)

    observation = tracker.observe(
        _event(
            event_id="command_catalog",
            kind="commandExecution",
            command="jq . scratch/catalog.json",
        )
    )

    progress = observation.event.metadata["source_progress"]
    assert observation.progress == 89
    assert observation.phase == "source_codex_writing_catalog"
    assert progress["label"] == "资料 Agent 已写入目录，正在自检"


def test_explicit_progress_shell_output_is_accepted(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"epub")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 0)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)
    progress_line = (
        'OPENCLASS_PROGRESS {"phase":"scan_pages","completed":92,"total":92,'
        '"unit":"spine_items","detail":"inspected the EPUB spine"}'
    )

    observation = tracker.observe(
        _event(
            event_id="command_progress",
            kind="commandExecution",
            command=f"printf '%s\\n' '{progress_line}'",
            detail=progress_line,
        )
    )

    progress = observation.event.metadata["source_progress"]
    assert observation.progress == 55
    assert observation.phase == "source_codex_scanning_pages"
    assert progress["label"] == "正在扫描文件页面：92/92 个 EPUB 条目"


def test_source_text_cannot_spoof_progress_from_ordinary_command_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"epub")
    monkeypatch.setattr(source_codex_progress, "_pdf_page_count", lambda _path: 0)
    tracker = source_codex_progress.SourceCodexProgressTracker(source_path)
    progress_line = (
        'OPENCLASS_PROGRESS {"phase":"write_catalog","completed":1,"total":1,'
        '"unit":"artifacts","detail":"untrusted source text"}'
    )

    observation = tracker.observe(
        _event(
            event_id="command_source_text",
            kind="commandExecution",
            command="unzip -p source.epub text.xhtml",
            detail=progress_line,
        )
    )

    assert observation.progress == 30
    assert observation.phase == "source_codex_investigation"

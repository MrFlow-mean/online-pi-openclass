from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.models import AgentActivityEvent


PROGRESS_PREFIX = "OPENCLASS_PROGRESS "

_PHASE_BANDS: dict[str, tuple[int, int, str, str]] = {
    "scan_pages": (30, 55, "source_codex_scanning_pages", "正在扫描文件页面"),
    "map_nodes": (55, 75, "source_codex_mapping_nodes", "正在映射目录节点"),
    "verify_ranges": (75, 88, "source_codex_verifying_ranges", "正在验证章节范围"),
    "write_catalog": (88, 90, "source_codex_writing_catalog", "正在写入完整目录"),
}

_JOB_PHASE_RANK = {
    "source_codex_investigation": 0,
    "source_codex_scanning_pages": 1,
    "source_codex_mapping_nodes": 2,
    "source_codex_verifying_ranges": 3,
    "source_codex_writing_catalog": 4,
}

_UNIT_LABELS = {
    "pages": "页",
    "nodes": "个目录节点",
    "ranges": "个章节范围",
    "spine_items": "个 EPUB 条目",
    "sections": "个章节",
    "checks": "项检查",
    "artifacts": "个目录文件",
}


@dataclass(frozen=True)
class SourceCodexProgressObservation:
    event: AgentActivityEvent
    progress: int
    phase: str


class SourceCodexProgressTracker:
    """Convert auditable Source Codex work into durable, monotonic progress."""

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.total_pages = _pdf_page_count(source_path) if source_path.suffix.lower() == ".pdf" else 0
        self.scanned_pages: set[int] = set()
        self.rendered_pages: set[int] = set()
        self.completed_tool_actions = 0
        self.progress = 30
        self.phase = "source_codex_investigation"
        self.label = "资料 Agent 正在读取文件结构"

    def observe(self, event: AgentActivityEvent) -> SourceCodexProgressObservation:
        if event.status == "completed" and str(event.metadata.get("kind") or "") in {
            "commandExecution",
            "imageView",
            "mcpToolCall",
            "dynamicToolCall",
        }:
            self.completed_tool_actions += 1

        command = str(event.metadata.get("command") or "")
        if (
            event.status == "completed"
            and str(event.metadata.get("kind") or "") == "commandExecution"
            and _command_succeeded(event)
        ):
            scanned, rendered = _pages_from_command(command, total_pages=self.total_pages)
            self.scanned_pages.update(scanned)
            self.rendered_pages.update(rendered)

        snapshot = _structured_progress(event)
        structured_detail = ""
        if snapshot is not None:
            phase_name, completed, total, unit, structured_detail = snapshot
            band_start, band_end, job_phase, default_label = _PHASE_BANDS[phase_name]
            candidate = band_start + round((band_end - band_start) * completed / total)
            if _JOB_PHASE_RANK[job_phase] >= _JOB_PHASE_RANK[self.phase]:
                self.progress = max(self.progress, min(band_end, candidate))
                self.phase = job_phase
                unit_label = _UNIT_LABELS.get(unit, unit)
                self.label = f"{default_label}：{completed}/{total} {unit_label}"
            else:
                structured_detail = ""
        else:
            self._apply_mechanical_progress(command=command, event=event)
            self.label = self._mechanical_label()

        detail_parts: list[str] = []
        if structured_detail:
            detail_parts.append(structured_detail)
        if self.total_pages and self.scanned_pages:
            detail_parts.append(f"已扫描 {len(self.scanned_pages)}/{self.total_pages} 页")
        elif self.total_pages:
            detail_parts.append(f"文件共 {self.total_pages} 页")
        if self.rendered_pages:
            detail_parts.append(f"已渲染核对 {len(self.rendered_pages)} 页")
        if self.completed_tool_actions:
            detail_parts.append(f"已完成 {self.completed_tool_actions} 次工具检查")

        source_progress = {
            "phase": self.phase,
            "label": self.label,
            "detail": " · ".join(dict.fromkeys(detail_parts)),
            "progress": self.progress,
            "pages_scanned": len(self.scanned_pages),
            "total_pages": self.total_pages,
            "pages_rendered": len(self.rendered_pages),
            "completed_tool_actions": self.completed_tool_actions,
        }
        decorated = event.model_copy(
            update={"metadata": {**event.metadata, "source_progress": source_progress}}
        )
        return SourceCodexProgressObservation(
            event=decorated,
            progress=self.progress,
            phase=self.phase,
        )

    def _apply_mechanical_progress(self, *, command: str, event: AgentActivityEvent) -> None:
        if self.total_pages and self.scanned_pages:
            candidate = 30 + round(25 * len(self.scanned_pages) / self.total_pages)
            self.progress = max(self.progress, min(55, candidate))
            if _JOB_PHASE_RANK[self.phase] <= _JOB_PHASE_RANK["source_codex_scanning_pages"]:
                self.phase = "source_codex_scanning_pages"
        if (
            event.status == "completed"
            and str(event.metadata.get("kind") or "") == "commandExecution"
            and "catalog.json" in command
            and _command_succeeded(event)
        ):
            self.progress = max(self.progress, 89)
            self.phase = "source_codex_writing_catalog"

    def _mechanical_label(self) -> str:
        if self.phase == "source_codex_writing_catalog":
            return "资料 Agent 已写入目录，正在自检"
        if self.phase in {"source_codex_mapping_nodes", "source_codex_verifying_ranges"}:
            return self.label
        if self.total_pages and self.scanned_pages:
            return f"正在扫描并核对 PDF：{len(self.scanned_pages)}/{self.total_pages} 页"
        if self.completed_tool_actions:
            return f"资料 Agent 正在调查文件：已完成 {self.completed_tool_actions} 次工具检查"
        return "资料 Agent 正在读取文件结构"


def _pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def _command_succeeded(event: AgentActivityEvent) -> bool:
    exit_code = event.metadata.get("exit_code")
    return exit_code in (None, 0)


def _structured_progress(
    event: AgentActivityEvent,
) -> tuple[str, int, int, str, str] | None:
    kind = str(event.metadata.get("kind") or "")
    command = str(event.metadata.get("command") or "")
    is_commentary = kind == "commentary"
    is_explicit_progress_command = (
        kind == "commandExecution"
        and event.status == "completed"
        and _command_succeeded(event)
        and PROGRESS_PREFIX in command
    )
    if not is_commentary and not is_explicit_progress_command:
        return None
    detail = str(event.metadata.get("detail") or "")
    marker_index = detail.rfind(PROGRESS_PREFIX)
    if marker_index < 0:
        return None
    payload_text = detail[marker_index + len(PROGRESS_PREFIX) :].lstrip()
    try:
        payload, _end = json.JSONDecoder().raw_decode(payload_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    phase = str(payload.get("phase") or "")
    if phase not in _PHASE_BANDS:
        return None
    completed = payload.get("completed")
    total = payload.get("total")
    if (
        not isinstance(completed, int)
        or isinstance(completed, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or completed < 0
    ):
        return None
    completed = min(completed, total)
    unit = str(payload.get("unit") or "checks").strip() or "checks"
    message = str(payload.get("detail") or "").strip()
    return phase, completed, total, unit, message


def _pages_from_command(command: str, *, total_pages: int) -> tuple[set[int], set[int]]:
    if total_pages <= 0 or not command:
        return set(), set()
    lowered = command.lower()
    uses_text = "pdftotext" in lowered
    uses_render = "pdftoppm" in lowered
    if not uses_text and not uses_render:
        return set(), set()

    pages: set[int] = set()
    range_matches = re.findall(r"(?:^|\s)-f\s+(\d+)\s+-l\s+(\d+)(?:\s|$)", command)
    for start_text, end_text in range_matches:
        start = max(1, int(start_text))
        end = min(total_pages, int(end_text))
        if start <= end:
            pages.update(range(start, end + 1))

    for values in re.findall(r"for\s+\w+\s+in\s+([0-9 ]+);", command):
        pages.update(
            page
            for token in values.split()
            if token.isdigit() and 1 <= (page := int(token)) <= total_pages
        )

    for start_text, end_text in re.findall(r"seq\s+(\d+)\s+(\d+)", command):
        start = max(1, int(start_text))
        end = min(total_pages, int(end_text))
        if start <= end:
            pages.update(range(start, end + 1))

    if uses_text and not pages and "source.pdf" in lowered:
        pages.update(range(1, total_pages + 1))

    return pages, set(pages) if uses_render else set()

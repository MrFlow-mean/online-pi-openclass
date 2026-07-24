#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-content-hash", required=True)
    parser.add_argument("--mime-type", default="application/pdf")
    parser.add_argument("--pages", required=True)
    args = parser.parse_args()
    input_path = Path(args.input).resolve()
    requested_pages = _pages(args.pages)
    reader = PdfReader(str(input_path))
    with tempfile.TemporaryDirectory(prefix="openclass-mineru-") as temporary:
        work_dir = Path(temporary)
        subset_path = work_dir / "selected-pages.pdf"
        writer = PdfWriter()
        for page_no in requested_pages:
            if page_no > len(reader.pages):
                raise ValueError(f"Page {page_no} exceeds PDF page count {len(reader.pages)}")
            writer.add_page(reader.pages[page_no - 1])
        with subset_path.open("wb") as stream:
            writer.write(stream)
        result_dir = work_dir / "output"
        executable = Path(sys.executable).parent / "mineru"
        completed = subprocess.run(
            [str(executable), "-p", str(subset_path), "-o", str(result_dir), "-b", "pipeline"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "MinerU failed")[-2000:])
        artifacts = sorted(result_dir.rglob("*_content_list.json"))
        if not artifacts:
            raise RuntimeError("MinerU produced no content-list JSON artifact.")
        items = json.loads(artifacts[-1].read_text(encoding="utf-8"))

    elements: list[dict[str, Any]] = []
    for order, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        subset_index = int(item.get("page_idx") or 0)
        if subset_index < 0 or subset_index >= len(requested_pages):
            continue
        page_no = requested_pages[subset_index]
        raw_type = str(item.get("type") or "text").lower()
        text = _content(item, raw_type)
        bbox = item.get("bbox") or []
        elements.append(
            {
                "element_id": str(item.get("id") or f"{args.source_id}:{page_no}:{order}"),
                "page_no": page_no,
                "element_type": _element_type(raw_type),
                "reading_order": order,
                "raw_text": text,
                "normalized_text": _normalize(text),
                "bbox": [float(value) for value in bbox] if isinstance(bbox, list) and len(bbox) == 4 else [],
                "confidence": _confidence(item.get("score") or item.get("confidence")),
                "metadata": {"mineru_type": raw_type, "page_idx": subset_index},
            }
        )
    payload = {
        "source_id": args.source_id,
        "source_content_hash": args.source_content_hash,
        "parser": "mineru",
        "parser_version": "3.4.4",
        "page_count": len(reader.pages),
        "elements": elements,
        "warnings": [],
        "metadata": {"backend": "pipeline", "selected_pages": requested_pages},
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _pages(raw: str) -> list[int]:
    pages = sorted({int(value) for value in raw.split(",") if value.strip()})
    if not pages or pages[0] < 1:
        raise ValueError("At least one positive page number is required.")
    return pages


def _content(item: dict[str, Any], raw_type: str) -> str:
    for key in ("text", "table_body", "latex", "content", "image_caption"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (list, dict)) and value:
            return json.dumps(value, ensure_ascii=False)
    return "" if raw_type in {"image", "picture"} else json.dumps(item, ensure_ascii=False)


def _element_type(raw_type: str) -> str:
    if "table" in raw_type:
        return "table"
    if raw_type in {"equation", "formula", "interline_equation", "inline_equation"}:
        return "formula"
    if "title" in raw_type or "heading" in raw_type:
        return "heading"
    if "list" in raw_type:
        return "list_item"
    if "caption" in raw_type:
        return "caption"
    if raw_type in {"image", "picture"}:
        return "image"
    if "footnote" in raw_type:
        return "footnote"
    return "paragraph"


def _confidence(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, parsed))


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()


if __name__ == "__main__":
    main()

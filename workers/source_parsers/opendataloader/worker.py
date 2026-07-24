#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

import opendataloader_pdf
from pypdf import PdfReader


PARSER_NAME = "opendataloader"
PARSER_VERSION = "2.5.0"
ELEMENT_TYPES = {
    "heading": "heading",
    "title": "heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "list": "list_item",
    "list_item": "list_item",
    "list-item": "list_item",
    "table": "table",
    "formula": "formula",
    "equation": "formula",
    "caption": "caption",
    "image": "image",
    "picture": "image",
    "footnote": "footnote",
    "code": "code",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-content-hash", required=True)
    parser.add_argument("--mime-type", default="application/pdf")
    args = parser.parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    with tempfile.TemporaryDirectory(prefix="openclass-opendataloader-") as temporary:
        result_dir = Path(temporary)
        opendataloader_pdf.convert(
            input_path=[str(input_path)],
            output_dir=str(result_dir),
            format="json",
            image_output="off",
        )
        json_files = sorted(
            (path for path in result_dir.rglob("*.json") if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if not json_files:
            raise RuntimeError("OpenDataLoader produced no JSON artifact.")
        raw_payload = json.loads(json_files[-1].read_text(encoding="utf-8"))

    raw_elements = list(_walk_elements(raw_payload))
    elements: list[dict[str, Any]] = []
    for order, item in enumerate(raw_elements):
        text = _element_text(item)
        raw_type = str(item.get("type") or item.get("element_type") or "paragraph").lower()
        page_no = _positive_int(
            item.get("page number")
            or item.get("page_number")
            or item.get("page_no")
            or item.get("page"),
            default=1,
        )
        bbox = item.get("bounding box") or item.get("bounding_box") or item.get("bbox") or []
        if not isinstance(bbox, list) or len(bbox) != 4:
            bbox = []
        elements.append(
            {
                "element_id": str(item.get("id") or f"{args.source_id}:{page_no}:{order}"),
                "page_no": page_no,
                "element_type": ELEMENT_TYPES.get(raw_type, "paragraph"),
                "reading_order": order,
                "raw_text": text,
                "normalized_text": _normalize_text(text),
                "bbox": [float(value) for value in bbox],
                "confidence": _confidence(item.get("confidence")),
                "metadata": {
                    "opendataloader_type": raw_type,
                    "heading_level": item.get("heading level") or item.get("heading_level"),
                },
            }
        )

    page_count = len(PdfReader(str(input_path)).pages)
    payload = {
        "source_id": args.source_id,
        "source_content_hash": args.source_content_hash,
        "parser": PARSER_NAME,
        "parser_version": PARSER_VERSION,
        "page_count": page_count,
        "elements": elements,
        "warnings": [],
        "metadata": {
            "mime_type": args.mime_type,
            "json_artifact_is_fact_source": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _walk_elements(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _walk_elements(item)
        return
    if not isinstance(value, dict):
        return
    if any(key in value for key in ("type", "element_type")) and any(
        key in value for key in ("content", "text", "html", "markdown")
    ):
        yield value
        return
    for key in ("elements", "children", "content", "pages", "items", "document"):
        if key in value:
            yield from _walk_elements(value[key])


def _element_text(item: dict[str, Any]) -> str:
    value = item.get("content")
    if value is None:
        value = item.get("text") or item.get("markdown") or item.get("html") or ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _confidence(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, parsed))


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()


if __name__ == "__main__":
    main()

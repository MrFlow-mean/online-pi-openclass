#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter
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
    with tempfile.TemporaryDirectory(prefix="openclass-docling-") as temporary:
        subset_path = Path(temporary) / "selected-pages.pdf"
        writer = PdfWriter()
        for page_no in requested_pages:
            if page_no > len(reader.pages):
                raise ValueError(f"Page {page_no} exceeds PDF page count {len(reader.pages)}")
            writer.add_page(reader.pages[page_no - 1])
        with subset_path.open("wb") as stream:
            writer.write(stream)
        conversion = DocumentConverter().convert(subset_path)
        document = conversion.document

    elements: list[dict[str, Any]] = []
    for order, pair in enumerate(document.iterate_items()):
        item = pair[0] if isinstance(pair, tuple) else pair
        provenance = list(getattr(item, "prov", []) or [])
        if not provenance:
            continue
        prov = provenance[0]
        subset_page = int(getattr(prov, "page_no", 1))
        if subset_page < 1 or subset_page > len(requested_pages):
            continue
        page_no = requested_pages[subset_page - 1]
        label = str(getattr(item, "label", "text")).split(".")[-1].lower()
        text = _item_text(item, document)
        bbox = getattr(prov, "bbox", None)
        box = []
        if bbox is not None:
            values = [getattr(bbox, key, None) for key in ("l", "t", "r", "b")]
            if all(isinstance(value, (int, float)) for value in values):
                box = [float(value) for value in values]
        elements.append(
            {
                "element_id": str(getattr(item, "self_ref", "") or f"{args.source_id}:{page_no}:{order}"),
                "page_no": page_no,
                "element_type": _element_type(label),
                "reading_order": order,
                "raw_text": text,
                "normalized_text": _normalize(text),
                "bbox": box,
                "confidence": 1.0,
                "metadata": {"docling_label": label, "subset_page": subset_page},
            }
        )
    payload = {
        "source_id": args.source_id,
        "source_content_hash": args.source_content_hash,
        "parser": "docling",
        "parser_version": "2.115.0",
        "page_count": len(reader.pages),
        "elements": elements,
        "warnings": [],
        "metadata": {"selected_pages": requested_pages},
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _item_text(item: object, document: object) -> str:
    text = getattr(item, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    export = getattr(item, "export_to_markdown", None)
    if callable(export):
        try:
            value = export(doc=document)
        except TypeError:
            value = export()
        if isinstance(value, str):
            return value
    data = getattr(item, "data", None)
    if data is not None:
        return json.dumps(data, ensure_ascii=False, default=str)
    return ""


def _element_type(label: str) -> str:
    if "table" in label:
        return "table"
    if label in {"formula", "equation"}:
        return "formula"
    if label in {"section_header", "title", "heading"}:
        return "heading"
    if "list" in label:
        return "list_item"
    if "caption" in label:
        return "caption"
    if label in {"picture", "image"}:
        return "image"
    if "footnote" in label:
        return "footnote"
    if "code" in label:
        return "code"
    return "paragraph"


def _pages(raw: str) -> list[int]:
    pages = sorted({int(value) for value in raw.split(",") if value.strip()})
    if not pages or pages[0] < 1:
        raise ValueError("At least one positive page number is required.")
    return pages


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()


if __name__ == "__main__":
    main()

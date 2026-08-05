from __future__ import annotations

import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest
from docx import Document

from app.services.docx_quality_check import DocxExportQualityError, assert_docx_export_quality, inspect_docx_export
from app.services.docx_styles import apply_textbook_docx_styles
from app.services.latex_to_omml import append_omml_math
from app.services.rich_document import build_document, export_docx
from app.services.rich_document import core as rich_document_core

_NS = {
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def _document_root(path):
    with ZipFile(path) as archive:
        return ET.fromstring(archive.read("word/document.xml"))


def _math_text(root: ET.Element) -> str:
    return "".join(node.text or "" for node in root.findall(".//m:t", _NS))


def test_latex_to_omml_converts_core_math_structures(tmp_path) -> None:
    document = Document()
    paragraph = document.add_paragraph()
    append_omml_math(
        paragraph,
        r"\|P\|=\max_{1\le i\le n}\Delta x_i,\quad S(P,\xi)=\sum_{i=1}^{n}f(\xi_i)",
        display=True,
    )
    export_path = tmp_path / "math.docx"
    document.save(export_path)

    root = _document_root(export_path)
    math_text = _math_text(root)

    assert root.findall(".//m:oMathPara", _NS)
    assert root.findall(".//m:sSubSup", _NS)
    assert "‖P‖" in math_text
    assert "ξ" in math_text
    assert "∑" in math_text


def test_rich_document_latex_fallback_reads_nested_braces() -> None:
    assert rich_document_core._latex_inline_text(
        r"\frac{1 + \sqrt{x}}{2}"
    ) == "(1 + √(x))/2"


def test_docx_quality_check_rejects_raw_latex_text(tmp_path) -> None:
    document = Document()
    document.add_paragraph(r"公式残留：\frac{1}{2}")
    export_path = tmp_path / "bad.docx"
    document.save(export_path)

    with pytest.raises(DocxExportQualityError):
        assert_docx_export_quality(export_path)


def test_docx_quality_check_accepts_native_export(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "# 标题\n\n"
            "设 \\(f\\) 在 \\([a,b]\\) 上可积。\n\n"
            "$$\\Phi(x)=\\int_a^x f(t)\\,dt,\\quad \\forall x\\in[a,b]$$"
        ),
    )
    export_path = tmp_path / "good.docx"

    export_docx(document, export_path)
    report = inspect_docx_export(export_path)

    assert report.passed
    assert report.omml_count > 0
    assert report.display_omml_count > 0


def test_docx_export_converts_explicit_inline_tuple_and_logical_negation(tmp_path) -> None:
    document = build_document(
        title="Logic notes",
        content_html=(
            r"<p>对项 \(s,t\)：</p>"
            r'<p>归结消去 <span data-type="inline-math" data-latex="L"></span> '
            r"与 \(\neg L\)，得到新子句。</p>"
        ),
        content_text=(
            "对项 \\(s,t\\)：\n\n"
            "归结消去 \\(L\\) 与 \\(\\neg L\\)，得到新子句。"
        ),
    )
    export_path = tmp_path / "logic.docx"

    export_docx(document, export_path)
    report = inspect_docx_export(export_path)
    math_text = _math_text(_document_root(export_path))

    assert report.passed
    assert report.omml_count >= 3
    assert "s,t" in math_text
    assert "¬ L" in math_text


def test_inline_math_detection_keeps_plain_word_lists_as_text() -> None:
    assert rich_document_core._auto_math_fragments(r"\(hello world\)") == [
        ("text", r"\(hello world\)"),
    ]
    assert rich_document_core._auto_math_fragments(r"\(Alice,Bob\)") == [
        ("text", r"\(Alice,Bob\)"),
    ]


def test_docx_styles_create_textbook_styles() -> None:
    document = Document()

    apply_textbook_docx_styles(document)

    assert document.styles["OpenClass Body Text"]
    assert document.styles["OpenClass Compact"]
    assert document.styles["OpenClass Formula"]
    assert document.styles["Heading 1"].font.bold is True

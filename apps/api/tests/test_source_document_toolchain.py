from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.services import source_document_toolchain


def _write_fake_poppler(root: Path, *, failing_tool: str | None = None) -> Path:
    bin_path = root / "bin"
    bin_path.mkdir(parents=True)
    for tool in source_document_toolchain.REQUIRED_PDF_TOOLS:
        if tool == "pdftoppm":
            body = (
                "#!/bin/sh\n"
                "for last do :; done\n"
                + (
                    "exit 7\n"
                    if failing_tool == tool
                    else "printf 'png' > \"${last}.png\"\n"
                )
            )
        else:
            body = (
                "#!/bin/sh\nexit 7\n"
                if failing_tool == tool
                else f"#!/bin/sh\nprintf '{tool} ready\\n'\n"
            )
        executable = bin_path / tool
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o755)
    return root


def test_resolve_poppler_root_prefers_explicit_absolute_configuration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler")
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))
    monkeypatch.setattr(
        source_document_toolchain.shutil,
        "which",
        lambda _tool: pytest.fail("PATH discovery must not run for explicit configuration"),
    )

    assert source_document_toolchain.resolve_poppler_root() == root.resolve()


def test_resolve_poppler_root_accepts_a_normal_path_installation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "installed-poppler")
    monkeypatch.delenv(source_document_toolchain.POPPLER_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        source_document_toolchain.shutil,
        "which",
        lambda tool: str(root / "bin" / tool) if tool == "pdfinfo" else None,
    )

    assert source_document_toolchain.resolve_poppler_root() == root.resolve()


def test_resolve_poppler_root_rejects_an_incomplete_toolchain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler")
    (root / "bin" / "pdftoppm").unlink()
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))

    with pytest.raises(
        source_document_toolchain.SourceDocumentToolchainError,
        match="pdftoppm",
    ):
        source_document_toolchain.resolve_poppler_root()


def test_resolve_poppler_root_requires_an_absolute_configured_path(
    monkeypatch,
) -> None:
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, "relative/poppler")

    with pytest.raises(
        source_document_toolchain.SourceDocumentToolchainError,
        match="absolute",
    ):
        source_document_toolchain.resolve_poppler_root()


def test_prepare_pdf_toolbox_stages_and_executes_every_required_tool(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler")
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))
    cwd = tmp_path / "workspace"
    scratch = cwd / "scratch"
    cwd.mkdir()
    source = cwd / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    toolbox = source_document_toolchain.prepare_source_document_toolbox(
        cwd=cwd,
        source_path=source,
        scratch_path=scratch,
    )

    assert sorted(path.name for path in (toolbox / "bin").iterdir()) == sorted(
        source_document_toolchain.REQUIRED_PDF_TOOLS
    )
    assert not list(scratch.glob(".poppler-preflight*"))
    isolated_path = source_document_toolchain.source_document_tool_path(toolbox).split(
        os.pathsep
    )
    assert isolated_path[0] == str(toolbox / "bin")
    assert all("installed-poppler" not in entry for entry in isolated_path)


def test_directory_only_pdf_toolbox_rejects_unbounded_or_large_extraction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler")
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))
    cwd = tmp_path / "workspace"
    scratch = cwd / "scratch"
    cwd.mkdir()
    source = cwd / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    toolbox = source_document_toolchain.prepare_source_document_toolbox(
        cwd=cwd,
        source_path=source,
        scratch_path=scratch,
        inspection_scope="directory_only",
    )
    pdftotext = toolbox / "bin" / "pdftotext"

    unbounded = subprocess.run(
        [str(pdftotext), str(source), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    oversized = subprocess.run(
        [str(pdftotext), "-f", "1", "-l", "33", str(source), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    bounded = subprocess.run(
        [str(pdftotext), "-f", "2", "-l", "4", str(source), "-"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert unbounded.returncode == 64
    assert "requires numeric -f and -l" in unbounded.stderr
    assert oversized.returncode == 64
    assert "limited to 32 pages" in oversized.stderr
    assert bounded.returncode == 0


def test_prepare_pdf_toolbox_rejects_a_tool_that_cannot_process_the_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler", failing_tool="pdftotext")
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))
    cwd = tmp_path / "workspace"
    scratch = cwd / "scratch"
    cwd.mkdir()
    source = cwd / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    with pytest.raises(
        source_document_toolchain.SourceDocumentToolchainError,
        match="pdftotext",
    ):
        source_document_toolchain.prepare_source_document_toolbox(
            cwd=cwd,
            source_path=source,
            scratch_path=scratch,
        )


def test_non_pdf_source_keeps_an_empty_isolated_toolbox(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(source_document_toolchain.POPPLER_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        source_document_toolchain.shutil,
        "which",
        lambda _tool: pytest.fail("non-PDF sources must not require Poppler"),
    )
    cwd = tmp_path / "workspace"
    scratch = cwd / "scratch"
    cwd.mkdir()
    source = cwd / "source.epub"
    source.write_bytes(b"fixture")

    toolbox = source_document_toolchain.prepare_source_document_toolbox(
        cwd=cwd,
        source_path=source,
        scratch_path=scratch,
    )

    assert list((toolbox / "bin").iterdir()) == []


def test_broad_system_prefix_does_not_copy_all_system_libraries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _write_fake_poppler(tmp_path / "poppler")
    (root / "lib").mkdir()
    (root / "lib" / "unrelated-library").write_bytes(b"library")
    monkeypatch.setenv(source_document_toolchain.POPPLER_ROOT_ENV, str(root))
    monkeypatch.setattr(
        source_document_toolchain,
        "BROAD_SYSTEM_PREFIXES",
        frozenset({root.resolve()}),
    )
    cwd = tmp_path / "workspace"
    scratch = cwd / "scratch"
    cwd.mkdir()
    source = cwd / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    toolbox = source_document_toolchain.prepare_source_document_toolbox(
        cwd=cwd,
        source_path=source,
        scratch_path=scratch,
    )

    assert not (toolbox / "lib").exists()

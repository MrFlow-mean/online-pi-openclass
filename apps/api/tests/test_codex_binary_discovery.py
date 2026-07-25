from __future__ import annotations

import os
from pathlib import Path

from app.services import codex_app_server


def test_codex_binary_path_prefers_path_discovery(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(
        codex_app_server.shutil,
        "which",
        lambda _name: "/opt/bin/codex",
    )

    assert codex_app_server.codex_binary_path() == "/opt/bin/codex"


def test_codex_binary_path_falls_back_to_chatgpt_bundle(monkeypatch) -> None:
    bundled_binary = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    monkeypatch.delenv("OPENCLASS_CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(codex_app_server.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "is_file", lambda path: path == bundled_binary)
    monkeypatch.setattr(
        os,
        "access",
        lambda path, mode: path == bundled_binary and mode == os.X_OK,
    )

    assert codex_app_server.codex_binary_path() == str(bundled_binary)

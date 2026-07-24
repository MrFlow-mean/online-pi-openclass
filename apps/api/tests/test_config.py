from __future__ import annotations

from pathlib import Path

import pytest

from app.services import config


STABLE_VALUE = "OPENCLASS_TEST_STABLE_CONFIG_VALUE"
ROOT_ONLY_VALUE = "OPENCLASS_TEST_ROOT_ONLY_CONFIG_VALUE"


def _write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_explicit_runtime_env_survives_missing_worktree_env(tmp_path, monkeypatch) -> None:
    runtime_env = tmp_path / "config" / "runtime.env"
    repository_root = tmp_path / "clean-worktree"
    _write_env(runtime_env, f"{STABLE_VALUE}=stable\n")
    repository_root.mkdir()

    monkeypatch.setattr(config, "ROOT_DIR", repository_root)
    monkeypatch.setenv(config.EXPLICIT_ENV_FILE_VARIABLE, str(runtime_env))
    monkeypatch.delenv(STABLE_VALUE, raising=False)

    try:
        config.load_root_dotenv()
        assert config.os.environ[STABLE_VALUE] == "stable"
    finally:
        config.os.environ.pop(STABLE_VALUE, None)


def test_explicit_runtime_env_precedes_worktree_fallback(tmp_path, monkeypatch) -> None:
    runtime_env = tmp_path / "config" / "runtime.env"
    repository_root = tmp_path / "worktree"
    _write_env(runtime_env, f"{STABLE_VALUE}=stable\n")
    _write_env(
        repository_root / ".env",
        f"{STABLE_VALUE}=worktree\n{ROOT_ONLY_VALUE}=root-only\n",
    )

    monkeypatch.setattr(config, "ROOT_DIR", repository_root)
    monkeypatch.setenv(config.EXPLICIT_ENV_FILE_VARIABLE, str(runtime_env))
    monkeypatch.delenv(STABLE_VALUE, raising=False)
    monkeypatch.delenv(ROOT_ONLY_VALUE, raising=False)

    try:
        config.load_root_dotenv()
        assert config.os.environ[STABLE_VALUE] == "stable"
        assert config.os.environ[ROOT_ONLY_VALUE] == "root-only"
    finally:
        config.os.environ.pop(STABLE_VALUE, None)
        config.os.environ.pop(ROOT_ONLY_VALUE, None)


def test_explicit_runtime_env_fails_fast_when_missing(tmp_path, monkeypatch) -> None:
    missing_env = tmp_path / "missing.env"
    monkeypatch.setenv(config.EXPLICIT_ENV_FILE_VARIABLE, str(missing_env))

    with pytest.raises(FileNotFoundError, match="OPENCLASS_ENV_FILE"):
        config.load_root_dotenv()

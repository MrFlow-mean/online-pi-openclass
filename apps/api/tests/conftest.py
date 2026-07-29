from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


_TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="openclass-pytest-"))
os.environ["OPENCLASS_TEST_RUNTIME_ROOT"] = str(_TEST_RUNTIME_ROOT)
os.environ["OPENCLASS_DATABASE_PATH"] = str(_TEST_RUNTIME_ROOT / "openclass.sqlite3")
os.environ["AI_USAGE_LOG_PATH"] = str(_TEST_RUNTIME_ROOT / "logs" / "ai-usage.jsonl")
os.environ["OPENCLASS_UPLOAD_DIR"] = str(_TEST_RUNTIME_ROOT / "uploads")
os.environ["OPENCLASS_EXPORT_DIR"] = str(_TEST_RUNTIME_ROOT / "exports")


@pytest.fixture(scope="session", autouse=True)
def isolated_test_runtime() -> None:
    yield
    shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)

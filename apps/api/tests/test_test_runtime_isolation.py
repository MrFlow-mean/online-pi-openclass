from __future__ import annotations

import os
from pathlib import Path

from app.services import ai_logging, workspace_state


def test_default_test_storage_is_outside_repository_runtime_data() -> None:
    test_root = Path(os.environ["OPENCLASS_TEST_RUNTIME_ROOT"]).resolve()
    repository_data = (Path(__file__).resolve().parents[1] / "data").resolve()
    configured_database = Path(os.environ["OPENCLASS_DATABASE_PATH"]).resolve()
    configured_log = Path(os.environ["AI_USAGE_LOG_PATH"]).resolve()

    assert test_root != repository_data
    assert not test_root.is_relative_to(repository_data)
    assert configured_database.is_relative_to(test_root)
    assert configured_log.is_relative_to(test_root)
    assert workspace_state.STORE.path.resolve() == configured_database
    assert ai_logging.ai_usage_logger.path.resolve() == configured_log

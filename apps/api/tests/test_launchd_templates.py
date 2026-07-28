from __future__ import annotations

import plistlib
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[3]
RUNTIME_PATH_PLACEHOLDER = "__OPENCLASS_RUNTIME_PATH__"


@pytest.mark.parametrize(
    "template_name",
    ("com.openclass.api.plist", "com.openclass.web.plist"),
)
def test_launch_agent_templates_use_discovered_runtime_path(
    template_name: str,
) -> None:
    template_path = ROOT_DIR / "launchd" / template_name

    with template_path.open("rb") as template:
        launch_agent = plistlib.load(template)

    assert (
        launch_agent["EnvironmentVariables"]["PATH"]
        == RUNTIME_PATH_PLACEHOLDER
    )

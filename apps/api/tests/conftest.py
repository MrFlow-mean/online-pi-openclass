"""Test-suite isolation from developer and CI provider configuration.

`app.services.workspace_state` calls `load_root_dotenv()` at import time, so the
repository `.env` is merged into `os.environ` as soon as any service module is
imported.  Without this file the suite therefore inherits whatever providers the
developer happens to have configured: the model catalog resolves to a different
default provider, routing changes underneath the tests, and adapters reach the
live network.  That makes results depend on the machine rather than the code.

`load_dotenv(override=False)` never replaces a key that is already present, so
defining these names here — at import time, before any `app.*` module is loaded —
pins every provider to its unconfigured state for the whole run.
"""

from __future__ import annotations

import os


# Provider credentials and endpoints. A populated value here would let an adapter
# select a different default model or issue a real request during a unit test.
_NEUTRALIZED_ENVIRONMENT = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "GOOGLE_CLOUD_TTS_ACCESS_TOKEN",
    "GOOGLE_CLOUD_TTS_API_KEY",
    "GOOGLE_CLOUD_TTS_ENDPOINT",
    "GOOGLE_CLOUD_TTS_PROJECT_ID",
    "GOOGLE_CLOUD_TTS_VOICES_ENDPOINT",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CODEX_MODEL",
    "OPENAI_REALTIME_BASE_URL",
    "OPENAI_TTS_ENDPOINT",
    "OPEN_NOTEBOOK_API_URL",
    "OPEN_NOTEBOOK_PASSWORD",
    "VOLCENGINE_TTS_API_KEY",
    "VOLCENGINE_TTS_ENDPOINT",
    # Runtime and integration switches that would otherwise reach a local binary,
    # a managed source backend, or a payment sandbox.
    "OPENCLASS_CODEX_APP_SERVER_ENABLED",
    "OPENCLASS_CODEX_CLI_PATH",
    "OPENCLASS_CODEX_HOME",
    "OPENCLASS_CODEX_REALTIME_PROXY_API_KEY",
    "OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE",
    "OPENCLASS_CODEX_REALTIME_PROXY_URL",
    "OPENCLASS_PAYPAL_CLIENT_ID",
    "OPENCLASS_PAYPAL_CLIENT_SECRET",
    "OPENCLASS_PAYPAL_WEBHOOK_ID",
    "OPENCLASS_PI_AGENT_DIR",
    "OPENCLASS_PI_BINARY",
    "OPENCLASS_PI_RUNTIME_ROOT",
    "OPENCLASS_SOURCE_BACKEND",
    "OPENCLASS_SPEECH_PROVIDER",
)

for _name in _NEUTRALIZED_ENVIRONMENT:
    os.environ[_name] = ""

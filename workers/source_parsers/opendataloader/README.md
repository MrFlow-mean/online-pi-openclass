# OpenDataLoader source parser worker

This worker keeps OpenDataLoader and its Java runtime outside the FastAPI environment. It accepts the OpenClass parser sidecar contract and emits `ParsedDocumentV2` JSON. The JSON artifact, not Markdown, is the indexing source of truth.

Requirements:

- Python 3.12
- Java 11 or newer

Install and configure:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x worker.py
export OPENCLASS_OPENDATALOADER_COMMAND="$PWD/worker.py"
export OPENCLASS_OPENDATALOADER_VERSION="2.5.0"
export OPENCLASS_OPENDATALOADER_SHADOW="1"
```

After the shadow evaluation passes, set `OPENCLASS_SOURCE_QA_FAST_PARSER=opendataloader` to make it the fast parser. All conversion is local; the worker does not configure a remote API.

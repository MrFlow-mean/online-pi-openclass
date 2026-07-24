# Docling enhancement worker

This isolated Python 3.12 worker receives only pages selected for complex layout, table, formula, or reading-order repair. Docling runs locally and returns page-remapped `ParsedDocumentV2` JSON.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x worker.py
export OPENCLASS_DOCLING_COMMAND="$PWD/worker.py"
export OPENCLASS_DOCLING_VERSION="2.115.0"
```

Docling execution is guarded by a process-wide concurrency limit of one.

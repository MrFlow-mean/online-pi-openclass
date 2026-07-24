# Local multilingual retrieval models

This Python 3.12 worker keeps the default multilingual embedding and reranking models outside FastAPI. It binds to loopback only and loads each model once, enabling warm-query latency measurement.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 5013
export OPENCLASS_SOURCE_QA_MODEL_URL="http://127.0.0.1:5013"
```

Models:

- `BAAI/bge-m3` for multilingual embeddings
- `BAAI/bge-reranker-v2-m3` for reranking at most 32 RRF candidates

Without the worker URL, OpenClass uses its deterministic local fallback so source ingestion remains available. Production rollout should enable this worker before the Source QA feature flag.

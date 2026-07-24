from __future__ import annotations

import os
import threading

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder, SentenceTransformer


EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
app = FastAPI(title="OpenClass local Source QA models")
_lock = threading.RLock()
_embedding_model: SentenceTransformer | None = None
_reranker_model: CrossEncoder | None = None


class EmbedRequest(BaseModel):
    model: str = EMBEDDING_MODEL
    texts: list[str] = Field(min_length=1, max_length=256)


class RerankRequest(BaseModel):
    model: str = RERANKER_MODEL
    query: str
    documents: list[str] = Field(min_length=1, max_length=32)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "embedding_model_loaded": _embedding_model is not None,
        "reranker_model_loaded": _reranker_model is not None,
    }


@app.post("/embed")
def embed(request: EmbedRequest) -> dict[str, object]:
    if request.model != EMBEDDING_MODEL:
        raise ValueError("Unsupported embedding model.")
    model = _get_embedding_model()
    vectors = model.encode(
        request.texts,
        batch_size=min(32, len(request.texts)),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return {"model": EMBEDDING_MODEL, "embeddings": vectors.tolist()}


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict[str, object]:
    if request.model != RERANKER_MODEL:
        raise ValueError("Unsupported reranker model.")
    model = _get_reranker_model()
    scores = model.predict(
        [(request.query, document) for document in request.documents],
        batch_size=min(32, len(request.documents)),
        show_progress_bar=False,
    )
    return {"model": RERANKER_MODEL, "scores": [float(value) for value in scores]}


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    with _lock:
        if _embedding_model is None:
            _embedding_model = SentenceTransformer(
                EMBEDDING_MODEL,
                device=os.getenv("OPENCLASS_SOURCE_QA_MODEL_DEVICE") or None,
            )
        return _embedding_model


def _get_reranker_model() -> CrossEncoder:
    global _reranker_model
    with _lock:
        if _reranker_model is None:
            _reranker_model = CrossEncoder(
                RERANKER_MODEL,
                device=os.getenv("OPENCLASS_SOURCE_QA_MODEL_DEVICE") or None,
            )
        return _reranker_model

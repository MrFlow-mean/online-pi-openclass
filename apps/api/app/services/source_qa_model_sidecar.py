from __future__ import annotations

import json
import math
import os
import re
from typing import Protocol, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.services.native_source_index import (
    DeterministicHashEmbeddingProvider,
    SourceEmbeddingProvider,
)


class SourceReranker(Protocol):
    provider: str
    model: str

    def rerank(self, *, query: str, documents: Sequence[str]) -> list[float]: ...


class BGEEmbeddingSidecar:
    provider = "openclass_local_sidecar"
    model = "BAAI/bge-m3"
    dimensions = 1024

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        payload = _post_json(
            self.base_url + "/embed",
            {"model": self.model, "texts": list(texts)},
        )
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("BGE embedding sidecar returned an invalid batch.")
        result = [[float(value) for value in vector] for vector in vectors if isinstance(vector, list)]
        if len(result) != len(texts) or any(len(vector) != self.dimensions for vector in result):
            raise RuntimeError("BGE embedding sidecar returned invalid dimensions.")
        return result


class BGERerankerSidecar:
    provider = "openclass_local_sidecar"
    model = "BAAI/bge-reranker-v2-m3"

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def rerank(self, *, query: str, documents: Sequence[str]) -> list[float]:
        payload = _post_json(
            self.base_url + "/rerank",
            {"model": self.model, "query": query, "documents": list(documents[:32])},
        )
        scores = payload.get("scores")
        if not isinstance(scores, list) or len(scores) != min(len(documents), 32):
            raise RuntimeError("BGE reranker sidecar returned an invalid batch.")
        return [float(value) for value in scores]


class LexicalFallbackReranker:
    provider = "openclass_local"
    model = "lexical-overlap-v1"

    def rerank(self, *, query: str, documents: Sequence[str]) -> list[float]:
        query_terms = _features(query)
        scores: list[float] = []
        for document in documents:
            document_terms = _features(document)
            overlap = len(query_terms & document_terms)
            scores.append(overlap / max(1, math.sqrt(len(query_terms) * len(document_terms))))
        return scores


def default_embedding_provider() -> SourceEmbeddingProvider:
    base_url = (os.getenv("OPENCLASS_SOURCE_QA_MODEL_URL") or "").strip()
    if base_url:
        return BGEEmbeddingSidecar(base_url=base_url)
    return DeterministicHashEmbeddingProvider()


def default_reranker() -> SourceReranker:
    base_url = (os.getenv("OPENCLASS_SOURCE_QA_MODEL_URL") or "").strip()
    if base_url:
        return BGERerankerSidecar(base_url=base_url)
    return LexicalFallbackReranker()


def embed_many(provider: SourceEmbeddingProvider, texts: Sequence[str]) -> list[list[float]]:
    batch_method = getattr(provider, "embed_many", None)
    if callable(batch_method):
        return batch_method(texts)
    return [provider.embed(text) for text in texts]


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Local Source QA model worker failed: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Local Source QA model worker returned invalid JSON.")
    return value


def _features(text: str) -> set[str]:
    normalized = text.lower()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    cjk = re.sub(r"[^\u3400-\u9fff]", "", normalized)
    words.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return {item for item in words if item}

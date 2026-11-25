"""Embedding providers.

* FastEmbedEmbedder - local ONNX model (BAAI/bge-small-en-v1.5, 384 dims). Free, no API key.
* HashEmbedder      - deterministic feature-hashing embedder for tests/CI (no model download).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Bag-of-words + bigram feature hashing, L2-normalised. Lexical, but stable and fast."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        for feat in features:
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbedder:
    def __init__(self, model_name: str, dim: int, cache_dir: str | None = None):
        self.model_name = model_name
        self.dim = dim
        self.cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir)
        return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        def work():
            return [v.tolist() for v in self._load().passage_embed(texts)]

        return await asyncio.to_thread(work)

    async def embed_query(self, text: str) -> list[float]:
        def work():
            return next(iter(self._load().query_embed(text))).tolist()

        return await asyncio.to_thread(work)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "hash":
        return HashEmbedder(s.embedding_dim)
    return FastEmbedEmbedder(s.embedding_model, s.embedding_dim, s.embedding_cache_dir)


def to_pgvector(vec: list[float]) -> str:
    """Serialise for a `%s::vector` parameter (avoids a numpy dependency)."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

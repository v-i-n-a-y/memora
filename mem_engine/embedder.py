"""Pluggable text embedder with a dependency-free fallback.

The engine only needs cosine similarity over short texts (dedup, recurrence
counting, recall ranking). Production can use bge-small via sentence-transformers;
tests and constrained environments fall back to deterministic feature hashing,
so the whole engine runs on stdlib + numpy with no model download or network.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Tuple

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic feature-hashing embedder (signed unigrams + bigrams).

    No external models, no network, stable across processes (uses hashlib, not
    the per-process-salted builtin hash()). Good enough for cosine dedup and
    ranking on short texts; not a semantic substitute for a real model.
    """

    name = "hashing"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _hash(self, token: str) -> Tuple[int, float]:
        h = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % self.dim
        sign = 1.0 if (h[4] & 1) else -1.0
        return idx, sign

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        toks = _tokens(text or "")
        if not toks:
            return vec
        feats: List[str] = list(toks)
        feats += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]  # bigrams
        for f in feats:
            idx, sign = self._hash(f)
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def embed_batch(self, texts: Iterable[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self.embed(t) for t in texts])


class SentenceTransformerEmbedder:
    """Optional bge-small embedder; used only if sentence-transformers imports."""

    name = "sentence-transformers"

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer  # lazy, optional

        self._m = SentenceTransformer(model)

    def embed(self, text: str) -> np.ndarray:
        v = self._m.encode([text or ""], normalize_embeddings=True)[0]
        return np.asarray(v, dtype=np.float32)

    def embed_batch(self, texts: Iterable[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            dim = self._m.get_sentence_embedding_dimension()
            return np.zeros((0, dim), dtype=np.float32)
        return np.asarray(
            self._m.encode(texts, normalize_embeddings=True), dtype=np.float32
        )


def get_embedder(name: str = "auto"):
    """Return an embedder. 'auto' prefers bge, falls back to hashing."""
    if name in ("auto", "sentence-transformers"):
        try:
            return SentenceTransformerEmbedder()
        except Exception:
            if name == "sentence-transformers":
                raise
            return HashingEmbedder()
    return HashingEmbedder()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

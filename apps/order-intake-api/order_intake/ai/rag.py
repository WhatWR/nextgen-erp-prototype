"""Knowledge retrieval over merchant-maintained NextGen Knowledge Articles.

Articles live in ERPNext (Desk-editable); this index caches them briefly,
chunks them, and ranks chunks for a query. Ranking prefers embeddings when an
embeddings model is configured, and degrades to a deterministic character
n-gram overlap score otherwise — Thai text has no word boundaries, so n-grams
beat token matching (same reasoning as :mod:`order_intake.matching`).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from ..erpnext_client import ERPNextClient, ERPNextError
from .client import AIError

_CACHE_TTL = 300.0
CHUNK_CHARS = 700
NGRAM_SIZE = 3
ARTICLES_METHOD = "nextgen_erp.ai.get_knowledge_articles"

# Embedder: takes a batch of texts, returns one vector per text.
Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class KnowledgeChunk:
    article: str
    title: str
    text: str


def _chunk_article(title: str, content: str) -> list[str]:
    """Split on blank lines, packing paragraphs up to CHUNK_CHARS per chunk."""
    paragraphs = [p.strip() for p in (content or "").split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > CHUNK_CHARS:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}".strip()
        while len(current) > CHUNK_CHARS:
            chunks.append(current[:CHUNK_CHARS])
            current = current[CHUNK_CHARS:].strip()
    if current:
        chunks.append(current)
    return chunks or ([content.strip()] if (content or "").strip() else [])


def _ngrams(text: str, size: int = NGRAM_SIZE) -> set[str]:
    normalized = "".join((text or "").casefold().split())
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def _overlap_score(query: str, chunk: KnowledgeChunk) -> float:
    query_grams = _ngrams(query)
    if not query_grams:
        return 0.0
    target = _ngrams(f"{chunk.title} {chunk.text}")
    return len(query_grams & target) / len(query_grams)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class KnowledgeIndex:
    def __init__(self, client: ERPNextClient | None = None, *, cache_ttl: float = _CACHE_TTL):
        self.client = client or ERPNextClient()
        self._ttl = cache_ttl
        self._chunks: list[KnowledgeChunk] = []
        self._fetched_at = 0.0
        self._loaded = False
        # chunk vectors keyed by embeddings-model name, aligned with _chunks
        self._vectors: dict[str, list[list[float]]] = {}

    def _refresh_if_stale(self) -> None:
        now = time.monotonic()
        if self._loaded and (now - self._fetched_at) <= self._ttl:
            return
        try:
            message = self.client.call_method(ARTICLES_METHOD)
        except ERPNextError:
            # Keep serving the previous snapshot; knowledge search is best-effort.
            self._fetched_at = now
            return
        articles = message.get("articles") if isinstance(message, dict) else []
        chunks: list[KnowledgeChunk] = []
        for article in articles or []:
            for text in _chunk_article(str(article.get("title") or ""), str(article.get("content") or "")):
                chunks.append(
                    KnowledgeChunk(
                        article=str(article.get("name") or ""),
                        title=str(article.get("title") or ""),
                        text=text,
                    )
                )
        self._chunks = chunks
        self._vectors = {}
        self._fetched_at = now
        self._loaded = True

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        embed: Embedder | None = None,
        embed_key: str = "",
    ) -> list[dict]:
        """Rank chunks for ``query``; embeddings when available, n-grams otherwise."""
        self._refresh_if_stale()
        if not query.strip() or not self._chunks:
            return []
        scored: list[tuple[float, KnowledgeChunk]] | None = None
        if embed is not None and embed_key:
            try:
                if embed_key not in self._vectors:
                    self._vectors[embed_key] = embed(
                        [f"{c.title}\n{c.text}" for c in self._chunks]
                    )
                query_vector = embed([query])[0]
                vectors = self._vectors[embed_key]
                scored = [
                    (_cosine(query_vector, vector), chunk)
                    for vector, chunk in zip(vectors, self._chunks)
                ]
            except (AIError, IndexError):
                scored = None  # degrade to the deterministic path below
        if scored is None:
            scored = [(_overlap_score(query, chunk), chunk) for chunk in self._chunks]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"title": chunk.title, "text": chunk.text, "score": round(score, 4)}
            for score, chunk in scored[:top_k]
            if score > 0
        ]

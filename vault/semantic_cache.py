"""Semantic cache — instant responses for similar past queries."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Callable

logger = logging.getLogger(__name__)

_cache_instance: SemanticCache | None = None
_cache_embed_fn = None


def _get_cache(settings, embed_fn=None) -> SemanticCache | None:
    """Get or create the singleton cache instance.

    embed_fn can be provided to enable semantic similarity matching.
    Once set, it's reused for the lifetime of the cache.
    """
    global _cache_instance, _cache_embed_fn
    if not getattr(settings, 'cache_enabled', True):
        return None
    if embed_fn is not None:
        _cache_embed_fn = embed_fn
    if _cache_instance is None:
        _cache_instance = SemanticCache(
            similarity_threshold=getattr(settings, 'cache_similarity_threshold', 0.85),
            max_entries=getattr(settings, 'cache_max_entries', 1000),
            ttl_hours=getattr(settings, 'cache_ttl_hours', 24),
            embed_fn=_cache_embed_fn,
        )
    elif embed_fn is not None and _cache_instance.embed_fn is None:
        # Update embed_fn on existing singleton (e.g., model loaded after first request)
        _cache_instance.embed_fn = embed_fn
    return _cache_instance


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (no numpy dependency)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """In-memory cache keyed by semantic similarity of queries.

    Falls back to exact string match when no embed_fn is provided.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        max_entries: int = 1000,
        ttl_hours: int = 24,
        embed_fn: Callable[[str], list[float] | None] | None = None,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.ttl_hours = ttl_hours
        self.embed_fn = embed_fn
        # Per-model caches: {model_name: OrderedDict[(query_hash, query) -> entry]}
        self._caches: dict[str, OrderedDict] = {}
        self._hits = 0
        self._misses = 0

    def get(self, query: str, model_name: str) -> dict | None:
        """Look up cached response. Returns {response, cached: True} or None."""
        cache = self._caches.setdefault(model_name, OrderedDict())
        now = time.time()

        # Evict expired entries
        expired = [k for k, v in cache.items() if (now - v["timestamp"]) > self.ttl_hours * 3600]
        for k in expired:
            cache.pop(k, None)

        if self.embed_fn is not None:
            query_emb = self.embed_fn(query)
            if query_emb:
                for key, entry in cache.items():
                    entry_emb = entry.get("embedding")
                    if entry_emb and _cosine_sim(query_emb, entry_emb) >= self.similarity_threshold:
                        cache.move_to_end(key)  # LRU bump
                        self._hits += 1
                        entry["hits"] = entry.get("hits", 0) + 1
                        return {"response": entry["response"], "cached": True, "similarity": True}

        # Exact match fallback
        q_hash = hash(query)
        key = (q_hash, query)
        if key in cache:
            self._hits += 1
            cache.move_to_end(key)
            return {"response": cache[key]["response"], "cached": True, "similarity": False}

        self._misses += 1
        return None

    def put(self, query: str, model_name: str, response: str,
            embedding: list[float] | None = None) -> None:
        """Store a query-response pair."""
        cache = self._caches.setdefault(model_name, OrderedDict())
        q_hash = hash(query)

        # Evict oldest if over capacity
        while len(cache) >= self.max_entries:
            cache.popitem(last=False)

        # Compute embedding if function available and not provided
        if embedding is None and self.embed_fn is not None:
            embedding = self.embed_fn(query)

        key = (q_hash, query)
        cache[key] = {
            "response": response,
            "embedding": embedding,
            "timestamp": time.time(),
            "hits": 0,
        }

    def invalidate(self, query: str, model_name: str) -> int:
        """Remove cache entries matching this query. Returns count removed."""
        cache = self._caches.get(model_name, {})
        removed = 0
        q_hash = hash(query)
        # Remove exact match
        key = (q_hash, query)
        if key in cache:
            del cache[key]
            removed += 1
        # If we have embeddings, also remove similar entries
        if self.embed_fn is not None:
            query_emb = self.embed_fn(query)
            if query_emb:
                to_remove = []
                for k, v in cache.items():
                    emb = v.get("embedding")
                    if emb and _cosine_sim(query_emb, emb) >= self.similarity_threshold:
                        to_remove.append(k)
                for k in to_remove:
                    del cache[k]
                    removed += 1
        return removed

    def clear(self, model_name: str | None = None) -> None:
        """Clear cache for a specific model or all models."""
        if model_name:
            self._caches.pop(model_name, None)
        else:
            self._caches.clear()
        self._hits = 0
        self._misses = 0

    def invalidate_all(self) -> None:
        """Alias for clear()."""
        self.clear()

    def stats(self) -> dict:
        """Cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            "total_entries": sum(len(c) for c in self._caches.values()),
            "models_cached": list(self._caches.keys()),
            "ttl_hours": self.ttl_hours,
            "max_entries": self.max_entries,
            "similarity_threshold": self.similarity_threshold,
            "embed_fn_available": self.embed_fn is not None,
        }

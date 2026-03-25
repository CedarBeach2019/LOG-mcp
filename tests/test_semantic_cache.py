"""Tests for semantic cache."""

import os
import time

os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test-key")

from vault.semantic_cache import SemanticCache, _cosine_sim


class TestCosineSimilarity:
    def test_identical(self):
        assert _cosine_sim([1, 0, 0], [1, 0, 0]) == 1.0

    def test_orthogonal(self):
        assert _cosine_sim([1, 0], [0, 1]) == 0.0

    def test_opposite(self):
        assert _cosine_sim([1, 0], [-1, 0]) == -1.0

    def test_zero_vector(self):
        assert _cosine_sim([0, 0], [1, 0]) == 0.0

    def test_similar(self):
        sim = _cosine_sim([1, 1, 0], [1, 0.9, 0])
        assert 0.95 < sim < 1.0


class TestExactMatchCache:
    def test_cache_miss_empty(self):
        cache = SemanticCache()
        assert cache.get("hello", "test-model") is None

    def test_cache_hit(self):
        cache = SemanticCache()
        cache.put("hello", "test-model", "Hi there!")
        result = cache.get("hello", "test-model")
        assert result is not None
        assert result["response"] == "Hi there!"
        assert result["cached"] is True

    def test_cache_miss_different_query(self):
        cache = SemanticCache()
        cache.put("hello", "test-model", "Hi there!")
        assert cache.get("goodbye", "test-model") is None

    def test_cache_miss_different_model(self):
        cache = SemanticCache()
        cache.put("hello", "model-a", "Hi!")
        assert cache.get("hello", "model-b") is None


class TestSemanticCache:
    def _embed_fn(self, text):
        """Simple mock: embeds "cat" as [1,0], "dog" as [0,1], etc."""
        if "cat" in text:
            return [1.0, 0.0]
        elif "dog" in text:
            return [0.0, 1.0]
        elif "feline" in text:
            return [0.9, 0.1]
        return [0.5, 0.5]

    def test_semantic_hit(self):
        cache = SemanticCache(embed_fn=self._embed_fn, similarity_threshold=0.8)
        cache.put("Tell me about cats", "model", "Cats are furry")
        result = cache.get("What are felines?", "model")
        assert result is not None
        assert result["cached"] is True
        assert result["similarity"] is True

    def test_semantic_miss_below_threshold(self):
        cache = SemanticCache(embed_fn=self._embed_fn, similarity_threshold=0.999)
        cache.put("Tell me about cats", "model", "Cats are furry")
        # "feline" embeds to [0.9, 0.1], similarity with [1, 0] ≈ 0.994 < 0.999
        assert cache.get("What are felines?", "model") is None

    def test_semantic_miss_different_topic(self):
        cache = SemanticCache(embed_fn=self._embed_fn, similarity_threshold=0.8)
        cache.put("Tell me about cats", "model", "Cats are furry")
        assert cache.get("Tell me about dogs", "model") is None


class TestLRUAndExpiry:
    def test_lru_eviction(self):
        cache = SemanticCache(max_entries=2)
        cache.put("a", "m", "A")
        cache.put("b", "m", "B")
        cache.put("c", "m", "C")  # should evict "a"
        assert cache.get("a", "m") is None
        assert cache.get("b", "m") is not None
        assert cache.get("c", "m") is not None

    def test_ttl_expiry(self):
        cache = SemanticCache(ttl_hours=0)  # instant expiry
        cache.put("test", "m", "old")
        time.sleep(0.01)
        assert cache.get("test", "m") is None


class TestInvalidation:
    def test_invalidate_exact(self):
        cache = SemanticCache()
        cache.put("hello", "m", "Hi!")
        assert cache.get("hello", "m") is not None
        removed = cache.invalidate("hello", "m")
        assert removed >= 1
        assert cache.get("hello", "m") is None

    def test_invalidate_semantic(self):
        def embed(text):
            return [1.0, 0.0]
        cache = SemanticCache(embed_fn=embed, similarity_threshold=0.8)
        cache.put("hello", "m", "Hi!")
        cache.put("hi there", "m", "Hey!")
        removed = cache.invalidate("hello", "m")
        assert removed >= 2  # both are similar

    def test_clear_all(self):
        cache = SemanticCache()
        cache.put("a", "m1", "A")
        cache.put("b", "m2", "B")
        cache.clear()
        assert cache.stats()["total_entries"] == 0

    def test_clear_single_model(self):
        cache = SemanticCache()
        cache.put("a", "m1", "A")
        cache.put("b", "m2", "B")
        cache.clear("m1")
        stats = cache.stats()
        assert stats["total_entries"] == 1


class TestStats:
    def test_initial_stats(self):
        cache = SemanticCache()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0
        assert stats["total_entries"] == 0

    def test_hit_rate(self):
        cache = SemanticCache()
        cache.put("q", "m", "a")
        cache.get("q", "m")  # hit
        cache.get("other", "m")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

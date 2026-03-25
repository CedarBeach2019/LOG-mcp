"""Tests for vault/model_discovery.py"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from vault.model_discovery import ModelDiscovery, ModelInfo, CAPABILITY_TAGS


# --- Fixtures ---

@pytest.fixture
def sample_openrouter_data():
    """Simulate OpenRouter API response."""
    return {
        "data": [
            {
                "id": "anthropic/claude-3-haiku",
                "name": "Claude 3 Haiku",
                "context_length": 200000,
                "pricing": {"prompt": "0.00000025", "completion": "0.00000125"},
                "description": "Fast and efficient assistant",
                "top_provider": {"name": "Anthropic"},
            },
            {
                "id": "openai/gpt-4o-mini",
                "name": "GPT-4o Mini",
                "context_length": 128000,
                "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                "description": "Affordable and capable chat model",
                "top_provider": {"name": "OpenAI"},
            },
            {
                "id": "meta-llama/llama-3.1-8b-instruct:free",
                "name": "Llama 3.1 8B Instruct",
                "context_length": 128000,
                "pricing": {"prompt": "0", "completion": "0"},
                "description": "Free open source model for coding and chat",
                "top_provider": {"name": "Together"},
            },
            {
                "id": "google/gemini-pro-vision",
                "name": "Gemini Pro Vision",
                "context_length": 32000,
                "pricing": {"prompt": "0.00000125", "completion": "0.000005"},
                "description": "Multimodal vision model",
                "top_provider": {"name": "Google"},
            },
        ]
    }


@pytest.fixture
def discovery(tmp_path):
    return ModelDiscovery(cache_dir=tmp_path)


@pytest.fixture
def loaded_discovery(discovery, sample_openrouter_data):
    """Discovery with models pre-loaded from a file."""
    data_file = tmp_path_factory.mktemp("discovery") / "models.json" if False else None
    # Load directly via internal method
    discovery._models = [
        discovery._parse_model(m) for m in sample_openrouter_data["data"]
    ]
    return discovery


# We need tmp_path in the fixture above, so let's restructure:

@pytest.fixture
def populated_discovery(tmp_path, sample_openrouter_data):
    """Discovery with models loaded from sample data."""
    d = ModelDiscovery(cache_dir=tmp_path)
    d._models = [d._parse_model(m) for m in sample_openrouter_data["data"]]
    return d


# --- ModelInfo Tests ---

class TestModelInfo:
    def test_is_free_true(self):
        m = ModelInfo(id="free/model", name="Free", prompt_price_per_mtok=0, completion_price_per_mtok=0)
        assert m.is_free

    def test_is_free_false(self):
        m = ModelInfo(id="paid/model", name="Paid", prompt_price_per_mtok=0.5, completion_price_per_mtok=2.0)
        assert not m.is_free

    def test_matches_capability_chat(self):
        m = ModelInfo(id="a/b", name="Chat Assistant Model", capabilities=["chat"])
        assert m.matches_capability("chat")
        assert m.matches_capability("any")
        assert not m.matches_capability("vision")

    def test_matches_capability_code(self):
        m = ModelInfo(id="a/b", name="Code Programmer Model", capabilities=["code"])
        assert m.matches_capability("code")

    def test_to_dict(self):
        m = ModelInfo(id="test/model", name="Test", context_length=4096,
                      prompt_price_per_mtok=1.0, completion_price_per_mtok=2.0)
        d = m.to_dict()
        assert d["id"] == "test/model"
        assert d["context_length"] == 4096
        assert d["is_free"] is False


# --- ModelDiscovery Tests ---

class TestModelDiscovery:
    def test_parse_model_pricing(self, sample_openrouter_data):
        d = ModelDiscovery()
        m = d._parse_model(sample_openrouter_data["data"][0])
        assert m.id == "anthropic/claude-3-haiku"
        assert m.prompt_price_per_mtok == 0.25  # 0.00000025 * 1M
        assert m.completion_price_per_mtok == 1.25
        assert m.context_length == 200000

    def test_parse_model_free(self, sample_openrouter_data):
        d = ModelDiscovery()
        m = d._parse_model(sample_openrouter_data["data"][2])
        assert m.is_free

    def test_search_all(self, populated_discovery):
        results = populated_discovery.search()
        assert len(results) == 4

    def test_search_by_query(self, populated_discovery):
        results = populated_discovery.search(query="claude")
        assert len(results) == 1
        assert "claude" in results[0].id

    def test_search_by_capability(self, populated_discovery):
        results = populated_discovery.search(capability="vision")
        assert len(results) >= 1
        assert any("gemini" in r.id.lower() or "vision" in r.name.lower() for r in results)

    def test_search_by_max_price(self, populated_discovery):
        results = populated_discovery.search(max_prompt_price=0.3)
        # Claude haiku is 0.25, gpt-4o-mini is 0.15, llama is free
        assert len(results) >= 2

    def test_search_by_context(self, populated_discovery):
        results = populated_discovery.search(min_context=150000)
        assert len(results) == 1  # Only Claude has 200k

    def test_search_limit(self, populated_discovery):
        results = populated_discovery.search(limit=2)
        assert len(results) == 2

    def test_get_model_found(self, populated_discovery):
        m = populated_discovery.get_model("openai/gpt-4o-mini")
        assert m is not None
        assert m.name == "GPT-4o Mini"

    def test_get_model_not_found(self, populated_discovery):
        m = populated_discovery.get_model("nonexistent/model")
        assert m is None

    def test_list_models(self, populated_discovery):
        models = populated_discovery.list_models()
        assert len(models) == 4

    def test_load_from_file(self, tmp_path, sample_openrouter_data):
        data_file = tmp_path / "models.json"
        data_file.write_text(json.dumps(sample_openrouter_data))
        d = ModelDiscovery(cache_dir=tmp_path)
        d.load_from_file(data_file)
        assert len(d.list_models()) == 4

    def test_capability_tags_coverage(self):
        """Ensure all expected capability keys exist."""
        for cap in ["chat", "code", "vision", "reasoning"]:
            assert cap in CAPABILITY_TAGS

    def test_empty_search_returns_empty(self, populated_discovery):
        results = populated_discovery.search(query="nonexistent_model_xyz_123")
        assert results == []

    def test_search_empty_models(self, tmp_path):
        d = ModelDiscovery(cache_dir=tmp_path)
        assert d.search() == []
        assert d.get_model("anything") is None

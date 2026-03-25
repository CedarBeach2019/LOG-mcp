"""Tests for vault/dataset_manager.py"""

import json
import pytest
import tempfile
from pathlib import Path

from vault.dataset_manager import (
    DatasetVersion,
    deduplicate_db,
    deduplicate_interactions,
    diversity_sample,
    generate_splits,
)


def _make_interaction(id, user_input="test question", response="A" * 100,
                      critique=None, feedback="up", created_at="2024-01-01T00:00:00"):
    return {
        "id": id,
        "user_input": user_input,
        "response": response,
        "critique": critique,
        "feedback": feedback,
        "target_model": "test",
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplicateInteractions:
    def test_removes_exact_duplicates(self):
        interactions = [
            _make_interaction(1, "What is Python?"),
            _make_interaction(2, "What is Python?"),
            _make_interaction(3, "What is Java?"),
        ]
        result = deduplicate_interactions(interactions)
        assert len(result) == 2

    def test_case_insensitive(self):
        interactions = [
            _make_interaction(1, "What is Python?"),
            _make_interaction(2, "WHAT IS PYTHON?"),
        ]
        result = deduplicate_interactions(interactions)
        assert len(result) == 1

    def test_whitespace_insensitive(self):
        interactions = [
            _make_interaction(1, "What is Python?"),
            _make_interaction(2, "  What  is  Python?  "),
        ]
        result = deduplicate_interactions(interactions)
        assert len(result) == 1

    def test_best_quality_strategy(self):
        interactions = [
            _make_interaction(1, "What is Python?", critique="Great!", response="A" * 300),
            _make_interaction(2, "What is Python?", critique=None, response="ok"),
        ]
        result = deduplicate_interactions(interactions, keep_strategy="best_quality")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_most_recent_strategy(self):
        interactions = [
            _make_interaction(1, "What is Python?", created_at="2024-01-01T00:00:00"),
            _make_interaction(2, "What is Python?", created_at="2024-01-02T00:00:00"),
        ]
        result = deduplicate_interactions(interactions, keep_strategy="most_recent")
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_empty_input(self):
        assert deduplicate_interactions([]) == []

    def test_no_duplicates(self):
        interactions = [
            _make_interaction(1, "Q1"),
            _make_interaction(2, "Q2"),
            _make_interaction(3, "Q3"),
        ]
        result = deduplicate_interactions(interactions)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Diversity sampling
# ---------------------------------------------------------------------------

class TestDiversitySample:
    def test_max_per_domain(self):
        interactions = [
            _make_interaction(1, "write a python function"),
            _make_interaction(2, "write a java function"),
            _make_interaction(3, "write a rust function"),
            _make_interaction(4, "calculate 2 + 3"),
            _make_interaction(5, "solve x^2 = 4"),
        ]
        result = diversity_sample(interactions, max_per_domain=1)
        assert len(result) <= 3  # code(1) + math(1) + general(0 or 1)

    def test_target_total(self):
        interactions = [
            _make_interaction(i, f"question {i}") for i in range(20)
        ]
        result = diversity_sample(interactions, target_total=5)
        assert len(result) <= 5

    def test_empty_input(self):
        assert diversity_sample([]) == []

    def test_preserves_all_domains(self):
        interactions = [
            _make_interaction(1, "write a python function"),       # code
            _make_interaction(2, "calculate the sum"),              # math
            _make_interaction(3, "write a poem"),                   # creative
            _make_interaction(4, "proofread my essay"),             # writing
            _make_interaction(5, "what is the capital of France?"), # factual
            _make_interaction(6, "hello there"),                    # general
        ]
        result = diversity_sample(interactions)
        assert len(result) == 6


# ---------------------------------------------------------------------------
# Split generation
# ---------------------------------------------------------------------------

class TestGenerateSplits:
    def test_splits_have_correct_ratios(self):
        interactions = [
            _make_interaction(i, f"question {i}") for i in range(100)
        ]
        splits = generate_splits(interactions, val_ratio=0.15, test_ratio=0.10, seed=42)
        total = len(splits["train"]) + len(splits["val"]) + len(splits["test"])
        assert total == 100
        assert len(splits["train"]) >= 70  # ~75%
        assert len(splits["val"]) >= 5     # ~15%
        assert len(splits["test"]) >= 5    # ~10%

    def test_empty_input(self):
        splits = generate_splits([])
        assert splits["train"] == []
        assert splits["val"] == []
        assert splits["test"] == []

    def test_deterministic(self):
        interactions = [_make_interaction(i, f"q{i}") for i in range(50)]
        s1 = generate_splits(interactions, seed=42)
        s2 = generate_splits(interactions, seed=42)
        assert [x["id"] for x in s1["train"]] == [x["id"] for x in s2["train"]]

    def test_small_dataset(self):
        interactions = [_make_interaction(i, f"q{i}") for i in range(5)]
        splits = generate_splits(interactions, seed=42)
        total = len(splits["train"]) + len(splits["val"]) + len(splits["test"])
        assert total == 5


# ---------------------------------------------------------------------------
# DatasetVersion
# ---------------------------------------------------------------------------

class TestDatasetVersion:
    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dv = DatasetVersion(tmpdir)
            interactions = [_make_interaction(i, f"q{i}") for i in range(10)]

            v1 = dv.create_version(interactions, {"source": "test"})
            assert v1 == "v1"

            v2 = dv.create_version(interactions, {"source": "test2"})
            assert v2 == "v2"

            versions = dv.list_versions()
            assert len(versions) == 2
            assert versions[0]["version"] == "v1"
            assert versions[0]["total_examples"] == 10

    def test_load_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dv = DatasetVersion(tmpdir)
            interactions = [_make_interaction(i, f"q{i}") for i in range(5)]
            dv.create_version(interactions)

            loaded = dv.load_version("v1")
            assert len(loaded) == 5

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dv = DatasetVersion(tmpdir)
            with pytest.raises(FileNotFoundError):
                dv.load_version("v99")

    def test_metadata_includes_quality_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dv = DatasetVersion(tmpdir)
            interactions = [
                _make_interaction(1, "write a python function",
                                 critique="Excellent!", response="A" * 300),
                _make_interaction(2, "ok", response="ok"),
            ]
            dv.create_version(interactions)
            versions = dv.list_versions()
            assert "avg_quality" in versions[0]
            assert versions[0]["low_effort_count"] == 1
            assert "domains" in versions[0]

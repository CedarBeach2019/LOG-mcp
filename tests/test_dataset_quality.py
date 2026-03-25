"""Tests for vault/dataset_quality.py"""

import pytest
from vault.dataset_quality import (
    QualityScore,
    canonicalize_prompt,
    classify_domain,
    filter_by_quality,
    score_ranking,
    score_rankings,
    _canonicalize_prompt,
    _score_reasoning,
    _score_response_length,
    _extract_reasoning_from_critique,
)


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

class TestClassifyDomain:
    def test_code(self):
        assert classify_domain("write a python function to sort a list") == "code"

    def test_code_with_backticks(self):
        assert classify_domain("what does ```python print('hi')``` do?") == "code"

    def test_math(self):
        assert classify_domain("calculate the sum of 2 + 3") == "math"

    def test_creative(self):
        assert classify_domain("write a poem about the ocean") == "creative"

    def test_writing(self):
        assert classify_domain("proofread my essay for grammar errors") == "writing"

    def test_factual(self):
        assert classify_domain("what is the capital of France?") == "factual"

    def test_general_fallback(self):
        assert classify_domain("hello there") == "general"

    def test_multi_domain_picks_strongest(self):
        # Has both code and math signals, code has more patterns
        text = "write a python function to calculate the sum of a list and prove the formula"
        result = classify_domain(text)
        assert result in ("code", "math")


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

def _make_interaction(**kwargs):
    defaults = {
        "id": 1,
        "user_input": "write a python function to sort",
        "response": "Here's a function that sorts a list using merge sort:\n```python\ndef merge_sort(arr):\n    ...",
        "critique": None,
        "feedback": "up",
        "target_model": "test-model",
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(kwargs)
    return defaults


class TestScoreRanking:
    def test_high_quality_with_reasoning(self):
        interaction = _make_interaction(
            critique="I chose this because it was well-structured and included error handling",
            feedback="up",
            response="A" * 300,
        )
        score = score_ranking(interaction)
        assert score.composite > 0.5
        assert score.reasoning_provided > 0
        assert score.domain == "code"

    def test_no_reasoning(self):
        interaction = _make_interaction(critique=None, feedback="up")
        score = score_ranking(interaction)
        assert score.reasoning_provided == 0.0
        assert "no_reasoning" in score.flags

    def test_short_response_flagged(self):
        interaction = _make_interaction(response="ok")
        score = score_ranking(interaction)
        assert score.response_length < 0.1
        assert "short_response" in score.flags

    def test_long_response(self):
        interaction = _make_interaction(response="A" * 600)
        score = score_ranking(interaction)
        assert score.response_length == 0.2

    def test_negative_feedback(self):
        interaction = _make_interaction(feedback="down")
        score = score_ranking(interaction)
        assert score.feedback_consistency == 0.05

    def test_no_feedback(self):
        interaction = _make_interaction(feedback=None)
        score = score_ranking(interaction)
        assert score.feedback_consistency == 0.0

    def test_json_critique_with_reasoning(self):
        interaction = _make_interaction(
            critique='{"user_reasoning": "This response was more detailed and accurate"}'
        )
        score = score_ranking(interaction)
        assert score.reasoning_provided == 0.3

    def test_json_critique_without_reasoning(self):
        interaction = _make_interaction(
            critique='{"winner_profile": "detailed"}'
        )
        score = score_ranking(interaction)
        # JSON parses but has no user_reasoning, falls to plain text scoring
        # The JSON string is 30 chars → 0.2
        assert score.reasoning_provided >= 0.1

    def test_is_low_effort(self):
        interaction = _make_interaction(critique=None, response="ok", feedback=None)
        score = score_ranking(interaction)
        assert score.is_low_effort

    def test_not_low_effort(self):
        interaction = _make_interaction(
            critique="This was great because...",
            response="A" * 300,
            feedback="up",
        )
        score = score_ranking(interaction)
        assert not score.is_low_effort

    def test_to_dict(self):
        interaction = _make_interaction()
        score = score_ranking(interaction)
        d = score.to_dict()
        assert "composite" in d
        assert "dimensions" in d
        assert "domain" in d
        assert "is_low_effort" in d
        assert "flags" in d
        assert d["interaction_id"] == 1


class TestScoreRankingsBatch:
    def test_uniqueness_across_batch(self):
        interactions = [
            _make_interaction(id=1, user_input="What is Python?"),
            _make_interaction(id=2, user_input="What is Python?"),  # duplicate
            _make_interaction(id=3, user_input="How to sort in Java?"),
        ]
        scores = score_rankings(interactions)
        # The unique one should have higher uniqueness than duplicates
        assert scores[0].uniqueness < scores[2].uniqueness
        assert scores[1].uniqueness < scores[2].uniqueness

    def test_empty_batch(self):
        assert score_rankings([]) == []


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

class TestCanonicalize:
    def test_basic(self):
        assert canonicalize_prompt("Hello World") == "hello world"

    def test_whitespace(self):
        assert canonicalize_prompt("  Hello   World  ") == "hello world"

    def test_pii_tokens(self):
        assert canonicalize_prompt("Email PERSON_A at EMAIL_B") == "email [pii] at [pii]"

    def test_empty(self):
        assert canonicalize_prompt("") == ""
        assert canonicalize_prompt(None) == ""


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------

class TestFilterByQuality:
    def test_filters_low_quality(self):
        interactions = [
            _make_interaction(id=1, critique="Great response!", response="A" * 300, feedback="up"),
            _make_interaction(id=2, critique=None, response="ok", feedback=None),
        ]
        filtered = filter_by_quality(interactions, min_composite=0.2)
        assert len(filtered) == 1
        assert filtered[0]["id"] == 1

    def test_exclude_low_effort(self):
        interaction = _make_interaction(id=1, critique=None, response="ok")
        filtered = filter_by_quality([interaction], exclude_low_effort=True)
        assert len(filtered) == 0

    def test_keep_low_effort_when_disabled(self):
        interaction = _make_interaction(id=1, critique=None, response="ok")
        filtered = filter_by_quality([interaction], exclude_low_effort=False, min_composite=0.0)
        assert len(filtered) == 1

    def test_empty_input(self):
        assert filter_by_quality([]) == []


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

class TestScoreReasoning:
    def test_long_text(self):
        assert _score_reasoning("This response was excellent and well-structured with great detail") == 0.3

    def test_medium_text(self):
        assert _score_reasoning("This was a good response overall") == 0.2

    def test_short_text(self):
        assert _score_reasoning("ok") == 0.0

    def test_empty(self):
        assert _score_reasoning(None) == 0.0
        assert _score_reasoning("") == 0.0

    def test_json_with_reasoning(self):
        text = '{"user_reasoning": "This was more detailed and accurate than the others"}'
        assert _score_reasoning(text) == 0.3


class TestScoreResponseLength:
    def test_very_long(self):
        assert _score_response_length("A" * 600) == 0.2

    def test_medium(self):
        assert _score_response_length("A" * 200) == 0.15

    def test_short(self):
        assert _score_response_length("A" * 60) == 0.05

    def test_tiny(self):
        assert _score_response_length("hi") == 0.0

    def test_none(self):
        assert _score_response_length(None) == 0.0


class TestExtractReasoning:
    def test_json(self):
        assert "detailed" in _extract_reasoning_from_critique(
            '{"user_reasoning": "It was more detailed"}')
    
    def test_plain_text(self):
        assert _extract_reasoning_from_critique("This was great") == "This was great"

    def test_none(self):
        assert _extract_reasoning_from_critique(None) == ""

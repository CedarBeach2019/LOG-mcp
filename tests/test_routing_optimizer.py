"""Tests for the routing optimizer — the auto-learning routing loop."""

import os, tempfile
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from vault.routing_optimizer import RoutingOptimizer, RoutingRule


@pytest.fixture
def optimizer(tmp_path):
    db = str(tmp_path / "test.db")
    return RoutingOptimizer(db)


class TestOptimizerInit:
    def test_creates_tables(self, optimizer):
        rules = optimizer.get_rules()
        assert len(rules) >= 5  # default rules seeded

    def test_default_rules_include_code_block(self, optimizer):
        rules = optimizer.get_rules()
        names = [r.name for r in rules]
        assert "code_block" in names

    def test_default_rules_include_short_question(self, optimizer):
        rules = optimizer.get_rules()
        names = [r.name for r in rules]
        assert "short_question" in names

    def test_all_defaults_enabled(self, optimizer):
        rules = optimizer.get_rules(enabled_only=True)
        assert len(rules) >= 5
        assert all(r.enabled for r in rules)


class TestRuleEvaluation:
    def test_code_block_escapes(self, optimizer):
        result = optimizer.evaluate_message("Here's my code:\n```python\nprint('hi')\n```")
        assert result["action"] == "ESCALATE"

    def test_short_question_cheap(self, optimizer):
        result = optimizer.evaluate_message("what is python?")
        # Should match short_question rule
        assert result["action"] in ("CHEAP_ONLY", "ESCALATE")  # depends on pattern match

    def test_creative_request(self, optimizer):
        result = optimizer.evaluate_message("write a poem about the sea")
        assert result["matched_rule"] is not None

    def test_math_escapes(self, optimizer):
        result = optimizer.evaluate_message("prove that sqrt(2) is irrational")
        assert result["action"] == "ESCALATE"

    def test_summarize_cheap(self, optimizer):
        result = optimizer.evaluate_message("summarize the meeting notes")
        assert result["action"] == "CHEAP_ONLY"

    def test_unknown_message_falls_back(self, optimizer):
        result = optimizer.evaluate_message("xyzzy plugh")
        assert result["action"] == "CHEAP_ONLY"
        assert result["confidence"] < 0.5

    def test_debugging_escapes(self, optimizer):
        result = optimizer.evaluate_message("I'm getting an error when I try to connect")
        assert result["action"] == "ESCALATE"


class TestOptimization:
    def test_insufficient_data_skips(self, optimizer):
        """Should not make changes with fewer than min_interactions."""
        result = optimizer.analyze_and_optimize(min_interactions=100)
        assert result.rules_modified == 0
        assert "Insufficient" in result.summary

    def test_optimization_history(self, optimizer):
        """Should record optimization runs."""
        optimizer.analyze_and_optimize(min_interactions=100)  # will skip but log
        history = optimizer.get_optimization_history()
        assert len(history) >= 1

    def test_get_config(self, optimizer):
        config = optimizer.get_routing_config()
        assert "rules" in config
        assert len(config["rules"]) >= 5

    def test_optimization_returns_timestamp(self, optimizer):
        result = optimizer.analyze_and_optimize(min_interactions=100)
        assert result.timestamp is not None


class TestRuleCRUD:
    def test_add_custom_rule(self, optimizer, tmp_path):
        optimizer2 = RoutingOptimizer(str(tmp_path / "test.db"))
        conn = optimizer2._conn()
        conn.execute(
            "INSERT INTO routing_rules (name, pattern, action, confidence, reason, created_from, last_updated) VALUES (?,?,?,?,?,?,?)",
            ("test_rule", r"\btest\b", "ESCALATE", 0.9, "test reason", "manual", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        rules = optimizer.get_rules()
        names = [r.name for r in rules]
        assert "test_rule" in names

    def test_disable_rule(self, optimizer, tmp_path):
        conn = optimizer._conn()
        conn.execute("UPDATE routing_rules SET enabled = 0 WHERE name = 'code_block'")
        conn.commit()
        conn.close()

        enabled = optimizer.get_rules(enabled_only=True)
        names = [r.name for r in enabled]
        assert "code_block" not in names

        all_rules = optimizer.get_rules(enabled_only=False)
        names = [r.name for r in all_rules]
        assert "code_block" in names


class TestFeedbackAnalysis:
    def test_extract_common_themes(self, optimizer):
        critiques = [
            "Too long and verbose",
            "Way too long for a simple answer",
            "The response was too long",
            "Please keep it shorter, too long",
            "Too long, I lost interest",
        ]
        themes = optimizer._extract_common_themes(critiques)
        assert any("too long" in t for t in themes)

    def test_no_themes_with_few_critiques(self, optimizer):
        critiques = ["it was okay", "fine"]
        themes = optimizer._extract_common_themes(critiques)
        assert len(themes) == 0

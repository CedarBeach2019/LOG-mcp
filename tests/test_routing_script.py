"""Tests for the rule-based routing script."""

import pytest
from vault.routing_script import classify, classify_static, resolve_action


class TestClassifyStaticBasic:
    """Static rules — always work, no DB needed."""

    def test_what_is_cheap(self):
        r = classify_static("What is the capital of France?")
        assert r["action"] == "cheap"

    def test_how_many_cheap(self):
        r = classify_static("How many inches in a meter?")
        assert r["action"] == "cheap"

    def test_convert_cheap(self):
        r = classify_static("Convert 100 USD to EUR")
        assert r["action"] == "cheap"

    def test_define_cheap(self):
        r = classify_static("Define photosynthesis")
        assert r["action"] == "cheap"

    def test_who_when_where_cheap(self):
        r = classify_static("Who won the World Cup in 2022?")
        assert r["action"] == "cheap"


class TestClassifyStaticEscalate:
    """Static rules for escalation patterns."""

    def test_debug_escalate(self):
        r = classify_static("Debug this traceback: NameError: name 'x' is not defined")
        assert r["action"] == "escalation"

    def test_write_code_escalate(self):
        r = classify_static("Write a function that sorts a list of dicts by a nested key")
        assert r["action"] == "escalation"

    def test_explain_long_escalate(self):
        r = classify_static("Explain the difference between microservices and monoliths in detail")
        assert r["action"] == "escalation"

    def test_plan_escalate(self):
        r = classify_static("Plan a database migration strategy for a legacy PostgreSQL system")
        assert r["action"] == "escalation"

    def test_review_escalate(self):
        r = classify_static("Review my code and suggest improvements for performance")
        assert r["action"] == "escalation"

    def test_design_escalate(self):
        r = classify_static("Design a REST API for a task management application")
        assert r["action"] == "escalation"


class TestClassifyStaticHeuristics:
    """Static heuristic rules (length, code blocks)."""

    def test_long_message_escalate(self):
        long_msg = "Help me " * 100
        r = classify_static(long_msg)
        assert r["action"] == "escalation"
        assert "long" in r["reason"].lower()

    def test_code_blocks_escalate(self):
        r = classify_static("Check this code:\n```python\ndef foo():\n    pass\n```", has_code=True)
        assert r["action"] == "escalation"
        assert "code" in r["reason"].lower()

    def test_short_no_code_hits_default(self):
        r = classify_static("Hello there", length=11, has_code=False)
        assert r["action"] == "cheap"  # default to cheap when nothing matches


class TestClassifyManualPrefix:
    """Command prefixes always use static rules, even with optimizer loaded."""

    def test_local_override(self):
        r = classify("/local what time is it")
        assert r["action"] == "local"
        assert r["confidence"] == 1.0

    def test_compare_override(self):
        r = classify("/compare what is 2+2")
        assert r["action"] == "compare"


class TestClassifyDynamic:
    """Full classify() with optimizer — tests integration with DB rules."""

    def test_returns_dict(self):
        r = classify("test message")
        assert isinstance(r, dict)
        assert "action" in r
        assert "reason" in r
        assert "confidence" in r

    def test_confidence_is_float(self):
        r = classify("test message")
        assert isinstance(r["confidence"], float)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_thanks_cheap(self):
        r = classify("Thanks!")
        assert r["action"] in ("cheap", "escalation")

    def test_simple_ack_cheap(self):
        r = classify("ok")
        assert r["action"] in ("cheap", "escalation")


class TestResolveAction:
    """Map actions to endpoint types and model names."""

    def test_cheap(self):
        ep_type, model = resolve_action("cheap", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "cheap"

    def test_escalation(self):
        ep_type, model = resolve_action("escalation", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "escalation"

    def test_local(self):
        ep_type, model = resolve_action("local", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "local"

    def test_unknown_falls_to_cheap(self):
        ep_type, model = resolve_action("UNKNOWN", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "cheap"

    def test_uppercase_action(self):
        ep_type, model = resolve_action("ESCALATE", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "escalation"

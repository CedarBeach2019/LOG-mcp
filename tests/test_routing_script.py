"""Tests for the rule-based routing script."""

import pytest
from vault.routing_script import classify, resolve_action


class TestClassifyBasic:
    def test_what_is_cheap(self):
        r = classify("What is the capital of France?")
        assert r["action"] == "CHEAP_ONLY"

    def test_how_many_cheap(self):
        r = classify("How many inches in a meter?")
        assert r["action"] == "CHEAP_ONLY"

    def test_convert_cheap(self):
        r = classify("Convert 100 USD to EUR")
        assert r["action"] == "CHEAP_ONLY"

    def test_define_cheap(self):
        r = classify("Define photosynthesis")
        assert r["action"] == "CHEAP_ONLY"

    def test_who_when_where_cheap(self):
        r = classify("Who won the World Cup in 2022?")
        assert r["action"] == "CHEAP_ONLY"


class TestClassifyEscalate:
    def test_debug_escalate(self):
        r = classify("Debug this traceback: NameError: name 'x' is not defined")
        assert r["action"] == "ESCALATE"

    def test_write_code_escalate(self):
        r = classify("Write a function that sorts a list of dicts by a nested key")
        assert r["action"] == "ESCALATE"

    def test_explain_long_escalate(self):
        r = classify("Explain the difference between microservices and monoliths in detail")
        assert r["action"] == "ESCALATE"

    def test_plan_escalate(self):
        r = classify("Plan a database migration strategy for a legacy PostgreSQL system")
        assert r["action"] == "ESCALATE"

    def test_review_escalate(self):
        r = classify("Review my code and suggest improvements for performance")
        assert r["action"] == "ESCALATE"

    def test_design_escalate(self):
        r = classify("Design a REST API for a task management application")
        assert r["action"] == "ESCALATE"


class TestClassifyHeuristics:
    def test_long_message_escalate(self):
        long_msg = "Help me " * 100
        r = classify(long_msg)
        assert r["action"] == "ESCALATE"
        assert "long message" in r["reason"]

    def test_code_blocks_escalate(self):
        r = classify("Check this code:\n```python\ndef foo():\n    pass\n```", has_code_blocks=True)
        assert r["action"] == "ESCALATE"
        assert "code blocks" in r["reason"]

    def test_short_no_code_hits_default(self):
        r = classify("Hello there", has_code_blocks=False, message_length=11)
        assert r["action"] == "ESCALATE"  # default for unmatched


class TestClassifyManual:
    def test_local_override(self):
        r = classify("/local what time is it")
        assert r["action"] == "local"
        assert r["confidence"] == 1.0

    def test_cloud_override(self):
        r = classify("/cloud write me a poem")
        assert r["action"] == "cloud"

    def test_reason_override(self):
        r = classify("/reason explain quantum entanglement")
        assert r["action"] == "reason"

    def test_compare_override(self):
        r = classify("/compare what is 2+2")
        assert r["action"] == "compare"


class TestClassifyDefaults:
    def test_uncertain_escalates(self):
        """Default is to escalate (safer)."""
        r = classify("help")
        assert r["action"] == "ESCALATE"
        assert r["reason"] == "default (uncertain)"

    def test_simple_acknowledgment_cheap(self):
        r = classify("ok")
        assert r["action"] == "CHEAP_ONLY"

    def test_thanks_cheap(self):
        r = classify("Thanks!")
        assert r["action"] == "CHEAP_ONLY"


class TestClassifyStructure:
    def test_returns_dict(self):
        r = classify("test")
        assert isinstance(r, dict)
        assert "action" in r
        assert "reason" in r
        assert "confidence" in r

    def test_confidence_is_float(self):
        r = classify("test")
        assert isinstance(r["confidence"], float)
        assert 0.0 <= r["confidence"] <= 1.0


class TestResolveAction:
    def test_cheap_only(self):
        ep_type, model = resolve_action("CHEAP_ONLY", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "cheap"
        assert model == "deepseek-chat"

    def test_escalate(self):
        ep_type, model = resolve_action("ESCALATE", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "escalation"
        assert model == "deepseek-reasoner"

    def test_reason(self):
        ep_type, model = resolve_action("reason", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "escalation"

    def test_unknown_falls_to_cheap(self):
        ep_type, model = resolve_action("UNKNOWN", "deepseek-chat", "deepseek-reasoner")
        assert ep_type == "cheap"

"""Tests for the prompt engineering pipeline."""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.prompt_pipeline import (
    select_template,
    build_system_prompt,
    apply_prompt_pipeline,
    ACTION_TO_TEMPLATE,
)
from vault.prompt_intelligence import (
    DEFAULT_SYSTEM_PROMPTS,
    ContextWindow,
    FewShotInjector,
    render_template,
)


class TestTemplateSelection:
    def test_default_template_for_cheap(self):
        assert select_template("cheap") == "default"

    def test_code_template_for_escalation(self):
        assert select_template("escalation") == "code"

    def test_override_takes_priority(self):
        assert select_template("cheap", override="debug") == "debug"

    def test_invalid_override_ignored(self):
        assert select_template("cheap", override="nonexistent") == "default"

    def test_compare_maps_to_analyst(self):
        assert select_template("compare") == "analyst"

    def test_draft_maps_to_creative(self):
        assert select_template("draft") == "creative"

    def test_debug_reason_overrides_action(self):
        assert select_template("cheap", route_reason="[static] debug") == "debug"

    def test_review_reason_maps_to_code(self):
        assert select_template("cheap", route_reason="review") == "code"


class TestBuildSystemPrompt:
    def test_includes_template_text(self):
        result = build_system_prompt("code", "preamble text", "style=concise")
        assert "expert software engineer" in result
        assert "preamble text" in result
        assert "style=concise" in result

    def test_unknown_template_falls_back(self):
        result = build_system_prompt("nonexistent", "preamble", "")
        assert "helpful AI assistant" in result  # default template

    def test_empty_preamble_and_prefs(self):
        result = build_system_prompt("concise", "", "")
        assert "concise assistant" in result


class TestContextWindow:
    def test_empty_messages(self):
        ctx = ContextWindow(max_tokens=100)
        assert ctx.truncate_messages([]) == []

    def test_short_messages_pass_through(self):
        ctx = ContextWindow(max_tokens=4096)
        msgs = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there"}]
        result = ctx.truncate_messages(msgs)
        assert len(result) == 2

    def test_truncation_keeps_recent(self):
        ctx = ContextWindow(max_tokens=50, reserve_for_response=10)
        msgs = [
            {"role": "user", "content": "A" * 200},
            {"role": "assistant", "content": "B" * 200},
            {"role": "user", "content": "Recent question"},
        ]
        result = ctx.truncate_messages(msgs)
        assert len(result) < 3
        # Most recent should be kept
        assert result[-1]["content"] == "Recent question"

    def test_token_estimation(self):
        ctx = ContextWindow()
        msgs = [{"role": "user", "content": "a" * 100}]
        tokens = ctx.estimate_tokens(msgs)
        assert tokens == 25  # 100 / 4


class TestFewShotInjector:
    def test_no_query_returns_empty(self):
        inj = FewShotInjector()
        assert inj.find_relevant_examples("", []) == []

    def test_no_interactions_returns_empty(self):
        inj = FewShotInjector()
        assert inj.find_relevant_examples("hello", []) == []

    def test_positive_feedback_boosts(self):
        inj = FewShotInjector(max_examples=10)
        interactions = [
            {"user_input": "python list comprehension", "response": "Use [x for x in ...]", "feedback": "up"},
            {"user_input": "python list comprehension", "response": "I don't know", "feedback": "down"},
        ]
        examples = inj.find_relevant_examples("python list comprehension", interactions)
        assert len(examples) >= 1
        assert examples[0]["output"] == "Use [x for x in ...]"

    def test_inject_after_system(self):
        inj = FewShotInjector()
        msgs = [{"role": "system", "content": "You are helpful"}]
        examples = [{"input": "Q1", "output": "A1"}]
        result = inj.inject_examples(msgs, examples)
        assert len(result) == 3  # system + user + assistant

    def test_inject_without_system(self):
        inj = FewShotInjector()
        msgs = [{"role": "user", "content": "Hello"}]
        examples = [{"input": "Q1", "output": "A1"}]
        result = inj.inject_examples(msgs, examples)
        # Should insert at beginning
        assert result[0]["role"] == "user"
        assert "Example question" in result[0]["content"]


class TestApplyPromptPipeline:
    @pytest.mark.asyncio
    async def test_basic_pipeline(self):
        msgs = [{"role": "user", "content": "Write a function"}]
        result, meta = await apply_prompt_pipeline(
            messages=msgs,
            route_action="escalation",
            preamble="Test preamble",
            prefs_text="style=concise",
        )
        assert isinstance(result, list)
        assert result[0]["role"] == "system"
        assert "expert software engineer" in result[0]["content"]
        assert meta["template"] == "code"
        assert "tokens_estimated" in meta

    @pytest.mark.asyncio
    async def test_no_reallog_still_works(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result, meta = await apply_prompt_pipeline(
            messages=msgs, route_action="cheap", reallog=None,
        )
        assert meta["few_shot_count"] == 0

    @pytest.mark.asyncio
    async def test_backward_compatible_default(self):
        msgs = [{"role": "user", "content": "What is 2+2?"}]
        result, meta = await apply_prompt_pipeline(
            messages=msgs, route_action="cheap",
        )
        assert len(result) == 2  # system + user
        assert meta["template"] == "default"


class TestRenderTemplate:
    def test_date_variable(self):
        result = render_template("Today is {date}")
        assert "Today is " in result
        assert len(result) > len("Today is ")

    def test_extra_vars_override(self):
        result = render_template("Hello {name}", extra_vars={"name": "Alice"})
        assert result == "Hello Alice"

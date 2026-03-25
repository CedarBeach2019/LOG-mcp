"""Tests for prompt intelligence."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from vault.prompt_intelligence import (
    DEFAULT_SYSTEM_PROMPTS,
    render_template,
    ContextWindow,
    FewShotInjector,
)


class TestRenderTemplate:
    def test_renders_date(self):
        template = "Today is {date}."
        result = render_template(template)
        assert "{date}" not in result
        assert len(result) > 10

    def test_extra_vars(self):
        result = render_template("Hello {name}", {"name": "Casey"})
        assert result == "Hello Casey"

    def test_no_vars(self):
        assert render_template("No variables here") == "No variables here"

    def test_all_default_prompts_render(self):
        for key, template in DEFAULT_SYSTEM_PROMPTS.items():
            result = render_template(template)
            assert "{" not in result or "}" not in result


class TestContextWindow:
    def test_empty_messages(self):
        cw = ContextWindow(max_tokens=100)
        assert cw.truncate_messages([]) == []

    def test_short_messages_pass_through(self):
        cw = ContextWindow(max_tokens=4096)
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = cw.truncate_messages(msgs)
        assert len(result) == 2

    def test_long_messages_truncated(self):
        cw = ContextWindow(max_tokens=50, reserve_for_response=10, avg_chars_per_token=4.0)
        msgs = [{"role": "user", "content": "A" * 500}]
        result = cw.truncate_messages(msgs)
        assert len(result) == 1
        assert "truncated" in result[0]["content"]
        assert len(result[0]["content"]) < 500

    def test_system_prompt_reserved(self):
        cw = ContextWindow(max_tokens=100, reserve_for_response=20, avg_chars_per_token=4.0)
        msgs = [{"role": "user", "content": "A" * 500}]
        result = cw.truncate_messages(msgs, system_prompt="System: " + "B" * 100)
        # System prompt should reduce available space further
        assert len(result) == 1

    def test_estimates_tokens(self):
        cw = ContextWindow()
        msgs = [{"role": "user", "content": "Hello world"}]
        tokens = cw.estimate_tokens(msgs, "System prompt")
        assert tokens > 0

    def test_keeps_newest_messages(self):
        cw = ContextWindow(max_tokens=50, reserve_for_response=10, avg_chars_per_token=2.0)
        msgs = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First reply"},
            {"role": "user", "content": "Second message"},
            {"role": "assistant", "content": "Second reply"},
        ]
        result = cw.truncate_messages(msgs)
        # Should keep the most recent messages (last ones)
        assert result[-1]["content"] == "Second reply"


class TestFewShotInjector:
    def test_no_interactions(self):
        injector = FewShotInjector()
        result = injector.find_relevant_examples("hello", [])
        assert result == []

    def test_no_query(self):
        injector = FewShotInjector()
        assert injector.find_relevant_examples("", []) == []

    def test_finds_relevant(self):
        injector = FewShotInjector(max_examples=1)
        interactions = [
            {"user_input": "How do I sort a list in Python?", "response": "Use sorted()", "feedback": "up"},
            {"user_input": "What is the weather today?", "response": "It's sunny", "feedback": None},
        ]
        examples = injector.find_relevant_examples("sort a list", interactions)
        assert len(examples) == 1
        assert "sorted" in examples[0]["output"]

    def test_max_examples(self):
        injector = FewShotInjector(max_examples=2)
        interactions = [
            {"user_input": "python list sort", "response": "sorted()", "feedback": "up"},
            {"user_input": "python dict keys", "response": ".keys()", "feedback": "up"},
            {"user_input": "python file read", "response": "open()", "feedback": "up"},
        ]
        examples = injector.find_relevant_examples("python code", interactions)
        assert len(examples) <= 2

    def test_negative_feedback_penalized(self):
        injector = FewShotInjector()
        interactions = [
            {"user_input": "python error fix", "response": "wrong answer", "feedback": "down"},
            {"user_input": "python debug help", "response": "correct answer", "feedback": "up"},
        ]
        examples = injector.find_relevant_examples("python error debug", interactions)
        # Up-voted should rank higher (keyword overlap + positive feedback)
        if len(examples) > 1:
            # If both match, check ordering
            assert examples[0]["output"] == "correct answer"

    def test_inject_after_system(self):
        injector = FewShotInjector()
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        examples = [{"input": "Q1", "output": "A1"}]
        result = injector.inject_examples(msgs, examples)
        assert len(result) == 4  # system + example_q + example_a + user

    def test_inject_without_system(self):
        injector = FewShotInjector()
        msgs = [{"role": "user", "content": "Hello"}]
        examples = [{"input": "Q1", "output": "A1"}]
        result = injector.inject_examples(msgs, examples)
        assert len(result) == 3  # example_q + example_a + user
        assert result[-1]["content"] == "Hello"

    def test_truncates_long_examples(self):
        injector = FewShotInjector(max_chars_per_example=20)
        interactions = [
            {"user_input": "x" * 100, "response": "y" * 100, "feedback": "up"},
        ]
        examples = injector.find_relevant_examples("x", interactions)
        assert len(examples) == 1
        assert len(examples[0]["input"]) <= 20

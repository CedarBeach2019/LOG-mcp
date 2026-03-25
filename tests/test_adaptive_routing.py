"""Tests for adaptive routing: model health, cost tracking, calibration."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from vault.adaptive_routing import (
    AdaptiveRouter,
    ModelHealth,
    CostTracker,
    ConfidenceCalibrator,
    get_adaptive_router,
)


class TestModelHealth:
    def test_avg_latency(self):
        m = ModelHealth(name="test", total_requests=3, total_latency_ms=300)
        assert m.avg_latency_ms == 100.0

    def test_error_rate(self):
        m = ModelHealth(name="test", total_requests=10, failed_requests=3)
        assert m.error_rate == 0.3

    def test_satisfaction_rate(self):
        m = ModelHealth(name="test", thumbs_up=8, thumbs_down=2)
        assert m.satisfaction_rate == 0.8

    def test_satisfaction_neutral_no_data(self):
        m = ModelHealth(name="test")
        assert m.satisfaction_rate == 0.5

    def test_reliability_score(self):
        m = ModelHealth(name="test", total_requests=10, total_latency_ms=1000,
                        failed_requests=1, thumbs_up=7, thumbs_down=3)
        score = m.reliability_score
        assert 0.0 <= score <= 1.0

    def test_reliability_neutral_low_data(self):
        m = ModelHealth(name="test", total_requests=1)
        assert m.reliability_score == 0.5

    def test_to_dict(self):
        m = ModelHealth(name="test", total_requests=5)
        d = m.to_dict()
        assert d["name"] == "test"
        assert "avg_latency_ms" in d
        assert "satisfaction_rate" in d


class TestCostTracker:
    def test_record_and_retrieve(self):
        ct = CostTracker()
        ct.record("deepseek-chat", input_tokens=100, output_tokens=50)
        assert ct.total_input_tokens == 100
        assert ct.total_output_tokens == 50

    def test_estimated_cost(self):
        ct = CostTracker()
        ct.record("deepseek-chat", input_tokens=1_000_000, output_tokens=1_000_000)
        assert ct.total_estimated_cost_usd > 0
        # deepseek-chat: $0.14 + $0.28 = $0.42 per 1M tokens each
        assert 0.40 < ct.total_estimated_cost_usd < 0.50

    def test_local_is_free(self):
        ct = CostTracker()
        ct.record("local", input_tokens=1_000_000, output_tokens=1_000_000)
        assert ct.total_estimated_cost_usd == 0.0

    def test_per_model_cost(self):
        ct = CostTracker()
        ct.record("deepseek-chat", input_tokens=500_000, output_tokens=500_000)
        ct.record("deepseek-reasoner", input_tokens=500_000, output_tokens=500_000)
        cost_chat = ct.get_model_cost("deepseek-chat")
        cost_reasoner = ct.get_model_cost("deepseek-reasoner")
        assert cost_reasoner > cost_chat  # reasoner is more expensive

    def test_to_dict(self):
        ct = CostTracker()
        ct.record("deepseek-chat", 100, 50)
        d = ct.to_dict()
        assert "total_estimated_cost_usd" in d
        assert "per_model" in d
        assert "deepseek-chat" in d["per_model"]


class TestConfidenceCalibrator:
    def test_record_prediction_and_outcome(self):
        cal = ConfidenceCalibrator()
        cal.record_prediction(0.8, "deepseek-chat")
        cal.record_outcome("deepseek-chat", "up")
        report = cal.get_calibration_report()
        assert report["sample_size"] == 1
        assert report["calibrated"] is not None

    def test_no_data(self):
        cal = ConfidenceCalibrator()
        report = cal.get_calibration_report()
        assert report["sample_size"] == 0
        assert report["calibrated"] is None

    def test_well_calibrated(self):
        cal = ConfidenceCalibrator()
        # Simulate well-calibrated predictions
        for _ in range(10):
            cal.record_prediction(0.9, "good-model")
            cal.record_outcome("good-model", "up")
        report = cal.get_calibration_report()
        assert report["sample_size"] == 10
        assert report["calibrated"] is True

    def test_poorly_calibrated(self):
        cal = ConfidenceCalibrator()
        # High confidence but bad outcomes
        for _ in range(10):
            cal.record_prediction(0.9, "bad-model")
            cal.record_outcome("bad-model", "down")
        report = cal.get_calibration_report()
        assert report["calibrated"] is False

    def test_max_history(self):
        cal = ConfidenceCalibrator(_max_history=5)
        for i in range(10):
            cal.record_prediction(0.5, "model")
            cal.record_outcome("model", "up" if i % 2 == 0 else "down")
        report = cal.get_calibration_report()
        assert report["sample_size"] == 5  # trimmed


class TestAdaptiveRouter:
    def test_record_request(self):
        ar = AdaptiveRouter()
        ar.record_request("test-model", latency_ms=100, success=True, confidence=0.8)
        health = ar.get_model_health("test-model")
        assert health["total_requests"] == 1
        assert health["success_rate"] == 1.0

    def test_record_feedback(self):
        ar = AdaptiveRouter()
        ar.record_request("test-model", latency_ms=100, success=True)
        ar.record_feedback("test-model", "up")
        health = ar.get_model_health("test-model")
        assert health["thumbs_up"] == 1

    def test_should_use_model_no_data(self):
        ar = AdaptiveRouter()
        assert ar.should_use_model("unknown-model") is True

    def test_should_use_model_degraded(self):
        ar = AdaptiveRouter()
        for _ in range(10):
            ar.record_request("bad-model", latency_ms=100, success=False)
        assert ar.should_use_model("bad-model") is False

    def test_suggest_model_both_healthy(self):
        ar = AdaptiveRouter()
        result = ar.suggest_model("model-a", "model-b")
        assert result["suggestion"] == "default"

    def test_suggest_model_cheap_degraded(self):
        ar = AdaptiveRouter()
        for _ in range(10):
            ar.record_request("cheap", latency_ms=100, success=False)
        result = ar.suggest_model("cheap", "escalation")
        assert result["suggestion"] == "escalate_all"

    def test_dashboard(self):
        ar = AdaptiveRouter()
        ar.record_request("m1", 100, True, input_tokens=50, output_tokens=25)
        ar.record_feedback("m1", "up")
        dashboard = ar.get_dashboard()
        assert "models" in dashboard
        assert "cost" in dashboard
        assert "calibration" in dashboard
        assert len(dashboard["models"]) == 1

    def test_singleton(self):
        from vault.adaptive_routing import _router
        r1 = get_adaptive_router()
        r2 = get_adaptive_router()
        assert r1 is r2

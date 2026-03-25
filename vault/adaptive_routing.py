"""Adaptive routing v2 — confidence calibration, cost tracking, model health.

Tracks per-model performance metrics and uses them to improve routing decisions.
Calibrates classifier confidence against actual user satisfaction.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("vault.adaptive_routing")


@dataclass
class ModelHealth:
    """Per-model health and performance tracking."""
    name: str = ""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    thumbs_up: int = 0
    thumbs_down: int = 0
    escalations_from: int = 0  # times this model's response was escalated
    first_seen: str = ""
    last_seen: str = ""

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_requests if self.total_requests else 0

    @property
    def error_rate(self) -> float:
        return self.failed_requests / self.total_requests if self.total_requests else 0

    @property
    def satisfaction_rate(self) -> float:
        total = self.thumbs_up + self.thumbs_down
        return self.thumbs_up / total if total else 0.5  # neutral if no data

    @property
    def reliability_score(self) -> float:
        """0-1 composite score: higher = more reliable."""
        if self.total_requests < 3:
            return 0.5  # not enough data
        latency_score = max(0, 1 - (self.avg_latency_ms / 10000))  # penalize >10s
        error_score = 1 - self.error_rate
        satisfaction_score = self.satisfaction_rate
        return (latency_score * 0.3 + error_score * 0.3 + satisfaction_score * 0.4)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_requests": self.total_requests,
            "success_rate": 1 - self.error_rate,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "min_latency_ms": round(self.min_latency_ms, 1) if self.min_latency_ms != float("inf") else None,
            "max_latency_ms": round(self.max_latency_ms, 1),
            "satisfaction_rate": round(self.satisfaction_rate, 2),
            "reliability_score": round(self.reliability_score, 2),
            "thumbs_up": self.thumbs_up,
            "thumbs_down": self.thumbs_down,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class CostTracker:
    """Track estimated API costs per model."""
    # Estimated costs per 1M tokens (input/output) in USD
    PRICING: dict = field(default_factory=lambda: {
        "deepseek-chat": (0.14, 0.28),       # $0.14/$0.28 per 1M
        "deepseek-reasoner": (0.55, 2.19),    # $0.55/$2.19 per 1M
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-3-haiku": (0.25, 1.25),
        "claude-3-sonnet": (3.00, 15.00),
        "local": (0.00, 0.00),
    })

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    model_tokens: dict = field(default_factory=lambda: defaultdict(lambda: {"input": 0, "output": 0}))
    total_estimated_cost_usd: float = 0.0

    def record(self, model_name: str, input_tokens: int = 0, output_tokens: int = 0):
        """Record token usage for a request."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.model_tokens[model_name]["input"] += input_tokens
        self.model_tokens[model_name]["output"] += output_tokens

        pricing = self.PRICING.get(model_name, (0.50, 1.50))  # default estimate
        cost = (input_tokens / 1_000_000) * pricing[0] + (output_tokens / 1_000_000) * pricing[1]
        self.total_estimated_cost_usd += cost

    def get_model_cost(self, model_name: str) -> float:
        """Get estimated cost for a specific model."""
        tokens = self.model_tokens.get(model_name, {"input": 0, "output": 0})
        pricing = self.PRICING.get(model_name, (0.50, 1.50))
        return (tokens["input"] / 1_000_000) * pricing[0] + (tokens["output"] / 1_000_000) * pricing[1]

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_estimated_cost_usd": round(self.total_estimated_cost_usd, 4),
            "per_model": {
                name: {
                    "input_tokens": t["input"],
                    "output_tokens": t["output"],
                    "estimated_cost_usd": round(self.get_model_cost(name), 4),
                }
                for name, t in self.model_tokens.items()
            },
        }


@dataclass
class ConfidenceCalibrator:
    """Compare predicted routing confidence against actual user feedback."""

    predictions: list = field(default_factory=list)  # [(predicted_conf, actual_good, model)]
    _max_history: int = 1000

    def record_prediction(self, confidence: float, model_name: str):
        """Record a routing prediction. Call record_outcome() later."""
        self.predictions.append({
            "confidence": confidence,
            "model": model_name,
            "actual": None,  # filled by record_outcome
            "timestamp": datetime.now().isoformat(),
        })
        if len(self.predictions) > self._max_history:
            self.predictions = self.predictions[-self._max_history:]

    def record_outcome(self, model_name: str, feedback: str):
        """Record actual user feedback. Matches to most recent prediction for this model."""
        for pred in reversed(self.predictions):
            if pred["model"] == model_name and pred["actual"] is None:
                pred["actual"] = 1.0 if feedback == "up" else 0.0
                break

    def get_calibration_report(self) -> dict:
        """Generate calibration report: are we well-calibrated?"""
        scored = [p for p in self.predictions if p["actual"] is not None]
        if not scored:
            return {"calibrated": None, "sample_size": 0, "message": "No feedback data yet"}

        # Bin predictions by confidence range
        bins = defaultdict(lambda: {"predicted": [], "actual": []})
        for p in scored:
            bucket = round(p["confidence"] * 4) / 4  # 0.0, 0.25, 0.5, 0.75, 1.0
            bins[bucket]["predicted"].append(p["confidence"])
            bins[bucket]["actual"].append(p["actual"])

        calibration = {}
        for bucket in sorted(bins.keys()):
            preds = bins[bucket]["predicted"]
            actuals = bins[bucket]["actual"]
            avg_predicted = sum(preds) / len(preds)
            avg_actual = sum(actuals) / len(actuals)
            calibration[f"{int(bucket*100)}%"] = {
                "predicted": round(avg_predicted, 2),
                "actual": round(avg_actual, 2),
                "count": len(preds),
                "gap": round(avg_actual - avg_predicted, 2),
            }

        # Overall calibration error
        total_gap = sum(abs(c["gap"]) for c in calibration.values())
        well_calibrated = total_gap < 0.3  # arbitrary threshold

        return {
            "calibrated": well_calibrated,
            "sample_size": len(scored),
            "overall_gap": round(total_gap, 2),
            "bins": calibration,
            "message": (
                "Routing confidence is well-calibrated"
                if well_calibrated
                else "Routing confidence needs recalibration — consider adjusting thresholds"
            ),
        }


class AdaptiveRouter:
    """Combines model health, cost, and calibration for routing decisions."""

    def __init__(self):
        self._models: dict[str, ModelHealth] = {}
        self._cost = CostTracker()
        self._calibrator = ConfidenceCalibrator()

    def record_request(self, model_name: str, latency_ms: float, success: bool,
                       input_tokens: int = 0, output_tokens: int = 0,
                       confidence: float = 0.5):
        """Record a completed request."""
        now = datetime.now().isoformat()

        # Model health
        if model_name not in self._models:
            self._models[model_name] = ModelHealth(name=model_name, first_seen=now)
        m = self._models[model_name]
        m.total_requests += 1
        m.last_seen = now
        m.total_latency_ms += latency_ms
        m.min_latency_ms = min(m.min_latency_ms, latency_ms)
        m.max_latency_ms = max(m.max_latency_ms, latency_ms)
        if success:
            m.successful_requests += 1
        else:
            m.failed_requests += 1

        # Cost
        self._cost.record(model_name, input_tokens, output_tokens)

        # Calibration
        self._calibrator.record_prediction(confidence, model_name)

    def record_feedback(self, model_name: str, feedback: str):
        """Record user feedback (up/down)."""
        if model_name not in self._models:
            return
        m = self._models[model_name]
        if feedback == "up":
            m.thumbs_up += 1
        elif feedback == "down":
            m.thumbs_down += 1
        self._calibrator.record_outcome(model_name, feedback)

    def record_escalation(self, from_model: str):
        """Record that a response was escalated (model gave bad answer)."""
        if from_model in self._models:
            self._models[from_model].escalations_from += 1

    def get_model_health(self, model_name: str) -> dict | None:
        """Get health report for a specific model."""
        if model_name not in self._models:
            return None
        return self._models[model_name].to_dict()

    def get_all_health(self) -> list[dict]:
        """Get health for all models."""
        return [m.to_dict() for m in sorted(self._models.values(), key=lambda m: m.total_requests, reverse=True)]

    def should_use_model(self, model_name: str) -> bool:
        """Should we route to this model? Checks health thresholds."""
        if model_name not in self._models:
            return True  # no data, allow
        m = self._models[model_name]
        if m.total_requests < 3:
            return True
        if m.error_rate > 0.5:
            return False
        if m.reliability_score < 0.2:
            return False
        return True

    def suggest_model(self, cheap_model: str, escalation_model: str) -> dict:
        """Suggest which model to use based on current health."""
        cheap_ok = self.should_use_model(cheap_model)
        esc_ok = self.should_use_model(escalation_model)

        if cheap_ok and esc_ok:
            return {"suggestion": "default", "reason": "Both models healthy"}
        if not cheap_ok and esc_ok:
            return {"suggestion": "escalate_all", "reason": f"{cheap_model} degraded ({self._models.get(cheap_model, ModelHealth()).error_rate:.0%} error rate)"}
        if cheap_ok and not esc_ok:
            return {"suggestion": "cheap_only", "reason": f"{escalation_model} degraded"}
        return {"suggestion": "local_or_error", "reason": "Both models degraded"}

    def get_dashboard(self) -> dict:
        """Full dashboard data."""
        return {
            "models": self.get_all_health(),
            "cost": self._cost.to_dict(),
            "calibration": self._calibrator.get_calibration_report(),
            "suggestion": self.suggest_model(
                list(self._models.keys())[0] if self._models else "unknown",
                list(self._models.keys())[1] if len(self._models) > 1 else "unknown",
            ),
        }


# Singleton
_router: AdaptiveRouter | None = None


def get_adaptive_router() -> AdaptiveRouter:
    global _router
    if _router is None:
        _router = AdaptiveRouter()
    return _router

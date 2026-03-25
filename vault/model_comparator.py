"""Model comparator — pick the best model for a use case based on benchmarks + cost + latency.

Combines benchmark results from vault.benchmark with model metadata from
vault.model_discovery to rank models per task type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("vault.model_comparator")

# Weights per task type: (latency_weight, quality_weight, cost_weight)
TASK_WEIGHTS = {
    "code": {"latency": 0.2, "quality": 0.6, "cost": 0.2},
    "chat": {"latency": 0.4, "quality": 0.3, "cost": 0.3},
    "reasoning": {"latency": 0.1, "quality": 0.7, "cost": 0.2},
    "general": {"latency": 0.3, "quality": 0.4, "cost": 0.3},
    "fast": {"latency": 0.7, "quality": 0.1, "cost": 0.2},
    "cheap": {"latency": 0.1, "quality": 0.3, "cost": 0.6},
}

# Quality category mapping to benchmark categories
TASK_TO_QUALITY_CATEGORY = {
    "code": "code",
    "chat": "chat",
    "reasoning": "reasoning",
    "general": "chat",
    "fast": "chat",
    "cheap": "chat",
}


@dataclass
class ModelScore:
    """Composite score for a model on a specific task."""
    model_id: str
    task_type: str
    composite_score: float = 0.0
    latency_score: float = 0.0
    quality_score: float = 0.0
    cost_score: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "task_type": self.task_type,
            "composite_score": round(self.composite_score, 3),
            "latency_score": round(self.latency_score, 3),
            "quality_score": round(self.quality_score, 3),
            "cost_score": round(self.cost_score, 3),
            "details": self.details,
        }


class ModelComparator:
    """Compare and rank models based on benchmarks, cost, and latency."""

    def __init__(self):
        pass

    def _normalize_latency(self, ttft_ms: float, max_ttft_ms: float = 5000.0) -> float:
        """Convert TTFT to 0-1 score (lower is better)."""
        if ttft_ms <= 0:
            return 0.0
        return max(0.0, 1.0 - (ttft_ms / max_ttft_ms))

    def _normalize_quality(self, quality_score: float) -> float:
        """Quality is already 0-1."""
        return max(0.0, min(1.0, quality_score))

    def _normalize_cost(self, prompt_price: float, max_price: float = 60.0) -> float:
        """Convert price per 1M tokens to 0-1 score (lower is better)."""
        if prompt_price <= 0:
            return 1.0  # free
        return max(0.0, 1.0 - (prompt_price / max_price))

    def score_model(
        self,
        model_id: str,
        task_type: str = "general",
        ttft_ms: float | None = None,
        total_ms: float | None = None,
        quality_score: float | None = None,
        prompt_price_per_mtok: float = 0.0,
        completion_price_per_mtok: float = 0.0,
    ) -> ModelScore:
        """Score a single model on a task type."""
        weights = TASK_WEIGHTS.get(task_type, TASK_WEIGHTS["general"])

        lat_score = self._normalize_latency(ttft_ms) if ttft_ms is not None else 0.5
        qual_score = self._normalize_quality(quality_score) if quality_score is not None else 0.5
        # Use average of prompt + completion price
        avg_price = (prompt_price_per_mtok + completion_price_per_mtok) / 2
        cost_score = self._normalize_cost(avg_price)

        composite = (
            weights["latency"] * lat_score
            + weights["quality"] * qual_score
            + weights["cost"] * cost_score
        )

        return ModelScore(
            model_id=model_id,
            task_type=task_type,
            composite_score=composite,
            latency_score=lat_score,
            quality_score=qual_score,
            cost_score=cost_score,
            details={
                "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
                "total_ms": round(total_ms, 1) if total_ms else None,
                "quality_score": round(quality_score, 3) if quality_score is not None else None,
                "prompt_price_per_mtok": prompt_price_per_mtok,
                "completion_price_per_mtok": completion_price_per_mtok,
            },
        )

    def compare_models(
        self,
        model_data: list[dict],
        task_type: str = "general",
    ) -> list[ModelScore]:
        """Compare multiple models and return ranked list.

        Each item in model_data should have:
          - model_id: str
          - ttft_ms: float | None
          - total_ms: float | None
          - quality_score: float | None
          - prompt_price_per_mtok: float
          - completion_price_per_mtok: float
        """
        scores = []
        for md in model_data:
            s = self.score_model(
                model_id=md["model_id"],
                task_type=task_type,
                ttft_ms=md.get("ttft_ms"),
                total_ms=md.get("total_ms"),
                quality_score=md.get("quality_score"),
                prompt_price_per_mtok=md.get("prompt_price_per_mtok", 0.0),
                completion_price_per_mtok=md.get("completion_price_per_mtok", 0.0),
            )
            scores.append(s)

        scores.sort(key=lambda s: s.composite_score, reverse=True)
        return scores

    def pick_best(
        self,
        model_data: list[dict],
        task_type: str = "general",
        top_n: int = 3,
    ) -> list[ModelScore]:
        """Return top N models for a task."""
        ranked = self.compare_models(model_data, task_type)
        return ranked[:top_n]

    def suggest_swap(
        self,
        current_model_id: str,
        candidate_data: list[dict],
        task_type: str = "general",
        min_improvement: float = 0.1,
    ) -> dict | None:
        """Suggest if switching from current model to a candidate is worthwhile.

        Returns None if no improvement, or dict with suggestion details.
        """
        current = next((m for m in candidate_data if m["model_id"] == current_model_id), None)
        if not current:
            return None

        current_score = self.score_model(
            model_id=current_model_id, task_type=task_type,
            ttft_ms=current.get("ttft_ms"),
            quality_score=current.get("quality_score"),
            prompt_price_per_mtok=current.get("prompt_price_per_mtok", 0.0),
            completion_price_per_mtok=current.get("completion_price_per_mtok", 0.0),
        )

        best = self.pick_best(candidate_data, task_type, top_n=1)
        if not best or best[0].model_id == current_model_id:
            return None

        improvement = best[0].composite_score - current_score.composite_score
        if improvement < min_improvement:
            return None

        return {
            "action": "swap",
            "from": current_model_id,
            "to": best[0].model_id,
            "improvement": round(improvement, 3),
            "current_score": round(current_score.composite_score, 3),
            "new_score": round(best[0].composite_score, 3),
            "task_type": task_type,
        }

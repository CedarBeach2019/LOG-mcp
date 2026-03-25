"""
Dataset analytics — coverage, quality distribution, growth tracking, readiness.

All queries run on SQLite data directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from vault.dataset_quality import (
    ALL_DOMAINS, classify_domain, score_rankings,
)

logger = logging.getLogger("vault.dataset_stats")

# Minimum thresholds for training readiness
MIN_LORA_EXAMPLES = 100
MIN_DPO_PAIRS = 200
MIN_HIGH_QUALITY = 50  # examples with composite > 0.5


def get_dataset_stats(db_path: str, days_back: int = 90) -> dict[str, Any]:
    """Compute full dataset analytics from the database."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

    # All interactions with feedback
    rows = conn.execute("""
        SELECT id, user_input, response, critique, feedback, target_model,
               route_action, created_at, response_latency_ms
        FROM interactions
        WHERE created_at >= ? AND feedback IS NOT NULL
        ORDER BY created_at DESC
    """, (cutoff,)).fetchall()
    conn.close()

    interactions = [dict(r) for r in rows]

    # Score all
    scores = score_rankings(interactions)

    # Domain coverage
    domain_counts = Counter(s.domain for s in scores)

    # Quality distribution (histogram buckets)
    quality_buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0,
                       "0.6-0.8": 0, "0.8-1.0": 0}
    for s in scores:
        bucket = min(int(s.composite * 5), 4)
        key = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"][bucket]
        quality_buckets[key] += 1

    # High quality count
    high_quality = sum(1 for s in scores if s.composite > 0.5)
    low_effort = sum(1 for s in scores if s.is_low_effort)

    # Average score
    avg_score = (sum(s.composite for s in scores) / len(scores)) if scores else 0.0

    # Growth tracking (per-week counts)
    weekly_counts = _weekly_growth(interactions)

    # Route action breakdown
    action_counts = Counter(r.get("route_action", "") for r in interactions)

    # Readiness assessment
    readiness = _assess_readiness(
        total=len(interactions),
        high_quality=high_quality,
        low_effort=low_effort,
        draft_count=action_counts.get("draft", 0) + action_counts.get("DRAFT", 0),
    )

    return {
        "period_days": days_back,
        "total_interactions": len(interactions),
        "quality_distribution": quality_buckets,
        "average_quality_score": round(avg_score, 3),
        "high_quality_count": high_quality,
        "low_effort_count": low_effort,
        "domain_coverage": {d: domain_counts.get(d, 0) for d in ALL_DOMAINS},
        "domains_with_data": len([c for c in domain_counts.values() if c > 0]),
        "action_breakdown": dict(action_counts),
        "weekly_growth": weekly_counts,
        "readiness": readiness,
    }


def _weekly_growth(interactions: list[dict]) -> list[dict]:
    """Count interactions per week."""
    counts: dict[str, int] = {}
    for r in interactions:
        ts = r.get("created_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            week_key = dt.strftime("%Y-W%W")
            counts[week_key] = counts.get(week_key, 0) + 1
        except (ValueError, TypeError):
            continue
    # Return sorted
    return [{"week": k, "count": v} for k, v in sorted(counts.items())]


def _assess_readiness(total: int, high_quality: int, low_effort: int,
                      draft_count: int) -> dict[str, Any]:
    """Assess if the dataset is ready for different training modes."""
    checks = {}

    checks["lora_instruction"] = {
        "ready": total >= MIN_LORA_EXAMPLES,
        "current": total,
        "needed": MIN_LORA_EXAMPLES,
        "note": "Instruction tuning on positive examples",
    }

    checks["dpo"] = {
        "ready": draft_count >= MIN_DPO_PAIRS,
        "current": draft_count,
        "needed": MIN_DPO_PAIRS,
        "note": "DPO needs ranked draft comparisons",
    }

    checks["high_quality"] = {
        "ready": high_quality >= MIN_HIGH_QUALITY,
        "current": high_quality,
        "needed": MIN_HIGH_QUALITY,
        "note": "Examples with composite score > 0.5",
    }

    quality_ratio = (high_quality / total) if total > 0 else 0.0
    checks["quality_ratio"] = {
        "ready": quality_ratio >= 0.3,
        "current": round(quality_ratio, 3),
        "needed": 0.3,
        "note": "At least 30% of data should be high quality",
    }

    overall_ready = all(c["ready"] for c in checks.values())
    blockers = [k for k, c in checks.items() if not c["ready"]]

    return {
        "overall_ready": overall_ready,
        "checks": checks,
        "blockers": blockers,
        "suggestion": (
            "Dataset is ready for training! Consider running the export pipeline."
            if overall_ready
            else f"Need more data: {', '.join(blockers)}"
        ),
    }

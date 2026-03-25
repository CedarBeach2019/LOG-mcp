"""Stats collector — queries interactions table for routing optimization signals."""

from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


@dataclass
class RoutingStats:
    """Aggregated routing statistics."""
    days: int = 7
    total_interactions: int = 0
    total_feedback: int = 0
    per_route_class: dict = field(default_factory=dict)
    per_model: dict = field(default_factory=dict)
    per_profile: dict = field(default_factory=dict)
    top_patterns: list = field(default_factory=list)
    cheap_wins: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# Minimum interactions before we trust a category's stats
MIN_THRESHOLD = 20


class StatsCollector:
    """Queries interactions table and computes routing optimization signals."""

    def __init__(self, db_path: str | Path | sqlite3.Connection):
        if isinstance(db_path, sqlite3.Connection):
            self._conn = db_path
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(str(db_path))
            self._conn.row_factory = sqlite3.Row
            self._owns_conn = True

    def close(self):
        if self._owns_conn and self._conn:
            self._conn.close()

    # ------------------------------------------------------------------
    def collect(self, days: int = 7) -> RoutingStats:
        """Return stats for the last N days."""
        stats = RoutingStats(days=days)

        cutoff = f"datetime('now', '-{days} days')"
        cur = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM interactions WHERE created_at >= {cutoff}"
        )
        stats.total_interactions = cur.fetchone()["n"]

        cur = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM interactions WHERE feedback IS NOT NULL AND created_at >= {cutoff}"
        )
        stats.total_feedback = cur.fetchone()["n"]

        stats.per_route_class = self.per_route_class_stats(days)
        stats.per_model = self.per_model_stats(days)
        stats.per_profile = self.per_profile_stats(days)
        stats.top_patterns = self.top_patterns(days)
        stats.cheap_wins = self.cheap_wins(days)

        return stats

    # ------------------------------------------------------------------
    def per_route_class_stats(self, days: int = 7) -> dict:
        """Per routing class: total_requests, avg_feedback_score, escalation_rate, avg_latency_ms."""
        cutoff = f"datetime('now', '-{days} days')"
        rows = self._conn.execute(f"""
            SELECT
                route_action,
                COUNT(*) AS total_requests,
                AVG(CASE WHEN feedback='up' THEN 1.0
                         WHEN feedback='down' THEN 0.0
                         ELSE NULL END) AS avg_feedback_score,
                AVG(response_latency_ms) AS avg_latency_ms,
                SUM(CASE WHEN feedback='up' THEN 1 ELSE 0 END) AS thumbs_up,
                SUM(CASE WHEN feedback='down' THEN 1 ELSE 0 END) AS thumbs_down
            FROM interactions
            WHERE created_at >= {cutoff}
            GROUP BY route_action
        """).fetchall()

        result = {}
        for r in rows:
            result[r["route_action"]] = {
                "total_requests": r["total_requests"],
                "avg_feedback_score": round(r["avg_feedback_score"] or 0, 4),
                "avg_latency_ms": round(r["avg_latency_ms"] or 0, 1),
                "thumbs_up": r["thumbs_up"],
                "thumbs_down": r["thumbs_down"],
            }
        return result

    # ------------------------------------------------------------------
    def per_model_stats(self, days: int = 7) -> dict:
        """Per model: total_calls, success_rate, avg_latency."""
        cutoff = f"datetime('now', '-{days} days')"
        rows = self._conn.execute(f"""
            SELECT
                target_model,
                COUNT(*) AS total_calls,
                AVG(CASE WHEN feedback='up' THEN 1.0
                         WHEN feedback='down' THEN 0.0
                         ELSE NULL END) AS success_rate,
                AVG(response_latency_ms) AS avg_latency_ms
            FROM interactions
            WHERE created_at >= {cutoff}
            GROUP BY target_model
        """).fetchall()

        result = {}
        for r in rows:
            result[r["target_model"]] = {
                "total_calls": r["total_calls"],
                "success_rate": round(r["success_rate"] or 0, 4),
                "avg_latency_ms": round(r["avg_latency_ms"] or 0, 1),
            }
        return result

    # ------------------------------------------------------------------
    def per_profile_stats(self, days: int = 7) -> dict:
        """Per draft profile: times_used, times_won, win_rate."""
        cutoff = f"datetime('now', '-{days} days')"
        # Draft interactions store profile name in route_reason as "profile=X"
        rows = self._conn.execute(f"""
            SELECT
                CASE WHEN route_action = 'DRAFT' THEN
                     SUBSTR(route_reason, INSTR(route_reason, '=') + 1)
                     ELSE NULL END AS profile_name,
                COUNT(*) AS times_used,
                SUM(CASE WHEN feedback='up' THEN 1 ELSE 0 END) AS times_won
            FROM interactions
            WHERE created_at >= {cutoff} AND route_action IN ('DRAFT', 'ELABORATE')
            GROUP BY profile_name
        """).fetchall()

        result = {}
        for r in rows:
            name = r["profile_name"] or "unknown"
            used = r["times_used"]
            won = r["times_won"]
            result[name] = {
                "times_used": used,
                "times_won": won,
                "win_rate": round(won / used, 4) if used else 0,
            }

        # Also check ELABORATE winner profiles from critique JSON
        elab_rows = self._conn.execute(f"""
            SELECT critique FROM interactions
            WHERE created_at >= {cutoff} AND route_action = 'DRAFT'
              AND critique IS NOT NULL AND critique LIKE '%winner_profile%'
        """).fetchall()

        winner_counts = {}
        for r in elab_rows:
            try:
                import json
                data = json.loads(r["critique"])
                w = data.get("winner_profile", "")
                if w:
                    winner_counts[w] = winner_counts.get(w, 0) + 1
            except Exception:
                pass

        # Merge winner counts into profile stats
        for name, wc in winner_counts.items():
            if name in result:
                result[name]["times_won"] = max(result[name]["times_won"], wc)
                result[name]["win_rate"] = round(
                    result[name]["times_won"] / result[name]["times_used"], 4
                ) if result[name]["times_used"] else 0

        return result

    # ------------------------------------------------------------------
    def top_patterns(self, days: int = 7) -> list[dict]:
        """Messages escalated that consistently got 👍 — candidates for ESCALATE rule.

        Groups by a simplified "pattern key" (first 40 chars lowercased) and
        returns those with high positive feedback rate.
        """
        cutoff = f"datetime('now', '-{days} days')"
        rows = self._conn.execute(f"""
            SELECT
                LOWER(SUBSTR(user_input, 1, 60)) AS pattern_key,
                COUNT(*) AS total,
                SUM(CASE WHEN feedback='up' THEN 1 ELSE 0 END) AS thumbs_up
            FROM interactions
            WHERE created_at >= {cutoff}
              AND route_action IN ('CHEAP_ONLY', 'default')
              AND feedback = 'up'
            GROUP BY pattern_key
            HAVING total >= {MIN_THRESHOLD}
            ORDER BY thumbs_up DESC
            LIMIT 20
        """).fetchall()

        return [
            {
                "pattern_key": r["pattern_key"],
                "total": r["total"],
                "thumbs_up": r["thumbs_up"],
                "feedback_rate": round(r["thumbs_up"] / r["total"], 4) if r["total"] else 0,
                "suggestion": "escalate",  # these work on cheap but might benefit from escalation
            }
            for r in rows
            if r["total"] >= MIN_THRESHOLD
        ]

    # ------------------------------------------------------------------
    def cheap_wins(self, days: int = 7) -> list[dict]:
        """Messages that got CHEAP_ONLY and got 👍 — candidates for tighter CHEAP_ONLY patterns."""
        cutoff = f"datetime('now', '-{days} days')"
        rows = self._conn.execute(f"""
            SELECT
                LOWER(SUBSTR(user_input, 1, 60)) AS pattern_key,
                COUNT(*) AS total,
                SUM(CASE WHEN feedback='up' THEN 1 ELSE 0 END) AS thumbs_up
            FROM interactions
            WHERE created_at >= {cutoff}
              AND route_action = 'CHEAP_ONLY'
            GROUP BY pattern_key
            HAVING total >= {MIN_THRESHOLD}
            ORDER BY thumbs_up DESC
            LIMIT 20
        """).fetchall()

        return [
            {
                "pattern_key": r["pattern_key"],
                "total": r["total"],
                "thumbs_up": r["thumbs_up"],
                "feedback_rate": round(r["thumbs_up"] / r["total"], 4) if r["total"] else 0,
                "suggestion": "tighten_cheap",
            }
            for r in rows
            if r["total"] >= MIN_THRESHOLD
        ]

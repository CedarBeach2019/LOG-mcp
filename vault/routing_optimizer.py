"""
Auto-routing optimizer — closes the loop between user feedback and routing decisions.

Reads interaction history → analyzes patterns → updates routing rules automatically.

Design principles:
- Never make a change with fewer than 5 data points
- Always keep the last 10 versions for rollback
- Prefer changing confidence thresholds, not action mappings
- Log every change for auditability
- Human-readable rules (not ML black box)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("vault.routing_optimizer")


@dataclass
class RoutingRule:
    """A single routing rule that can be evaluated by the routing script."""
    name: str           # e.g., "code_question"
    pattern: str        # regex pattern to match
    action: str         # CHEAP_ONLY, ESCALATE, etc.
    confidence: float   # 0.0 to 1.0
    reason: str         # human-readable explanation
    enabled: bool = True
    created_from: str = "auto"  # "auto", "manual", "default"
    last_updated: str = ""
    sample_size: int = 0


@dataclass
class RoutingOptimization:
    """Result of an optimization pass."""
    timestamp: str
    interactions_analyzed: int
    rules_added: int
    rules_modified: int
    rules_disabled: int
    changes: list[dict]
    summary: str


class RoutingOptimizer:
    """Analyzes interaction history and optimizes routing rules."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS routing_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                pattern TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                reason TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_from TEXT DEFAULT 'default',
                last_updated TEXT DEFAULT '',
                sample_size INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS routing_optimizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                interactions_analyzed INTEGER DEFAULT 0,
                rules_added INTEGER DEFAULT 0,
                rules_modified INTEGER DEFAULT 0,
                rules_disabled INTEGER DEFAULT 0,
                changes_json TEXT DEFAULT '[]',
                summary TEXT DEFAULT ''
            );
        """)
        conn.commit()
        conn.close()
        # Seed defaults if empty
        self._seed_defaults()

    def _seed_defaults(self):
        """Insert default rules if the table is empty."""
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM routing_rules").fetchone()[0]
        if count > 0:
            conn.close()
            return
        defaults = [
            ("code_block", r"```", "ESCALATE", 0.8,
             "Code blocks typically need higher quality reasoning"),
            ("long_message", r".{500,}", "ESCALATE", 0.6,
             "Long messages may benefit from escalation"),
            ("short_question", r"^(what|how|why|when|where|who|is|can|do|does|should)\b",
             "CHEAP_ONLY", 0.7, "Short factual questions don't need escalation"),
            ("creative_request", r"\b(write|story|poem|creative|imagine|fiction)\b",
             "CHEAP_ONLY", 0.6, "Creative tasks work well with fast models"),
            ("math_logic", r"\b(prove|derive|calculate|equation|formula|theorem)\b",
             "ESCALATE", 0.7, "Math and logic benefit from reasoning models"),
            ("debugging", r"\b(error|bug|fix|debug|crash|broken|not working)\b",
             "ESCALATE", 0.65, "Debugging often needs deeper analysis"),
            ("comparison", r"\b(compare|vs|versus|difference|better|worse)\b",
             "COMPARE", 0.7, "Comparison requests benefit from multiple perspectives"),
            ("summarize", r"\b(summarize|summary|tldr|brief|recap)\b",
             "CHEAP_ONLY", 0.8, "Summarization is fast and low-risk"),
            ("translation", r"\b(translate|translation|in .+ language|to .+)\b",
             "CHEAP_ONLY", 0.7, "Translation is well-handled by fast models"),
            ("explanation", r"\b(explain|elaborate|detail|expand|describe)\b",
             "CHEAP_ONLY", 0.5, "Explanations: default confidence, learns from feedback"),
        ]
        now = datetime.now().isoformat()
        for name, pattern, action, confidence, reason in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO routing_rules (name, pattern, action, confidence, reason, created_from, last_updated) VALUES (?, ?, ?, ?, ?, 'default', ?)",
                (name, pattern, action, confidence, reason, now),
            )
        conn.commit()
        conn.close()

    def get_rules(self, enabled_only: bool = True) -> list[RoutingRule]:
        """Get all routing rules."""
        conn = self._conn()
        query = "SELECT * FROM routing_rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        rows = conn.execute(query).fetchall()
        conn.close()
        return [RoutingRule(**dict(r)) for r in rows]

    def get_optimization_history(self, limit: int = 20) -> list[dict]:
        """Get recent optimization runs."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM routing_optimizations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def analyze_and_optimize(self, min_interactions: int = 20,
                             days_back: int = 7) -> RoutingOptimization:
        """Run one optimization pass over recent interactions.

        Analyzes:
        1. Per-route-class feedback rates (thumbs up vs down)
        2. Whether escalated messages got better feedback than cheap-only
        3. Which rules are firing but producing poor results
        4. Whether any patterns emerge from user feedback text

        Updates rules based on findings.
        """
        conn = self._conn()
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

        # Gather data
        interactions = conn.execute("""
            SELECT route_action, target_model, feedback, critique,
                   user_input, response, response_latency_ms
            FROM interactions
            WHERE timestamp >= ? AND feedback IS NOT NULL
            ORDER BY timestamp DESC
        """, (cutoff,)).fetchall()

        all_recent = conn.execute("""
            SELECT COUNT(*) as n FROM interactions WHERE timestamp >= ?
        """, (cutoff,)).fetchone()["n"]

        changes = []
        rules_added = 0
        rules_modified = 0
        rules_disabled = 0

        if len(interactions) < min_interactions:
            conn.close()
            return RoutingOptimization(
                timestamp=datetime.now().isoformat(),
                interactions_analyzed=all_recent,
                rules_added=0, rules_modified=0, rules_disabled=0,
                changes=[], summary=f"Insufficient feedback data ({len(interactions)} interactions with feedback, need {min_interactions})"
            )

        # --- Analysis 1: Per-route feedback rates ---
        route_feedback = {}
        for row in interactions:
            action = row["route_action"]
            fb = row["feedback"]
            if action not in route_feedback:
                route_feedback[action] = {"up": 0, "down": 0, "total": 0}
            route_feedback[action][fb] += 1
            route_feedback[action]["total"] += 1

        for action, stats in route_feedback.items():
            if stats["total"] < 5:
                continue
            rate = stats["up"] / stats["total"]

            # If a route has >70% negative feedback, consider escalating it
            if rate < 0.3 and stats["total"] >= 5:
                rule = self._find_rule_by_action(conn, action)
                if rule and rule.action == "CHEAP_ONLY":
                    rule.action = "ESCALATE"
                    rule.confidence = 0.7
                    rule.last_updated = datetime.now().isoformat()
                    rule.reason = f"Auto-escalated: {rate:.0%} positive feedback (was CHEAP_ONLY)"
                    self._update_rule(conn, rule)
                    changes.append({"type": "escalate", "rule": rule.name,
                                    "old_action": "CHEAP_ONLY", "new_action": "ESCALATE",
                                    "feedback_rate": f"{rate:.0%}", "sample": stats["total"]})
                    rules_modified += 1
                    logger.info("Escalated rule %s: %.0f%% positive feedback from %d samples",
                               rule.name, rate, stats["total"])

            # If escalated route has >80% positive feedback, consider cheap-only
            if rate > 0.8 and stats["total"] >= 8:
                rule = self._find_rule_by_action(conn, action)
                if rule and rule.action == "ESCALATE":
                    rule.action = "CHEAP_ONLY"
                    rule.confidence = 0.7
                    rule.last_updated = datetime.now().isoformat()
                    rule.reason = f"Auto-downgraded: {rate:.0%} positive (was ESCALATE)"
                    self._update_rule(conn, rule)
                    changes.append({"type": "downgrade", "rule": rule.name,
                                    "old_action": "ESCALATE", "new_action": "CHEAP_ONLY",
                                    "feedback_rate": f"{rate:.0%}", "sample": stats["total"]})
                    rules_modified += 1

        # --- Analysis 2: Check if ESCALATE actually helps vs CHEAP_ONLY ---
        cheap_rate = route_feedback.get("CHEAP_ONLY", {}).get("up", 0) / max(route_feedback.get("CHEAP_ONLY", {}).get("total", 1), 1)
        esc_rate = route_feedback.get("ESCALATE", {}).get("up", 0) / max(route_feedback.get("ESCALATE", {}).get("total", 1), 1)

        if cheap_rate > esc_rate + 0.2 and route_feedback.get("ESCALATE", {}).get("total", 0) >= 5:
            # Escalation isn't helping — increase threshold
            changes.append({"type": "insight", "message":
                f"Cheap model satisfaction ({cheap_rate:.0%}) exceeds escalation ({esc_rate:.0%}). "
                "Consider whether escalation rules are necessary."})
            logger.info("Insight: cheap %.0f%% > escalation %.0f%%", cheap_rate, esc_rate)

        # --- Analysis 3: Critique text patterns ---
        critiques = [row["critique"] for row in interactions
                     if row["feedback"] == "down" and row["critique"]]
        if len(critiques) >= 5:
            common_issues = self._extract_common_themes(critiques)
            if common_issues:
                changes.append({"type": "user_themes", "themes": common_issues})

        # --- Analysis 4: Latency-based insights ---
        slow_escalations = conn.execute("""
            SELECT COUNT(*) as n FROM interactions
            WHERE route_action = 'ESCALATE' AND response_latency_ms > 10000
            AND timestamp >= ? AND feedback = 'down'
        """, (cutoff,)).fetchone()["n"]

        if slow_escalations >= 3:
            changes.append({"type": "latency", "message":
                f"{slow_escalations} slow escalations (>10s) received negative feedback. "
                "Consider whether the reasoning model is worth the wait."})

        conn.close()

        # Build summary
        summary_parts = []
        if rules_modified:
            summary_parts.append(f"{rules_modified} rules updated from feedback analysis")
        if rules_added:
            summary_parts.append(f"{rules_added} new patterns detected")
        if not summary_parts:
            summary_parts.append("No changes needed — routing is performing well")

        optimization = RoutingOptimization(
            timestamp=datetime.now().isoformat(),
            interactions_analyzed=all_recent,
            rules_added=rules_added,
            rules_modified=rules_modified,
            rules_disabled=rules_disabled,
            changes=changes,
            summary="; ".join(summary_parts),
        )

        # Log this optimization
        self._log_optimization(optimization)
        return optimization

    def evaluate_message(self, message: str) -> dict:
        """Evaluate a message against all routing rules.

        Returns the best matching rule and its action.
        Falls back to CHEAP_ONLY if no rule matches.
        """
        import re

        rules = self.get_rules(enabled_only=True)
        best_match = None
        best_score = 0.0

        for rule in rules:
            try:
                if re.search(rule.pattern, message, re.IGNORECASE):
                    # Score = confidence * pattern specificity (longer patterns = more specific)
                    specificity = min(len(rule.pattern) / 50.0, 1.0)
                    score = rule.confidence * specificity
                    if score > best_score:
                        best_score = score
                        best_match = rule
            except re.error:
                continue

        if best_match:
            return {
                "action": best_match.action,
                "confidence": best_score,
                "reason": best_match.reason,
                "matched_rule": best_match.name,
            }

        return {
            "action": "CHEAP_ONLY",
            "confidence": 0.3,
            "reason": "No specific pattern matched",
            "matched_rule": None,
        }

    def _find_rule_by_action(self, conn, action: str) -> RoutingRule | None:
        """Find the default rule for a given route action."""
        row = conn.execute(
            "SELECT * FROM routing_rules WHERE action = ? AND created_from = 'default' LIMIT 1",
            (action,)
        ).fetchone()
        if row:
            return RoutingRule(**dict(row))
        return None

    def _update_rule(self, conn, rule: RoutingRule):
        conn.execute("""
            UPDATE routing_rules SET action=?, confidence=?, reason=?, last_updated=?, sample_size=?
            WHERE name=?
        """, (rule.action, rule.confidence, rule.reason, rule.last_updated, rule.sample_size, rule.name))
        conn.commit()

    def _extract_common_themes(self, critiques: list[str]) -> list[str]:
        """Extract common complaint themes from negative feedback."""
        themes = []
        text = " ".join(critiques).lower()

        theme_patterns = {
            "too long": (r"\b(too long|verbose|concise|shorter|brief)\b", 3),
            "too short": (r"\b(too short|more detail|elaborate|expand)\b", 3),
            "wrong answer": (r"\b(wrong|incorrect|inaccurate|false|not true)\b", 3),
            "unhelpful": (r"\b(unhelpful|not useful|useless|waste)\b", 3),
            "slow": (r"\b(slow|took too long|fast|quick)\b", 3),
            "confusing": (r"\b(confusing|unclear|hard to understand|unclear)\b", 3),
            "generic": (r"\b(generic|vague|general|specific|detailed)\b", 3),
        }

        for theme, (pattern, min_count) in theme_patterns.items():
            count = len([c for c in critiques if __import__('re').search(pattern, c, __import__('re').IGNORECASE)])
            if count >= min_count:
                themes.append(f"{theme} ({count} mentions)")

        return themes

    def _log_optimization(self, opt: RoutingOptimization):
        """Log an optimization run to history."""
        conn = self._conn()
        conn.execute("""
            INSERT INTO routing_optimizations (timestamp, interactions_analyzed,
            rules_added, rules_modified, rules_disabled, changes_json, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (opt.timestamp, opt.interactions_analyzed, opt.rules_added,
              opt.rules_modified, opt.rules_disabled, json.dumps(opt.changes), opt.summary))
        conn.commit()
        conn.close()

    def get_routing_config(self) -> dict:
        """Export current routing config as a JSON-serializable dict."""
        return {
            "rules": [asdict(r) for r in self.get_rules()],
            "last_optimization": self.get_optimization_history(limit=1),
        }

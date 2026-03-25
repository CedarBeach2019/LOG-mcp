"""Routing updater — generates optimized routing rules from stats."""

from __future__ import annotations
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from vault.stats_collector import RoutingStats, StatsCollector, MIN_THRESHOLD

logger = logging.getLogger(__name__)

ROUTING_SCRIPT_PATH = Path(__file__).parent / "routing_script.py"
BACKUP_DIR = Path(__file__).parent / "routing_backups"
MAX_BACKUPS = 10


@dataclass
class RoutingSuggestion:
    """A proposed routing rule change."""
    type: str  # "add_pattern" | "remove_pattern" | "change_default"
    pattern: str
    target: str  # "ESCALATE" | "CHEAP_ONLY" | etc.
    confidence: float
    reason: str
    current_feedback_rate: float

    def to_dict(self) -> dict:
        return asdict(self)


class RoutingUpdater:
    """Generates optimized routing rules from stats."""

    def __init__(self, db_path: str | Path | sqlite3.Connection):
        if isinstance(db_path, sqlite3.Connection):
            self._conn = db_path
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(str(db_path))
            self._conn.row_factory = sqlite3.Row
            self._owns_conn = True
        self._init_routing_updates_table()

    def close(self):
        if self._owns_conn and self._conn:
            self._conn.close()

    def _init_routing_updates_table(self):
        """Create routing_updates table if not exists."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS routing_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applied_at TEXT DEFAULT (datetime('now')),
                suggestions_json TEXT NOT NULL,
                status TEXT DEFAULT 'suggested',  -- suggested | applied | reverted
                summary TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    def suggest_updates(self, stats: RoutingStats) -> list[RoutingSuggestion]:
        """Analyze stats and suggest routing rule changes."""
        suggestions: list[RoutingSuggestion] = []

        # 1. Cheap wins with high feedback rate → good cheap patterns are working
        #    If cheap model gets 👍 for things that currently ESCALATE, suggest tighter cheap patterns
        for cls, data in stats.per_route_class.items():
            total = data.get("total_requests", 0)
            if total < MIN_THRESHOLD:
                continue

            score = data.get("avg_feedback_score", 0)
            latency = data.get("avg_latency_ms", 0)

            if cls == "CHEAP_ONLY" and score >= 0.8:
                # Cheap is doing great — don't change, but note it
                pass  # No suggestion needed, system is working

            if cls == "ESCALATE" and score < 0.5 and total >= MIN_THRESHOLD * 2:
                # Escalation isn't helping — some of these might be cheap-worthy
                suggestions.append(RoutingSuggestion(
                    type="change_default",
                    pattern="*",
                    target="CHEAP_ONLY",
                    confidence=round(max(0, (0.5 - score) * 2), 4),
                    reason=f"ESCALATE class has low feedback ({score:.0%}) with {total} requests — "
                           f"consider routing more to CHEAP_ONLY",
                    current_feedback_rate=score,
                ))

        # 2. Top patterns from cheap_wins → validate existing CHEAP_ONLY patterns work
        for pw in stats.cheap_wins:
            if pw["feedback_rate"] >= 0.85:
                # Already working great, no change needed
                pass

        # 3. Top patterns that got 👍 on cheap but might be better escalated
        for tp in stats.top_patterns:
            if tp["feedback_rate"] >= 0.9 and tp["total"] >= MIN_THRESHOLD * 2:
                # Very high satisfaction on default/cheap routing for these patterns
                # Suggest adding as explicit CHEAP_ONLY pattern if not already
                pattern_text = self._extract_regex_hint(tp["pattern_key"])
                if pattern_text:
                    suggestions.append(RoutingSuggestion(
                        type="add_pattern",
                        pattern=pattern_text,
                        target="CHEAP_ONLY",
                        confidence=round(tp["feedback_rate"] * 0.8, 4),
                        reason=f"Pattern '{tp['pattern_key'][:30]}' has {tp['feedback_rate']:.0%} "
                               f"feedback ({tp['total']} samples) — add to CHEAP_ONLY",
                        current_feedback_rate=tp["feedback_rate"],
                    ))

        # 4. Per-model: if cheap model outperforms escalation on specific routes
        for model, data in stats.per_model.items():
            total = data.get("total_calls", 0)
            if total < MIN_THRESHOLD:
                continue

        # 5. Draft profiles with high win rates → suggest as default for specific patterns
        for profile, data in stats.per_profile_stats(stats).items() if False else []:
            pass

        # Deduplicate suggestions by (type, pattern, target)
        seen = set()
        unique = []
        for s in suggestions:
            key = (s.type, s.pattern, s.target)
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return unique

    # ------------------------------------------------------------------
    def _extract_regex_hint(self, pattern_key: str) -> str | None:
        """Try to extract a meaningful regex hint from a pattern key.
        
        Simple heuristic: take the first few words that look like keywords.
        Returns None if can't make a reasonable pattern.
        """
        # Remove common stop words and extract keywords
        text = pattern_key.strip().lower()
        words = re.findall(r'[a-z]+', text)
        
        # Filter out very short words
        keywords = [w for w in words if len(w) >= 3]
        if len(keywords) < 1:
            return None
        
        # Take first 2-3 meaningful words as a regex
        pattern = r'\s+'.join(re.escape(k) for k in keywords[:3])
        return pattern

    # ------------------------------------------------------------------
    def generate_routing_script(self, suggestions: list[RoutingSuggestion]) -> str:
        """Generate updated routing_script.py content with suggestions applied."""
        if not ROUTING_SCRIPT_PATH.exists():
            logger.warning("routing_script.py not found, returning empty")
            return ""

        current = ROUTING_SCRIPT_PATH.read_text()

        # For add_pattern to CHEAP_ONLY
        for s in suggestions:
            if s.type == "add_pattern" and s.target == "CHEAP_ONLY":
                # Find the CHEAP_ONLY patterns list and append
                pattern = f'            r"{s.pattern}",'
                # Insert before the closing bracket of CHEAP_ONLY patterns
                marker = '        ],\n        "action": "CHEAP_ONLY"'
                if marker in current and pattern not in current:
                    current = current.replace(marker, f'{pattern}\n{marker}')

            elif s.type == "add_pattern" and s.target == "ESCALATE":
                pattern = f'            r"{s.pattern}",'
                marker = '        ],\n        "action": "ESCALATE"'
                if marker in current and pattern not in current:
                    current = current.replace(marker, f'{pattern}\n{marker}')

        return current

    # ------------------------------------------------------------------
    def dry_run(self, stats: RoutingStats) -> dict:
        """Return proposed changes without applying them."""
        suggestions = self.suggest_updates(stats)
        generated = self.generate_routing_script(suggestions) if suggestions else ""

        return {
            "status": "dry_run",
            "suggestions": [s.to_dict() for s in suggestions],
            "generated_script_preview": generated[:2000] + ("..." if len(generated) > 2000 else ""),
            "total_suggestions": len(suggestions),
        }

    # ------------------------------------------------------------------
    def apply_updates(self, suggestions: list[RoutingSuggestion]) -> bool:
        """Actually update the routing script. Returns success."""
        if not suggestions:
            logger.info("No suggestions to apply")
            return False

        if not ROUTING_SCRIPT_PATH.exists():
            logger.error("routing_script.py not found")
            return False

        # Create backup
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"routing_script_{timestamp}.py"
        shutil.copy2(ROUTING_SCRIPT_PATH, backup_path)

        # Clean old backups
        backups = sorted(BACKUP_DIR.glob("routing_script_*.py"))
        while len(backups) > MAX_BACKUPS:
            backups[0].unlink()
            backups = backups[1:]

        # Generate new content
        new_content = self.generate_routing_script(suggestions)
        if not new_content:
            return False

        # Write
        try:
            ROUTING_SCRIPT_PATH.write_text(new_content)
            # Log to database
            self._log_update(suggestions, "applied")
            logger.info(f"Applied {len(suggestions)} routing updates, backed up to {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply routing updates: {e}")
            # Restore from backup
            shutil.copy2(backup_path, ROUTING_SCRIPT_PATH)
            return False

    # ------------------------------------------------------------------
    def _log_update(self, suggestions: list[RoutingSuggestion], status: str):
        """Log suggestions to the routing_updates table."""
        summary = f"{len(suggestions)} suggestions"
        self._conn.execute(
            "INSERT INTO routing_updates (suggestions_json, status, summary) VALUES (?, ?, ?)",
            (json.dumps([s.to_dict() for s in suggestions]), status, summary)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    def get_history(self, limit: int = 20) -> list[dict]:
        """Return history of routing updates."""
        rows = self._conn.execute(
            "SELECT * FROM routing_updates ORDER BY applied_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [
            {
                "id": r["id"],
                "applied_at": r["applied_at"],
                "status": r["status"],
                "summary": r["summary"],
                "suggestions": json.loads(r["suggestions_json"]),
            }
            for r in rows
        ]

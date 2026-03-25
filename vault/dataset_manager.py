"""
Dataset lifecycle management — deduplication, diversity sampling, splitting, versioning.

All operations work on SQLite data with no external dependencies.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vault.dataset_quality import (
    QualityScore, classify_domain, score_ranking, score_rankings,
    canonicalize_prompt, _canonicalize_prompt, filter_by_quality,
)

logger = logging.getLogger("vault.dataset_manager")

# Known domains for diversity tracking
ALL_DOMAINS = ["code", "math", "creative", "writing", "factual", "general"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_interactions(interactions: list[dict[str, Any]],
                             keep_strategy: str = "best_quality") -> list[dict[str, Any]]:
    """Remove duplicate prompts, keeping the best interaction per canonical prompt.

    Args:
        interactions: list of interaction dicts
        keep_strategy: "best_quality" (score and keep highest), "most_recent" (by timestamp)
    """
    scores = score_rankings(interactions)

    groups: dict[str, list[tuple[dict, QualityScore]]] = {}
    for interaction, score in zip(interactions, scores):
        canonical = _canonicalize_prompt(interaction.get("user_input", ""))
        groups.setdefault(canonical, []).append((interaction, score))

    result = []
    removed = 0
    for canonical, group in groups.items():
        if len(group) == 1:
            result.append(group[0][0])
        else:
            if keep_strategy == "most_recent":
                best = max(group, key=lambda x: x[0].get("created_at", "") or "")
            else:  # best_quality
                best = max(group, key=lambda x: x[1].composite)
            result.append(best[0])
            removed += len(group) - 1

    if removed:
        logger.info("Deduplicated: kept %d, removed %d duplicates", len(result), removed)
    return result


def deduplicate_db(db_path: str, days_back: int = 90) -> dict:
    """Run deduplication on the database. Marks (doesn't delete) duplicates.

    Returns summary of what was found.
    """
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

    rows = conn.execute("""
        SELECT id, user_input, created_at FROM interactions
        WHERE created_at >= ? AND feedback IS NOT NULL
        ORDER BY created_at DESC
    """, (cutoff,)).fetchall()
    conn.close()

    interactions = [dict(r) for r in rows]
    before = len(interactions)
    after = deduplicate_interactions(interactions)
    return {
        "before": before,
        "after": len(after),
        "removed": before - len(after),
    }


# ---------------------------------------------------------------------------
# Diversity sampling
# ---------------------------------------------------------------------------

def diversity_sample(interactions: list[dict[str, Any]],
                     max_per_domain: int | None = None,
                     target_total: int | None = None) -> list[dict[str, Any]]:
    """Sample interactions to ensure domain diversity.

    If max_per_domain is set, caps each domain at that many.
    If target_total is set, aims for roughly equal distribution across domains.
    """
    # Classify
    classified: dict[str, list[dict]] = {d: [] for d in ALL_DOMAINS}
    for interaction in interactions:
        domain = classify_domain(interaction.get("user_input", ""))
        classified.setdefault(domain, []).append(interaction)

    # Sort each domain by quality (composite score descending)
    for domain in classified:
        scores = score_rankings(classified[domain])
        paired = sorted(zip(classified[domain], scores),
                        key=lambda x: x[1].composite, reverse=True)
        classified[domain] = [p[0] for p in paired]

    if max_per_domain is not None:
        for domain in classified:
            classified[domain] = classified[domain][:max_per_domain]

    if target_total is not None:
        active = {d: items for d, items in classified.items() if items}
        per_domain = max(target_total // max(len(active), 1), 1)
        for domain in active:
            classified[domain] = classified[domain][:per_domain]

    result = []
    for domain in ALL_DOMAINS:
        result.extend(classified.get(domain, []))

    return result


# ---------------------------------------------------------------------------
# Split generation
# ---------------------------------------------------------------------------

def generate_splits(interactions: list[dict[str, Any]],
                    val_ratio: float = 0.15,
                    test_ratio: float = 0.10,
                    seed: int = 42) -> dict[str, list[dict[str, Any]]]:
    """Split interactions into train/val/test with stratified domain sampling.

    Uses deterministic shuffling via a simple hash-based approach.
    """
    # Stratify by domain
    by_domain: dict[str, list[dict]] = {}
    for interaction in interactions:
        domain = classify_domain(interaction.get("user_input", ""))
        by_domain.setdefault(domain, []).append(interaction)

    train, val, test = [], [], []

    for domain, items in by_domain.items():
        # Deterministic sort by (id % seed bucket, id) for reproducibility
        items.sort(key=lambda x: (x.get("id", 0) % seed, x.get("id", 0)))

        n = len(items)
        n_val = max(1, int(n * val_ratio)) if n > 0 else 0
        n_test = max(1, int(n * test_ratio)) if n > n_val else 0

        domain_val = items[:n_val]
        domain_test = items[n_val:n_val + n_test]
        domain_train = items[n_val + n_test:]

        val.extend(domain_val)
        test.extend(domain_test)
        train.extend(domain_train)

    return {"train": train, "val": val, "test": test}


# ---------------------------------------------------------------------------
# Version tracking
# ---------------------------------------------------------------------------

class DatasetVersion:
    """Track dataset versions with metadata."""

    def __init__(self, versions_dir: str | Path):
        self.versions_dir = Path(versions_dir)
        self.versions_dir.mkdir(parents=True, exist_ok=True)

    def create_version(self, interactions: list[dict[str, Any]],
                       metadata: dict[str, Any] | None = None) -> str:
        """Create a new versioned dataset snapshot.

        Returns version string like "v1", "v2", etc.
        """
        version_num = self._next_version_num()
        version_id = f"v{version_num}"
        version_dir = self.versions_dir / version_id
        version_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "version": version_id,
            "created_at": datetime.now().isoformat(),
            "total_examples": len(interactions),
            **(metadata or {}),
        }

        # Score and save stats
        scores = score_rankings(interactions)
        meta["avg_quality"] = round(
            sum(s.composite for s in scores) / max(len(scores), 1), 3
        )
        meta["low_effort_count"] = sum(1 for s in scores if s.is_low_effort)
        meta["domains"] = {}
        for s in scores:
            meta["domains"][s.domain] = meta["domains"].get(s.domain, 0) + 1

        # Save interactions
        with open(version_dir / "interactions.json", "w") as f:
            json.dump(interactions, f, ensure_ascii=False, indent=2)

        # Save metadata
        with open(version_dir / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        logger.info("Created dataset %s with %d interactions", version_id, len(interactions))
        return version_id

    def _next_version_num(self) -> int:
        existing = [d.name for d in self.versions_dir.iterdir()
                    if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()]
        if not existing:
            return 1
        return max(int(d[1:]) for d in existing) + 1

    def list_versions(self) -> list[dict]:
        versions = []
        for d in sorted(self.versions_dir.iterdir()):
            if not d.is_dir() or not d.name.startswith("v"):
                continue
            meta_file = d / "metadata.json"
            if meta_file.exists():
                with open(meta_file) as f:
                    versions.append(json.load(f))
            else:
                versions.append({"version": d.name, "total_examples": None})
        return versions

    def load_version(self, version_id: str) -> list[dict[str, Any]]:
        path = self.versions_dir / version_id / "interactions.json"
        if not path.exists():
            raise FileNotFoundError(f"Version {version_id} not found")
        with open(path) as f:
            return json.load(f)

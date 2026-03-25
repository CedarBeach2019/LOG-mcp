"""
Training pipeline — export draft ranking data as LoRA fine-tuning dataset.

Core insight: The draft round (multiple models respond, user picks best) generates
the highest-quality comparative data available anywhere. This pipeline exports that
data in formats suitable for fine-tuning with llama-cpp-python or axolotl.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("vault.training")


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def extract_ranking_data(db_path: str, days_back: int = 30) -> list[dict]:
    """Extract draft ranking data from the database.

    Returns list of dicts with:
    - user_input: original user message
    - winner_model: the model the user selected as best
    - winner_response: the winning response text
    - loser_models: list of models that weren't selected
    - loser_responses: list of non-winning response texts
    - reasoning: user's reasoning for selection (if provided)
    - timestamp: when the interaction happened
    - ranked_models: ordered list of models from best to worst (if multiple ranks)
    """
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

    # Get all interactions that have draft/ranking feedback
    rows = conn.execute("""
        SELECT i.id, i.session_id, i.user_input, i.route_action, i.target_model,
               i.response, i.feedback, i.critique, i.created_at
        FROM interactions i
        WHERE i.created_at >= ?
          AND i.route_action = 'draft'
          AND i.feedback IS NOT NULL
        ORDER BY i.created_at DESC
    """, (cutoff,)).fetchall()

    results = []
    for row in rows:
        result = {
            "id": row["id"],
            "user_input": row["user_input"],
            "winner_model": row["target_model"],
            "winner_response": row["response"],
            "feedback": row["feedback"],
            "reasoning": row["critique"] or "",
            "timestamp": row["created_at"],
            "loser_models": [],
            "loser_responses": [],
            "ranked_models": [],
        }
        results.append(result)

    conn.close()
    logger.info("Extracted %d ranking interactions from last %d days", len(results), days_back)
    return results


def extract_feedback_data(db_path: str, days_back: int = 30) -> list[dict]:
    """Extract all feedback data (thumbs up/down) for training signal.

    Returns list of dicts with:
    - user_input, response, model, feedback (up/down), critique
    """
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

    rows = conn.execute("""
        SELECT user_input, response, target_model, feedback, critique, created_at
        FROM interactions
        WHERE created_at >= ? AND feedback IS NOT NULL
        ORDER BY created_at DESC
    """, (cutoff,)).fetchall()

    results = [dict(r) for r in rows]
    conn.close()
    logger.info("Extracted %d feedback interactions", len(results))
    return results


# ---------------------------------------------------------------------------
# Export formats
# ---------------------------------------------------------------------------

def export_for_lora(rankings: list[dict], output_path: str | Path,
                    system_prompt: str | None = None) -> int:
    """Export ranking data as JSONL for LoRA fine-tuning.

    Format: one JSON object per line.
    Each object is a training example where the winning response is the target.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    system = system_prompt or "You are a helpful AI assistant. Provide clear, accurate, and well-structured responses."
    count = 0

    with open(output_path, "w", encoding="utf-8") as f:
        for r in rankings:
            if not r["winner_response"] or not r["user_input"]:
                continue
            example = {
                "instruction": r["user_input"],
                "input": "",
                "output": r["winner_response"],
                "system": system,
                "metadata": {
                    "model": r["winner_model"],
                    "feedback": r["feedback"],
                    "reasoning": r["reasoning"],
                    "source": "draft_ranking",
                },
            }
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
            count += 1

    logger.info("Exported %d LoRA training examples to %s", count, output_path)
    return count


def export_for_dpo(rankings: list[dict], output_path: str | Path) -> int:
    """Export as DPO (Direct Preference Optimization) pairs.

    Format: chosen (winner) vs rejected (loser) response pairs.
    Requires at least one losing response per ranking.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with open(output_path, "w", encoding="utf-8") as f:
        for r in rankings:
            if not r["loser_responses"] or not r["winner_response"]:
                continue
            for i, loser_resp in enumerate(r["loser_responses"]):
                pair = {
                    "prompt": r["user_input"],
                    "chosen": r["winner_response"],
                    "rejected": loser_resp,
                    "metadata": {
                        "chosen_model": r["winner_model"],
                        "rejected_model": r["loser_models"][i] if i < len(r["loser_models"]) else "unknown",
                        "reasoning": r["reasoning"],
                    },
                }
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                count += 1

    logger.info("Exported %d DPO preference pairs to %s", count, output_path)
    return count


def export_for_analysis(feedback: list[dict], output_path: str | Path) -> int:
    """Export feedback data as CSV for analysis.

    Columns: timestamp, model, feedback, user_input_length, response_length, has_critique
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "model", "feedback", "has_critique",
            "input_length", "response_length",
        ])
        for row in feedback:
            writer.writerow([
                row.get("created_at", ""),
                row.get("target_model", ""),
                row.get("feedback", ""),
                "yes" if row.get("critique") else "no",
                len(row.get("user_input", "")),
                len(row.get("response", "")),
            ])

    logger.info("Exported %d feedback rows to %s", len(feedback), output_path)
    return len(feedback)


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------

def filter_quality(data: list[dict], min_response_length: int = 50,
                   min_input_length: int = 5) -> list[dict]:
    """Filter out low-quality training examples."""
    filtered = [
        r for r in data
        if len(r.get("winner_response", "")) >= min_response_length
        and len(r.get("user_input", "")) >= min_input_length
    ]
    removed = len(data) - len(filtered)
    if removed:
        logger.info("Filtered out %d low-quality examples (min response: %d, min input: %d)",
                    removed, min_response_length, min_input_length)
    return filtered


def deduplicate(data: list[dict]) -> list[dict]:
    """Remove duplicate user inputs, keeping the most recent."""
    seen = {}
    for r in data:
        key = r.get("user_input", "").strip().lower()
        if key and (key not in seen or r.get("timestamp", "") > seen[key].get("timestamp", "")):
            seen[key] = r
    removed = len(data) - len(seen)
    if removed:
        logger.info("Deduplicated: removed %d duplicates", removed)
    return list(seen.values())


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def run_export_pipeline(db_path: str, output_dir: str | Path,
                        days_back: int = 30, min_examples: int = 5) -> dict:
    """Run the full export pipeline.

    1. Extract ranking + feedback data
    2. Filter quality
    3. Deduplicate
    4. Export in all formats
    5. Return summary
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract
    rankings = extract_ranking_data(db_path, days_back)
    feedback = extract_feedback_data(db_path, days_back)

    # Process
    rankings = filter_quality(rankings)
    rankings = deduplicate(rankings)

    # Export
    lora_count = export_for_lora(rankings, output_dir / "lora_train.jsonl")
    dpo_count = export_for_dpo(rankings, output_dir / "dpo_pairs.jsonl")
    csv_count = export_for_analysis(feedback, output_dir / "feedback_analysis.csv")

    summary = {
        "exported_at": datetime.now().isoformat(),
        "period_days": days_back,
        "rankings": len(rankings),
        "feedback_total": len(feedback),
        "lora_examples": lora_count,
        "dpo_pairs": dpo_count,
        "feedback_rows": csv_count,
        "output_dir": str(output_dir),
        "ready_for_training": lora_count >= min_examples,
        "min_examples_needed": min_examples,
    }

    # Save summary
    with open(output_dir / "export_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("Export pipeline complete: %d LoRA, %d DPO, %d CSV",
                lora_count, dpo_count, csv_count)
    return summary

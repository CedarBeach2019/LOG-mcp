"""Tests for training pipeline."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import json
import pytest
from pathlib import Path
from vault.training_pipeline import (
    extract_ranking_data,
    extract_feedback_data,
    export_for_lora,
    export_for_dpo,
    export_for_analysis,
    filter_quality,
    deduplicate,
    run_export_pipeline,
)


@pytest.fixture
def db(tmp_path):
    """Create a test DB with sample ranking and feedback data."""
    import sqlite3
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            user_input TEXT NOT NULL DEFAULT '',
            route_action TEXT NOT NULL DEFAULT '',
            target_model TEXT NOT NULL DEFAULT '',
            response TEXT NOT NULL DEFAULT '',
            feedback TEXT DEFAULT NULL,
            critique TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO interactions (user_input, route_action, target_model, response, feedback, critique) VALUES
        ('What is Python?', 'draft', 'deepseek-chat', 'Python is a programming language.', 'up', 'Clear and concise'),
        ('Explain quantum entanglement', 'draft', 'deepseek-reasoner', 'Quantum entanglement is a phenomenon...', 'up', ''),
        ('Write a poem', 'cheap', 'deepseek-chat', 'Roses are red...', 'down', 'Too generic'),
        ('Hello', 'cheap', 'deepseek-chat', 'Hi there!', NULL, NULL),
        ('Debug this code', 'escalation', 'deepseek-reasoner', 'The issue is a null pointer...', 'up', 'Spot on');
    """)
    conn.commit()
    conn.close()
    return db_path


class TestExtractRankingData:
    def test_extracts_draft_interactions(self, db):
        data = extract_ranking_data(db, days_back=90)
        assert len(data) == 2  # only draft interactions with feedback

    def test_includes_feedback(self, db):
        data = extract_ranking_data(db, days_back=90)
        assert all(r["feedback"] for r in data)

    def test_empty_for_no_data(self, tmp_path):
        import sqlite3
        empty_db = str(tmp_path / "empty.db")
        conn = sqlite3.connect(empty_db)
        conn.execute("CREATE TABLE interactions (id INTEGER PRIMARY KEY, session_id TEXT, user_input TEXT, route_action TEXT, target_model TEXT, response TEXT, feedback TEXT, critique TEXT, created_at TEXT)")
        conn.close()
        data = extract_ranking_data(empty_db, days_back=90)
        assert data == []


class TestExtractFeedbackData:
    def test_extracts_all_feedback(self, db):
        data = extract_feedback_data(db, days_back=90)
        assert len(data) == 4  # all with feedback

    def test_excludes_null_feedback(self, db):
        data = extract_feedback_data(db, days_back=90)
        assert all(r["feedback"] for r in data)


class TestExportForLora:
    def test_exports_jsonl(self, db, tmp_path):
        rankings = extract_ranking_data(db)
        path = tmp_path / "train.jsonl"
        count = export_for_lora(rankings, path)
        assert count == 2
        assert path.exists()

    def test_each_line_is_valid_json(self, db, tmp_path):
        rankings = extract_ranking_data(db)
        path = tmp_path / "train.jsonl"
        export_for_lora(rankings, path)
        lines = path.read_text().strip().split("\n")
        for line in lines:
            obj = json.loads(line)
            assert "instruction" in obj
            assert "output" in obj

    def test_custom_system_prompt(self, db, tmp_path):
        rankings = extract_ranking_data(db)
        path = tmp_path / "train.jsonl"
        export_for_lora(rankings, path, system_prompt="Custom prompt")
        obj = json.loads(path.read_text().strip().split("\n")[0])
        assert obj["system"] == "Custom prompt"


class TestExportForDpo:
    def test_exports_pairs(self, db, tmp_path):
        rankings = extract_ranking_data(db)
        rankings[0]["loser_responses"] = ["A mediocre response"]
        rankings[0]["loser_models"] = ["other-model"]
        path = tmp_path / "dpo.jsonl"
        count = export_for_dpo(rankings, path)
        assert count == 1

    def test_skips_no_losers(self, db, tmp_path):
        rankings = extract_ranking_data(db)
        path = tmp_path / "dpo.jsonl"
        count = export_for_dpo(rankings, path)
        assert count == 0


class TestExportForAnalysis:
    def test_exports_csv(self, db, tmp_path):
        feedback = extract_feedback_data(db)
        path = tmp_path / "analysis.csv"
        count = export_for_analysis(feedback, path)
        assert count == 4
        assert path.exists()


class TestFilterQuality:
    def test_removes_short_responses(self):
        data = [
            {"user_input": "hello", "winner_response": "hi"},
            {"user_input": "explain quantum physics", "winner_response": "A" * 100},
        ]
        result = filter_quality(data, min_response_length=50)
        assert len(result) == 1

    def test_removes_short_inputs(self):
        data = [
            {"user_input": "ok", "winner_response": "A" * 100},
            {"user_input": "What is the meaning of life?", "winner_response": "A" * 100},
        ]
        result = filter_quality(data, min_input_length=10)
        assert len(result) == 1


class TestDeduplicate:
    def test_removes_duplicate_inputs(self):
        data = [
            {"user_input": "What is Python?", "timestamp": "2026-01-01"},
            {"user_input": "What is Python?", "timestamp": "2026-01-02"},
            {"user_input": "What is Java?", "timestamp": "2026-01-01"},
        ]
        result = deduplicate(data)
        assert len(result) == 2
        # Keeps the most recent
        assert result[0]["timestamp"] == "2026-01-02"


class TestRunExportPipeline:
    def test_full_pipeline(self, db, tmp_path):
        summary = run_export_pipeline(db, tmp_path / "output", days_back=90, min_examples=0)
        assert summary["lora_examples"] >= 0  # quality filtering may pass some
        assert summary["feedback_rows"] == 4
        assert (Path(tmp_path) / "output" / "lora_train.jsonl").exists()
        assert (Path(tmp_path) / "output" / "feedback_analysis.csv").exists()
        assert (Path(tmp_path) / "output" / "export_summary.json").exists()

    def test_summary_includes_metadata(self, db, tmp_path):
        summary = run_export_pipeline(db, tmp_path / "output", days_back=90, min_examples=0)
        assert "exported_at" in summary
        assert "ready_for_training" in summary
        assert "period_days" in summary

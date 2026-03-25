"""Lightweight benchmark runner — latency and quality measurements for API models.

Stores results in SQLite for trend analysis. Designed for Jetson 8GB (no heavy
local inference — benchmarks call external APIs).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("vault.benchmark")

# Standardized prompts for quality benchmarking (small subsets)
QUALITY_PROMPTS = {
    "code": [
        {"prompt": "Write a Python function that checks if a string is a palindrome.", "keywords": ["def", "return", "str", "reverse", "=="]},
        {"prompt": "Write a Python function to flatten a nested list.", "keywords": ["def", "flatten", "list", "for", "isinstance"]},
        {"prompt": "Write a function that finds the two numbers in a list that add up to a target.", "keywords": ["def", "target", "sum", "dict", "hash"]},
    ],
    "chat": [
        {"prompt": "Explain quantum computing in 2-3 sentences for a 10 year old.", "keywords": ["qubit", "superposition", "quantum", "computer"]},
        {"prompt": "What are 3 benefits of regular exercise?", "keywords": ["health", "heart", "mental", "energy", "strength"]},
        {"prompt": "Summarize the plot of The Matrix in one sentence.", "keywords": ["neo", "matrix", "reality", "simulation", "machine"]},
    ],
    "reasoning": [
        {"prompt": "If it takes 5 machines 5 minutes to make 5 widgets, how long would it take 100 machines to make 100 widgets?", "keywords": ["5", "minute"]},
        {"prompt": "A bat and ball cost $1.10 total. The bat costs $1 more than the ball. How much does the ball cost?", "keywords": ["0.05", "5", "cent", "10"]},
        {"prompt": "If you have a 3-gallon jug and a 5-gallon jug, how can you measure exactly 4 gallons?", "keywords": ["fill", "pour", "4", "gallon"]},
    ],
}

# Latency benchmark prompt (short, to measure TTFT accurately)
LATENCY_PROMPT = {"role": "user", "content": "Say hello in one sentence."}


@dataclass
class LatencyResult:
    """Result from a latency benchmark run."""
    model_id: str
    time_to_first_token_ms: float
    total_time_ms: float
    output_tokens: int = 0
    error: str | None = None
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "time_to_first_token_ms": round(self.time_to_first_token_ms, 1),
            "total_time_ms": round(self.total_time_ms, 1),
            "output_tokens": self.output_tokens,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class QualityResult:
    """Result from a quality benchmark run."""
    model_id: str
    category: str
    total_prompts: int = 0
    matched_keywords: int = 0
    score: float = 0.0  # 0-1
    responses: list[dict] = field(default_factory=list)
    error: str | None = None
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "category": self.category,
            "total_prompts": self.total_prompts,
            "matched_keywords": self.matched_keywords,
            "score": round(self.score, 3),
            "error": self.error,
            "timestamp": self.timestamp,
        }


class BenchmarkDB:
    """SQLite storage for benchmark results."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        return self._conn

    def _migrate(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS benchmark_latency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                time_to_first_token_ms REAL,
                total_time_ms REAL,
                output_tokens INTEGER DEFAULT 0,
                error TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS benchmark_quality (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                category TEXT NOT NULL,
                score REAL,
                total_prompts INTEGER DEFAULT 0,
                matched_keywords INTEGER DEFAULT 0,
                responses_json TEXT,
                error TEXT,
                timestamp TEXT NOT NULL
            );
        """)

    def save_latency(self, result: LatencyResult):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO benchmark_latency (model_id, time_to_first_token_ms, total_time_ms, output_tokens, error, timestamp) VALUES (?,?,?,?,?,?)",
            (result.model_id, result.time_to_first_token_ms, result.total_time_ms, result.output_tokens, result.error, result.timestamp),
        )
        conn.commit()

    def save_quality(self, result: QualityResult):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO benchmark_quality (model_id, category, score, total_prompts, matched_keywords, responses_json, error, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (result.model_id, result.category, result.score, result.total_prompts, result.matched_keywords, json.dumps(result.responses), result.error, result.timestamp),
        )
        conn.commit()

    def get_latency_history(self, model_id: str, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM benchmark_latency WHERE model_id = ? ORDER BY timestamp DESC LIMIT ?",
            (model_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_quality_history(self, model_id: str, category: str | None = None, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        if category:
            rows = conn.execute(
                "SELECT * FROM benchmark_quality WHERE model_id = ? AND category = ? ORDER BY timestamp DESC LIMIT ?",
                (model_id, category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM benchmark_quality WHERE model_id = ? ORDER BY timestamp DESC LIMIT ?",
                (model_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


class BenchmarkRunner:
    """Run latency and quality benchmarks against API models."""

    def __init__(self, db_path: Path | str | None = None, api_key: str = "",
                 base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if db_path:
            self.db = BenchmarkDB(db_path)
        else:
            self.db = None

    def _score_response(self, response: str, keywords: list[str]) -> float:
        """Score a response based on keyword presence. Returns 0-1."""
        if not response or not keywords:
            return 0.0
        response_lower = response.lower()
        matched = sum(1 for kw in keywords if kw.lower() in response_lower)
        return matched / len(keywords)

    async def run_latency_benchmark(
        self, model_id: str, client: httpx.AsyncClient | None = None,
        max_tokens: int = 100,
    ) -> LatencyResult:
        """Measure TTFT and total generation time for a model."""
        close_client = client is None
        c = client or httpx.AsyncClient(timeout=60.0)
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            url = f"{self.base_url}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model_id,
                "messages": [LATENCY_PROMPT],
                "max_tokens": max_tokens,
                "stream": True,
            }

            t0 = time.monotonic()
            first_token_time = None
            full_text = ""
            output_tokens = 0

            async with c.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    return LatencyResult(
                        model_id=model_id, time_to_first_token_ms=0,
                        total_time_ms=0, error=f"HTTP {resp.status_code}: {body[:200]}",
                        timestamp=timestamp,
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            if first_token_time is None:
                                first_token_time = time.monotonic()
                            full_text += content
                            # Estimate tokens (rough: 4 chars per token)
                            output_tokens = len(full_text) // 4
                    except json.JSONDecodeError:
                        continue

            total_time = time.monotonic() - t0
            ttft = (first_token_time - t0) if first_token_time else total_time

            result = LatencyResult(
                model_id=model_id,
                time_to_first_token_ms=ttft * 1000,
                total_time_ms=total_time * 1000,
                output_tokens=output_tokens,
                timestamp=timestamp,
            )
            if self.db:
                self.db.save_latency(result)
            return result

        except Exception as exc:
            result = LatencyResult(
                model_id=model_id, time_to_first_token_ms=0,
                total_time_ms=0, error=str(exc)[:200], timestamp=timestamp,
            )
            if self.db:
                self.db.save_latency(result)
            return result
        finally:
            if close_client:
                await c.aclose()

    async def run_quality_benchmark(
        self, model_id: str, category: str = "code",
        client: httpx.AsyncClient | None = None,
        max_tokens: int = 512,
    ) -> QualityResult:
        """Run quality prompts and score keyword presence."""
        prompts = QUALITY_PROMPTS.get(category, QUALITY_PROMPTS["chat"])
        timestamp = datetime.now(timezone.utc).isoformat()

        close_client = client is None
        c = client or httpx.AsyncClient(timeout=120.0)

        try:
            url = f"{self.base_url}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

            total_matched = 0
            total_keywords = 0
            responses = []

            for prompt_info in prompts:
                payload = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt_info["prompt"]}],
                    "max_tokens": max_tokens,
                }
                try:
                    resp = await c.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        keywords = prompt_info["keywords"]
                        score = self._score_response(content, keywords)
                        total_matched += int(score * len(keywords))
                        total_keywords += len(keywords)
                        responses.append({"prompt": prompt_info["prompt"], "response": content[:200], "score": round(score, 2)})
                    else:
                        responses.append({"prompt": prompt_info["prompt"], "error": f"HTTP {resp.status_code}"})
                except Exception as exc:
                    responses.append({"prompt": prompt_info["prompt"], "error": str(exc)[:100]})

            overall_score = total_matched / total_keywords if total_keywords else 0
            result = QualityResult(
                model_id=model_id, category=category,
                total_prompts=len(prompts), matched_keywords=total_matched,
                score=overall_score, responses=responses, timestamp=timestamp,
            )
            if self.db:
                self.db.save_quality(result)
            return result

        except Exception as exc:
            result = QualityResult(
                model_id=model_id, category=category,
                error=str(exc)[:200], timestamp=timestamp,
            )
            if self.db:
                self.db.save_quality(result)
            return result
        finally:
            if close_client:
                await c.aclose()

    def get_history(self, model_id: str) -> dict:
        """Get all benchmark history for a model."""
        if not self.db:
            return {"model_id": model_id, "latency": [], "quality": []}
        return {
            "model_id": model_id,
            "latency": self.db.get_latency_history(model_id),
            "quality": self.db.get_quality_history(model_id),
        }

    def close(self):
        if self.db:
            self.db.close()

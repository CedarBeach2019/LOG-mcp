"""
Observability — structured logging, request tracing, and metrics.

Every request gets a trace ID. Logs include structured JSON for easy parsing.
Metrics are tracked in-memory and exposed via API.
"""

from __future__ import annotations

import logging
import time
import uuid
import json
from contextlib import contextmanager
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

logger = logging.getLogger("gateway.observability")


# ---------------------------------------------------------------------------
# Request tracing
# ---------------------------------------------------------------------------

_trace_var = {}


def start_trace(request_path: str, method: str = "POST") -> str:
    """Start a new request trace. Returns trace_id."""
    trace_id = uuid.uuid4().hex[:12]
    _trace_var["id"] = trace_id
    _trace_var["path"] = request_path
    _trace_var["method"] = method
    _trace_var["start"] = time.monotonic()
    _trace_var["spans"] = []
    logger.info("[%s] START %s %s", trace_id, method, request_path)
    return trace_id


def end_trace(trace_id: str, status_code: int, route_action: str = "",
              model: str = "", cached: bool = False):
    """End a request trace and log summary."""
    elapsed = (time.monotonic() - _trace_var.get("start", time.monotonic())) * 1000
    spans = _trace_var.get("spans", [])

    logger.info("[%s] END %s → %d (%.0fms) route=%s model=%s cached=%s spans=%d",
                trace_id, _trace_var.get("path", "?"), status_code, elapsed,
                route_action, model, cached, len(spans))

    # Record in metrics
    MetricsCollector.record_request(
        path=_trace_var.get("path", ""),
        status_code=status_code,
        latency_ms=elapsed,
        route_action=route_action,
        model=model,
        cached=cached,
        spans=spans,
    )

    _trace_var.clear()


def add_span(name: str, **kwargs):
    """Add a timing span to the current trace."""
    span = {
        "name": name,
        "timestamp": datetime.now().isoformat(),
        **kwargs,
    }
    if "spans" not in _trace_var:
        _trace_var["spans"] = []
    _trace_var["spans"].append(span)


@contextmanager
def trace_span(name: str, **kwargs):
    """Context manager for timing a code block."""
    t0 = time.monotonic()
    try:
        yield
    finally:
        ms = (time.monotonic() - t0) * 1000
        add_span(name, duration_ms=round(ms, 1), **kwargs)


def get_trace_id() -> str:
    """Get the current trace ID (or empty string)."""
    return _trace_var.get("id", "")


# ---------------------------------------------------------------------------
# Metrics collector (in-memory)
# ---------------------------------------------------------------------------

@dataclass
class MetricsCollector:
    """In-memory metrics for the current server session."""

    _requests: list = field(default_factory=list, repr=False)
    _max_requests: int = 1000  # keep last N requests

    @classmethod
    def record_request(cls, path: str, status_code: int, latency_ms: float,
                       route_action: str = "", model: str = "",
                       cached: bool = False, spans: list | None = None):
        """Record a completed request."""
        cls._requests.append({
            "timestamp": datetime.now().isoformat(),
            "path": path,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 1),
            "route_action": route_action,
            "model": model,
            "cached": cached,
            "spans": spans or [],
        })
        # Trim to max size
        if len(cls._requests) > cls._max_requests:
            cls._requests = cls._requests[-cls._max_requests:]

    @classmethod
    def get_summary(cls, minutes: int = 60) -> dict:
        """Get metrics summary for the last N minutes."""
        cutoff = (datetime.now() - __import__('datetime').timedelta(minutes=minutes)).isoformat()
        recent = [r for r in cls._requests if r["timestamp"] >= cutoff]

        if not recent:
            return {"total_requests": 0, "period_minutes": minutes}

        total = len(recent)
        latencies = [r["latency_ms"] for r in recent]
        statuses = defaultdict(int)
        routes = defaultdict(int)
        models = defaultdict(int)
        cache_hits = sum(1 for r in recent if r["cached"])
        errors = sum(1 for r in recent if r["status_code"] >= 400)

        for r in recent:
            statuses[r["status_code"]] += 1
            if r["route_action"]:
                routes[r["route_action"]] += 1
            if r["model"]:
                models[r["model"]] += 1

        return {
            "total_requests": total,
            "period_minutes": minutes,
            "latency_ms": {
                "p50": sorted(latencies)[len(latencies) // 2],
                "p95": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 20 else latencies[-1] if latencies else 0,
                "avg": sum(latencies) / total,
                "max": max(latencies),
            },
            "status_codes": dict(statuses),
            "route_actions": dict(routes),
            "models": dict(models),
            "cache_hits": cache_hits,
            "cache_hit_rate": cache_hits / total if total else 0,
            "error_rate": errors / total,
            "requests_per_minute": total / max(minutes, 1),
        }

    @classmethod
    def get_recent_requests(cls, limit: int = 20) -> list[dict]:
        """Get the most recent requests."""
        return cls._requests[-limit:]

    @classmethod
    def reset(cls):
        """Clear all metrics."""
        cls._requests = []

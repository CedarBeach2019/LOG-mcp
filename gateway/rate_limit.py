"""Simple in-memory rate limiter — token bucket per IP."""

from __future__ import annotations

import time
import logging
from collections import defaultdict

logger = logging.getLogger("gateway.rate_limit")


class RateLimiter:
    """Fixed-window rate limiter. Per-key (IP), configurable limits."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60,
                 burst: int = 10):
        self.max_requests = max_requests
        self.window = window_seconds
        self.burst = burst
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> tuple[bool, dict]:
        """Check if request is allowed. Returns (allowed, info)."""
        now = time.time()
        cutoff = now - self.window

        # Clean old entries
        bucket = self._buckets[key]
        bucket[:] = [t for t in bucket if t > cutoff]

        # Check burst limit (requests in last second)
        recent_burst = sum(1 for t in bucket if t > now - 1)
        if recent_burst >= self.burst:
            logger.warning("Rate limit burst hit for %s", key)
            return False, {
                "limit": self.max_requests,
                "remaining": 0,
                "reset_at": now + 1,
                "reason": "burst",
            }

        # Check window limit
        if len(bucket) >= self.max_requests:
            oldest = min(bucket) if bucket else now
            return False, {
                "limit": self.max_requests,
                "remaining": 0,
                "reset_at": oldest + self.window,
                "reason": "window",
            }

        # Allow
        bucket.append(now)
        return True, {
            "limit": self.max_requests,
            "remaining": self.max_requests - len(bucket),
            "reset_at": now + self.window,
        }

    def reset(self, key: str):
        """Reset a bucket."""
        self._buckets.pop(key, None)


# Global instance
_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter

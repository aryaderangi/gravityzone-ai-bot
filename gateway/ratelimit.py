"""
Rate limiter (Task #5)
======================
Per-Telegram-user sliding-window rate limiter.

    Max: 20 requests per minute per user (config.RATE_LIMIT / config.RATE_WINDOW)
    Returns a friendly message when exceeded — never crashes.
"""
import logging
import time

from . import config

log = logging.getLogger("gravity.ratelimit")


class RateLimiter:
    def __init__(
        self,
        max_requests: int = config.RATE_LIMIT,
        window: float = config.RATE_WINDOW,
    ):
        self.max_requests = max_requests
        self.window = window  # seconds
        self._buckets: dict[str, list[float]] = {}

    def allow(self, user: str) -> bool:
        """
        Check whether *user* may make a request right now.

        Returns True if allowed, False if rate-limited.
        """
        now = time.monotonic()
        bucket = self._buckets.get(user)
        if bucket is None:
            self._buckets[user] = [now]
            return True

        # Drop timestamps outside the window
        cutoff = now - self.window
        fresh = [t for t in bucket if t >= cutoff]
        if len(fresh) >= self.max_requests:
            self._buckets[user] = fresh
            return False

        fresh.append(now)
        self._buckets[user] = fresh
        return True

    def remaining(self, user: str) -> int:
        """How many requests *user* has left in the current window."""
        now = time.monotonic()
        bucket = self._buckets.get(user, [])
        fresh = [t for t in bucket if t >= now - self.window]
        return max(0, self.max_requests - len(fresh))

    def cleanup(self):
        """Drop all stale buckets (call periodically)."""
        now = time.monotonic()
        cutoff = now - self.window
        stale = [u for u, b in self._buckets.items() if all(t < cutoff for t in b)]
        for u in stale:
            del self._buckets[u]


# Singleton
rate_limiter = RateLimiter()

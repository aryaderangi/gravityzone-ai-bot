"""
Request cache (Task #3)
=======================
Async-safe TTL cache for successful AI responses.

    Key:   (model, prompt)
    TTL:   30 seconds (config.CACHE_TTL)
    Safe:  single asyncio.Lock guards all mutations
    Auto-expire: entries are expired lazily on access + periodic sweep
"""
import asyncio
import hashlib
import logging
import time

from . import config

log = logging.getLogger("gravity.cache")


def _make_key(model: str, prompt: str) -> str:
    """Deterministic key — hash the prompt so long prompts don't bloat memory."""
    raw = f"{model.lower().strip()}::{prompt.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


class TTLCache:
    def __init__(self, ttl: float = config.CACHE_TTL):
        self.ttl = ttl
        self._store: dict[str, tuple[str, float]] = {}  # key -> (value, expires_at)
        self._lock = asyncio.Lock()

    async def get(self, model: str, prompt: str):
        """Return cached value or None (if miss / expired)."""
        key = _make_key(model, prompt)
        now = time.monotonic()
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if now >= expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, model: str, prompt: str, value: str):
        """Store a successful response."""
        key = _make_key(model, prompt)
        expires_at = time.monotonic() + self.ttl
        async with self._lock:
            self._store[key] = (value, expires_at)

    async def clear(self):
        """Wipe the entire cache (admin action)."""
        async with self._lock:
            count = len(self._store)
            self._store.clear()
        log.info("Cache cleared | entries_removed=%d", count)
        return count

    async def sweep(self):
        """Remove all expired entries (called periodically)."""
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now >= exp]
            for k in expired:
                del self._store[k]
        if expired:
            log.info("Cache sweep | expired_removed=%d | remaining=%d", len(expired), len(self._store))

    @property
    def size(self) -> int:
        return len(self._store)


# Singleton
cache = TTLCache()

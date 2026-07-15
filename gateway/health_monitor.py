"""
Background health monitor (Task #8)
===================================
An asyncio task that pings every provider every 60 seconds and caches the
result. The ``GET /health`` endpoint serves this cached snapshot instead of
blocking on live probes — faster and gentler on upstreams.
"""
import asyncio
import logging

from . import config
from .cache import cache
from .providers import PROVIDERS
from .ratelimit import rate_limiter

log = logging.getLogger("gravity.health_monitor")


class HealthMonitor:
    def __init__(self, interval: float = config.HEALTH_MONITOR_INTERVAL):
        self.interval = interval
        self._status: dict[str, bool] = {n: False for n in PROVIDERS}
        self._last_check: float = 0.0
        self._task: asyncio.Task | None = None

    async def _probe_once(self):
        results = {}
        for name, provider in PROVIDERS.items():
            try:
                results[name] = await provider.health()
            except Exception:  # noqa: BLE001
                results[name] = False
        self._status = results
        self._last_check = asyncio.get_event_loop().time()
        # ── Housekeeping: prune expired cache entries + stale rate-limit buckets.
        # Prevents unbounded memory growth for one-off prompts / inactive users.
        try:
            await cache.sweep()
        except Exception:  # noqa: BLE001
            pass
        try:
            rate_limiter.cleanup()
        except Exception:  # noqa: BLE001
            pass
        log.info(
            "Health probe complete | %s",
            " ".join(f"{k}={'on' if v else 'off'}" for k, v in results.items()),
        )

    async def _run(self):
        """Loop forever until cancelled."""
        # immediate probe at startup
        await self._probe_once()
        while True:
            await asyncio.sleep(self.interval)
            await self._probe_once()

    def start(self):
        """Start the background monitor task (called from app startup)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            log.info("Health monitor started | interval=%ss", self.interval)

    async def stop(self):
        """Cancel the background task (called from app shutdown)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            log.info("Health monitor stopped")

    def snapshot(self) -> dict:
        """Return cached provider status as online/offline strings."""
        return {
            name: ("online" if ok else "offline")
            for name, ok in self._status.items()
        }


# Singleton
monitor = HealthMonitor()

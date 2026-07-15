"""
Metrics collector (Task #2)
===========================
Thread-safe / async-safe in-memory metrics.

Tracked:
    * total requests, errors, timeouts (gateway-wide)
    * per-provider: requests, errors, avg_latency
"""
import logging
import threading
import time

log = logging.getLogger("gravity.metrics")


class ProviderMetrics:
    __slots__ = ("requests", "errors", "_latency_sum", "_latency_count")

    def __init__(self):
        self.requests = 0
        self.errors = 0
        self._latency_sum = 0.0
        self._latency_count = 0

    def record(self, latency: float, success: bool):
        self.requests += 1
        self._latency_sum += latency
        self._latency_count += 1
        if not success:
            self.errors += 1

    def reset(self):
        self.requests = 0
        self.errors = 0
        self._latency_sum = 0.0
        self._latency_count = 0

    @property
    def avg_latency(self) -> float:
        if self._latency_count == 0:
            return 0.0
        return round(self._latency_sum / self._latency_count, 3)

    def snapshot(self) -> dict:
        return {
            "requests": self.requests,
            "errors": self.errors,
            "avg_latency": self.avg_latency,
        }


class Metrics:
    def __init__(self):
        self._start = time.time()
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._total_timeouts = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._rate_limited = 0
        self._providers: dict[str, ProviderMetrics] = {
            "node1": ProviderMetrics(),
            "node2": ProviderMetrics(),
            "openrouter": ProviderMetrics(),
        }

    def record_request(self, provider: str, latency: float, success: bool):
        with self._lock:
            self._total_requests += 1
            if not success:
                self._total_errors += 1
            pm = self._providers.get(provider)
            if pm:
                pm.record(latency, success)

    def record_timeout(self):
        with self._lock:
            self._total_timeouts += 1

    def record_cache_hit(self):
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self):
        with self._lock:
            self._cache_misses += 1

    def record_rate_limited(self):
        with self._lock:
            self._rate_limited += 1

    def reset(self):
        """Zero all counters (used by tests / admin reset)."""
        with self._lock:
            self._total_requests = 0
            self._total_errors = 0
            self._total_timeouts = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._rate_limited = 0
            self._start = time.time()
            for pm in self._providers.values():
                pm.reset()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime": int(time.time() - self._start),
                "requests": self._total_requests,
                "errors": self._total_errors,
                "timeouts": self._total_timeouts,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "rate_limited": self._rate_limited,
                "providers": {
                    name: pm.snapshot() for name, pm in self._providers.items()
                },
            }


# Singleton
metrics = Metrics()

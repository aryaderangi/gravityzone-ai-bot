"""
Circuit Breaker (Task #1)
=========================
Real circuit breaker per provider.

States:
    CLOSED     — normal operation, requests flow through.
    OPEN       — disabled until `disabled_until` passes. Requests are skipped.
    HALF_OPEN  — cooldown elapsed; one trial request is allowed. If it
                 succeeds, the breaker resets to CLOSED. If it fails, it
                 re-opens for another cooldown.

Rules (from spec):
    * 5 consecutive failures  -> OPEN for 60 seconds
    * While OPEN, providers are skipped automatically
    * After 60 s              -> allow ONE trial (HALF_OPEN)
    * Trial success           -> reset failure_count, go CLOSED
"""
import asyncio
import logging
import time

from . import config

log = logging.getLogger("gravity.circuit")

# Breaker states
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, name: str):
        self.name = name
        self.failure_count = 0
        self.last_failure: float = 0.0
        self.disabled_until: float = 0.0
        self._state = CLOSED
        self._half_open_trial_in_progress = False  # limits HALF_OPEN to ONE trial
        self._lock = asyncio.Lock()

    def _refresh_state(self):
        """Transition OPEN -> HALF_OPEN once cooldown elapses (call under lock)."""
        if self._state == OPEN and time.monotonic() >= self.disabled_until:
            self._state = HALF_OPEN
            self._half_open_trial_in_progress = False
            log.info("Circuit HALF_OPEN | provider=%s (trial allowed)", self.name)

    @property
    def state(self) -> str:
        return self._state

    def is_available(self) -> bool:
        """
        Return True if the provider should be attempted.

        In HALF_OPEN only ONE trial is permitted at a time: once a trial is
        in progress, further requests see the provider as unavailable until
        the trial resolves (success -> CLOSED, failure -> OPEN).
        """
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            # cooldown may have elapsed; flip lazily (read-only check, no lock)
            return time.monotonic() >= self.disabled_until and not self._half_open_trial_in_progress
        # HALF_OPEN
        return not self._half_open_trial_in_progress

    def begin_trial(self):
        """Reserve the single HALF_OPEN trial slot (call under lock)."""
        self._refresh_state()
        if self._state == HALF_OPEN and not self._half_open_trial_in_progress:
            self._half_open_trial_in_progress = True
            return True
        return False

    async def record_success(self):
        """Reset the breaker after a successful call."""
        async with self._lock:
            self._refresh_state()
            old = self._state
            self.failure_count = 0
            self.last_failure = 0.0
            self.disabled_until = 0.0
            self._half_open_trial_in_progress = False
            self._state = CLOSED
            if old != CLOSED:
                log.info("Circuit CLOSED | provider=%s (recovered)", self.name)

    async def record_failure(self):
        """Record a failure; trip the breaker if threshold reached."""
        async with self._lock:
            self._refresh_state()
            self.failure_count += 1
            self.last_failure = time.monotonic()
            self._half_open_trial_in_progress = False
            if self._state == HALF_OPEN or self.failure_count >= config.CB_FAILURE_THRESHOLD:
                self._state = OPEN
                self.disabled_until = time.monotonic() + config.CB_COOLDOWN
                log.warning(
                    "Circuit OPEN | provider=%s | failures=%d | cooldown=%ds",
                    self.name, self.failure_count, config.CB_COOLDOWN,
                )

    async def force_reset(self):
        """Admin reset — fully clear the breaker."""
        async with self._lock:
            self.failure_count = 0
            self.last_failure = 0.0
            self.disabled_until = 0.0
            self._half_open_trial_in_progress = False
            self._state = CLOSED
            log.info("Circuit force-reset | provider=%s", self.name)

    def status(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "last_failure": round(self.last_failure, 2) if self.last_failure else None,
            "disabled_until": round(self.disabled_until, 2) if self.disabled_until else None,
        }


class BreakerRegistry:
    """Holds one breaker per provider name."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name)
        return self._breakers[name]

    def is_available(self, name: str) -> bool:
        return self.get(name).is_available()

    def begin_trial(self, name: str) -> bool:
        """Atomically claim the single HALF_OPEN trial slot for *name*."""
        return self.get(name).begin_trial()

    async def record_success(self, name: str):
        await self.get(name).record_success()

    async def record_failure(self, name: str):
        await self.get(name).record_failure()

    async def reset_all(self):
        for b in self._breakers.values():
            await b.force_reset()
        log.info("All circuits reset")

    def status_all(self) -> dict:
        return {name: b.status() for name, b in self._breakers.items()}


# Singleton
breakers = BreakerRegistry()

"""
Automatic model router — optimized.

Routing rules
-------------
    gravity-ai -> Node1        phi -> Node1
    qwen       -> Node2        gemma -> Node2        deepseek -> Node2
    gpt        -> OpenRouter
    auto       -> last healthy provider, then full chain

Optimizations:
    * Auto Router remembers the last successful provider and tries it first.
    * Retry only once before failover.
    * Per-provider timeouts handled inside providers (Local=12 / Remote=18 / GPT=30).
"""
import asyncio
import logging
import time
from typing import AsyncGenerator, List, Tuple

from . import config
from .circuit import breakers
from .cache import cache
from .metrics import metrics
from .providers import PROVIDERS, ProviderError
from .structured_log import log_event

log = logging.getLogger("gravity.router")

ChainStep = Tuple[str, str, str]  # (alias, provider_name, model_id)


class RoutingError(RuntimeError):
    """Raised when every provider in the chain failed."""


# ─────────────────────────────────────────────
# Last-healthy-provider memory (requirement #4)
# ─────────────────────────────────────────────
_last_healthy_provider: str | None = None
_last_healthy_alias: str | None = None


def remember_healthy(provider_name: str, alias: str):
    """Record which provider last answered successfully (for auto-router)."""
    global _last_healthy_provider, _last_healthy_alias
    _last_healthy_provider = provider_name
    _last_healthy_alias = alias


def last_healthy() -> Tuple[str | None, str | None]:
    return _last_healthy_provider, _last_healthy_alias


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _normalize(model: str) -> str:
    return (model or "").strip().lower()


def _dedupe(chain: List[ChainStep]) -> List[ChainStep]:
    out: List[ChainStep] = []
    for step in chain:
        if step not in out:
            out.append(step)
    return out


def _available_ram_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return None


def _ram_preferred_model() -> ChainStep:
    ram = _available_ram_gb()
    if ram is None:
        return config.RAM_DEFAULT_MODEL
    if ram < 4:
        log_event("ram_priority", ram_gb=round(ram, 1), preferred="phi", reason="<4GB")
        return config.RAM_LOW_MODEL
    if ram > 10:
        log_event("ram_priority", ram_gb=round(ram, 1), preferred="gemma", reason=">10GB")
        return config.RAM_HIGH_MODEL
    log_event("ram_priority", ram_gb=round(ram, 1), preferred="qwen", reason="4-10GB")
    return config.RAM_DEFAULT_MODEL


def _auto_chain() -> List[ChainStep]:
    """
    Build the auto-router chain.

    Requirement #4: remember the last healthy provider and try it FIRST.
    Fall back to RAM-aware default, then the other node, then GPT.
    """
    hp, ha = last_healthy()
    chain: List[ChainStep] = []

    # 1) If we remember a healthy provider/model, put it first.
    if hp and ha:
        provider_name, model_id = config.ROUTING.get(ha, (hp, ""))
        if model_id:
            chain.append((ha, hp, model_id))

    # 2) RAM-aware preferred model.
    preferred = _ram_preferred_model()
    chain.append(preferred)

    # 3) Ensure the opposite node is represented.
    if preferred[1] == "node1":
        chain.append(config.NODE2_FALLBACK)
    else:
        chain.append(config.NODE1_FALLBACK)

    # 4) GPT as last resort.
    chain.append(config.GPT_STEP)

    return _dedupe(chain)


def build_chain(model: str) -> List[ChainStep]:
    """Build the ordered failover chain for a request."""
    model = _normalize(model)

    if model in ("", "auto") or model not in config.ROUTING:
        return _auto_chain()

    provider_name, model_id = config.ROUTING[model]
    chain: List[ChainStep] = [(model, provider_name, model_id)]

    if provider_name == "node1":
        chain.append(config.NODE2_FALLBACK)
        chain.append(config.GPT_STEP)
    elif provider_name == "node2":
        chain.append(config.NODE1_FALLBACK)
        chain.append(config.GPT_STEP)

    return _dedupe(chain)


# ─────────────────────────────────────────────
# Main routing (non-streaming) — retry once, then failover
# ─────────────────────────────────────────────
MAX_ATTEMPTS = 2  # initial + 1 retry (requirement #5)


async def route_chat(model: str, prompt: str, user: str | None = None) -> dict:
    """
    Execute the failover chain.

    Returns:
        {"response": str, "model": str, "provider": str, "cached": bool}
    Raises RoutingError if every step fails.
    """
    # Cache check
    if config.CACHE_ENABLED:
        cached_val = await cache.get(model, prompt)
        if cached_val is not None:
            metrics.record_cache_hit()
            log_event("cache_hit", model=model, user=user)
            return {"response": cached_val, "model": model, "provider": "cache", "cached": True}
        metrics.record_cache_miss()

    chain = build_chain(model)
    log_event("incoming_request", model=model, user=user, chain_len=len(chain))

    last_error: Exception = RoutingError("no providers attempted")
    total_started = time.perf_counter()

    for idx, (alias, provider_name, model_id) in enumerate(chain, start=1):
        provider = PROVIDERS[provider_name]

        # Circuit breaker check
        if not breakers.is_available(provider_name):
            log_event("circuit_skip", provider=provider_name, model=model_id, reason="open")
            continue
        breakers.begin_trial(provider_name)

        log_event("provider_selected", provider=provider_name, model=model_id,
                  step=idx, total_steps=len(chain), user=user)

        # Retry once, then failover
        for attempt in range(1, MAX_ATTEMPTS + 1):
            t0 = time.perf_counter()
            try:
                content = await provider.chat(model_id, prompt)
                elapsed = time.perf_counter() - t0

                await breakers.record_success(provider_name)
                metrics.record_request(provider_name, elapsed, success=True)
                remember_healthy(provider_name, alias)  # requirement #4

                if config.CACHE_ENABLED:
                    await cache.set(model, prompt, content)

                log_event("chat_response", provider=provider_name, model=model_id,
                          latency=round(elapsed, 3), success=True, attempt=attempt, user=user)
                log_event("request_complete",
                          total_latency=round(time.perf_counter() - total_started, 3),
                          model=alias, provider=provider_name, user=user)

                return {"response": content, "model": alias, "provider": provider_name, "cached": False}

            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - t0
                await breakers.record_failure(provider_name)
                metrics.record_request(provider_name, elapsed, success=False)
                if isinstance(exc, ProviderError) and "timed out" in str(exc):
                    metrics.record_timeout()

                log_event("provider_error", provider=provider_name, model=model_id,
                          latency=round(elapsed, 3), success=False,
                          attempt=attempt, error=str(exc)[:200], user=user)
                last_error = exc

                if attempt < MAX_ATTEMPTS:
                    log_event("retry", provider=provider_name, model=model_id, next_attempt=attempt + 1)
                    continue
                break

        if idx < len(chain):
            nxt = chain[idx]
            log_event("failover", provider=provider_name, model=model_id,
                      next_provider=nxt[1], next_model=nxt[2], reason="exhausted")

    log_event("all_providers_failed", model=model, error=str(last_error)[:200], user=user)
    raise RoutingError(f"All providers failed. Last error: {last_error}")


# ─────────────────────────────────────────────
# Streaming routing — retry before first token only
# ─────────────────────────────────────────────
async def route_stream(model: str, prompt: str, user: str | None = None) -> AsyncGenerator[str, None]:
    chain = build_chain(model)
    log_event("stream_request", model=model, user=user)

    last_error: Exception = RoutingError("no providers attempted")

    for idx, (alias, provider_name, model_id) in enumerate(chain, start=1):
        provider = PROVIDERS[provider_name]

        if not breakers.is_available(provider_name):
            log_event("circuit_skip", provider=provider_name, model=model_id, reason="open")
            continue
        breakers.begin_trial(provider_name)

        t0 = time.perf_counter()

        if not provider.supports_streaming:
            try:
                content = await provider.chat(model_id, prompt)
                await breakers.record_success(provider_name)
                metrics.record_request(provider_name, time.perf_counter() - t0, success=True)
                remember_healthy(provider_name, alias)
                log_event("stream_fallback", provider=provider_name, model=model_id, user=user)
                yield content
                return
            except Exception as exc:  # noqa: BLE001
                await breakers.record_failure(provider_name)
                metrics.record_request(provider_name, time.perf_counter() - t0, success=False)
                last_error = exc
                continue

        yielded_any = False
        try:
            async for token in provider.stream_chat(model_id, prompt):
                yielded_any = True
                yield token
            await breakers.record_success(provider_name)
            metrics.record_request(provider_name, time.perf_counter() - t0, success=True)
            remember_healthy(provider_name, alias)
            log_event("stream_done", provider=provider_name, model=model_id, user=user)
            return
        except Exception as exc:  # noqa: BLE001
            await breakers.record_failure(provider_name)
            metrics.record_request(provider_name, time.perf_counter() - t0, success=False)
            log_event("stream_error", provider=provider_name, model=model_id,
                      error=str(exc)[:200], user=user)
            last_error = exc
            if yielded_any:
                raise RoutingError(
                    f"Stream from {provider_name}/{model_id} broke after partial output: {exc}"
                ) from exc
            continue

    raise RoutingError(f"Streaming failed on all providers. Last error: {last_error}")

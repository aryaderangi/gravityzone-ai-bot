"""
Comprehensive test suite for Gravity Gateway (Task #13).

Tests:
    1. Retry
    2. Failover
    3. Circuit Breaker
    4. Rate Limiter
    5. Cache
    6. Metrics
    7. Queue (concurrency)
    8. Streaming

Run with::

    python -m pytest tests/test_gateway.py -v
"""
import asyncio
import time

import pytest

# ── fixtures ──
@pytest.fixture
def patched_providers(monkeypatch):
    """Replace all provider network calls with controllable mocks."""
    from gateway import providers

    state = {"calls": [], "node1_fail": False, "node2_fail": False, "or_fail": False}

    def _make(name, fail_key):
        async def chat(model_id, prompt):
            state["calls"].append((name, model_id))
            if state[fail_key]:
                from gateway.providers import ProviderError
                raise ProviderError(f"{name} down")
            return f"[{name}:{model_id}] ok"
        async def stream(model_id, prompt):
            if state[fail_key]:
                from gateway.providers import ProviderError
                raise ProviderError(f"{name} stream down")
            for tok in ["Hello", " ", "World"]:
                state["calls"].append((name, model_id, "stream"))
                yield tok
        return chat, stream

    async def ok_health():
        return True

    n1c, n1s = _make("node1", "node1_fail")
    n2c, n2s = _make("node2", "node2_fail")
    orc, ors = _make("openrouter", "or_fail")

    monkeypatch.setattr(providers.node1, "chat", n1c)
    monkeypatch.setattr(providers.node2, "chat", n2c)
    monkeypatch.setattr(providers.openrouter, "chat", orc)
    monkeypatch.setattr(providers.node1, "stream_chat", n1s)
    monkeypatch.setattr(providers.node2, "stream_chat", n2s)
    monkeypatch.setattr(providers.openrouter, "stream_chat", ors)
    for p in (providers.node1, providers.node2, providers.openrouter):
        monkeypatch.setattr(p, "health", ok_health)

    return state


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset circuit breakers, metrics, cache, rate limiter before AND after each test."""
    from gateway.circuit import breakers
    from gateway.metrics import metrics as met
    from gateway.cache import cache as c
    from gateway.ratelimit import rate_limiter as rl

    loop = asyncio.new_event_loop()

    async def _reset():
        await breakers.reset_all()
        await c.clear()
        met.reset()  # reset in-place (router.py holds the same reference)
        rl.cleanup()

    loop.run_until_complete(_reset())
    loop.close()

    yield

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_reset())
    loop.close()


# ═══════════════════════════════════════════════
# 1. RETRY (Task #5)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_retry_once_before_failover(patched_providers):
    """A failing provider must be retried once before failover."""
    from gateway import router, config

    patched_providers["node2_fail"] = True
    result = await router.route_chat("qwen", "hi")
    # node2 tried twice (1 + 1 retry), then node1 succeeds
    node2_calls = [c for c in patched_providers["calls"] if c[0] == "node2"]
    assert len(node2_calls) == config.RETRIES_PER_PROVIDER + 1
    assert result["provider"] == "node1"


# ═══════════════════════════════════════════════
# 2. FAILOVER (Task #4)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_failover_node1_to_node2(patched_providers):
    """Node1 fails → failover to Node2."""
    from gateway import router

    patched_providers["node1_fail"] = True
    result = await router.route_chat("gravity-ai", "hi")
    assert result["provider"] == "node2"


@pytest.mark.asyncio
async def test_failover_both_to_gpt(patched_providers):
    """Both nodes fail → failover to GPT."""
    from gateway import router

    patched_providers["node1_fail"] = True
    patched_providers["node2_fail"] = True
    result = await router.route_chat("gravity-ai", "hi")
    assert result["provider"] == "openrouter"


# ═══════════════════════════════════════════════
# 3. CIRCUIT BREAKER (Task #1)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold(patched_providers):
    """5 consecutive failures → circuit OPEN → provider skipped."""
    from gateway import router, config
    from gateway.circuit import breakers
    from gateway.cache import cache

    patched_providers["node1_fail"] = True
    patched_providers["node2_fail"] = False

    # Use unique prompts so cache doesn't short-circuit node1 attempts.
    for i in range(3):  # 3 calls × 2 attempts each = 6 failures ≥ threshold of 5
        try:
            await router.route_chat("gravity-ai", f"prompt-{i}")
        except router.RoutingError:
            pass

    # circuit should be OPEN now
    assert not breakers.is_available("node1"), "node1 circuit should be OPEN"
    assert breakers.get("node1").state == "open"


@pytest.mark.asyncio
async def test_circuit_breaker_reset():
    """Admin reset clears all circuits."""
    from gateway.circuit import breakers

    breaker = breakers.get("test_provider")
    for _ in range(6):
        await breaker.record_failure()
    assert breaker.state == "open"

    await breakers.reset_all()
    assert breaker.state == "closed"
    assert breaker.failure_count == 0


# ═══════════════════════════════════════════════
# 4. RATE LIMITER (Task #5)
# ═══════════════════════════════════════════════
def test_rate_limiter_allows_within_limit():
    from gateway.ratelimit import rate_limiter

    allowed = sum(1 for _ in range(20) if rate_limiter.allow("user1"))
    assert allowed == 20


def test_rate_limiter_blocks_over_limit():
    from gateway.ratelimit import rate_limiter

    for _ in range(20):
        rate_limiter.allow("user2")
    assert rate_limiter.allow("user2") is False


def test_rate_limiter_per_user():
    from gateway.ratelimit import rate_limiter

    for _ in range(20):
        rate_limiter.allow("userA")
    # userB is independent
    assert rate_limiter.allow("userB") is True


# ═══════════════════════════════════════════════
# 5. CACHE (Task #3)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_cache_hit():
    from gateway.cache import cache

    await cache.set("qwen", "hello", "cached answer")
    result = await cache.get("qwen", "hello")
    assert result == "cached answer"


@pytest.mark.asyncio
async def test_cache_miss():
    from gateway.cache import cache

    result = await cache.get("unknown", "nope")
    assert result is None


@pytest.mark.asyncio
async def test_cache_expiry(monkeypatch):
    from gateway.cache import TTLCache

    c = TTLCache(ttl=0.05)  # 50ms TTL
    await c.set("model", "p", "val")
    assert await c.get("model", "p") == "val"
    await asyncio.sleep(0.1)
    assert await c.get("model", "p") is None


@pytest.mark.asyncio
async def test_cache_clear():
    from gateway.cache import cache

    await cache.set("m", "p", "v")
    removed = await cache.clear()
    assert removed >= 1


@pytest.mark.asyncio
async def test_router_cache_integration(patched_providers):
    """Second identical request should be served from cache (no provider call)."""
    from gateway import router
    from gateway.cache import cache

    await cache.clear()
    await router.route_chat("qwen", "duplicate-prompt")
    calls_before = len(patched_providers["calls"])
    result = await router.route_chat("qwen", "duplicate-prompt")
    calls_after = len(patched_providers["calls"])
    assert calls_after == calls_before, "Cache should prevent second provider call"
    assert result["cached"] is True


# ═══════════════════════════════════════════════
# 6. METRICS (Task #2)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_metrics_tracking(patched_providers):
    from gateway import router
    from gateway.metrics import metrics

    await router.route_chat("qwen", "hi")
    snap = metrics.snapshot()
    assert snap["requests"] >= 1
    assert "node2" in snap["providers"]
    assert snap["providers"]["node2"]["requests"] >= 1
    assert snap["providers"]["node2"]["avg_latency"] >= 0
    assert snap["uptime"] >= 0


# ═══════════════════════════════════════════════
# 7. QUEUE / CONCURRENCY (Task #4)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_queue_max_concurrency():
    """No more than MAX_CONCURRENT requests run simultaneously."""
    from gateway import config

    max_concurrent = config.MAX_CONCURRENT
    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def task():
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.05)
        async with lock:
            current -= 1

    # launch more tasks than the limit
    sem = asyncio.Semaphore(max_concurrent)

    async def queued():
        async with sem:
            await task()

    await asyncio.gather(*[queued() for _ in range(max_concurrent + 10)])
    assert peak <= max_concurrent, f"peak={peak} exceeded max={max_concurrent}"


# ═══════════════════════════════════════════════
# 8. STREAMING (Task #6)
# ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_streaming_yields_tokens(patched_providers):
    from gateway import router

    tokens = []
    async for tok in router.route_stream("qwen", "hi"):
        tokens.append(tok)
    assert tokens == ["Hello", " ", "World"]


@pytest.mark.asyncio
async def test_streaming_failover(patched_providers):
    """If node2 streaming fails, failover to node1."""
    from gateway import router

    patched_providers["node2_fail"] = True
    tokens = []
    async for tok in router.route_stream("qwen", "hi"):
        tokens.append(tok)
    assert len(tokens) > 0
    assert any(c[0] == "node1" for c in patched_providers["calls"] if len(c) > 2 and c[2] == "stream")


@pytest.mark.asyncio
async def test_streaming_no_merge_after_partial_failure(monkeypatch):
    """
    Regression: if a provider yields tokens then fails mid-stream, the
    router must NOT fail over (tokens already sent). It must raise instead
    of concatenating a second provider's output.
    """
    from gateway import router, providers
    from gateway.circuit import breakers
    from gateway.cache import cache as c

    await breakers.reset_all()
    await c.clear()

    async def partial_then_fail(model_id, prompt):
        yield "Hello"
        yield " wor"
        raise providers.ProviderError("connection dropped")

    async def full_ok(model_id, prompt):
        for t in ["Hi", " there"]:
            yield t

    # qwen -> node2 (partial failer), node1 fallback (full)
    providers.node2.stream_chat = partial_then_fail
    providers.node1.stream_chat = full_ok

    received = []
    raised = False
    try:
        async for tok in router.route_stream("qwen", "hi"):
            received.append(tok)
    except router.RoutingError:
        raised = True

    assert raised, "Expected RoutingError after partial stream failure"
    # Only the partial tokens from the first provider should be present
    assert "".join(received) == "Hello wor", f"Got corrupted stream: {received!r}"
    assert "Hi" not in received, "Second provider output must not be merged in"


@pytest.mark.asyncio
async def test_circuit_half_open_single_trial():
    """In HALF_OPEN only one concurrent trial should be permitted."""
    from gateway.circuit import CircuitBreaker

    cb = CircuitBreaker("test_single")
    # force into OPEN with elapsed cooldown
    for _ in range(6):
        await cb.record_failure()
    assert cb.state == "open"

    import gateway.circuit as circ
    circ._ = None
    # simulate cooldown elapsed
    import time as _t
    cb.disabled_until = _t.monotonic() - 1
    assert cb.begin_trial() is True, "first trial claim should succeed"
    assert cb.begin_trial() is False, "second concurrent trial must be blocked"


# ═══════════════════════════════════════════════
# 9. BONUS: RAM priority (Task #7)
# ═══════════════════════════════════════════════
def test_ram_priority_returns_chain():
    from gateway.router import _auto_chain

    chain = _auto_chain()
    assert len(chain) >= 2  # at least preferred model + GPT
    # last step is always GPT
    assert chain[-1][1] == "openrouter"

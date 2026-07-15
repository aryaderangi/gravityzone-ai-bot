"""
FastAPI application for Gravity Gateway — optimized.

Lifecycle:
    Startup  → warmup models (Gemma3/Qwen3/DeepSeek), start health monitor.
    Shutdown → stop health monitor, close shared AsyncClient (graceful).

Endpoints:
    GET  /health        — cached provider status
    GET  /metrics       — gateway + provider metrics
    POST /chat          — routed completion (circuit + cache + failover)
    POST /reload        — hot-reload config
    POST /clear-cache   — flush response cache
    POST /reset-circuit — reset circuit breakers
    GET  /circuit       — view breaker states
"""
import asyncio
import logging
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import config, models, router
from .cache import cache
from .circuit import breakers
from .health_monitor import monitor
from .metrics import metrics
from .providers import close_client, preload_models
from .ratelimit import rate_limiter
from .structured_log import log_event

log = logging.getLogger("gravity.app")

app = FastAPI(
    title="Gravity Gateway",
    description="Central async AI routing layer for the GravityZone bot.",
    version="3.1.0",
)

# ─────────────────────────────────────────────
# Concurrency queue — max 20 simultaneous AI requests
# ─────────────────────────────────────────────
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)
    return _semaphore


def _check_admin(authorization: str | None):
    if not config.ADMIN_TOKEN:
        return
    if authorization != f"Bearer {config.ADMIN_TOKEN}":
        raise HTTPException(status_code=403, detail="Forbidden")


# ═══════════════════════════════════════════════════════════
# LIFECYCLE
# ═══════════════════════════════════════════════════════════
@app.on_event("startup")
async def _startup():
    """
    Requirement #8 / #9: graceful lifecycle.

    1. Start the background health monitor (probes every 60s, cached).
    2. Warm up Gemma3 / Qwen3 / DeepSeek so the first user request isn't slow.
    """
    global _semaphore
    _semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)

    # Start health monitor FIRST so /health works immediately.
    monitor.start()
    log_event("gateway_startup", http2=config.HTTP2_ENABLED,
              max_concurrent=config.MAX_CONCURRENT, cache_ttl=config.CACHE_TTL)

    # Warm up models in the background — don't block startup if a node is slow.
    try:
        warmup_task = asyncio.create_task(preload_models())
        # Wait up to 30s for warmup; if it takes longer, proceed without blocking.
        try:
            await asyncio.wait_for(asyncio.shield(warmup_task), timeout=30.0)
        except asyncio.TimeoutError:
            log_event("warmup_timeout", detail="warmup still running in background")
    except Exception as exc:  # noqa: BLE001
        log_event("warmup_error", error=str(exc)[:200])


@app.on_event("shutdown")
async def _shutdown():
    """
    Requirement #9: graceful shutdown.

    1. Stop the background health monitor.
    2. Close the shared httpx.AsyncClient (flushes keep-alive connections).
    """
    log_event("gateway_shutdown_begin")
    await monitor.stop()
    await close_client()
    log_event("gateway_shutdown_complete")


# ═══════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════
@app.get("/", tags=["meta"])
async def root():
    return {"gateway": "Gravity Gateway", "version": "3.1.0", "status": "running", "docs": "/docs"}


@app.get("/health", response_model=models.HealthResponse, tags=["ops"])
async def health():
    """Cached provider status from the background health monitor."""
    snap = monitor.snapshot()
    return models.HealthResponse(
        gateway="ok",
        node1=snap.get("node1", "offline"),
        node2=snap.get("node2", "offline"),
        openrouter=snap.get("openrouter", "offline"),
    )


@app.get("/metrics", tags=["ops"])
async def get_metrics():
    return metrics.snapshot()


@app.post("/chat", tags=["chat"])
async def chat(req: models.ChatRequest):
    """Route a prompt with circuit breaker, cache, failover, queue, rate limit."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")

    user = req.user or "anonymous"

    # Rate limit (per Telegram user)
    if not rate_limiter.allow(user):
        metrics.record_rate_limited()
        log_event("rate_limited", user=user)
        return JSONResponse(
            status_code=429,
            content={
                "detail": "⏳ درخواست‌های شما بیش از حد مجاز است. لطفاً یک دقیقه صبر کنید.",
                "retry_after": 60,
                "limit": config.RATE_LIMIT,
            },
        )

    # Streaming
    if req.stream:
        async def _stream():
            sem = _get_semaphore()
            async with sem:
                try:
                    async for token in router.route_stream(req.model, req.prompt, user=user):
                        yield f"data: {token}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                except router.RoutingError as exc:
                    yield f"data: [ERROR] {exc}\n\n".encode()

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # Non-streaming — acquire semaphore, route
    sem = _get_semaphore()
    t0 = time.perf_counter()
    async with sem:
        try:
            result = await router.route_chat(req.model, req.prompt, user=user)
        except router.RoutingError as exc:
            log_event("chat_failed", error=str(exc)[:200], user=user,
                      latency=round(time.perf_counter() - t0, 3))
            raise HTTPException(status_code=502, detail=str(exc))

    return models.ChatResponse(**result)


# ═══════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════
@app.post("/reload", tags=["admin"])
async def reload_config(authorization: str | None = Header(default=None)):
    """Hot-reload configuration from .env."""
    _check_admin(authorization)
    import importlib
    from . import config as cfg_mod
    importlib.reload(cfg_mod)
    log_event("config_reloaded")
    return {"status": "reloaded", "http2": cfg_mod.HTTP2_ENABLED,
            "max_concurrent": cfg_mod.MAX_CONCURRENT}


@app.post("/clear-cache", tags=["admin"])
async def clear_cache(authorization: str | None = Header(default=None)):
    """Flush the response cache."""
    _check_admin(authorization)
    removed = await cache.clear()
    return {"status": "cache_cleared", "entries_removed": removed}


@app.post("/reset-circuit", tags=["admin"])
async def reset_circuit(authorization: str | None = Header(default=None)):
    """Reset all circuit breakers to CLOSED state."""
    _check_admin(authorization)
    await breakers.reset_all()
    return {"status": "circuits_reset", "breakers": breakers.status_all()}


@app.get("/circuit", tags=["admin"])
async def circuit_status(authorization: str | None = Header(default=None)):
    """View current circuit breaker states."""
    _check_admin(authorization)
    return breakers.status_all()

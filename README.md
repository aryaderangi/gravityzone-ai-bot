# Gravity Gateway (Enterprise v3.0)

Central **async AI routing layer** for the GravityZone Telegram bot, hardened
for production with circuit breakers, caching, rate limiting, streaming,
metrics, and structured logging.

```
gateway/
├── app.py              # FastAPI app: /chat, /health, /metrics, admin endpoints
├── router.py           # routing + failover + circuit + cache + RAM priority
├── providers.py        # node1, node2, openrouter — async httpx + HTTP/2 + streaming
├── circuit.py          # circuit breaker (CLOSED → OPEN → HALF_OPEN)
├── metrics.py          # in-memory metrics collector
├── cache.py            # async-safe TTL cache
├── ratelimit.py        # per-user sliding-window rate limiter
├── health_monitor.py   # background health probe (every 60s)
├── structured_log.py   # JSON structured logging + secret sanitizer
├── models.py           # Pydantic schemas
├── config.py           # all config from .env (nothing hardcoded)
└── __init__.py

services/
└── gateway_client.py   # async ask(model, prompt, user) — the ONLY bot entry point

tests/
└── test_gateway.py     # 18 tests: retry, failover, circuit, rate, cache, metrics, queue, streaming
```

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     Telegram Bot (bot.py)                      │
│                  aiogram · Whisper · Edge-TTS                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ services/gateway_client.ask(model, prompt, user)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Gravity Gateway :8000                        │
│                      (FastAPI / uvicorn)                        │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ Rate     │→ │ Queue    │→ │ Cache    │→ │ Router        │ │
│  │ Limiter  │  │ (Sem 20) │  │ (TTL 30s)│  │               │ │
│  └──────────┘  └──────────┘  └──────────┘  │  ┌─────────┐  │ │
│                                             │  │ Circuit │  │ │
│                                             │  │ Breaker │  │ │
│                                             │  └────┬────┘  │ │
│                                             └───────┼───────┘ │
└─────────────────────────────────────────────┼─────────────────┘
                          ┌───────────────────┼───────────────┐
                          ▼                   ▼               ▼
                   ┌─────────────┐    ┌─────────────┐  ┌─────────────┐
                   │   Node 1    │    │   Node 2    │  │ OpenRouter  │
                   │ (Ollama)    │    │ (Ollama)    │  │  (Cloud)    │
                   │ gravity-ai  │    │ qwen        │  │ gpt         │
                   │ phi         │    │ gemma       │  │             │
                   │             │    │ deepseek    │  │             │
                   └─────────────┘    └─────────────┘  └─────────────┘
                          ↕                   ↕
                   ◀── Failover: Node1 ⇄ Node2 → GPT ──▶

  Background: HealthMonitor probes every 60s → cached /health
  Metrics:    every request tracked → GET /metrics
```

---

## Enterprise Features

### 1. Circuit Breaker
Each provider has its own breaker: **5 consecutive failures → OPEN for 60s** →
providers are skipped automatically. After cooldown, one trial (HALF_OPEN) is
allowed; success resets the breaker.

### 2. Metrics API (`GET /metrics`)
```json
{
  "uptime": 3600, "requests": 3456, "errors": 12, "timeouts": 3,
  "cache_hits": 200, "cache_misses": 3256,
  "providers": {
    "node1": {"requests": 1200, "errors": 2, "avg_latency": 0.42},
    "node2": {"requests": 1800, "errors": 4, "avg_latency": 0.67},
    "openrouter": {"requests": 456, "errors": 1, "avg_latency": 1.30}
  }
}
```

### 3. Request Cache
Successful responses cached for **30s** (TTL). Key = `(model, prompt)` hash.
Async-safe. Auto-expires. `POST /clear-cache` to flush.

### 4. Request Queue
Max **20 concurrent** AI requests. Overflow waits asynchronously via
`asyncio.Semaphore`. No blocking, no thread creation.

### 5. Rate Limiter
**20 requests per minute** per Telegram user. Returns HTTP 429 with a friendly
Persian message. Never crashes.

### 6. Streaming Support
`POST /chat` with `"stream": true` returns SSE tokens. Ollama streams NDJSON;
OpenRouter streams SSE. Non-streaming fallback for unsupported providers.

### 7. Provider Priority (RAM-aware)
Auto Router selects the first model based on available RAM:
| Available RAM | Preferred model |
|---------------|-----------------|
| < 4 GB        | Phi (lightweight) |
| > 10 GB       | Gemma3 (larger) |
| 4–10 GB       | Qwen3 (balanced) |

### 8. Background Health Monitor
Every **60s**, all providers are probed. `GET /health` returns the cached
snapshot — no live blocking.

### 9. Structured Logging
All logs are JSON. Example:
```json
{"event":"chat_response","provider":"node2","model":"qwen3","latency":0.41,"success":true,"user":123456}
```
**Never** logs API keys, tokens, or prompt text.

### 10–11. Security & Performance
- No hardcoded secrets — all from `.env`.
- API keys never appear in logs or error messages.
- Shared `httpx.AsyncClient` with **HTTP/2**, keep-alive (20/100 pool).
- Optional `GATEWAY_ADMIN_TOKEN` to protect admin endpoints.

### 12. Admin Endpoints
| Endpoint | Method | Action |
|----------|--------|--------|
| `/metrics` | GET | Full metrics snapshot |
| `/health` | GET | Cached provider status |
| `/reload` | POST | Hot-reload config from .env |
| `/clear-cache` | POST | Flush response cache |
| `/reset-circuit` | POST | Reset all circuit breakers |
| `/circuit` | GET | View breaker states |

---

## HTTP API

### `POST /chat`
```json
// request
{ "model": "qwen", "prompt": "hello", "user": "123", "stream": false }

// response
{ "response": "...", "model": "qwen", "provider": "node2", "cached": false }
```

### `GET /health`
```json
{ "gateway": "ok", "node1": "online", "node2": "online", "openrouter": "offline" }
```

---

## Deployment

### 1. Install
```bash
pip install -r requirements.txt
cp .env.example .env   # then edit with your real values
```

### 2. Start Gateway
```bash
# dev
uvicorn gateway.app:app --host 127.0.0.1 --port 8000

# production (systemd)
sudo cp gravity-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gravity-gateway
sudo journalctl -u gravity-gateway -f
```

### 3. Start Bot
```bash
python bot.py    # or: sudo systemctl restart gravity-bot
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Bot returns "Gateway در دسترس نیست" | `curl http://127.0.0.1:8000/health` — is gateway running? |
| All providers offline | `GET /health` → check `node1`/`node2`/`openrouter` status |
| Rate limited (429) | Wait 60s, or increase `RATE_LIMIT` in `.env` |
| Circuit stuck OPEN | `POST /reset-circuit` to force-reset all breakers |
| High latency | `GET /metrics` → check `avg_latency` per provider |
| Cache stale | `POST /clear-cache` to flush |
| HTTP/2 not working | `pip install h2` then restart gateway |

### Run Tests
```bash
python -m pytest tests/test_gateway.py -v
```

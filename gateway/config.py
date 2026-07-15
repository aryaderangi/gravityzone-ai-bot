"""
Central configuration for Gravity Gateway.

All endpoints are read from environment variables (.env) — nothing is
hardcoded (Task #10).
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Provider endpoints  (NEVER hardcoded — always from env)
# ─────────────────────────────────────────────
NODE1_URL = os.getenv("NODE1_URL", "https://ol.gravityzoneshop.top")
NODE2_URL = os.getenv("NODE2_URL", "https://oll.gravityzoneshop.top")

OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ─────────────────────────────────────────────
# Timeouts / retries
# ─────────────────────────────────────────────
REQUEST_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT", "60"))
HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "10"))
RETRIES_PER_PROVIDER = int(os.getenv("GATEWAY_RETRIES", "1"))

# ─────────────────────────────────────────────
# Uvicorn bind
# ─────────────────────────────────────────────
HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("GATEWAY_PORT", "8000"))

# ─────────────────────────────────────────────
# Circuit breaker (Task #1)
# ─────────────────────────────────────────────
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_COOLDOWN = float(os.getenv("CB_COOLDOWN", "60"))

# ─────────────────────────────────────────────
# Request cache (Task #3)
# ─────────────────────────────────────────────
CACHE_TTL = float(os.getenv("CACHE_TTL", "30"))
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"

# ─────────────────────────────────────────────
# Request queue / concurrency (Task #4)
# ─────────────────────────────────────────────
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "20"))

# ─────────────────────────────────────────────
# Rate limiter (Task #5)
# ─────────────────────────────────────────────
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "20"))        # requests
RATE_WINDOW = float(os.getenv("RATE_WINDOW", "60"))    # per N seconds

# ─────────────────────────────────────────────
# Background health monitor (Task #8)
# ─────────────────────────────────────────────
HEALTH_MONITOR_INTERVAL = float(os.getenv("HEALTH_MONITOR_INTERVAL", "60"))

# ─────────────────────────────────────────────
# HTTP/2 (Task #11)
# ─────────────────────────────────────────────
# Try to enable; gracefully falls back to HTTP/1.1 if `h2` not installed.
HTTP2_ENABLED = False
try:
    import importlib
    importlib.import_module("h2")
    HTTP2_ENABLED = os.getenv("HTTP2_ENABLED", "true").lower() == "true"
except ImportError:
    HTTP2_ENABLED = False

# ─────────────────────────────────────────────
# Admin auth (Task #10 / #12)
# Optional shared secret to protect admin endpoints. If unset, admin
# endpoints are open but only reachable on 127.0.0.1 by default.
# ─────────────────────────────────────────────
ADMIN_TOKEN = os.getenv("GATEWAY_ADMIN_TOKEN", "")

# ─────────────────────────────────────────────
# Routing table
#   alias  ->  (provider_name, real_model_id)
# ─────────────────────────────────────────────
ROUTING = {
    # Node1 (ol.gravityzoneshop.top)
    "gravity-ai":  ("node1", "partai/dorna-llama3:8b-instruct-q4_0"),
    "gravityai":   ("node1", "partai/dorna-llama3:8b-instruct-q4_0"),
    "dorna":       ("node1", "partai/dorna-llama3:8b-instruct-q4_0"),
    "phi":         ("node1", "phi3.5:latest"),
    "phi_local":   ("node1", "phi3.5:latest"),
    # Node2 (oll.gravityzoneshop.top)
    "qwen":        ("node2", "qwen3:8b"),
    "qwen3":       ("node2", "qwen3:8b"),
    "gemma":       ("node2", "gemma3:12b"),
    "gemma3":      ("node2", "gemma3:12b"),
    "deepseek":    ("node2", "deepseek-r1:8b"),
    "deepseek-r1": ("node2", "deepseek-r1:8b"),
    # Cloud (OpenRouter)
    "gpt":         ("openrouter", "openai/gpt-4.1-mini"),
    "gpt5":        ("openrouter", "openai/gpt-4.1-mini"),
    "nemotron":    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
}

# ─────────────────────────────────────────────
# Failover building blocks  (alias, provider_name, model_id)
# ─────────────────────────────────────────────
NODE1_FALLBACK = ("gravity-ai", "node1", "partai/dorna-llama3:8b-instruct-q4_0")
NODE2_FALLBACK = ("qwen", "node2", "qwen3:8b")
GPT_STEP = ("gpt", "openrouter", "openai/gpt-4.1-mini")

# Default Auto Router chain (overridden at runtime by RAM-aware priority).
AUTO_CHAIN = [NODE1_FALLBACK, NODE2_FALLBACK, GPT_STEP]

# RAM-aware auto-router preferences (Task #7)
# (preferred_model, provider, model_id) per RAM tier
RAM_LOW_MODEL = ("phi", "node1", "phi3.5:latest")          # < 4 GB
RAM_DEFAULT_MODEL = ("qwen", "node2", "qwen3:8b")          # 4-10 GB
RAM_HIGH_MODEL = ("gemma", "node2", "gemma3:12b")          # > 10 GB

AUTO_ALIAS = "auto"

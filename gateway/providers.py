"""
Async provider clients — optimized.

Three providers:
    node1      -> NODE1_URL   (Local Ollama)   timeout = 12s
    node2      -> NODE2_URL   (Remote Ollama)  timeout = 18s
    openrouter -> OpenRouter  (Cloud / GPT)     timeout = 30s

Optimizations:
    * ONE shared httpx.AsyncClient (keep-alive, pool limits, HTTP/2).
    * Health results cached for 30 seconds — never probed per request.
    * Per-provider timeouts (Local/Remote/GPT).
    * preload_models() to warm up Gemma3 / Qwen3 / DeepSeek on startup.
"""
import json
import logging
import os
import time

import httpx

log = logging.getLogger("gravity.providers")

# ─────────────────────────────────────────────
# Per-provider timeouts (seconds)
# ─────────────────────────────────────────────
LOCAL_TIMEOUT = float(os.getenv("LOCAL_TIMEOUT", "12"))
REMOTE_TIMEOUT = float(os.getenv("REMOTE_TIMEOUT", "60"))
GPT_TIMEOUT = float(os.getenv("GPT_TIMEOUT", "30"))
HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "10"))
HEALTH_CACHE_TTL = float(os.getenv("HEALTH_CACHE_TTL", "30"))

# Read endpoints from env (fall back to config if importable).
try:
    from . import config as _cfg
    _NODE1_URL = _cfg.NODE1_URL
    _NODE2_URL = _cfg.NODE2_URL
    _OPENROUTER_URL = _cfg.OPENROUTER_URL
    _OPENROUTER_KEY = _cfg.OPENROUTER_API_KEY
    _HTTP2 = _cfg.HTTP2_ENABLED
except Exception:  # pragma: no cover
    _NODE1_URL = os.getenv("NODE1_URL", "https://ol.gravityzoneshop.top")
    _NODE2_URL = os.getenv("NODE2_URL", "https://oll.gravityzoneshop.top")
    _OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
    _OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
    _HTTP2 = False


class ProviderError(RuntimeError):
    """Raised when a provider returns a non-usable answer or times out."""


# ─────────────────────────────────────────────
# Shared, reused AsyncClient (single instance)
# ─────────────────────────────────────────────
_shared_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30,
            ),
            timeout=GPT_TIMEOUT,
            http2=_HTTP2,
        )
        log.info("Shared AsyncClient created (keepalive=20, max=100, http2=%s)", _HTTP2)
    return _shared_client


async def close_client() -> None:
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        log.info("Shared AsyncClient closed")
    _shared_client = None


# ─────────────────────────────────────────────
# Base provider with health caching
# ─────────────────────────────────────────────
class BaseProvider:
    name: str = "base"
    _health_ttl: float = HEALTH_CACHE_TTL

    def __init__(self):
        self._health_ok: bool | None = None
        self._health_checked_at: float = 0.0

    async def chat(self, model_id: str, prompt: str) -> str:
        raise NotImplementedError

    async def stream_chat(self, model_id: str, prompt: str):
        raise NotImplementedError

    @property
    def supports_streaming(self) -> bool:
        return False

    async def _probe_health(self) -> bool:
        """Perform the actual live probe. Override in subclasses."""
        raise NotImplementedError

    async def health(self) -> bool:
        """
        Return cached health status.

        The real probe runs at most once every HEALTH_CACHE_TTL seconds (30s
        by default). Between probes the cached result is returned instantly —
        never blocking a request on a health check.
        """
        now = time.monotonic()
        if self._health_ok is not None and (now - self._health_checked_at) < self._health_ttl:
            return self._health_ok
        try:
            self._health_ok = await self._probe_health()
        except Exception:  # noqa: BLE001
            self._health_ok = False
        self._health_checked_at = now
        return self._health_ok

    def invalidate_health(self):
        """Force the next health() call to do a real probe."""
        self._health_ok = None
        self._health_checked_at = 0.0


# ─────────────────────────────────────────────
# Ollama (Node1 / Node2)
# ─────────────────────────────────────────────
class OllamaProvider(BaseProvider):
    def __init__(self, name: str, base_url: str, timeout: float):
        super().__init__()
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._can_stream = True

    @property
    def supports_streaming(self) -> bool:
        return self._can_stream

    async def chat(self, model_id: str, prompt: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        client = _get_client()
        try:
            resp = await client.post(url, json=payload, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport error: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise ProviderError(f"{self.name} returned an empty response")
        return content.strip()

    async def stream_chat(self, model_id: str, prompt: str):
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        client = _get_client()
        try:
            async with client.stream("POST", url, json=payload, timeout=self.timeout) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(f"{self.name} HTTP {resp.status_code}: {body[:200]}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        break
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        yield token
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} stream timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} stream transport error: {exc}") from exc

    async def _probe_health(self) -> bool:
        try:
            client = _get_client()
            resp = await client.get(f"{self.base_url}/api/tags", timeout=HEALTH_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False


# ─────────────────────────────────────────────
# OpenRouter (Cloud)
# ─────────────────────────────────────────────
class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def __init__(self, url: str, api_key: str, timeout: float):
        super().__init__()
        self.url = url
        self.api_key = api_key
        self.timeout = timeout
        self._can_stream = True

    @property
    def supports_streaming(self) -> bool:
        return self._can_stream

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(self, model_id: str, prompt: str) -> str:
        if not self.api_key:
            raise ProviderError("OPENROUTER_API_KEY is not configured")

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
        }
        client = _get_client()
        try:
            resp = await client.post(
                self.url, json=payload, headers=self._headers(), timeout=self.timeout
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"openrouter timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter transport error: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"openrouter HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content", "")
        if not content:
            raise ProviderError("openrouter returned an empty response")
        return content.strip()

    async def stream_chat(self, model_id: str, prompt: str):
        if not self.api_key:
            raise ProviderError("OPENROUTER_API_KEY is not configured")

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        client = _get_client()
        try:
            async with client.stream(
                "POST", self.url, json=payload, headers=self._headers(),
                timeout=self.timeout,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(f"openrouter HTTP {resp.status_code}: {body[:200]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or [{}]
                    token = (choices[0].get("delta") or {}).get("content", "")
                    if token:
                        yield token
        except httpx.TimeoutException as exc:
            raise ProviderError(f"openrouter stream timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter stream transport error: {exc}") from exc

    async def _probe_health(self) -> bool:
        if not self.api_key:
            return False
        # https://openrouter.ai/api/v1/chat/completions  ->  /api/v1/models
        models_url = self.url.replace("/chat/completions", "/models")
        try:
            client = _get_client()
            resp = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=HEALTH_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─────────────────────────────────────────────
# Singleton provider instances
#   Local  = 12s,  Remote = 18s,  GPT = 30s
# ─────────────────────────────────────────────
node1 = OllamaProvider("node1", _NODE1_URL, LOCAL_TIMEOUT)
node2 = OllamaProvider("node2", _NODE2_URL, REMOTE_TIMEOUT)
openrouter = OpenRouterProvider(_OPENROUTER_URL, _OPENROUTER_KEY, GPT_TIMEOUT)

PROVIDERS = {
    "node1": node1,
    "node2": node2,
    "openrouter": openrouter,
}

# Models to warm up on startup (requirement #7)
WARMUP_MODELS = [
    ("node2", "gemma3:12b"),
    ("node2", "gemma3:12b"),
    ("node2", "phi4-mini:latest"),
]


async def preload_models() -> dict:
    """
    Warm up models on startup (requirement #7 / #8).

    Sends a tiny prompt to each model so the Ollama server loads the weights
    into RAM before the first real user request.  Runs concurrently and never
    raises — failures are logged but do not block startup.
    """
    import asyncio

    async def _warm(provider_name: str, model_id: str) -> tuple[str, str, bool, str]:
        provider = PROVIDERS.get(provider_name)
        if provider is None:
            return (provider_name, model_id, False, "provider not found")
        try:
            await provider.chat(model_id, "hi")
            log.info("Warmup OK | provider=%s model=%s", provider_name, model_id)
            return (provider_name, model_id, True, "ok")
        except Exception as exc:  # noqa: BLE001
            log.warning("Warmup FAILED | provider=%s model=%s | %s", provider_name, model_id, exc)
            return (provider_name, model_id, False, str(exc)[:120])

    results = await asyncio.gather(
        *[_warm(p, m) for p, m in WARMUP_MODELS]
    )
    summary = {
        f"{p}/{m}": {"success": ok, "detail": detail}
        for p, m, ok, detail in results
    }
    log.info("Preload complete | %s", summary)
    return summary

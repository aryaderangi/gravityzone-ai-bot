"""
services/gateway_client.py
==========================
The ONLY async entry point the bot uses to talk to Gravity Gateway.

    from services.gateway_client import ask as gateway_ask

The bot must NEVER call Ollama / OpenRouter directly. Everything goes
through::

    POST http://127.0.0.1:8000/chat

This client is defensive: it NEVER raises. If the gateway is unreachable
or every provider failed, it returns a human-readable error STRING so
that no exception ever reaches bot.py.

Enterprise: now passes ``user`` for per-user rate limiting (Task #5)
and reuses a shared AsyncClient (Task #11).
"""
import logging
import os
import threading

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("gravity.bot_client")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
GATEWAY_TIMEOUT = float(os.getenv("GATEWAY_CLIENT_TIMEOUT", "120"))

# Shared client (Task #11 — no per-call recreation)
_client_lock = threading.Lock()
_shared_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = httpx.AsyncClient(
                    timeout=GATEWAY_TIMEOUT,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                )
    return _shared_client


async def ask(model: str, prompt: str, user: str | None = None) -> str:
    """
    Send a prompt to Gravity Gateway and return the answer text.

    Parameters
    ----------
    model : str
        Friendly alias (gravity-ai, phi, qwen, gemma, deepseek, gpt, auto).
    prompt : str
        The user message.
    user : str | None
        Telegram user id — used by the gateway for per-user rate limiting.

    Returns
    -------
    str
        The model's response text. On total failure an error STRING is
        returned (never raises) so bot.py always gets a str.
    """
    payload = {"model": model or "auto", "prompt": prompt}
    if user:
        payload["user"] = user

    try:
        client = _get_client()
        resp = await client.post(f"{GATEWAY_URL}/chat", json=payload)
    except httpx.HTTPError as exc:
        log.error("Gateway unreachable: %s", exc)
        return "❌ Gateway در دسترس نیست.\nلطفاً چند لحظه بعد تلاش کنید."

    if resp.status_code == 429:
        # Rate limited — return the friendly message (Task #5)
        data = resp.json()
        return data.get("detail", "⏳ درخواست‌های شما بیش از حد مجاز است. یک دقیقه صبر کنید.")

    if resp.status_code != 200:
        log.error("Gateway error %s: %s", resp.status_code, resp.text[:200])
        return "❌ خطای Gateway. لطفاً مدل دیگری امتحان کنید."

    data = resp.json()
    return (data.get("response") or "").strip()


async def health() -> dict:
    """Return the gateway /health payload (or an error dict)."""
    try:
        client = _get_client()
        resp = await client.get(f"{GATEWAY_URL}/health", timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"gateway": "error", "error": str(exc)}

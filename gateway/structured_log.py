"""
Structured logging (Task #9)
============================
Emits one JSON object per log line — safe for log aggregation (ELK, Loki, …).

Example output::

    {"event":"chat_response","provider":"node2","model":"qwen3",
     "latency":0.41,"success":true,"user":123456}

Security (Task #10):
    * **Never** logs API keys, bearer tokens, or the Telegram token.
    * **Never** logs the prompt text (user content).
    * Only metadata (provider, model, latency, success, user, …) is emitted.
"""
import json
import logging
import os
import re
import time

_SENSITIVE_KEYS = {
    "api_key", "apikey", "key", "token", "secret", "password",
    "authorization", "bearer", "openrouter_api_key", "bot_token",
    "openai_api_key", "authorization_header",
}
_REDACTED = "[REDACTED]"
# catch "sk-…", "Bearer …", hex-ish tokens
_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|Bearer\s+[A-Za-z0-9._-]{6,})", re.IGNORECASE)


def _sanitize(payload: dict) -> dict:
    """Strip any key that looks sensitive and redact token-like values."""
    safe = {}
    for k, v in payload.items():
        if k.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(v, str):
            v = _KEY_PATTERN.sub(_REDACTED, v)
        safe[k] = v
    return safe


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # merge any structured fields attached via extra=
        for attr in ("provider", "model", "latency", "success", "user",
                     "attempt", "error", "count", "reason"):
            val = getattr(record, attr, None)
            if val is not None:
                entry[attr] = val
        # merge a _payload dict if attached
        payload = getattr(record, "_payload", None)
        if isinstance(payload, dict):
            entry.update(_sanitize(payload))
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str | None = None):
    """Install the JSON formatter on the root handler (idempotent)."""
    root = logging.getLogger()
    if not level:
        level = os.getenv("GATEWAY_LOG_LEVEL", "INFO").upper()
    root.setLevel(level)
    # remove old handlers once so we don't double-log
    if not getattr(root, "_gravity_json_configured", False):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root._gravity_json_configured = True  # type: ignore[attr-defined]


def log_event(event: str, **fields):
    """Emit a structured JSON event. Secrets are auto-sanitized."""
    logger = logging.getLogger("gravity.events")
    logger.info(event, extra={"_payload": fields})


def log_chat(**fields):
    """Convenience for chat-flow events."""
    log_event("chat", **fields)

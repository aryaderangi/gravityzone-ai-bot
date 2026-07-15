"""
Gravity Gateway
===============
Central async AI routing layer for the GravityZone bot (Enterprise v3.0).

The bot never talks to Ollama / OpenRouter directly — every AI request
flows through one endpoint:

    POST http://127.0.0.1:8000/chat

Structured JSON logging is installed as early as possible (Task #9).
"""
from .structured_log import setup_logging

# Install JSON structured logging before anything else logs.
setup_logging()

# Re-export the FastAPI app for ``uvicorn gateway.app:app``.
from .app import app  # noqa: E402

__all__ = ["app"]
__version__ = "3.0.0"

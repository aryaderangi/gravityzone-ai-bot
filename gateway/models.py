"""Pydantic request / response schemas for the gateway HTTP API."""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Body of POST /chat."""
    model: str = Field(
        default="auto",
        description="Model alias: gravity-ai, phi, qwen, gemma, deepseek, gpt, auto",
    )
    prompt: str = Field(..., description="User prompt / message text")
    # Enterprise additions (backward-compatible — both default off)
    stream: bool = Field(default=False, description="Stream tokens via SSE")
    user: Optional[str] = Field(
        default=None,
        description="Telegram user id (for per-user rate limiting & logging)",
    )


class ChatResponse(BaseModel):
    """Body returned by POST /chat."""
    response: str
    model: str = Field(..., description="Alias that actually answered")
    provider: str = Field(..., description="Provider that answered (node1/node2/openrouter)")
    cached: bool = Field(default=False, description="Whether the response came from cache")


class HealthResponse(BaseModel):
    """Body returned by GET /health."""
    gateway: str
    node1: str
    node2: str
    openrouter: str

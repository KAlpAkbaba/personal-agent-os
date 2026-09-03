"""checks.research (GET /v1/system/health): synthesis posture, no I/O, no secrets."""

from __future__ import annotations

from typing import Any

from app.config import Settings


def research_health(settings: Settings) -> dict[str, Any]:
    anthropic_configured = bool(settings.anthropic_api_key)
    openai_configured = bool(settings.openai_api_key or settings.voice_openai_api_key)
    default = settings.research_default_synthesis
    if default == "auto":
        effective = (
            "anthropic"
            if anthropic_configured
            else "openai"
            if openai_configured
            else "deterministic"
        )
    else:
        effective = default
    return {
        "status": "ok",
        "latency_ms": 0.0,
        "default_synthesis": default,
        "effective_synthesis": effective,
        "providers_configured": {
            "anthropic": anthropic_configured,
            "openai": openai_configured,
            "deterministic": True,
        },
        "worker_mode": settings.worker_mode,
    }


__all__ = ["research_health"]

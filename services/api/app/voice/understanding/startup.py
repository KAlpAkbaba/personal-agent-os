"""Start-up of the semantic engine (ADR-0224 layer 2 in production).

``configure_understanding(settings, embedder, report=...)`` is the ONE call ``create_app``
makes, with the memory runtime's embedder and its report. It configures ``combine``'s
default engine from the shipped exemplars ONLY when that embedder is the local semantic one
(``LocalEmbedder``, ADR-0200: the report says ``active == "local"`` and ``semantic``):

* the deterministic fallback is a lexical hash - it scored "Bugün nasılsın" as weather_query
  0.60 and "Araştırmayı iptal etme" as research_cancel 0.86 - so with it NOTHING is
  configured and one line, ``understanding_engine_not_configured``, says why;
* a semantic provider that is not on this host (OpenAI) is refused too: layer 2 is "no
  network call" by the ADR, and the index build would bill every exemplar.

The index is built off the start-up path, on a daemon thread: until it is done
``default_engine()`` raises, which ``policy.configured_engine()`` already reads as "the rule
tables and layer 1 decide". A build that fails leaves it that way and logs the reason.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.logging import get_logger
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.providers import PROVIDER_LOCAL, EmbedderReport
from app.voice.understanding import combine
from app.voice.understanding.exemplars import load_exemplars

logger = get_logger("app.voice.understanding.startup")

EVENT_NOT_CONFIGURED: Final = "understanding_engine_not_configured"
EVENT_CONFIGURED: Final = "understanding_engine_configured"
_THREAD_NAME: Final = "understanding-index-build"

Spawn = Callable[[Callable[[], None]], Any]


@dataclass(slots=True)
class UnderstandingStartup:
    """What start-up did: ``configured`` once the default engine answers, ``building`` while
    the index is being built, ``reason`` when nothing is (or could be) configured."""

    configured: bool = False
    building: bool = False
    reason: str | None = None
    exemplars: int = 0
    build_ms: float | None = None
    thread: threading.Thread | None = None


def _refusal(settings: Any, embedder: Embedder, report: EmbedderReport) -> str | None:
    """Why this embedder may not decide, or None when it is the local semantic one."""
    if not bool(getattr(settings, "understanding_semantic_enabled", True)):
        return "understanding_semantic_enabled is false"
    if isinstance(embedder, DeterministicEmbedder) or not report.semantic:
        why = report.fallback_reason or "no semantic provider is configured"
        return f"the embedder is the deterministic (lexical) one, not semantic: {why}"
    if report.active != PROVIDER_LOCAL:
        return f"the semantic provider {report.active!r} is not on this host (need 'local')"
    return None


def _thread_spawn(state: UnderstandingStartup) -> Spawn:
    def spawn(build: Callable[[], None]) -> None:
        state.thread = threading.Thread(target=build, name=_THREAD_NAME, daemon=True)
        state.thread.start()

    return spawn


def configure_understanding(
    settings: Any,
    embedder: Embedder,
    *,
    report: EmbedderReport,
    exemplars: Sequence[tuple[str, str]] | None = None,
    spawn: Spawn | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> UnderstandingStartup:
    """Configure the default engine when ``embedder`` is the local semantic one; otherwise
    configure nothing and log the reason. Returns before the index is built.

    ``exemplars``: the shipped file unless given. ``spawn`` runs the build (a daemon thread
    unless given) and ``clock`` times it - both are seams for the tests.
    """
    state = UnderstandingStartup()
    reason = _refusal(settings, embedder, report)
    if reason is not None:
        state.reason = reason
        logger.info(EVENT_NOT_CONFIGURED, reason=reason, provider=report.active)
        return state

    def build() -> None:
        started = clock()
        try:
            rows = list(exemplars) if exemplars is not None else load_exemplars()
            combine.configure_default_engine(embedder, rows)
        except Exception as exc:  # noqa: BLE001 - a dead index is the rule tables, not a dead API
            state.reason = f"index build failed: {type(exc).__name__}"
            state.building = False
            logger.warning(EVENT_NOT_CONFIGURED, reason=state.reason, provider=report.active)
            return
        state.exemplars = len(rows)
        state.build_ms = round((clock() - started) * 1000.0, 1)
        state.configured = True
        state.building = False
        logger.info(
            EVENT_CONFIGURED,
            provider=report.active,
            model_id=report.model_id,
            exemplars=state.exemplars,
            build_ms=state.build_ms,
        )

    state.building = True
    (spawn or _thread_spawn(state))(build)
    return state


__all__ = [
    "EVENT_CONFIGURED",
    "EVENT_NOT_CONFIGURED",
    "UnderstandingStartup",
    "configure_understanding",
]

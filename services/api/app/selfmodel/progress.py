"""UI state publishing for a self-model index run.

While the indexer walks the checkout the owner's surfaces should show THINKING
for the ``self_model`` subsystem -- and nothing else. The metadata carries
counters and a phase name only: never a module id, never a path, never a
docstring. An index run is allowed to say "I am busy, 40% through symbols"; it
is not allowed to leak what it is reading into a UI frame.

``app.uistate`` is owned by another track of this build and may not be present
in every checkout, so this module binds to it at call time if it exists and
falls back to a structured log line otherwise. That keeps the indexer honest
(it always publishes) without importing a package that may not be there yet.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from app.logging import get_logger

logger = get_logger("app.selfmodel.progress")

#: Mirrors ``UiState.THINKING``. Kept as a constant so the indexer never has to
#: import an optional package just to name a state.
UI_STATE_THINKING: Final[str] = "thinking"
UI_STATE_IDLE: Final[str] = "idle"

SUBSYSTEM_SELF_MODEL: Final[str] = "self_model"

#: The closed set of phase names an index run may publish. Anything outside it
#: is rejected rather than forwarded -- a phase name is the only string that
#: reaches the UI, so it must be vocabulary, not free text.
PHASES: Final[tuple[str, ...]] = (
    "discovering",
    "parsing",
    "linking",
    "documenting",
    "provenance",
    "done",
)


class UiStatePublisher(Protocol):
    """What the indexer needs from whatever owns UI state."""

    def __call__(self, state: str, *, subsystem: str, metadata: dict[str, Any]) -> None: ...


def _log_publisher(state: str, *, subsystem: str, metadata: dict[str, Any]) -> None:
    logger.info("ui_state", state=state, subsystem=subsystem, **metadata)


def resolve_publisher() -> UiStatePublisher:
    """Bind to ``app.uistate`` when it exists, else log.

    Looked up by name at call time rather than imported at module load so that
    a checkout without the UI-state track still indexes.
    """
    try:  # pragma: no cover - exercised only where app.uistate is merged
        from app import uistate as _uistate
    except ImportError:
        return _log_publisher

    publish = getattr(_uistate, "publish", None) or getattr(_uistate, "publish_state", None)
    if not callable(publish):  # pragma: no cover - defensive
        return _log_publisher

    state_enum = getattr(_uistate, "UiState", None)
    thinking = getattr(state_enum, "THINKING", None) if state_enum is not None else None
    idle = getattr(state_enum, "IDLE", None) if state_enum is not None else None

    def _bound(state: str, *, subsystem: str, metadata: dict[str, Any]) -> None:
        resolved: Any = state
        if state == UI_STATE_THINKING and thinking is not None:
            resolved = thinking
        elif state == UI_STATE_IDLE and idle is not None:
            resolved = idle
        try:
            publish(resolved, subsystem=subsystem, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 - UI state must never break indexing
            logger.warning("ui_state_publish_failed", error_class=type(exc).__name__)

    return _bound


def safe_metadata(**values: Any) -> dict[str, Any]:
    """Counters, ratios and closed-vocabulary phase names only.

    Any other value is dropped rather than published: this is the single place
    where the index could accidentally hand file contents to a UI, so it fails
    closed.
    """
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if key == "phase":
            if value in PHASES:
                clean[key] = value
            continue
        if isinstance(value, bool) or isinstance(value, int | float):
            clean[key] = value
    return clean


class IndexProgress:
    """Publishes THINKING for ``self_model`` across an index run.

    Progress is reported as integers (``done``/``total``/``percent``) plus the
    current phase. ``finish`` returns the subsystem to idle exactly once.
    """

    def __init__(self, publisher: UiStatePublisher | None = None) -> None:
        self._publish = publisher or resolve_publisher()
        self._finished = False
        #: every frame this run published, for tests and for the index report.
        self.published: list[tuple[str, dict[str, Any]]] = []

    def _emit(self, state: str, metadata: dict[str, Any]) -> None:
        self.published.append((state, metadata))
        self._publish(state, subsystem=SUBSYSTEM_SELF_MODEL, metadata=metadata)

    def phase(self, phase: str, *, done: int = 0, total: int = 0) -> None:
        percent = int(round(100.0 * done / total)) if total > 0 else 0
        self._emit(
            UI_STATE_THINKING,
            safe_metadata(phase=phase, done=done, total=total, percent=percent),
        )

    def finish(self, *, modules: int = 0) -> None:
        if self._finished:
            return
        self._finished = True
        self._emit(UI_STATE_IDLE, safe_metadata(phase="done", done=modules, total=modules))


__all__ = [
    "PHASES",
    "SUBSYSTEM_SELF_MODEL",
    "UI_STATE_IDLE",
    "UI_STATE_THINKING",
    "IndexProgress",
    "UiStatePublisher",
    "resolve_publisher",
    "safe_metadata",
]

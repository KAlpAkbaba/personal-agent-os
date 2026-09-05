"""Action vocabulary, validation and the execution seam (M18 task brief §Actions).

This module NEVER executes a side effect. It validates and normalizes a declarative
descriptor of WHAT should happen — ``app.routines.service`` records that descriptor
verbatim (``RoutineFiring.actions_snapshot``) and hands each one to a
``RoutineDispatcher``, a ``Protocol`` a real executor implements later, once one exists.
``NoopDispatcher`` is the deterministic do-nothing implementation this package and its
tests use in the meantime — it never plays audio, opens a browser tab or touches a display.
The real implementation is ``app.routines.dispatch.ActionDispatcher`` (ADR-0060).

Five closed kinds:

- ``voice_briefing``: text to narrate. Declarative only; narration itself is
  ``app.narration``'s job, not this package's.
- ``alarm``: rings the owner awake. Carries a wake-volume RAMP, not a single level — the
  task brief is explicit that a wake volume must ramp rather than jump to maximum, so
  ``validate_alarm`` refuses a descriptor whose start volume already equals or exceeds a
  full jolt and enforces ``start <= end``.
- ``media_playback``: an owner-NAMED url/title. ``validate_media_playback`` refuses a
  descriptor with no url — this package must never let a routine be created that would
  choose content on the owner's behalf — and otherwise passes the url/title through
  UNCHANGED (no trimming, casing, or rewriting): the task brief's own acceptance test is
  that a media action "preserves the owner's requested item" end to end.
- ``browser_action`` / ``display_action``: free-form declarative descriptors for the
  browser/display subsystems' own vocabularies, which this package does not own or import;
  only that a non-empty ``action`` name is present is checked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

ACTION_KIND_VOICE_BRIEFING = "voice_briefing"
ACTION_KIND_ALARM = "alarm"
ACTION_KIND_MEDIA_PLAYBACK = "media_playback"
ACTION_KIND_BROWSER_ACTION = "browser_action"
ACTION_KIND_DISPLAY_ACTION = "display_action"

ACTION_KINDS: tuple[str, ...] = (
    ACTION_KIND_VOICE_BRIEFING,
    ACTION_KIND_ALARM,
    ACTION_KIND_MEDIA_PLAYBACK,
    ACTION_KIND_BROWSER_ACTION,
    ACTION_KIND_DISPLAY_ACTION,
)

#: Wake-volume ramp defaults (task brief: "configurable wake volume that ramps rather than
#: jumping to maximum"). A caller may override any of the three; validation still refuses a
#: ramp that would start at/above a full jolt.
DEFAULT_WAKE_VOLUME_START = 0.05
DEFAULT_WAKE_VOLUME_END = 0.8
DEFAULT_WAKE_VOLUME_RAMP_SECONDS = 60
#: A start volume at or above this is not a ramp — it is most of the way to a jolt already.
MAX_WAKE_VOLUME_START = 0.5


class InvalidActionDescriptor(ValueError):
    """An action descriptor is malformed, names an unknown kind, or violates a closed
    invariant (e.g. an alarm that does not actually ramp)."""


def _require_dict(detail: Any, *, kind: str) -> dict[str, Any]:
    if detail is None:
        return {}
    if not isinstance(detail, dict):
        raise InvalidActionDescriptor(
            f"{kind} detail must be an object, got {type(detail).__name__}"
        )
    return detail


def validate_voice_briefing(detail: dict[str, Any]) -> dict[str, Any]:
    text = detail.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InvalidActionDescriptor("voice_briefing requires a non-empty 'text'")
    return {**detail, "text": text}


def _wake_volume(raw: dict[str, Any] | None) -> dict[str, float | int]:
    raw = raw or {}
    start = float(raw.get("start", DEFAULT_WAKE_VOLUME_START))
    end = float(raw.get("end", DEFAULT_WAKE_VOLUME_END))
    ramp_seconds = raw.get("ramp_seconds", DEFAULT_WAKE_VOLUME_RAMP_SECONDS)
    if not (0.0 <= start <= 1.0) or not (0.0 <= end <= 1.0):
        raise InvalidActionDescriptor("wake_volume start/end must each be in 0..1")
    if start > end:
        raise InvalidActionDescriptor("wake_volume start must be <= end (it is a RAMP, not a drop)")
    if start >= MAX_WAKE_VOLUME_START:
        raise InvalidActionDescriptor(
            f"wake_volume start must be < {MAX_WAKE_VOLUME_START} — a routine must ramp the "
            "owner awake, never jump straight to a jolt"
        )
    if not isinstance(ramp_seconds, int) or isinstance(ramp_seconds, bool) or ramp_seconds <= 0:
        raise InvalidActionDescriptor("wake_volume ramp_seconds must be a positive int")
    return {"start": start, "end": end, "ramp_seconds": ramp_seconds}


def validate_alarm(detail: dict[str, Any]) -> dict[str, Any]:
    wake_volume = _wake_volume(detail.get("wake_volume"))
    return {**detail, "wake_volume": wake_volume}


def validate_media_playback(detail: dict[str, Any]) -> dict[str, Any]:
    url = detail.get("url")
    if not isinstance(url, str) or not url:
        raise InvalidActionDescriptor(
            "media_playback requires an owner-named 'url' — this package never chooses "
            "content on the owner's behalf"
        )
    # Deliberately no trimming/normalization beyond the object copy: the owner's exact
    # url/title must round-trip unchanged (module docstring).
    return dict(detail)


def validate_browser_action(detail: dict[str, Any]) -> dict[str, Any]:
    action = detail.get("action")
    if not isinstance(action, str) or not action:
        raise InvalidActionDescriptor("browser_action requires a non-empty 'action'")
    return dict(detail)


def validate_display_action(detail: dict[str, Any]) -> dict[str, Any]:
    action = detail.get("action")
    if not isinstance(action, str) or not action:
        raise InvalidActionDescriptor("display_action requires a non-empty 'action'")
    return dict(detail)


_VALIDATORS = {
    ACTION_KIND_VOICE_BRIEFING: validate_voice_briefing,
    ACTION_KIND_ALARM: validate_alarm,
    ACTION_KIND_MEDIA_PLAYBACK: validate_media_playback,
    ACTION_KIND_BROWSER_ACTION: validate_browser_action,
    ACTION_KIND_DISPLAY_ACTION: validate_display_action,
}


def validate_action(raw: dict[str, Any]) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in ACTION_KINDS:
        raise InvalidActionDescriptor(
            f"unknown action kind: {kind!r}; must be one of {ACTION_KINDS}"
        )
    detail = _require_dict(raw.get("detail"), kind=kind)
    normalized_detail = _VALIDATORS[kind](detail)
    return {"kind": kind, "detail": normalized_detail}


# ------------------------------------------------------------------ execution seam


#: DispatchOutcome.status closed vocabulary (M18 dispatch task brief). Distinct from
#: RoutineFiring.dispatch_status (app.routines.models), which is the AGGREGATE across
#: every action in one firing (adds "partial" and "none") — this is the per-action verdict.
DISPATCH_STATUS_SUCCEEDED = "succeeded"
DISPATCH_STATUS_FAILED = "failed"
DISPATCH_STATUS_REFUSED = "refused"

DISPATCH_STATUSES: tuple[str, ...] = (
    DISPATCH_STATUS_SUCCEEDED,
    DISPATCH_STATUS_FAILED,
    DISPATCH_STATUS_REFUSED,
)


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What a dispatcher reports back for one action. Never raises past the caller —
    ``app.routines.service`` treats a dispatcher exception as ``ok=False`` and keeps going,
    the same "never a hard dependency" discipline as ``app.goals.service``'s ledger calls.

    ``status`` is derived from ``ok`` in ``__post_init__`` when the caller does not supply
    one, so ``NoopDispatcher`` and every existing ``DispatchOutcome(ok=..., detail=...)``
    call site (this module, every existing test) keeps constructing a valid instance
    unchanged. New callers should prefer the :meth:`succeeded`/:meth:`failed`/
    :meth:`refused` constructors, which set ``ok`` and ``status`` consistently by
    construction instead of relying on the derivation.
    """

    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    #: closed set: succeeded | failed | refused (see DISPATCH_STATUSES above).
    status: str = ""
    #: short machine-readable reason token; empty for a plain, unremarkable success.
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            object.__setattr__(
                self, "status", DISPATCH_STATUS_SUCCEEDED if self.ok else DISPATCH_STATUS_FAILED
            )
        if self.status not in DISPATCH_STATUSES:
            raise ValueError(
                f"unknown dispatch status: {self.status!r}; must be one of {DISPATCH_STATUSES}"
            )

    @classmethod
    def succeeded(cls, detail: dict[str, Any] | None = None) -> DispatchOutcome:
        return cls(ok=True, detail=dict(detail or {}), status=DISPATCH_STATUS_SUCCEEDED)

    @classmethod
    def failed(cls, reason: str, detail: dict[str, Any] | None = None) -> DispatchOutcome:
        return cls(
            ok=False, detail=dict(detail or {}), status=DISPATCH_STATUS_FAILED, reason=reason
        )

    @classmethod
    def refused(cls, reason: str, detail: dict[str, Any] | None = None) -> DispatchOutcome:
        return cls(
            ok=False, detail=dict(detail or {}), status=DISPATCH_STATUS_REFUSED, reason=reason
        )


class RoutineDispatcher(Protocol):
    """Performs one action. This package only ever calls this — it never plays audio, opens
    a browser tab, or touches a display itself (module docstring)."""

    def dispatch(
        self, *, routine_id: UUID, firing_id: UUID, action: dict[str, Any]
    ) -> DispatchOutcome: ...


class NoopDispatcher:
    """Deterministic do-nothing dispatcher: the default for tests and for any caller that
    has not yet wired a real executor. Always reports ``ok=True`` with an explicit
    ``dispatched: False`` — an execution log built on this never lies about having acted."""

    def dispatch(
        self, *, routine_id: UUID, firing_id: UUID, action: dict[str, Any]
    ) -> DispatchOutcome:
        return DispatchOutcome(ok=True, detail={"dispatched": False, "reason": "noop_dispatcher"})


__all__ = [
    "ACTION_KINDS",
    "ACTION_KIND_ALARM",
    "ACTION_KIND_BROWSER_ACTION",
    "ACTION_KIND_DISPLAY_ACTION",
    "ACTION_KIND_MEDIA_PLAYBACK",
    "ACTION_KIND_VOICE_BRIEFING",
    "DEFAULT_WAKE_VOLUME_END",
    "DEFAULT_WAKE_VOLUME_RAMP_SECONDS",
    "DEFAULT_WAKE_VOLUME_START",
    "DISPATCH_STATUSES",
    "DISPATCH_STATUS_FAILED",
    "DISPATCH_STATUS_REFUSED",
    "DISPATCH_STATUS_SUCCEEDED",
    "DispatchOutcome",
    "InvalidActionDescriptor",
    "MAX_WAKE_VOLUME_START",
    "NoopDispatcher",
    "RoutineDispatcher",
    "validate_action",
    "validate_alarm",
    "validate_browser_action",
    "validate_display_action",
    "validate_media_playback",
    "validate_voice_briefing",
]

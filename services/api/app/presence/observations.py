"""Structured presence observations: the ONLY thing that may cross the
perception boundary (M18_HOLOGRAPHIC_CORE_SPEC.md §2).

CRITICAL PRIVACY INVARIANT — read this before touching the file. Local
perception on the owner's own device looks at a camera frame and is allowed
to hand this process at most seven bounded fields
(``person_present``, ``presence_confidence``, ``activity_level``, ``posture``,
``awake_state``, ``observed_at``, ``source``). The frame itself never leaves
that process (spec §2: "raw frames are never continuously persisted... only
structured observations leave the perception layer"). This module is the
boundary that makes that a property of the CODE, not a promise in a document:
every entry point runs the same screen, and it REFUSES rather than redacts —
there is no safe way to store "most of an image".

The technique is the one already proven twice in this codebase, applied to a
new shape of forbidden content:

* ``app.uistate.publisher.is_forbidden_metadata_key`` refuses a UI-state
  metadata key whose normalized form contains a forbidden substring
  ("text", "transcript", "audio", ...) — content has no business being UI
  state, so it is refused by KEY shape at the point of entry.
* ``app.ledger.screening.screen_text`` refuses (never redacts) at an API
  boundary, because a rejected request is recoverable and a silently
  mutated one is not (that module's docstring).

``screen_observation_payload`` combines both: it refuses by KEY shape
(any key that normalizes to contain "image", "frame", "base64", ...) AND by
VALUE shape (a data: URI or a long base64-looking string under an
otherwise-innocent key), because a privacy boundary that only reads field
names is one rename away from a bypass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

ACTIVITY_LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high")
POSTURES: Final[tuple[str, ...]] = ("unknown", "upright", "resting")
AWAKE_STATES: Final[tuple[str, ...]] = ("awake", "resting", "uncertain")
SOURCES: Final[tuple[str, ...]] = ("camera", "input", "voice", "task")

#: The exact, closed shape of a structured observation (spec §2). Anything
#: outside this set is refused before it is even type-checked.
OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "person_present",
        "presence_confidence",
        "activity_level",
        "posture",
        "awake_state",
        "observed_at",
        "source",
    }
)

#: Key-shape screen: normalize (strip non-alphanumerics, lowercase) then look
#: for a forbidden substring, so "frameBase64", "frame_base64" and
#: "FRAME-BASE64" are all caught by the same two tokens. Extending this list
#: is the one-line fix if a new imagery-shaped field name shows up in review
#: — same discipline as app.uistate.publisher._FORBIDDEN_KEY_PARTS.
_FORBIDDEN_KEY_PARTS: Final[tuple[str, ...]] = (
    "image",
    "img",
    "frame",
    "photo",
    "picture",
    "snapshot",
    "video",
    "clip",
    "blob",
    "base64",
    "jpg",
    "jpeg",
    "png",
    "webp",
    "bitmap",
    "dataurl",
    "screenshot",
    "thumbnail",
)

#: A data: URI carrying an image, however it arrived (an allowed-looking key
#: with a smuggled value).
_DATA_URI_IMAGE_RE = re.compile(r"^data:image/[a-z0-9.+-]+;base64,", re.IGNORECASE)

#: A bare base64 blob long enough to plausibly be image/binary data. None of
#: this schema's seven fields can ever legitimately hold a string this long
#: (the longest legitimate value is a short enum token), so length alone is a
#: safe, cheap tripwire against a value smuggled under an innocent key.
_LOOKS_LIKE_BASE64_BLOB_RE = re.compile(r"^[A-Za-z0-9+/_=-]{128,}$")


def _normalize_key(key: Any) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def is_forbidden_observation_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    return any(part in normalized for part in _FORBIDDEN_KEY_PARTS)


def _looks_like_imagery_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_DATA_URI_IMAGE_RE.match(value)) or bool(_LOOKS_LIKE_BASE64_BLOB_RE.match(value))


class ObservationRejected(ValueError):
    """Raised for anything this boundary refuses: an unknown or
    imagery-shaped field, an imagery-shaped value, or an ordinary schema
    violation. One exception type so every caller — the HTTP route or an
    internal publisher (voice, task) — handles rejection the same way."""


def screen_observation_payload(payload: Any) -> None:
    """Refuse anything imagery-shaped, by key or by value.

    Raises :class:`ObservationRejected`; never mutates or redacts. There is
    no "about to be spoken" step here the way there is for
    ``app.ledger.screening.safe_evidence_text`` — an observation payload is
    either exactly the allowed shape or it is refused outright.
    """
    if not isinstance(payload, dict):
        raise ObservationRejected("observation payload must be a JSON object")
    for key, value in payload.items():
        if is_forbidden_observation_key(key):
            raise ObservationRejected(
                f"field {key!r} is not permitted: PersonalAgentOS never accepts image, "
                "video or binary data at the presence observation boundary "
                "(M18_HOLOGRAPHIC_CORE_SPEC.md §2)"
            )
        if key not in OBSERVATION_FIELDS:
            raise ObservationRejected(f"unknown observation field: {key!r}")
        if _looks_like_imagery_value(value):
            raise ObservationRejected(
                f"value for {key!r} looks like an image/binary blob and was refused "
                "(M18_HOLOGRAPHIC_CORE_SPEC.md §2 privacy invariant)"
            )


@dataclass(frozen=True, slots=True)
class Observation:
    """One structured perception reading. Never a frame, never derived text —
    exactly the seven bounded fields of spec §2."""

    person_present: bool
    presence_confidence: float
    activity_level: str
    posture: str
    awake_state: str
    observed_at: datetime
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_present": self.person_present,
            "presence_confidence": self.presence_confidence,
            "activity_level": self.activity_level,
            "posture": self.posture,
            "awake_state": self.awake_state,
            "observed_at": _iso(self.observed_at),
            "source": self.source,
        }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_observation(payload: dict[str, Any]) -> Observation:
    """Validate a raw payload into an :class:`Observation`.

    Screens for imagery FIRST, before any value is coerced or interpreted —
    a payload that fails the privacy screen must never reach type coercion,
    which could itself be a place a hostile value does something unexpected.
    Every failure mode raises :class:`ObservationRejected`; a caller (the
    HTTP route, or a future internal publisher) never has to distinguish
    "privacy refusal" from "ordinary bad request" — from this boundary's
    perspective they are the same thing: a payload that does not match the
    one shape allowed to cross it.
    """
    screen_observation_payload(payload)
    missing = OBSERVATION_FIELDS - payload.keys()
    if missing:
        raise ObservationRejected(f"missing required field(s): {sorted(missing)}")

    person_present = payload["person_present"]
    if not isinstance(person_present, bool):
        raise ObservationRejected("person_present must be a boolean")

    presence_confidence = payload["presence_confidence"]
    if isinstance(presence_confidence, bool) or not isinstance(presence_confidence, int | float):
        raise ObservationRejected("presence_confidence must be a number")
    presence_confidence = float(presence_confidence)
    if not (0.0 <= presence_confidence <= 1.0):
        raise ObservationRejected("presence_confidence must be within 0..1")

    activity_level = payload["activity_level"]
    if activity_level not in ACTIVITY_LEVELS:
        raise ObservationRejected(f"activity_level must be one of {ACTIVITY_LEVELS}")

    posture = payload["posture"]
    if posture not in POSTURES:
        raise ObservationRejected(f"posture must be one of {POSTURES}")

    awake_state = payload["awake_state"]
    if awake_state not in AWAKE_STATES:
        raise ObservationRejected(f"awake_state must be one of {AWAKE_STATES}")

    source = payload["source"]
    if source not in SOURCES:
        raise ObservationRejected(f"source must be one of {SOURCES}")

    observed_at = payload["observed_at"]
    if isinstance(observed_at, str):
        try:
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ObservationRejected("observed_at must be an ISO-8601 timestamp") from exc
    if not isinstance(observed_at, datetime):
        raise ObservationRejected("observed_at must be a timestamp")
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)

    return Observation(
        person_present=person_present,
        presence_confidence=presence_confidence,
        activity_level=activity_level,
        posture=posture,
        awake_state=awake_state,
        observed_at=observed_at.astimezone(UTC),
        source=source,
    )


__all__ = [
    "ACTIVITY_LEVELS",
    "AWAKE_STATES",
    "OBSERVATION_FIELDS",
    "Observation",
    "ObservationRejected",
    "POSTURES",
    "SOURCES",
    "is_forbidden_observation_key",
    "parse_observation",
    "screen_observation_payload",
]

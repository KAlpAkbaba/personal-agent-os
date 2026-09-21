"""Gesture -> ResolvedIntent table (Stage 1 of "el hareketiyle kumanda").

A hand gesture recognised client-side (apps/web's hand tracker) is DISCRETE and carries
no words, so it never touches the text router (app.voice.intent_router.get_intent_router)
or resolve_intent(): the mapping from a gesture NAME to an intent is fixed and total,
decided here, the one place both the route validator
(app.voice.realtime_sessions.routes.ClientEvent) and the session service
(app.voice.realtime_sessions.service's "gesture" event branch) import from.

The closed set (fixed, owner-decided) and its mapping:

* swipe_left / swipe_right / swipe_up / swipe_down -> OPERATOR_KEY, key_press
  "left"/"right"/"up"/"down".
* rotate_cw / rotate_ccw -> OPERATOR_KEY, key_press "up"/"down" (the focused player's own
  volume keys; never media.volume - see resolve_gesture).
* spread -> OPERATOR_KEY, key_press "f" (fullscreen; YouTube and most web players
  honour the "f" key).
* pinch_start / pinch_release -> recorded (matched, gesture) but Intent.NONE, so
  ``.capability`` is None and no tool is ever named - Stage 2 (pinch-mouse) acts on
  these later. This is intentional: no tool is wired for a pinch yet.
"""

from __future__ import annotations

from app.voice.intents import Intent, ResolvedIntent

GESTURE_SWIPE_LEFT = "swipe_left"
GESTURE_SWIPE_RIGHT = "swipe_right"
GESTURE_SWIPE_UP = "swipe_up"
GESTURE_SWIPE_DOWN = "swipe_down"
GESTURE_ROTATE_CW = "rotate_cw"
GESTURE_ROTATE_CCW = "rotate_ccw"
GESTURE_SPREAD = "spread"
GESTURE_PINCH_START = "pinch_start"
GESTURE_PINCH_RELEASE = "pinch_release"

#: The closed set, fixed and already decided (browser-engineer's hand tracker emits
#: exactly these names) - the one source of truth the route validator (a 422 for
#: anything else) and the session service both import, so it can never drift between
#: "what the route accepts" and "what this table knows how to resolve".
GESTURE_NAMES: tuple[str, ...] = (
    GESTURE_SWIPE_LEFT,
    GESTURE_SWIPE_RIGHT,
    GESTURE_SWIPE_UP,
    GESTURE_SWIPE_DOWN,
    GESTURE_ROTATE_CW,
    GESTURE_ROTATE_CCW,
    GESTURE_SPREAD,
    GESTURE_PINCH_START,
    GESTURE_PINCH_RELEASE,
)

#: "el hareketi" ("hand gesture") - what a gesture-resolved ResolvedIntent carries in
#: ``matched`` in place of the token/phrase an utterance would have matched, so an
#: audit row or the corpus harness can tell a gesture turn from a spoken one at a
#: glance.
MATCHED_GESTURE = "el hareketi"

#: The window a gesture's key goes to - resolved by ``tools_operator._resolve_window_id``
#: against the device's live window list: never the shell's own window, a player's window
#: (YouTube, a film site, VLC) first, else the foreground. Spelled here as a string so this
#: pure table imports nothing from the tools; the tools module asserts the two agree.
WINDOW_REF_MEDIA = "media"

#: swipe direction -> the arrow key OPERATOR_KEY presses.
_ARROW_KEY_BY_SWIPE: dict[str, str] = {
    GESTURE_SWIPE_LEFT: "left",
    GESTURE_SWIPE_RIGHT: "right",
    GESTURE_SWIPE_UP: "up",
    GESTURE_SWIPE_DOWN: "down",
}


#: Recorded but not (yet) wired to a tool - Stage 2 (pinch-mouse) gives these meaning.
_UNWIRED_GESTURES: frozenset[str] = frozenset({GESTURE_PINCH_START, GESTURE_PINCH_RELEASE})


#: The player's OWN volume keys, for a rotate when no owner media session is live:
#: YouTube and most web players raise/lower their volume on the arrow keys, and that is
#: what the owner is watching when they turn the cap (found on the first live trial,
#: 2026-09-21: every rotate reached media.volume and was refused "volume_failed" because
#: the video had been opened by hand, not by "YouTube'u aç").
_PLAYER_VOLUME_KEY_BY_ROTATE: dict[str, str] = {
    GESTURE_ROTATE_CW: "up",
    GESTURE_ROTATE_CCW: "down",
}


def resolve_gesture(gesture: str) -> ResolvedIntent:
    """The one fixed, total mapping from a gesture NAME to a ``ResolvedIntent``.

    Pure and total on its own, and does not trust its caller: a gesture outside the
    closed set raises ``ValueError`` even though the only caller
    (``app.voice.realtime_sessions.service``) can only reach a name the route's own
    validator already accepted against :data:`GESTURE_NAMES` - defence in depth, the
    same discipline every pure table in ``app.voice.intents`` follows.

    A rotate is the FOCUSED PLAYER's own volume keys, never ``media.volume``: that tool
    knows only the media session this service opened, and on the first live trial
    (2026-09-21) a stale "playing" row from the day before kept every rotate on it and
    refused - while the owner was turning the cap at a video opened by hand. The owner
    watches in a browser window either way, and the arrows are that player's volume.
    """
    # Every key a gesture presses goes to the MEDIA window (tools_operator
    # WINDOW_REF_MEDIA): the owner has two screens, the last click was on the cockpit, and
    # "current" sent every arrow to the shell's own window (live trial, 2026-09-21).
    if gesture in _ARROW_KEY_BY_SWIPE:
        return ResolvedIntent(
            intent=Intent.OPERATOR_KEY,
            matched=MATCHED_GESTURE,
            gesture=gesture,
            key_press=_ARROW_KEY_BY_SWIPE[gesture],
            window_ref=WINDOW_REF_MEDIA,
        )
    if gesture in _PLAYER_VOLUME_KEY_BY_ROTATE:
        return ResolvedIntent(
            intent=Intent.OPERATOR_KEY,
            matched=MATCHED_GESTURE,
            gesture=gesture,
            key_press=_PLAYER_VOLUME_KEY_BY_ROTATE[gesture],
            window_ref=WINDOW_REF_MEDIA,
        )
    if gesture == GESTURE_SPREAD:
        return ResolvedIntent(
            intent=Intent.OPERATOR_KEY,
            matched=MATCHED_GESTURE,
            gesture=gesture,
            key_press="f",
            window_ref=WINDOW_REF_MEDIA,
        )
    if gesture in _UNWIRED_GESTURES:
        # Intent.NONE carries no entry in CAPABILITY_BY_INTENT/QUERY_TOOL_BY_INTENT, so
        # ResolvedIntent.capability (and therefore the turn's "tool") stay None - there
        # is no tool to call for a pinch yet, on purpose.
        return ResolvedIntent(
            intent=Intent.NONE,
            matched=MATCHED_GESTURE,
            gesture=gesture,
        )
    raise ValueError(f"unknown gesture: {gesture!r}")


__all__ = ["GESTURE_NAMES", "MATCHED_GESTURE", "resolve_gesture"]

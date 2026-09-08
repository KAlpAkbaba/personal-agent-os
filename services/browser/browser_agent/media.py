"""M18.3 Track B: the alarm media surface (BROWSER_CAPABILITIES.md §1/§2/§3, contract v1.2).

The wake alarm needs a browser that really plays a named piece of music, in a
window that is *ours* — never the owner's own Chrome, never the research
profile, never a tab the owner is using. This module holds everything about
that surface which can be decided WITHOUT a browser: the profile/session-kind
vocabulary, the Chrome launch arguments, the in-page scripts, the ramp-script
generator, and the failure classification. ``browser_agent.worker`` owns the
Playwright calls; every rule below is a pure function so the whole taxonomy is
unit-testable and no test ever launches a browser.

Four rules this module exists to keep honest:

1. **A dedicated alarm profile.** ``profile: "alarm"`` resolves to a persistent
   directory *beside* the research profile (:func:`alarm_profile_dir_for`) and
   is refused if it would equal the research profile. ``ManagedBackend``
   independently refuses any path inside a real browser profile tree, so the
   owner's own ``User Data`` is unreachable from here by construction.
2. **The autoplay flag is a preference for our own window, not a bypass.**
   ``--autoplay-policy=no-user-gesture-required`` tells Chrome that in THIS
   dedicated profile a page may start audio without a click. It is not an
   anti-bot measure, it defeats no site protection, and it is applied only to
   a ``session_kind="media"`` launch (:func:`media_launch_args`). Nothing else
   about the browser is spoofed or masked.
3. **Playback is verified, never assumed.** ``media_play`` sets the volume
   first, calls ``play()``, and then proves the media element's own
   ``currentTime`` advanced (:data:`VERIFY_MIN_ADVANCE_S`) while
   ``paused === false``. A result that says ``verified: true`` means the page
   really moved.
4. **A wall is reported, never opened.** A CAPTCHA, a sign-in wall or a
   consent dialog the page cannot leave is a *reason* on a successful command
   (:func:`classify_landing`, :func:`classify_missing_media`). The worker
   never clicks it, never retries it, never routes around it, and never
   touches DRM, ads or downloads.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .errors import BrowserError, ErrorClass
from .search_engines import detect_google_interstitial

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

#: ``session_open.session_kind`` values (contract §2). ``research`` is the
#: historical behaviour and stays the default for every existing consumer.
RESEARCH_SESSION_KIND = "research"
MEDIA_SESSION_KIND = "media"
SESSION_KINDS: frozenset[str] = frozenset({RESEARCH_SESSION_KIND, MEDIA_SESSION_KIND})

#: ``session_open.profile`` values (contract §2).
RESEARCH_PROFILE = "research"
ISOLATED_PROFILE = "isolated"
ALARM_PROFILE = "alarm"
#: v1.3 (Latest News Mode): a THIRD dedicated persistent profile. News playback opens a
#: real YouTube watch page and reuses the same proven ``media_play``/``media_status``/
#: ``media_stop`` verification the alarm surface already has (a raw ``<video>`` element,
#: consent-wall/challenge classification and all) - but it must never share the alarm's
#: profile (a news video must never be able to interrupt or replace the wake song) and
#: must never share the research profile (M13's one-owner-per-profile lifecycle guard
#: would otherwise make a live research run and a spoken "haberleri aç" fight over the
#: same browser). ``session_kind="media"`` is allowed on this profile the same way it is
#: on ``alarm`` (the guard below only ties ``alarm`` to requiring ``media``, and only
#: refuses ``media`` on ``research`` - a media session on ``news`` was never excluded).
NEWS_PROFILE = "news"
PROFILES: frozenset[str] = frozenset(
    {RESEARCH_PROFILE, ISOLATED_PROFILE, ALARM_PROFILE, NEWS_PROFILE}
)

#: Chrome switch applied ONLY to a media session's own dedicated window.
#: It is a preference for our own window and is not an anti-bot measure: it
#: changes how OUR profile treats OUR page's ``play()`` call, and has no effect
#: on any site's bot detection, DRM, advertising or consent handling.
AUTOPLAY_POLICY_ARG = "--autoplay-policy=no-user-gesture-required"

#: ``media_play`` waits at most this long for a ``<video>`` element (spec §4).
VIDEO_WAIT_TIMEOUT_MS = 10_000
#: ``currentTime`` must advance by at least this much over ``verify_seconds``.
VERIFY_MIN_ADVANCE_S = 0.5
#: ``verify_seconds`` bounds (spec §4 payload).
MIN_VERIFY_SECONDS = 1
MAX_VERIFY_SECONDS = 10
#: In-page ramp step (spec §4: 250 ms steps).
RAMP_STEP_MS = 250
#: ``ramp_seconds`` bounds (spec §4 payload).
MAX_RAMP_SECONDS = 120.0
#: A ramp this short or shorter is awaited to completion before the op returns;
#: a longer one returns as soon as the in-page interval has started.
RAMP_AWAIT_CEILING_S = 2.0

#: Every value ``media_play.reason`` may take. ``None`` means "played, verified".
MEDIA_FAILURE_REASONS: tuple[str, ...] = (
    "no_media_element",
    "autoplay_blocked",
    "challenge",
    "consent_wall",
    "navigation_failed",
    "error",
)

#: ``page_kind`` values that mean a wall stands between us and the media and we
#: will not open it (contract §3 vocabulary; CAPTCHA and sign-in wall).
_CHALLENGE_PAGE_KINDS: frozenset[str] = frozenset({"captcha", "auth_wall", "blocked"})

#: Hosts that serve nothing but a consent gate. Checked on the FINAL url, so a
#: redirect into one is caught even when the requested url looked ordinary.
_CONSENT_HOST_PREFIXES: tuple[str, ...] = (
    "https://consent.youtube.",
    "http://consent.youtube.",
    "https://consent.google.",
    "http://consent.google.",
)

#: Consent wording, consulted ONLY after no media element appeared (see
#: :func:`classify_missing_media`). A cookie banner floating over a video that
#: plays anyway is not a consent wall and must not be reported as one.
_CONSENT_TEXT_MARKERS: tuple[str, ...] = (
    "before you continue to youtube",
    "before you continue to google",
    "youtube'a devam etmeden önce",
    "youtube'a devam etmeden once",
    "devam etmeden önce",
    "accept all",
    "reject all",
    "tümünü kabul et",
    "tumunu kabul et",
    "tümünü reddet",
    "tumunu reddet",
    "çerezleri kabul",
    "cerezleri kabul",
    "manage privacy settings",
    "gizlilik ayarlarını yönet",
)

#: The DOMException name Chrome raises when its autoplay policy refused the
#: ``play()`` call. Reported truthfully; never worked around beyond the
#: profile preference above.
AUTOPLAY_BLOCKED_ERROR_NAME = "NotAllowedError"


# --------------------------------------------------------------------------- #
# profile resolution
# --------------------------------------------------------------------------- #


def alarm_profile_dir_for(research_profile_dir: Path) -> Path:
    """The alarm profile directory: a sibling of the research profile.

    ``<...>/profile`` -> ``<...>/profile-alarm``. Deliberately derived rather
    than configured so the two can never collapse onto one directory by a
    configuration mistake — two Chrome instances on one profile directory is
    the exact shape of the 2026-09-03 owner-machine incident (ADR-0050
    addendum). ``ManagedBackend`` rejects real browser profile trees for both.
    """
    return research_profile_dir.parent / f"{research_profile_dir.name}-alarm"


def news_profile_dir_for(research_profile_dir: Path) -> Path:
    """The news profile directory: a second sibling of the research profile,
    ``<...>/profile`` -> ``<...>/profile-news`` (the same derivation
    :func:`alarm_profile_dir_for` uses for its own sibling, so the three
    persistent profiles can never collapse onto one directory by a
    configuration mistake)."""
    return research_profile_dir.parent / f"{research_profile_dir.name}-news"


def _require_distinct(candidate: Path, candidate_label: str, other: Path, other_label: str) -> None:
    """Refuse ``candidate`` when it is (or is inside, or contains) ``other``."""
    try:
        candidate_resolved = candidate.resolve()
        other_resolved = other.resolve()
    except OSError:  # unresolvable path: judge what we were given
        candidate_resolved, other_resolved = candidate, other
    same = candidate_resolved == other_resolved
    nested = not same and (
        candidate_resolved.is_relative_to(other_resolved)
        or other_resolved.is_relative_to(candidate_resolved)
    )
    if same or nested:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"the {candidate_label} profile directory must be separate from the "
            f"{other_label} profile ({candidate_resolved} vs {other_resolved})",
            retryable=False,
            evidence={f"{candidate_label}_profile_dir": str(candidate_resolved)},
        )


def require_distinct_alarm_profile(alarm_dir: Path, research_dir: Path) -> None:
    """Refuse an alarm profile that is (or is inside) the research profile."""
    _require_distinct(alarm_dir, "alarm", research_dir, "research")


def require_distinct_news_profile(news_dir: Path, research_dir: Path, alarm_dir: Path) -> None:
    """Refuse a news profile that collides with EITHER other persistent profile: a news
    video must never be able to land in the research browser (the M13 one-owner-per-
    profile lifecycle guard would otherwise make research and news fight over the same
    Chrome) or in the alarm browser (a news video must never be able to interrupt or
    replace the owner's wake song)."""
    _require_distinct(news_dir, "news", research_dir, "research")
    _require_distinct(news_dir, "news", alarm_dir, "alarm")


def media_launch_args(session_kind: str) -> list[str]:
    """Extra Chrome switches for a session of this kind.

    ONLY a media session gets the autoplay preference; a research session's
    Chrome is launched exactly as it always was.
    """
    if session_kind == MEDIA_SESSION_KIND:
        return [AUTOPLAY_POLICY_ARG]
    return []


# --------------------------------------------------------------------------- #
# payload validation (pure)
# --------------------------------------------------------------------------- #


def parse_level(value: object, *, field: str, op: str) -> float:
    """A 0..1 volume level. Bools are rejected (``True`` is not a volume)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{op}: '{field}' must be a number between 0 and 1",
            retryable=False,
        )
    level = float(value)
    if not (0.0 <= level <= 1.0) or math.isnan(level):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{op}: '{field}' must be between 0 and 1, got {value!r}",
            retryable=False,
        )
    return level


def parse_verify_seconds(value: object) -> int:
    if value is None:
        return 3
    if isinstance(value, bool) or not isinstance(value, int):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"media_play: 'verify_seconds' must be an integer in "
            f"{MIN_VERIFY_SECONDS}..{MAX_VERIFY_SECONDS}",
            retryable=False,
        )
    if not (MIN_VERIFY_SECONDS <= value <= MAX_VERIFY_SECONDS):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"media_play: 'verify_seconds' must be in "
            f"{MIN_VERIFY_SECONDS}..{MAX_VERIFY_SECONDS}, got {value!r}",
            retryable=False,
        )
    return int(value)


def parse_ramp_seconds(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"media_volume: 'ramp_seconds' must be a number in 0..{MAX_RAMP_SECONDS:g}",
            retryable=False,
        )
    ramp = float(value)
    if math.isnan(ramp) or not (0.0 <= ramp <= MAX_RAMP_SECONDS):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"media_volume: 'ramp_seconds' must be in 0..{MAX_RAMP_SECONDS:g}, got {value!r}",
            retryable=False,
        )
    return ramp


def ramp_step_count(ramp_seconds: float, *, step_ms: int = RAMP_STEP_MS) -> int:
    """How many 250 ms steps this ramp takes. ``0`` means "set it at once"."""
    if ramp_seconds <= 0:
        return 0
    return max(1, round(ramp_seconds * 1000.0 / step_ms))


# --------------------------------------------------------------------------- #
# in-page scripts
# --------------------------------------------------------------------------- #

#: Window property holding the running ramp's interval handle, so a new ramp
#: cancels the previous one instead of two intervals fighting over ``volume``.
RAMP_HANDLE_PROPERTY = "__pagentosMediaRamp"

#: Set the volume FIRST (spec §4 is explicit about the order: the owner must
#: never be hit by full-volume audio for the instant before a ramp starts),
#: then unmute, then ``play()``. A rejected ``play()`` is reported by its
#: DOMException name — never retried, never "helped".
MEDIA_PLAY_JS = """
async (volume) => {
  const v = document.querySelector('video');
  if (!v) { return {present: false}; }
  v.volume = volume;
  v.muted = false;
  const started_at = v.currentTime;
  try {
    await v.play();
  } catch (err) {
    return {
      present: true,
      played: false,
      error_name: (err && err.name) ? String(err.name) : 'Error',
      error_message: (err && err.message) ? String(err.message).slice(0, 200) : '',
      current_time: v.currentTime,
      duration: Number.isFinite(v.duration) ? v.duration : null,
      volume: v.volume,
      muted: v.muted,
      paused: v.paused
    };
  }
  return {
    present: true,
    played: true,
    started_at: started_at,
    current_time: v.currentTime,
    duration: Number.isFinite(v.duration) ? v.duration : null,
    volume: v.volume,
    muted: v.muted,
    paused: v.paused
  };
}
"""

#: A pure read of the media element (``media_status``, and the verification
#: read at the end of ``media_play``). Touches nothing.
MEDIA_READ_JS = """
() => {
  const v = document.querySelector('video');
  if (!v) { return {present: false}; }
  return {
    present: true,
    paused: v.paused,
    ended: v.ended,
    current_time: v.currentTime,
    duration: Number.isFinite(v.duration) ? v.duration : null,
    volume: v.volume,
    muted: v.muted
  };
}
"""

#: Pause without closing anything (``media_stop`` pauses, then the session is
#: closed by the ordinary session_close path).
MEDIA_PAUSE_JS = """
() => {
  const v = document.querySelector('video');
  if (!v) { return {present: false}; }
  const was_playing = !v.paused && !v.ended;
  v.pause();
  return {present: true, was_playing: was_playing, paused: v.paused};
}
"""


def build_volume_ramp_script(
    level_to: float,
    ramp_seconds: float,
    *,
    step_ms: int = RAMP_STEP_MS,
    handle_property: str = RAMP_HANDLE_PROPERTY,
) -> str:
    """Generate the in-page ramp script (a pure function; no browser).

    The ramp runs INSIDE the page on a ``setInterval`` so the worker does not
    have to hold a command open for the whole ramp — ``media_volume`` returns
    once the interval is armed. The script:

    - reads the element's CURRENT volume as ``level_from`` (the caller never
      guesses it);
    - cancels any ramp already running (``handle_property``) so two overlapping
      ramps can never fight — the duck/restore pair around the alarm greeting
      issues three ramps in a few seconds;
    - steps linearly every ``step_ms`` and clamps every write to 0..1;
    - lands exactly on ``level_to`` on the final step and clears its handle;
    - with ``ramp_seconds <= 0`` sets the level at once and arms nothing.

    Numbers are embedded as JSON literals from already-validated floats, so the
    generated source has no interpolation of anything caller-controlled beyond
    two bounded numbers. The result is an ARROW FUNCTION, like every other
    script in this module, so Playwright always calls it rather than having to
    guess whether the source is an expression.
    """
    steps = ramp_step_count(ramp_seconds, step_ms=step_ms)
    to_literal = json.dumps(round(level_to, 6))
    steps_literal = json.dumps(steps)
    step_ms_literal = json.dumps(int(step_ms))
    handle = f"window[{json.dumps(handle_property)}]"
    return f"""
() => {{
  const v = document.querySelector('video');
  if (!v) {{ return {{applied: false, level_from: null, steps: 0}}; }}
  if ({handle}) {{ clearInterval({handle}); {handle} = null; }}
  const from = v.volume;
  const to = {to_literal};
  const steps = {steps_literal};
  const clamp = (x) => Math.min(1, Math.max(0, x));
  if (steps <= 0) {{
    v.volume = clamp(to);
    return {{applied: true, level_from: from, steps: 0}};
  }}
  let i = 0;
  {handle} = setInterval(() => {{
    i += 1;
    if (i >= steps) {{
      v.volume = clamp(to);
      clearInterval({handle});
      {handle} = null;
      return;
    }}
    v.volume = clamp(from + (to - from) * (i / steps));
  }}, {step_ms_literal});
  return {{applied: true, level_from: from, steps: steps}};
}}
"""


# --------------------------------------------------------------------------- #
# failure classification (pure)
# --------------------------------------------------------------------------- #


def detect_media_interstitial(final_url: str | None, body_text: str) -> str | None:
    """``"captcha"`` / ``"consent"`` / ``None`` for a page we landed on.

    Reuses the existing Google interstitial detector (contract §3a) and adds
    the consent hosts the media path actually meets (``consent.youtube.*``).
    Detection only — neither kind is ever answered.
    """
    lowered_url = (final_url or "").lower()
    if lowered_url.startswith(_CONSENT_HOST_PREFIXES):
        return "consent"
    return detect_google_interstitial(body_text or "", final_url)


def classify_landing(*, page_kind: str, final_url: str | None, body_text: str) -> str | None:
    """The failure reason a landed page already proves, before we look for a video.

    Returns ``None`` when nothing is in the way and the caller should go on to
    wait for the media element.
    """
    interstitial = detect_media_interstitial(final_url, body_text)
    if interstitial == "consent":
        return "consent_wall"
    if interstitial == "captcha":
        return "challenge"
    if page_kind in _CHALLENGE_PAGE_KINDS:
        return "challenge"
    if page_kind == "error_page":
        return "navigation_failed"
    return None


def classify_missing_media(body_text: str) -> str:
    """No ``<video>`` appeared within the wait: was a consent gate the reason?

    Consent WORDING alone is never enough (every second page carries a cookie
    banner); it counts only here, where we can also say the page never produced
    a media element for us — a dialog the page could not leave without a click
    we will not make.
    """
    lowered = (body_text or "").lower()
    if any(marker in lowered for marker in _CONSENT_TEXT_MARKERS):
        return "consent_wall"
    return "no_media_element"


def classify_play_error(error_name: str | None) -> str:
    """``play()`` rejected: Chrome's autoplay refusal, or anything else."""
    if (error_name or "") == AUTOPLAY_BLOCKED_ERROR_NAME:
        return "autoplay_blocked"
    return "error"


def verified_from_readings(
    *, started_at: float | None, current_time: float | None, paused: bool
) -> bool:
    """The only definition of "it is really playing" this package accepts."""
    if paused or started_at is None or current_time is None:
        return False
    return (current_time - started_at) >= VERIFY_MIN_ADVANCE_S


__all__ = [
    "ALARM_PROFILE",
    "AUTOPLAY_BLOCKED_ERROR_NAME",
    "AUTOPLAY_POLICY_ARG",
    "ISOLATED_PROFILE",
    "MAX_RAMP_SECONDS",
    "MAX_VERIFY_SECONDS",
    "MEDIA_FAILURE_REASONS",
    "MEDIA_PAUSE_JS",
    "MEDIA_PLAY_JS",
    "MEDIA_READ_JS",
    "MEDIA_SESSION_KIND",
    "MIN_VERIFY_SECONDS",
    "NEWS_PROFILE",
    "PROFILES",
    "RAMP_AWAIT_CEILING_S",
    "RAMP_HANDLE_PROPERTY",
    "RAMP_STEP_MS",
    "RESEARCH_PROFILE",
    "RESEARCH_SESSION_KIND",
    "SESSION_KINDS",
    "VERIFY_MIN_ADVANCE_S",
    "VIDEO_WAIT_TIMEOUT_MS",
    "alarm_profile_dir_for",
    "build_volume_ramp_script",
    "classify_landing",
    "classify_missing_media",
    "classify_play_error",
    "detect_media_interstitial",
    "media_launch_args",
    "news_profile_dir_for",
    "parse_level",
    "parse_ramp_seconds",
    "parse_verify_seconds",
    "ramp_step_count",
    "require_distinct_alarm_profile",
    "require_distinct_news_profile",
    "verified_from_readings",
]

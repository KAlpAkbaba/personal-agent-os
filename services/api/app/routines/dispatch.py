"""The real ``RoutineDispatcher`` (M18 §3/§4, ADR-0060).

``app.routines.actions.RoutineDispatcher`` is a ``Protocol``; until this module existed the
only implementation was ``NoopDispatcher``, so a routine could decide, record a
``RoutineFiring`` and write ledger events — and then nothing ever happened. ``ActionDispatcher``
closes that gap by routing each of the six action kinds to the subsystem that owns it,
through two small ``Protocol`` ports so this module is unit-testable with no network, no
device, no audio and no browser:

* :class:`BriefingPort` — narrate a ``voice_briefing`` to the owner's live realtime
  companion. Its one real implementation is :class:`RealtimeSayBriefing`.
* :class:`DeviceActionPort` — run one device command over the broker path. Its one real
  implementation is :class:`BrokerDeviceAction`, which is the SAME path
  ``app.research.browser_activities.select_device_activity`` uses: ``select_device`` over
  ``app.devices.service.list_device_views``, then ``app.devices.commands.DeviceCommandClient``.

Every test fakes these two ports; nothing in this module's own test suite opens a browser,
plays audio, changes system volume or touches a display (``services/browser/tests/
test_test_isolation_guards.py`` is the reason that boundary exists at all).

Routing summary — which action kinds execute end to end today vs. which are wired against a
capability that does not exist on the device side yet:

* ``voice_briefing`` — executes end to end today, against whatever realtime companion is
  actually live.
* ``alarm`` — wired against ``desktop.alarm_start``, a capability the Windows agent is
  building in parallel on another branch of this same worktree. Until it advertises that
  capability, dispatch fails honestly with ``no_capable_device`` rather than pretending to
  have rung anything.
* ``media_playback`` / ``browser_action`` — execute end to end against any device that
  advertises the ``browser.chrome`` family (already real and qualified, M13).
* ``display_action`` — refused, always, on purpose (see ``DISPLAY_ACTION_QUALIFIED`` below).
  This is not "not implemented yet"; it is "built but deliberately unreachable" until its
  own separate owner qualification exists (M18 spec §4, §7's last line).
* ``wake_alarm`` (M18.3) — handed to :class:`WakeAlarmPort`, whose one implementation is
  ``app.alarms.routine_port.WakeAlarmRunner``. A third port, for the same reason as the
  first two: the whole wake sequence is a subsystem this package must not import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandSucceeded,
    DeviceCommandClient,
    DeviceCommandClientProtocol,
    get_broker_runtime,
)
from app.devices.selection import NoCapableDeviceError, select_device
from app.devices.service import list_device_views
from app.logging import get_logger
from app.narration.normalizer import normalize
from app.narration.service import pronunciation_map
from app.routines.actions import (
    ACTION_KIND_ALARM,
    ACTION_KIND_BROWSER_ACTION,
    ACTION_KIND_DISPLAY_ACTION,
    ACTION_KIND_MEDIA_PLAYBACK,
    ACTION_KIND_VOICE_BRIEFING,
    ACTION_KIND_WAKE_ALARM,
    DEFAULT_WAKE_VOLUME_END,
    DEFAULT_WAKE_VOLUME_RAMP_SECONDS,
    DEFAULT_WAKE_VOLUME_START,
    MAX_WAKE_VOLUME_START,
    DispatchOutcome,
    RoutineDispatcher,
)
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CREATED,
    RealtimeSessionRow,
)
from app.voice.realtime_sessions.sideband import SB_SAY, SidebandPusher, sideband_frame

logger = get_logger("app.routines.dispatch")

# ------------------------------------------------------------------------------- ports


@dataclass(frozen=True, slots=True)
class BriefingDelivery:
    """What actually happened when a ``voice_briefing`` was pushed to the owner.

    ``delivered`` is the sideband push's own return value and NOTHING else (ADR-0060): not
    "a session existed", not "the text was queued" — whether the frame reached a live
    device. That is the one fact the greeting cooldown is allowed to depend on.
    """

    delivered: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class BriefingPort(Protocol):
    def narrate(self, *, text: str, routine_id: UUID, firing_id: UUID) -> BriefingDelivery: ...


@dataclass(frozen=True, slots=True)
class DeviceRunResult:
    """What actually happened when one device command ran (or could not)."""

    ok: bool
    error_class: str = ""
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)


class DeviceActionPort(Protocol):
    def run(
        self,
        *,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
    ) -> DeviceRunResult: ...


class WakeAlarmPort(Protocol):
    """Runs the whole M18.3 wake sequence for one alarm (spec §3.5).

    A ``Protocol`` here and an implementation in ``app.alarms.routine_port`` — deliberately
    that way round. ``app.alarms`` creates routines (it imports ``app.routines.service``),
    so a direct import back would be a cycle; more importantly the wake sequence is not
    this package's business. This package fires triggers; what a wake alarm DOES is the
    alarms package's, and the seam is one method.
    """

    def fire(
        self, *, alarm_id: UUID, routine_id: UUID, firing_id: UUID
    ) -> DispatchOutcome: ...


# --------------------------------------------------------------- real: voice briefing


class RealtimeSayBriefing:
    """Narrates a ``voice_briefing`` action over the owner's live realtime companion.

    The path (ADR-0060): normalize the text through the existing narration pipeline
    (``app.narration.normalizer.normalize`` with the owner's pronunciation map), find the
    single live ``RealtimeSessionRow`` (single-owner system — there is at most one), and
    push a ``say`` sideband frame (``SB_SAY``) to its bound device. ``SB_SAY`` was declared
    in ``app.voice.realtime_sessions.sideband`` with no producer anywhere in the codebase
    before this; this is its first one.

    ``delivered`` is exactly ``SidebandPusher.push(...)``'s boolean return — never "a
    session existed" or "the frame was built". A caller with no live realtime session, no
    device bound to it, or a push that fails at the transport all get ``delivered=False``:
    the honest answer is "nobody heard this", not "we tried".
    """

    def __init__(self, *, session_factory: sessionmaker[Session], sideband: SidebandPusher) -> None:
        self._session_factory = session_factory
        self._sideband = sideband

    def narrate(self, *, text: str, routine_id: UUID, firing_id: UUID) -> BriefingDelivery:
        session = self._session_factory()
        try:
            normalized = normalize(text, mode="narration", pronunciation=pronunciation_map(session))
            row = self._live_session(session)
            if row is None:
                return BriefingDelivery(False, "no_live_session")
            if row.device_id is None:
                return BriefingDelivery(False, "no_bound_device", {"session_id": str(row.id)})
            frame = sideband_frame(
                row.id,
                SB_SAY,
                {"text": normalized, "routine_id": str(routine_id), "firing_id": str(firing_id)},
            )
            delivered = self._sideband.push(device_id=row.device_id, frame=frame)
            reason = "delivered" if delivered else "push_failed"
            return BriefingDelivery(delivered, reason, {"session_id": str(row.id)})
        finally:
            session.close()

    @staticmethod
    def _live_session(session: Session) -> RealtimeSessionRow | None:
        now = datetime.now(UTC)
        stmt = (
            select(RealtimeSessionRow)
            .where(
                RealtimeSessionRow.state.in_((REALTIME_STATE_CREATED, REALTIME_STATE_ACTIVE)),
                RealtimeSessionRow.expires_at > now,
            )
            .order_by(RealtimeSessionRow.created_at.desc())
        )
        return session.execute(stmt).scalars().first()


# ---------------------------------------------------------------- real: device action


class BrokerDeviceAction:
    """Runs one device command over the SAME proven device/broker path
    ``app.research.browser_activities.select_device_activity`` uses: select a device by
    capability over the live device views, then ``DeviceCommandClient.run``.

    ``NoCapableDeviceError`` never escapes this class — it becomes a failed
    :class:`DeviceRunResult` with ``error_class="no_capable_device"`` (task brief).
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        command_client: DeviceCommandClientProtocol | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._command_client: DeviceCommandClientProtocol = command_client or DeviceCommandClient(
            session_factory
        )

    def run(
        self,
        *,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
    ) -> DeviceRunResult:
        runtime = get_broker_runtime()
        if runtime is None:
            return DeviceRunResult(
                False, "dependency_unavailable", "broker runtime not registered in this process"
            )

        session = self._session_factory()
        try:
            views = list_device_views(session, runtime)
        finally:
            session.close()

        try:
            selection = select_device(views, capability=capability)
        except NoCapableDeviceError as exc:
            return DeviceRunResult(False, "no_capable_device", exc.detail_tr)

        outcome = self._command_client.run(
            device_id=selection.device.id,
            capability=capability,
            payload=payload,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
            trace_id=idempotency_key,
        )
        if isinstance(outcome, CommandSucceeded):
            return DeviceRunResult(True, result=dict(outcome.result))
        if isinstance(outcome, CommandFailed):
            return DeviceRunResult(False, outcome.error_class, outcome.message)
        if isinstance(outcome, CommandExpired):
            return DeviceRunResult(False, "timeout", "command expired before completion")
        # pragma: no cover - every DeviceCommandClient outcome type is handled above
        return DeviceRunResult(False, "internal_bug", f"unexpected outcome: {outcome!r}")


# ------------------------------------------------------------------------ browser allowlist

#: BROWSER_CAPABILITIES.md §1's per-operation names (the "browser." prefix stripped), also
#: mirrored device-side in devices/windows-agent's
#: PagentOS.Agent.Core.Protocol.ProtocolConstants.BrowserCapabilities.Operations. A
#: browser_action naming anything outside this set is refused (never silently dropped, never
#: forwarded to the device on a guess) — read only, never edited: change the document first.
BROWSER_ACTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        "session_open",
        "session_close",
        "worker_status",
        "navigate",
        "back",
        "forward",
        "tab_list",
        "tab_new",
        "tab_close",
        "tab_select",
        "inspect",
        "find",
        "click",
        "fill",
        "select_option",
        "set_checked",
        "scroll",
        "wait",
        "extract",
        "snapshot",
        "screenshot",
        "download",
        "search",
        "fetch_evidence",
        # BROWSER_CAPABILITIES.md v1.2 (M18.3 spec §4): the four media operations the
        # browser worker gained for the wake alarm. The cloud side of the allowlist is
        # Track C's (this file); the worker, the protocol constants and the companion host
        # allowlist are Track B's. Listed here so a wake alarm can reach them at all —
        # without these four names a media_play is refused by this allowlist before the
        # device is ever asked, and the alarm falls back to the tone for a reason that
        # would have been a bug rather than a device fact.
        "media_play",
        "media_volume",
        "media_status",
        "media_stop",
    }
)

CAPABILITY_DESKTOP_ALARM_START = "desktop.alarm_start"
CAPABILITY_BROWSER_SESSION_OPEN = "browser.session_open"
CAPABILITY_BROWSER_NAVIGATE = "browser.navigate"

#: M18.3 device capabilities (spec §5). Named here, beside the alarm capability that was
#: already here, because ``app.alarms`` dispatches every one of them through this module's
#: ``DeviceActionPort`` — one place names what Cloud Core may ask a device to do, so a
#: capability the Windows agent has not shipped yet fails as ``no_capable_device`` at
#: selection rather than as a typo nobody notices.
CAPABILITY_DESKTOP_ALARM_STOP = "desktop.alarm_stop"
CAPABILITY_DESKTOP_ALARM_ARM = "desktop.alarm_arm"
CAPABILITY_DESKTOP_ALARM_DISARM = "desktop.alarm_disarm"
CAPABILITY_DESKTOP_DISPLAY_WAKE = "desktop.display_wake"
CAPABILITY_DESKTOP_DISPLAY_OFF = "desktop.display_off"
CAPABILITY_DESKTOP_DISPLAY_STATUS = "desktop.display_status"
CAPABILITY_DESKTOP_ACTIVITY_STATUS = "desktop.activity_status"
CAPABILITY_DESKTOP_PLAY_AUDIO = "desktop.play_audio"
CAPABILITY_BROWSER_MEDIA_PLAY = "browser.media_play"
CAPABILITY_BROWSER_MEDIA_VOLUME = "browser.media_volume"
CAPABILITY_BROWSER_MEDIA_STATUS = "browser.media_status"
CAPABILITY_BROWSER_MEDIA_STOP = "browser.media_stop"

#: Display power is the one machine-state action this milestone builds but does not wire
#: live (M18 spec §4, §7's last line: "Display-off gets its own separate qualification").
#: The Windows-side capability (`desktop.display_off`) is real; what is missing is the
#: owner-visible qualification run that would make firing it from an unattended routine
#: safe. Flipping this constant is therefore a PRODUCT decision, not a bugfix — it belongs
#: behind one obvious named gate rather than a scattered "TODO: enable me" comment, so a
#: future change is a one-line, reviewable diff instead of a rewrite of this method.
DISPLAY_ACTION_QUALIFIED = False


def _assert_wake_volume_ramp(wake_volume: dict[str, Any]) -> tuple[bool, str]:
    """Defence in depth (ADR-0060): ``validate_alarm`` already normalises and ceiling-checks
    a wake volume at ROUTINE-CREATION time, but a row written before that validator existed
    (or edited directly) could still violate the ramp invariant. Dispatch re-asserts it and
    refuses rather than invent or silently clamp a volume."""
    try:
        start = float(wake_volume.get("start", DEFAULT_WAKE_VOLUME_START))
        end = float(wake_volume.get("end", DEFAULT_WAKE_VOLUME_END))
    except (TypeError, ValueError):
        return False, "wake_volume_malformed"
    ramp_seconds = wake_volume.get("ramp_seconds", DEFAULT_WAKE_VOLUME_RAMP_SECONDS)
    if not (0.0 <= start <= 1.0) or not (0.0 <= end <= 1.0):
        return False, "wake_volume_out_of_range"
    if start >= MAX_WAKE_VOLUME_START:
        return False, "start_too_high"
    if start > end:
        return False, "start_above_end"
    if not isinstance(ramp_seconds, int) or isinstance(ramp_seconds, bool) or ramp_seconds <= 0:
        return False, "ramp_seconds_invalid"
    return True, ""


def _max_duration_s(detail: dict[str, Any]) -> int:
    raw = detail.get("max_duration_s", 300)
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return 300
    return value


def _outcome_from_device_result(result: DeviceRunResult) -> DispatchOutcome:
    if result.ok:
        return DispatchOutcome.succeeded(result.result)
    reason = f"{result.error_class or 'device_error'}:{result.message}"
    return DispatchOutcome.failed(reason, {"error_class": result.error_class})


# ------------------------------------------------------------------------- the dispatcher


class ActionDispatcher:
    """Routes each of the five action kinds to the subsystem that owns it (M18 §3/§4).

    ``routine_label`` is an optional ``routine_id -> name | None`` lookup used only to fill
    an alarm's optional ``label`` — this class otherwise never touches ``app.routines.models``
    or the database directly, so it stays testable from plain fakes.
    """

    def __init__(
        self,
        *,
        briefing: BriefingPort,
        device_action: DeviceActionPort,
        browser_allowlist: frozenset[str] = BROWSER_ACTION_ALLOWLIST,
        routine_label: Callable[[UUID], str | None] | None = None,
        wake_alarm: WakeAlarmPort | None = None,
    ) -> None:
        self._briefing = briefing
        self._device_action = device_action
        self._browser_allowlist = browser_allowlist
        self._routine_label = routine_label
        self._wake_alarm = wake_alarm

    def dispatch(
        self, *, routine_id: UUID, firing_id: UUID, action: dict[str, Any]
    ) -> DispatchOutcome:
        kind = action.get("kind")
        detail = dict(action.get("detail") or {})
        if kind == ACTION_KIND_VOICE_BRIEFING:
            return self._voice_briefing(routine_id, firing_id, detail)
        if kind == ACTION_KIND_ALARM:
            return self._alarm(routine_id, firing_id, detail)
        if kind == ACTION_KIND_MEDIA_PLAYBACK:
            return self._media_playback(routine_id, firing_id, detail)
        if kind == ACTION_KIND_BROWSER_ACTION:
            return self._browser_action(routine_id, firing_id, detail)
        if kind == ACTION_KIND_DISPLAY_ACTION:
            return self._display_action(routine_id, firing_id, detail)
        if kind == ACTION_KIND_WAKE_ALARM:
            return self._wake_alarm_action(routine_id, firing_id, detail)
        return DispatchOutcome.refused(f"unknown_action_kind:{kind}")

    # -------------------------------------------------------------- voice_briefing

    def _voice_briefing(
        self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]
    ) -> DispatchOutcome:
        text = detail.get("text")
        if not isinstance(text, str) or not text.strip():
            # validate_voice_briefing already refuses this at creation; a defensive
            # second check for a row written before that validator existed.
            return DispatchOutcome.refused("voice_briefing_missing_text")
        delivery = self._briefing.narrate(text=text, routine_id=routine_id, firing_id=firing_id)
        if not delivery.delivered:
            return DispatchOutcome.failed(
                f"briefing_not_delivered:{delivery.reason}", delivery.detail
            )
        return DispatchOutcome.succeeded(delivery.detail)

    # ------------------------------------------------------------------------ alarm

    def _alarm(self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]) -> DispatchOutcome:
        wake_volume = detail.get("wake_volume") or {}
        ok, reason = _assert_wake_volume_ramp(wake_volume)
        if not ok:
            return DispatchOutcome.refused(
                f"alarm_wake_volume_invalid:{reason}", {"wake_volume": wake_volume}
            )
        payload: dict[str, Any] = {
            "alarm_id": str(firing_id),
            "wake_volume": {
                "start": float(wake_volume.get("start", DEFAULT_WAKE_VOLUME_START)),
                "end": float(wake_volume.get("end", DEFAULT_WAKE_VOLUME_END)),
                "ramp_seconds": wake_volume.get("ramp_seconds", DEFAULT_WAKE_VOLUME_RAMP_SECONDS),
            },
            "max_duration_s": _max_duration_s(detail),
        }
        label = self._routine_label(routine_id) if self._routine_label else None
        if label:
            payload["label"] = label
        result = self._device_action.run(
            capability=CAPABILITY_DESKTOP_ALARM_START,
            payload=payload,
            idempotency_key=f"routine-alarm:{firing_id}",
            timeout_s=15.0,
        )
        return _outcome_from_device_result(result)

    # ---------------------------------------------------------------- media_playback

    def _media_playback(
        self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]
    ) -> DispatchOutcome:
        del routine_id
        url = detail.get("url")
        if not isinstance(url, str) or not url:
            # validate_media_playback already refuses this at creation; this package must
            # never let a routine choose content on the owner's behalf, so re-assert here.
            return DispatchOutcome.refused("media_playback_missing_url")

        session_id = f"routine-{firing_id}"
        open_result = self._device_action.run(
            capability=CAPABILITY_BROWSER_SESSION_OPEN,
            payload={
                "session_id": session_id,
                "profile": "isolated",
                "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
                "channel": "chrome",
            },
            idempotency_key=f"routine-media-open:{firing_id}",
            timeout_s=60.0,
        )
        if not open_result.ok:
            return _outcome_from_device_result(open_result)

        # No CAPTCHA / anti-bot handling of any kind is implemented here, or anywhere in
        # this dispatcher (M18 spec §3, threat model §5): a media action can only navigate
        # to the url the owner named, byte for byte, never rewritten or substituted, and
        # never helped past a challenge on the way.
        navigate_result = self._device_action.run(
            capability=CAPABILITY_BROWSER_NAVIGATE,
            payload={"url": url, "session_id": session_id},
            idempotency_key=f"routine-media-navigate:{firing_id}",
            timeout_s=90.0,
        )
        return _outcome_from_device_result(navigate_result)

    # ----------------------------------------------------------------- browser_action

    def _browser_action(
        self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]
    ) -> DispatchOutcome:
        del routine_id
        action_name = detail.get("action")
        if not isinstance(action_name, str) or not action_name:
            return DispatchOutcome.refused("browser_action_missing_action")
        if action_name not in self._browser_allowlist:
            return DispatchOutcome.refused(f"browser_action_not_allowed:{action_name}")
        payload = {k: v for k, v in detail.items() if k != "action"}
        result = self._device_action.run(
            capability=f"browser.{action_name}",
            payload=payload,
            idempotency_key=f"routine-browser-action:{firing_id}",
            timeout_s=90.0,
        )
        return _outcome_from_device_result(result)

    # ----------------------------------------------------------------- display_action

    def _display_action(
        self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]
    ) -> DispatchOutcome:
        del routine_id, firing_id, detail
        # Always refused while DISPLAY_ACTION_QUALIFIED is False (module docstring / M18
        # spec §4, §7's last line). The reason is owner-facing Turkish because a refused
        # display action is exactly the kind of thing "neden olmadı?" will ask about; the
        # short English token stays in `detail` for tests/telemetry to match on.
        return DispatchOutcome.refused(
            "Ekranı kapatma eylemi henüz kendi ayrı onayından geçmedi; bu rutinden çalıştırılamaz.",
            {"code": "display_action_not_qualified"},
        )

    # ------------------------------------------------------------------ wake_alarm

    def _wake_alarm_action(
        self, routine_id: UUID, firing_id: UUID, detail: dict[str, Any]
    ) -> DispatchOutcome:
        """Hand the alarm id to the wake sequence (M18.3 spec §3.5).

        A missing port is a FAILURE, never a silent success: a process wired without one
        would otherwise record "the routine fired" for an alarm that never rang. The
        honest answer is that nothing woke the owner.
        """
        raw = detail.get("alarm_id")
        if not isinstance(raw, str) or not raw:
            return DispatchOutcome.refused("wake_alarm_missing_alarm_id")
        try:
            alarm_id = UUID(raw)
        except ValueError:
            return DispatchOutcome.refused(f"wake_alarm_alarm_id_not_uuid:{raw[:64]}")
        if self._wake_alarm is None:
            return DispatchOutcome.failed(
                "wake_alarm_port_unavailable", {"alarm_id": raw}
            )
        return self._wake_alarm.fire(
            alarm_id=alarm_id, routine_id=routine_id, firing_id=firing_id
        )


# ------------------------------------------------------------------------- module registry

#: Same house style as app.devices.commands.register_broker_runtime/get_broker_runtime:
#: app.main's lifespan sets this once at startup; app.routines.service falls back to
#: NoopDispatcher() when nothing is registered (e.g. every unit test that never wires it).
_routine_dispatcher: RoutineDispatcher | None = None


def register_routine_dispatcher(dispatcher: RoutineDispatcher | None) -> None:
    global _routine_dispatcher
    _routine_dispatcher = dispatcher


def get_routine_dispatcher() -> RoutineDispatcher | None:
    return _routine_dispatcher


__all__ = [
    "BROWSER_ACTION_ALLOWLIST",
    "CAPABILITY_BROWSER_MEDIA_PLAY",
    "CAPABILITY_BROWSER_MEDIA_STATUS",
    "CAPABILITY_BROWSER_MEDIA_STOP",
    "CAPABILITY_BROWSER_MEDIA_VOLUME",
    "CAPABILITY_BROWSER_NAVIGATE",
    "CAPABILITY_BROWSER_SESSION_OPEN",
    "CAPABILITY_DESKTOP_ACTIVITY_STATUS",
    "CAPABILITY_DESKTOP_ALARM_ARM",
    "CAPABILITY_DESKTOP_ALARM_DISARM",
    "CAPABILITY_DESKTOP_ALARM_START",
    "CAPABILITY_DESKTOP_ALARM_STOP",
    "CAPABILITY_DESKTOP_DISPLAY_OFF",
    "CAPABILITY_DESKTOP_DISPLAY_STATUS",
    "CAPABILITY_DESKTOP_DISPLAY_WAKE",
    "CAPABILITY_DESKTOP_PLAY_AUDIO",
    "DISPLAY_ACTION_QUALIFIED",
    "ActionDispatcher",
    "BriefingDelivery",
    "BriefingPort",
    "BrokerDeviceAction",
    "DeviceActionPort",
    "DeviceRunResult",
    "RealtimeSayBriefing",
    "WakeAlarmPort",
    "get_routine_dispatcher",
    "register_routine_dispatcher",
]

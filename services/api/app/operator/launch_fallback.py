"""A single-step application launch on a device that does not carry the Operator (ADR-0209).

"Ofis bilgisayarımda hesap makinesini aç" (2026-09-29). The office PC is the owner's
employer's machine and is enrolled WITHOUT the Digital Operator on purpose (ADR-0203): it
advertises ``desktop.open_application`` and no ``app.launch``. The launch tool
(``operator.app_open``) always planned ``app.launch`` (``app.operator.plans.open_application``),
selection found no online device advertising it, and the owner heard "Hesap Makinesi
açamadım efendim" for a sentence ``desktop.open_application`` was written to serve.

The rule, and the whole of it:

* the device the launch would go to advertises ``app.launch`` -> the Operator path, exactly
  as before (verify the foreground window, focus stack, the operator task and its ledger);
* it does not, but does advertise ``desktop.open_application`` -> that capability, for the
  applications its contract names, and a refusal for any other - never a retry another way,
  never another machine;
* it advertises neither -> the Operator path, which answers ``no_capable_device`` as it
  always did;
* (ADR-0208) the SESSION's own device can open an application only the direct way, while
  another online device carries the Operator -> the direct way, on the session's device: the
  owner at the office does not get the calculator on the home PC.

This module decides a ROUTE. It does not select a device (that is ``app.devices.selection``,
reached through the one ``DeviceActionPort``), does not build a plan and does not run a
command; the tool that calls it does those. It knows one port method beyond the port's
protocol, ``selection_for(capability)`` (or, failing that, ``can_run``), and a port without
either (every test fake, any future implementation) simply keeps the Operator path.

A launch is only this rule when it IS a launch. A request with anything after the opening
("hesap makinesini aç ve 5 yaz") is a mission and never reaches ``operator.app_open``; it
stays a mission, still needs the Operator, and still answers ``no_capable_device`` on a
device without one.
"""

from __future__ import annotations

from typing import Any, Final

from app.devices.selection import REASON_SESSION_AFFINITY

#: What ``app.operator.plans.open_application`` demands first. A test holds this equal to
#: the plan's own first step so the two cannot drift.
CAPABILITY_APP_LAUNCH: Final = "app.launch"
#: The pre-Operator launch (DEVICE_PROTOCOL.md section 6, M1), executed by the session
#: companion's ``AppLauncher``.
CAPABILITY_DESKTOP_OPEN_APPLICATION: Final = "desktop.open_application"

#: The names DEVICE_PROTOCOL.md section 6 gives ``desktop.open_application``'s ``application``
#: field - the device's own default allowlist (``AppLauncher.DefaultAllowlist``), which is
#: also what the office companion enforces. Not an allowlist the cloud invents: the device
#: still refuses (``capability_missing``) anything it was narrowed away from, and a test reads
#: this tuple back from the C# source and the protocol document. Each is also an id of
#: ``operator-allowlists.json``, so the payload name is the id the router already resolved.
DESKTOP_OPEN_APPLICATION_NAMES: Final[tuple[str, ...]] = ("notepad", "calc", "mspaint")

ROUTE_OPERATOR: Final = "operator"
ROUTE_DIRECT: Final = "direct"
ROUTE_REFUSED: Final = "refused"


def route_for(device_action: Any, application: str) -> str:
    """Which path a single-step launch of ``application`` takes on ``device_action``.

    ``ROUTE_OPERATOR`` is the answer whenever this cannot be sure of anything else - a port
    that cannot be asked, a probe that raises - because it is the path that has always run.
    """
    try:
        selection_for = getattr(device_action, "selection_for", None)
        if callable(selection_for):
            launch = selection_for(CAPABILITY_APP_LAUNCH)
            direct = selection_for(CAPABILITY_DESKTOP_OPEN_APPLICATION)
            # ADR-0208 with ADR-0209: when the SESSION's own device can open an application
            # only the direct way, that is the way it is opened - on that device. Taking the
            # Operator path here would act on the other machine (the home PC, whenever it is
            # online) while the owner sits at the office one. Nothing about a session that
            # is not on such a device changes: the Operator keeps every launch it can do.
            if (
                direct is not None
                and direct.reason == REASON_SESSION_AFFINITY
                and (launch is None or launch.device.id != direct.device.id)
            ):
                return _direct_or_refused(application)
            launch_possible = launch is not None
            direct_possible = direct is not None
        else:
            probe = getattr(device_action, "can_run", None)
            if not callable(probe):
                return ROUTE_OPERATOR
            launch_possible = bool(probe(CAPABILITY_APP_LAUNCH))
            direct_possible = bool(probe(CAPABILITY_DESKTOP_OPEN_APPLICATION))
    except Exception:  # noqa: BLE001 - "I cannot tell" is the old path, never a new failure
        return ROUTE_OPERATOR
    if launch_possible or not direct_possible:
        return ROUTE_OPERATOR
    return _direct_or_refused(application)


def _direct_or_refused(application: str) -> str:
    return ROUTE_DIRECT if application in DESKTOP_OPEN_APPLICATION_NAMES else ROUTE_REFUSED


__all__ = [
    "CAPABILITY_APP_LAUNCH",
    "CAPABILITY_DESKTOP_OPEN_APPLICATION",
    "DESKTOP_OPEN_APPLICATION_NAMES",
    "ROUTE_DIRECT",
    "ROUTE_OPERATOR",
    "ROUTE_REFUSED",
    "route_for",
]

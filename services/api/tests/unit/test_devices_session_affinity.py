"""ADR-0208: a command that names no device goes to the device its SESSION is on.

Found 2026-09-29: the owner sat at the office PC (GMKADIRAKBABA), said "hesap makinesini
aç" in the web shell with the home PC (MAIL) off, and heard that no device could do it.
Whatever the cause of that particular refusal, the rule underneath was wrong for two online
machines too: a command that names no machine was handed to "the healthiest", which is the
home PC on any day it has been seen a second more recently - while the owner is looking at
the other one.

The rule tested here, in order: an explicit target (id, exact name, Turkish alias) wins
unchanged; else the session's own device when it is enrolled, not revoked, online, capable
and policy-allowed; else the old rule, byte for byte. A device id the session merely
CLAIMS grants nothing (M19b: reachability and a claimed id are not authority) - it can only
choose among devices the owner could have chosen anyway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.devices import selection
from app.devices.health import CommandOutcome, DeviceHealth
from app.devices.presence import PRESENCE_OFFLINE, PRESENCE_ONLINE
from app.devices.selection import NoCapableDeviceError, select_device
from app.devices.types import DeviceView

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
OPEN_APP = "desktop.open_application"
HOME_CAPS = (OPEN_APP, "desktop.open_artifact", "app.launch", "window.current", "screen.capture")
OFFICE_CAPS = (OPEN_APP, "desktop.open_artifact")  # ADR-0203: no operator family


def _view(
    name: str,
    *,
    presence: str = PRESENCE_ONLINE,
    capabilities: tuple[str, ...] = (OPEN_APP,),
    aliases: tuple[str, ...] = (),
    policy: dict | None = None,
    status: str = "enrolled",
    failures: int = 0,
    last_seen: datetime | None = NOW,
) -> DeviceView:
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform="windows",
        status=status,
        presence=presence,
        capabilities=capabilities,
        enrolled_at=NOW,
        last_seen_at=last_seen,
        aliases=aliases,
        policy=policy or {},
        health=DeviceHealth(
            last_hello_at=None,
            software_version=None,
            heartbeat_age_s=None,
            recent_outcomes=tuple(
                CommandOutcome(
                    command_id=str(i),
                    capability="x",
                    status="failed",
                    error_class="x",
                    terminal_at=None,
                )
                for i in range(failures)
            ),
        ),
    )


def _home() -> DeviceView:
    """The healthier of the two: seen a second more recently, so the old rule prefers it."""
    return _view(
        "MAIL", aliases=("ev",), capabilities=HOME_CAPS, last_seen=NOW + timedelta(seconds=1)
    )


def _office() -> DeviceView:
    return _view("GMKADIRAKBABA", aliases=("ofis", "iş"), capabilities=OFFICE_CAPS)


# ------------------------------------------------------------------ the rule


def test_the_session_device_is_chosen_when_two_devices_are_online_and_capable() -> None:
    home, office = _home(), _office()

    old = select_device([home, office], capability=OPEN_APP)
    assert old.device.id == home.id  # the premise: the old rule prefers the OTHER machine

    result = select_device([home, office], capability=OPEN_APP, session_device_ids=[office.id])

    assert result.device.id == office.id
    assert result.reason == selection.REASON_SESSION_AFFINITY == "session_affinity"
    # Affinity is not the owner NAMING a device: `explicit` keeps meaning "he said which".
    assert result.explicit is False


def test_affinity_is_symmetric_the_home_session_gets_the_home_device() -> None:
    home, office = _home(), _office()
    result = select_device([office, home], capability=OPEN_APP, session_device_ids=[home.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_SESSION_AFFINITY)


@pytest.mark.parametrize(
    ("target", "expected_reason"),
    [
        ("ev", selection.REASON_EXPLICIT_ALIAS),
        ("MAIL", selection.REASON_EXPLICIT_NAME),
        ("ev bilgisayarımda aç", selection.REASON_EXPLICIT_ALIAS),
    ],
)
def test_an_explicit_target_beats_the_session_device(target: str, expected_reason: str) -> None:
    home, office = _home(), _office()
    result = select_device(
        [home, office], capability=OPEN_APP, target=target, session_device_ids=[office.id]
    )
    assert result.device.id == home.id
    assert result.reason == expected_reason
    assert result.explicit is True


def test_an_explicit_device_id_beats_the_session_device() -> None:
    home, office = _home(), _office()
    result = select_device(
        [home, office], capability=OPEN_APP, target=str(home.id), session_device_ids=[office.id]
    )
    assert (result.device.id, result.reason) == (home.id, selection.REASON_EXPLICIT_ID)


def test_an_explicit_target_that_matches_nothing_never_falls_back_to_the_session_device() -> None:
    home, office = _home(), _office()
    with pytest.raises(NoCapableDeviceError) as exc:
        select_device(
            [home, office], capability=OPEN_APP, target="bahçe", session_device_ids=[office.id]
        )
    assert exc.value.reason == "target_not_found"


def test_an_explicit_target_that_is_offline_never_falls_back_to_the_session_device() -> None:
    home = _view("MAIL", aliases=("ev",), presence=PRESENCE_OFFLINE)
    office = _office()
    with pytest.raises(NoCapableDeviceError) as exc:
        select_device(
            [home, office], capability=OPEN_APP, target="ev", session_device_ids=[office.id]
        )
    assert exc.value.reason == "offline"


# ---------------------------------------------- the session device is not eligible


def _same_as_old_rule(devices: list[DeviceView], session_ids: list[uuid.UUID]) -> None:
    old = select_device(devices, capability=OPEN_APP)
    new = select_device(devices, capability=OPEN_APP, session_device_ids=session_ids)
    assert (new.device.id, new.reason, new.explicit) == (old.device.id, old.reason, old.explicit)
    assert new.reason == selection.REASON_AUTO


def test_an_unknown_session_device_falls_back_to_the_old_rule() -> None:
    _same_as_old_rule([_home(), _office()], [uuid.uuid4()])


def test_a_revoked_session_device_is_ignored_and_never_chosen() -> None:
    home = _home()
    revoked = _view("ESKI", status="revoked", capabilities=HOME_CAPS)
    result = select_device([home, revoked], capability=OPEN_APP, session_device_ids=[revoked.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


def test_an_offline_session_device_falls_back_to_the_old_rule() -> None:
    office = _view("GMKADIRAKBABA", presence=PRESENCE_OFFLINE, capabilities=OFFICE_CAPS)
    home = _home()
    result = select_device([home, office], capability=OPEN_APP, session_device_ids=[office.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


def test_an_incapable_session_device_falls_back_to_the_old_rule() -> None:
    """The office PC has no operator family (ADR-0203): a session there asking for
    `app.launch` is served by the machine that can - exactly as before this change."""
    home, office = _home(), _office()
    result = select_device([home, office], capability="app.launch", session_device_ids=[office.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


def test_a_policy_denied_session_device_falls_back_to_the_old_rule() -> None:
    home = _home()
    office = _view("GMKADIRAKBABA", capabilities=OFFICE_CAPS, policy={"deny": [OPEN_APP]})
    result = select_device([home, office], capability=OPEN_APP, session_device_ids=[office.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


def test_a_policy_allow_list_that_omits_the_capability_is_a_denial_too() -> None:
    home = _home()
    office = _view("GMKADIRAKBABA", capabilities=OFFICE_CAPS, policy={"allow": ["browser"]})
    result = select_device([home, office], capability=OPEN_APP, session_device_ids=[office.id])
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


def test_when_the_old_rule_also_fails_the_error_is_the_old_error_unchanged() -> None:
    office = _office()
    with pytest.raises(NoCapableDeviceError) as with_hint:
        select_device([office], capability="app.launch", session_device_ids=[office.id])
    with pytest.raises(NoCapableDeviceError) as without:
        select_device([office], capability="app.launch")
    assert with_hint.value.reason == without.value.reason == "capability_missing"
    assert with_hint.value.detail_tr == without.value.detail_tr
    assert with_hint.value.target == without.value.target is None


# --------------------------------------------- no session device: the old rule, exactly


@pytest.mark.parametrize("ids", [None, [], ()])
def test_no_session_device_is_the_old_rule_byte_for_byte(ids) -> None:
    home, office = _home(), _office()
    hinted = select_device([home, office], capability=OPEN_APP, session_device_ids=ids)
    plain = select_device([home, office], capability=OPEN_APP)
    assert (hinted.device.id, hinted.reason, hinted.explicit) == (
        plain.device.id,
        "auto",
        False,
    )
    assert plain.device.id == home.id


@pytest.mark.parametrize(
    ("devices", "capability", "target", "reason", "detail"),
    [
        ([], OPEN_APP, None, "offline", "Şu anda çevrimiçi bir cihaz bulunamadı."),
        (
            [_view("A", presence=PRESENCE_OFFLINE)],
            OPEN_APP,
            "A",
            "offline",
            "'A' cihazı şu anda çevrimiçi değil.",
        ),
        (
            [_view("A")],
            "app.launch",
            None,
            "capability_missing",
            "'app.launch' yeteneğine sahip çevrimiçi bir cihaz bulunamadı.",
        ),
        (
            [_view("A", policy={"deny": [OPEN_APP]})],
            OPEN_APP,
            None,
            "policy_denied",
            "Politika hiçbir cihazda bu işleme izin vermiyor.",
        ),
        (
            [_view("A")],
            OPEN_APP,
            "B",
            "target_not_found",
            "'B' adında veya takma adında kayıtlı bir cihaz bulunamadı.",
        ),
    ],
)
def test_the_old_failure_strings_are_untouched(devices, capability, target, reason, detail) -> None:
    with pytest.raises(NoCapableDeviceError) as exc:
        select_device(devices, capability=capability, target=target, session_device_ids=[])
    assert (exc.value.reason, exc.value.detail_tr) == (reason, detail)


# ----------------------------------------------------------------- the id list


def test_the_first_id_that_names_an_enrolled_device_is_the_session_device() -> None:
    home, office = _home(), _office()
    # an unknown id first is skipped, as if never declared; the next real one is the device
    result = select_device(
        [home, office], capability=OPEN_APP, session_device_ids=[uuid.uuid4(), office.id, home.id]
    )
    assert (result.device.id, result.reason) == (office.id, selection.REASON_SESSION_AFFINITY)


def test_a_revoked_id_is_skipped_like_an_unknown_one() -> None:
    home, office = _home(), _office()
    revoked = _view("ESKI", status="revoked", capabilities=HOME_CAPS)
    result = select_device(
        [home, office, revoked], capability=OPEN_APP, session_device_ids=[revoked.id, office.id]
    )
    assert (result.device.id, result.reason) == (office.id, selection.REASON_SESSION_AFFINITY)


def test_an_ineligible_first_session_device_is_not_skipped_for_the_next_id() -> None:
    """The first id that names a live device IS the session's device. If it cannot do the
    job the old rule applies; a weaker hint further down the list does not get a second try."""
    home = _home()
    office = _view("GMKADIRAKBABA", presence=PRESENCE_OFFLINE, capabilities=OFFICE_CAPS)
    other = _view("DIGER", capabilities=OFFICE_CAPS, last_seen=NOW - timedelta(days=1))
    result = select_device(
        [home, office, other], capability=OPEN_APP, session_device_ids=[office.id, other.id]
    )
    assert (result.device.id, result.reason) == (home.id, selection.REASON_AUTO)


# ----------------------------------------- today's event, at the selection boundary


def test_the_owner_at_the_office_with_the_home_pc_off_gets_the_office_device() -> None:
    """2026-09-29: session on GMKADIRAKBABA, MAIL offline, no device named."""
    home = _view("MAIL", aliases=("ev",), presence=PRESENCE_OFFLINE, capabilities=HOME_CAPS)
    office = _office()
    result = select_device([home, office], capability=OPEN_APP, session_device_ids=[office.id])
    assert (result.device.id, result.reason) == (office.id, selection.REASON_SESSION_AFFINITY)

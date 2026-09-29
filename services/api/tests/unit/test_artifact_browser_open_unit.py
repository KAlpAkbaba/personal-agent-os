"""Unit tests: the pure decisions in ``app.artifacts.browser_open`` (ADR-0210).

The behaviour through the real application object - which device, which steps, what the owner
hears - is in ``test_research_open_in_owner_chrome.py``. This file holds what does not need a
world: the origin a browser is sent to, what a port that cannot be probed means, and the
contract mirror (the capabilities the open sends are the ones BROWSER_CAPABILITIES.md names, and
the risk classes they carry fit the session policy the open asks for).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.artifacts import browser_open
from app.artifacts.browser_open import origin_for, route_for
from app.devices.selection import REASON_AUTO, REASON_SESSION_AFFINITY, SelectionResult
from app.devices.status import get_status_registry
from app.routines.dispatch import BROWSER_ACTION_ALLOWLIST

ROOT = Path(__file__).resolve().parents[4]


def _device(capabilities: list[str], *, name: str = "PC") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, aliases=(), capabilities=capabilities)


class _Port:
    """A port that answers ``selection_for`` from a table."""

    def __init__(self, answers: dict[str, SelectionResult | None]) -> None:
        self.answers = answers

    def selection_for(self, capability: str):
        return self.answers.get(capability)


def _selection(device, reason: str = REASON_AUTO) -> SelectionResult:
    return SelectionResult(device=device, reason=reason, explicit=False)


# ------------------------------------------------------------------ the origin


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://100.90.158.26:8001", "http://100.90.158.26:8001"),
        ("http://100.90.158.26:8001/", "http://100.90.158.26:8001"),
        ("  https://edge.example.net  ", "https://edge.example.net"),
    ],
)
def test_the_configured_origin_is_used(configured: str, expected: str) -> None:
    assert origin_for(uuid.uuid4(), configured) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "/v1/relative",
        "100.90.158.26:8001",
        "ftp://100.90.158.26",
        "javascript:alert(1)",
        "http://user:pw@100.90.158.26:8001",
        "http://100.90.158.26:8001/some/path",
        "http://100.90.158.26:8001/?x=1",
        "http://",
        "file:///c:/x",
    ],
)
def test_an_origin_that_is_not_a_bare_http_origin_is_no_origin(bad: str) -> None:
    """A browser is never sent a relative URL, another scheme, credentials or a path prefix
    the operator did not mean: the answer is "no origin", which is said, not guessed."""
    assert origin_for(uuid.uuid4(), bad) == ""


def test_the_devices_own_dial_origin_is_the_fallback() -> None:
    device_id = uuid.uuid4()
    get_status_registry().record_dial_origin(device_id, "http://100.90.158.26:8001")
    assert origin_for(device_id, "") == "http://100.90.158.26:8001"


def test_a_different_devices_dial_origin_is_never_borrowed() -> None:
    other = uuid.uuid4()
    get_status_registry().record_dial_origin(other, "http://100.90.158.26:8001")
    assert origin_for(uuid.uuid4(), "") == ""


def test_the_configured_origin_wins_over_the_dial_origin() -> None:
    device_id = uuid.uuid4()
    get_status_registry().record_dial_origin(device_id, "http://100.90.158.26")
    assert origin_for(device_id, "http://100.90.158.26:8001") == "http://100.90.158.26:8001"


# --------------------------------------------------------------------- route_for


def test_a_port_that_cannot_be_probed_is_the_file_path() -> None:
    assert route_for(None) is None
    assert route_for(object()) is None


def test_a_probe_that_raises_is_the_file_path() -> None:
    class Broken:
        def selection_for(self, capability: str):
            raise RuntimeError("boom")

    assert route_for(Broken()) is None


def test_no_browser_device_is_the_file_path() -> None:
    assert route_for(_Port({})) is None


def test_a_browser_device_missing_one_of_the_steps_is_the_file_path() -> None:
    """The steps go through the same port and could land on another machine if the chosen
    one lacked one of them: such a device is not a candidate at all."""
    partial = _device(["browser.session_open", "browser.tab_new"])
    port = _Port({"browser.session_open": _selection(partial)})
    assert route_for(port) is None


def test_the_family_marker_alone_carries_the_steps() -> None:
    office = _device(["browser.chrome"])
    port = _Port({"browser.session_open": _selection(office)})
    assert route_for(port) is not None


def test_nothing_that_can_fetch_a_file_means_the_browser_device() -> None:
    office = _device(["browser.chrome"])
    chosen = _selection(office)
    assert route_for(_Port({"browser.session_open": chosen})) is chosen


def test_a_device_that_can_fetch_and_is_the_one_probed_keeps_the_file_path() -> None:
    both = _device(["browser.chrome", "file.fetch"])
    port = _Port(
        {
            "browser.session_open": _selection(both, REASON_SESSION_AFFINITY),
            "file.fetch": _selection(both, REASON_SESSION_AFFINITY),
        }
    )
    assert route_for(port) is None


def test_the_sessions_own_browser_device_wins_over_another_machines_fetch() -> None:
    office = _device(["browser.chrome"], name="office")
    home = _device(["file.fetch"], name="home")
    chosen = _selection(office, REASON_SESSION_AFFINITY)
    port = _Port({"browser.session_open": chosen, "file.fetch": _selection(home)})
    assert route_for(port) is chosen


def test_without_session_affinity_another_machines_fetch_keeps_the_file_path() -> None:
    """The ordinary rule is not reordered: an unbound session with a home PC that can fetch a
    file opens it there, as it always did."""
    office = _device(["browser.chrome"], name="office")
    home = _device(["file.fetch"], name="home")
    port = _Port({"browser.session_open": _selection(office), "file.fetch": _selection(home)})
    assert route_for(port) is None


# ------------------------------------------------------------- the contract mirror


def test_every_capability_the_open_sends_is_in_the_worker_contract_and_the_allowlist() -> None:
    doc = (ROOT / "packages" / "protocol" / "BROWSER_CAPABILITIES.md").read_text(encoding="utf-8")
    table = doc.split("## 1. Capability names", 1)[1].split("## 2. Sessions", 1)[0]
    sent = {
        browser_open.CAPABILITY_SESSION_OPEN,
        browser_open.CAPABILITY_TAB_NEW,
        browser_open.CAPABILITY_INSPECT,
        browser_open.CAPABILITY_SESSION_CLOSE,
    }
    for capability in sent:
        assert re.search(rf"`{re.escape(capability)}`", table), capability
        assert capability.split(".", 1)[1] in BROWSER_ACTION_ALLOWLIST, capability


def test_the_risk_classes_the_open_asks_for_cover_what_it_sends() -> None:
    """The session is READ + NAVIGATE. ``tab_new`` and the session calls are NAVIGATE and
    ``inspect`` is READ (BROWSER_CAPABILITIES.md section 4): nothing the open sends needs more,
    so the owner's own browser is never given more than a page load and a look at it."""
    doc = (ROOT / "packages" / "protocol" / "BROWSER_CAPABILITIES.md").read_text(encoding="utf-8")
    table = doc.split("## 1. Capability names", 1)[1].split("## 2. Sessions", 1)[0]
    for capability, risk in (
        ("browser.session_open", "NAVIGATE"),
        ("browser.session_close", "NAVIGATE"),
        ("browser.tab_new", "NAVIGATE"),
        ("browser.inspect", "READ"),
    ):
        line = next(row for row in table.splitlines() if f"`{capability}`" in row)
        assert f"| {risk} |" in line, (capability, line)


def test_the_production_compose_forwards_the_origin_setting_to_every_colour() -> None:
    """Compose forwards only the variables it names (the api has no ``env_file``), so a
    setting in ``/opt/pagentos/.env`` that compose does not name never reaches the process.
    ``PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN`` was such a setting until ADR-0210; it sits in the
    environment block both colours and the plain ``api`` share (``&cloud-core-env``)."""
    compose = (ROOT / "infra" / "docker" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    shared = compose.split("environment: &cloud-core-env", 1)[1].split("volumes:", 1)[0]
    assert "PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN: ${PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN:-}" in shared
    assert compose.count("<<: *cloud-core-env") == 2

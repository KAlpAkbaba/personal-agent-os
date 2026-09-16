"""B31 req 172/173/180/181: the browser contract's transfer half, held from the cloud.

- 181: every operation the companion advertises (C# ``BrowserCapabilities.Operations``)
  is one the cloud's dispatch allowlist admits, and ``browser.upload`` is now among them
  with a handler in the worker - the flag is no longer a "yalan duyuru".
- 180: the contract names the size cap and the authorisation shape; the worker's
  constants match the contract's numbers (read from its source).
- 172: the owner's Chrome stays closed to autonomous research by construction - the
  enrolment script writes the flag false, no cloud code sets it, the gateway opens the
  ``research`` profile only (ADR-0113's boundary, preserved; the privacy decision is the
  owner's checkpoint).
- 173: a research session is reused - the cloud remembers it per process, the worker
  answers ``created: false`` for a live one, and a cloud restart reopens once without
  losing the session.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.devices.commands import CommandSucceeded
from app.research import browser_gateway
from app.research.browser_gateway import DeviceBrowserGateway, reset_known_open_sessions
from app.routines.dispatch import BROWSER_ACTION_ALLOWLIST
from tests.device_command_support import FakeDeviceCommandClient

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages/protocol/BROWSER_CAPABILITIES.md"
PROTOCOL_CS = REPO / "devices/windows-agent/src/PagentOS.Agent.Core/Protocol/ProtocolConstants.cs"
WORKER = REPO / "services/browser/browser_agent/worker.py"
POLICY = REPO / "services/browser/browser_agent/policy.py"
ENROLL = REPO / "scripts/browser/enroll-owner-chrome.ps1"


def _companion_operations() -> list[str]:
    source = PROTOCOL_CS.read_text("utf-8")
    start = source.index("public static class BrowserCapabilities")
    block = source[start : source.index("public static readonly IReadOnlyList<string> All", start)]
    family = re.search(r'public const string Family = "(browser\.[a-z_]+)";', block).group(1)
    names = re.findall(r'public const string \w+ = "(browser\.[a-z_]+)";', block)
    return [name for name in names if name != family]


# ------------------------------------------------------------------------- 181


def test_every_companion_operation_is_admitted_by_the_cloud_allowlist() -> None:
    operations = _companion_operations()
    assert "browser.upload" in operations, "contract v1.5 names the upload operation"
    actions = {op.removeprefix("browser.") for op in operations}
    missing = sorted(actions - BROWSER_ACTION_ALLOWLIST)
    assert not missing, f"the companion advertises what the cloud refuses: {missing}"


def test_the_worker_has_a_handler_and_a_risk_class_for_upload() -> None:
    worker = WORKER.read_text("utf-8")
    assert '"browser.upload": Worker._op_upload' in worker
    policy = POLICY.read_text("utf-8")
    assert '"browser.upload"' in policy
    contract = CONTRACT.read_text("utf-8")
    assert "| `browser.upload` | HIGH_IMPACT |" in contract


# ------------------------------------------------------------------------- 180


def test_the_contract_and_the_worker_agree_on_the_cap_and_the_reference_shape() -> None:
    contract = CONTRACT.read_text("utf-8")
    worker = WORKER.read_text("utf-8")
    assert "MAX_TRANSFER_BYTES = 64 * 1024 * 1024" in worker
    assert "64 MiB" in contract
    assert "8–128 characters" in contract or "8-128 characters" in contract
    shape = re.search(r'AUTHORIZATION_REF_RE = re\.compile\(r"([^"]+)"\)', worker).group(1)
    pattern = re.compile(shape)
    assert pattern.match("approval:2026-09-14:0001")
    assert not pattern.match("x")
    assert not pattern.match("a" * 129)
    assert "enforce_transfer_size(result.path" in worker, "a download is measured against the cap"
    assert 'enforce_transfer_size(path, cap=cap, op="browser.upload", delete=False)' in worker
    assert "_require_within_file_io_root(directory" in (
        REPO / "services/browser/browser_agent/session.py"
    ).read_text("utf-8")


# ------------------------------------------------------------------------- 172


def test_the_owners_chrome_stays_closed_to_autonomous_research() -> None:
    assert "owner_authorized_for_research  = $false" in ENROLL.read_text("utf-8")
    api = REPO / "services/api/app"
    offenders = [
        str(p.relative_to(REPO))
        for p in api.rglob("*.py")
        if "owner_authorized_for_research" in p.read_text("utf-8", errors="ignore")
    ]
    assert not offenders, f"the cloud must never touch the research grant: {offenders}"
    gateway = (REPO / "services/api/app/research/browser_gateway.py").read_text("utf-8")
    assert '"profile": "research"' in gateway
    assert '"owner"' not in gateway, "autonomous research never asks for the owner profile"
    worker = WORKER.read_text("utf-8")
    assert "require_research_authorization(" in worker, "the worker's own gate is still there"


# ------------------------------------------------------------------------- 173


def _gateway(client: FakeDeviceCommandClient, device_id: uuid.UUID, task_id: str):
    return DeviceBrowserGateway(client, device_id=device_id, task_id=task_id, timeout_s=5.0)


def test_a_live_session_is_reused_in_process_and_across_a_restart() -> None:
    reset_known_open_sessions()
    device_id = uuid.uuid4()
    task_id = f"research-{uuid.uuid4()}"
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {"session_id": task_id, "created": True, "lifecycle": {"reused": False}}
        )
    )
    first = _gateway(client, device_id, task_id)
    opened = first.ensure_session()
    assert opened["created"] is True
    assert len(client.calls) == 1
    # Same process, another gateway for the same run: the session is known, no device call.
    second = _gateway(client, device_id, task_id)
    assert second.ensure_session() == {"created": False}
    assert len(client.calls) == 1
    # After a restart the memory is gone; the worker answers "already open" and the cloud
    # accepts it as the same session rather than opening another.
    reset_known_open_sessions()
    client.factory = lambda **_kw: CommandSucceeded(
        {"session_id": task_id, "created": False, "lifecycle": {"reused": True}}
    )
    third = _gateway(client, device_id, task_id)
    reopened = third.ensure_session()
    assert reopened["created"] is False
    assert reopened["lifecycle"]["reused"] is True
    assert len(client.calls) == 2
    assert client.calls[-1].payload["session_id"] == task_id
    assert (str(device_id), task_id) in browser_gateway._KNOWN_OPEN_SESSIONS
    reset_known_open_sessions()

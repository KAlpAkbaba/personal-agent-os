"""The session Cloud Core's research REALLY opens, on the cloud worker (ADR-0213, order 2b).

``app.research.browser_gateway`` sends one ``browser.session_open`` for every device and it
says ``channel: chrome`` and ``policy.visible: true`` - right for the owner's machines. The
cloud image has no Google Chrome and no display, and the worker lets the payload override
its own ``--channel chromium --headless``. The cloud device decides what it launches:
``browser_agent.cloud.policy.clamp_command``.

Contract halves read each other: the payload here is built by the gateway's OWN
``_open_session`` (compiled from its source; this venv cannot import the api package), goes
through the real clamp and into the real worker's ``session_open``, where the backend the
worker would launch is recorded instead of started. No browser, no network.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from browser_agent import worker as worker_module
from browser_agent.cloud import config
from browser_agent.cloud import policy as cloud_policy
from browser_agent.cloud.__main__ import build_worker_args
from browser_agent.worker import Worker, build_arg_parser
from tests.unit.test_cloud_worker import HANDSHAKE, FakeSocket, FakeWorker, _command, _serve

REPO = Path(__file__).resolve().parents[4]
GATEWAY = REPO / "services" / "api" / "app" / "research" / "browser_gateway.py"

SESSION_ID = "0b6f3c0e-5f0a-4d57-9a41-3f2f3d1f8a10"


def _gateway_tree() -> ast.Module:
    return ast.parse(GATEWAY.read_text(encoding="utf-8"))


def _gateway_constant(name: str) -> str:
    for node in _gateway_tree().body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return str(ast.literal_eval(node.value))
    raise AssertionError(f"browser_gateway.py has no {name}")  # pragma: no cover


def _gateway_session_open() -> dict[str, Any]:
    """The ``client.run(...)`` arguments of the gateway's own ``_open_session``, for a run on
    its research profile: the method is taken out of the gateway's source and RUN against a
    recording client, so what it sends is never retyped here."""
    gateway = next(
        node
        for node in _gateway_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "DeviceBrowserGateway"
    )
    method = next(
        node
        for node in gateway.body
        if isinstance(node, ast.FunctionDef) and node.name == "_open_session"
    )
    # ``compile`` inherits this module's ``from __future__ import annotations`` - the
    # gateway's own - so the method's annotations are not evaluated here.
    code = compile(ast.Module(body=[method], type_ignores=[]), str(GATEWAY), "exec")
    scope: dict[str, Any] = {
        "_outcome_or_raise": lambda outcome: outcome,
        "_registry_lock": threading.Lock(),
        "_KNOWN_OPEN_SESSIONS": set(),
    }
    exec(code, scope)  # noqa: S102 - the repository's own gateway source
    sent: list[dict[str, Any]] = []

    def run(**kwargs: Any) -> dict[str, Any]:
        sent.append(kwargs)
        return {"created": True}

    gateway_self = SimpleNamespace(
        _client=SimpleNamespace(run=run),
        _device_id="11111111-2222-4333-8444-555555555555",
        _session_id=SESSION_ID,
        _profile=_gateway_constant("PROFILE_RESEARCH"),
        _timeout_s=60.0,
        _trace_id="trace",
        _session_open_attempt=0,
        _session_opened=False,
        _registry_key=lambda: ("device", SESSION_ID),
    )
    scope["_open_session"](gateway_self)
    assert len(sent) == 1
    return sent[0]


#: Whatever a caller could say about the window, beside what the gateway says today.
HOSTILE = {
    "session_id": SESSION_ID,
    "profile": "research",
    "policy": {"allowed_risk_classes": ["READ"], "visible": True},
    "channel": "chrome",
}


def _payloads() -> list[Any]:
    return [
        pytest.param(_gateway_session_open()["payload"], id="the-gateways-own"),
        pytest.param(HOSTILE, id="visible-chrome-spelled-out"),
    ]


class _WouldLaunch(Exception):
    """Raised in place of a launch; carries what the worker asked ``ManagedBackend`` for."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__("launch recorded")
        self.kwargs = kwargs


def _refuse_to_launch(**kwargs: Any) -> None:
    raise _WouldLaunch(kwargs)


def _cloud_worker(tmp_path: Path) -> tuple[Worker, config.CloudConfig]:
    cfg = config.CloudConfig(
        broker_http_url="http://api:8000",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        token_file=tmp_path / "state" / "enroll.token",
        max_concurrent=2,
    )
    return Worker(build_arg_parser().parse_args(build_worker_args(cfg))), cfg


def _launch_of(worker: Worker, payload: dict[str, Any]) -> dict[str, Any]:
    with pytest.raises(_WouldLaunch) as launched:
        asyncio.run(worker._execute("browser.session_open", payload))
    return launched.value.kwargs


def test_the_gateway_opens_its_session_as_a_research_session_open() -> None:
    sent = _gateway_session_open()
    assert sent["capability"] == "browser.session_open"
    assert sent["payload"]["session_id"] == SESSION_ID
    assert sent["payload"]["profile"] in cloud_policy.ALLOWED_PROFILES


@pytest.mark.parametrize("payload", _payloads())
def test_the_session_research_opens_on_the_cloud_launches_headless_chromium(
    tmp_path, monkeypatch, payload
) -> None:
    monkeypatch.setattr(worker_module, "ManagedBackend", _refuse_to_launch)
    worker, cfg = _cloud_worker(tmp_path)

    clamped = cloud_policy.clamp_command("browser.session_open", payload)
    launch = _launch_of(worker, clamped)

    assert launch["headless"] is True
    assert launch["channel"] == "chromium"
    assert Path(launch["profile_dir"]) == cfg.data_dir / "profile"


def test_without_the_clamp_the_same_payload_would_launch_a_visible_chrome(
    tmp_path, monkeypatch
) -> None:
    """Why the clamp has to say it: the worker takes the window from the payload, ahead of
    the ``--channel chromium --headless`` it was started with."""
    monkeypatch.setattr(worker_module, "ManagedBackend", _refuse_to_launch)
    worker, _ = _cloud_worker(tmp_path)

    launch = _launch_of(worker, dict(HOSTILE))

    assert (launch["headless"], launch["channel"]) == (False, "chrome")


@pytest.mark.parametrize("payload", _payloads())
def test_the_bridge_hands_the_worker_a_headless_chromium_session_open(payload) -> None:
    socket = FakeSocket(HANDSHAKE, [_command("browser.session_open", payload=payload)])
    worker = FakeWorker()

    _serve(socket, worker)

    capability, seen = worker.calls[0]
    assert capability == "browser.session_open"
    assert seen["channel"] == "chromium"
    assert seen["policy"]["visible"] is False
    assert seen["session_id"] == payload["session_id"]
    assert seen["profile"] == payload["profile"]


def test_the_clamp_leaves_the_callers_own_payload_as_it_was() -> None:
    payload = _gateway_session_open()["payload"]
    before = repr(payload)
    cloud_policy.clamp_command("browser.session_open", payload)
    assert repr(payload) == before

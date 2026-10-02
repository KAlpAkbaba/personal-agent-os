"""The cloud browser worker (ADR-0213 PR 2): the Linux companion that dials the broker.

No Docker, no network, no browser: the bridge is driven against a fake socket and a fake
worker link; the container definition is parsed as YAML. Crypto and YAML tests skip when
the interpreter lacks ``cryptography`` / ``yaml`` (the ``cloud`` extra carries the first).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path

import pytest

from browser_agent import policy
from browser_agent.cloud import broker, config, healthcheck
from browser_agent.cloud import policy as cloud_policy
from browser_agent.cloud.__main__ import build_worker_args, main
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import Worker, build_arg_parser

REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "infra" / "docker" / "cloud-browser" / "compose.fragment.yml"
EVIDENCE = REPO / "docs" / "evidence" / "adr-0213-cloud-worker-memory-2026-09-30.json"
API_ROUTES = REPO / "services" / "api" / "app" / "broker" / "routes.py"
API_FRAMES = REPO / "services" / "api" / "app" / "broker" / "frames.py"

DEVICE_ID = "11111111-2222-4333-8444-555555555555"


# --------------------------------------------------------------------------- #
# the session policy
# --------------------------------------------------------------------------- #


def test_the_cloud_default_policy_is_exactly_read_and_navigate():
    assert cloud_policy.CLOUD_SESSION_CLASSES == {policy.RiskClass.READ, policy.RiskClass.NAVIGATE}


def test_a_policy_that_allows_more_than_read_and_navigate_is_refused():
    wider = cloud_policy.CLOUD_SESSION_CLASSES | {policy.RiskClass.REVERSIBLE_WRITE}
    with pytest.raises(BrowserError) as raised:
        cloud_policy.assert_policy_within(wider)
    assert raised.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_a_policy_that_is_equal_or_narrower_is_accepted():
    cloud_policy.assert_policy_within(cloud_policy.CLOUD_SESSION_CLASSES)
    cloud_policy.assert_policy_within({policy.RiskClass.READ})


def test_a_session_open_without_a_policy_is_given_read_and_navigate():
    out = cloud_policy.clamp_command("browser.session_open", {"profile": "research"})
    assert sorted(out["policy"]["allowed_risk_classes"]) == ["NAVIGATE", "READ"]


def test_a_session_open_asking_for_a_wider_policy_is_refused_before_the_worker_sees_it():
    payload = {"policy": {"allowed_risk_classes": ["READ", "NAVIGATE", "HIGH_IMPACT"]}}
    with pytest.raises(BrowserError) as raised:
        cloud_policy.clamp_command("browser.session_open", payload)
    assert raised.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_a_session_open_asking_for_a_narrower_policy_keeps_it():
    payload = {"policy": {"allowed_risk_classes": ["READ"], "visible": False}}
    out = cloud_policy.clamp_command("browser.session_open", payload)
    assert out["policy"]["allowed_risk_classes"] == ["READ"]
    assert out["policy"]["visible"] is False


def test_a_session_open_is_headless_chromium_whatever_the_payload_asks_for():
    asked = {"channel": "chrome", "policy": {"visible": True}}
    out = cloud_policy.clamp_command("browser.session_open", asked)
    assert out["channel"] == cloud_policy.CLOUD_CHANNEL == "chromium"
    assert out["policy"]["visible"] is False
    assert asked == {"channel": "chrome", "policy": {"visible": True}}


def test_a_session_open_that_says_nothing_about_the_window_is_headless_chromium_too():
    out = cloud_policy.clamp_command("browser.session_open", {})
    assert out["channel"] == "chromium"
    assert out["policy"]["visible"] is False


def test_a_session_open_naming_the_owner_profile_is_refused():
    with pytest.raises(BrowserError):
        cloud_policy.clamp_command("browser.session_open", {"profile": "owner"})


def test_a_command_that_is_not_a_session_open_passes_through_untouched():
    payload = {"url": "https://example.org/"}
    assert cloud_policy.clamp_command("browser.navigate", payload) == payload


# --------------------------------------------------------------------------- #
# the entry module builds the worker
# --------------------------------------------------------------------------- #


def _cfg(tmp_path: Path, **over) -> config.CloudConfig:
    base = {
        "broker_http_url": "http://api:8000",
        "state_dir": tmp_path / "state",
        "data_dir": tmp_path / "data",
        "token_file": tmp_path / "state" / "enroll.token",
        "max_concurrent": 2,
    }
    base.update(over)
    return config.CloudConfig(**base)


def test_the_worker_is_built_headless_on_chromium_with_a_dedicated_profile(tmp_path):
    args = build_arg_parser().parse_args(build_worker_args(_cfg(tmp_path)))
    assert args.headless is True
    assert args.visible is False
    assert args.channel == "chromium"
    assert Path(args.profile_dir).parent == tmp_path / "data"
    worker = Worker(args)
    assert worker._default_visible is False


def test_the_worker_is_started_without_a_trusted_origin_or_private_destinations(tmp_path):
    argv = build_worker_args(_cfg(tmp_path))
    args = build_arg_parser().parse_args(argv)
    assert args.trusted_origin is None
    assert args.allow_private_destinations is False
    assert "--owner-enrollment-file" not in argv


def test_building_the_worker_with_a_wider_policy_is_refused(tmp_path):
    wider = cloud_policy.CLOUD_SESSION_CLASSES | {policy.RiskClass.EXTERNAL_COMMUNICATION}
    with pytest.raises(BrowserError):
        build_worker_args(_cfg(tmp_path), session_classes=wider)


# --------------------------------------------------------------------------- #
# configuration: no enrollment material, no start
# --------------------------------------------------------------------------- #


def _env(tmp_path: Path, **over) -> dict[str, str]:
    env = {
        "PAGENTOS_CLOUD_BROKER_URL": "http://api:8000",
        "PAGENTOS_CLOUD_STATE_DIR": str(tmp_path / "state"),
        "PAGENTOS_CLOUD_DATA_DIR": str(tmp_path / "data"),
        "PAGENTOS_CLOUD_ENROLLMENT_TOKEN_FILE": str(tmp_path / "state" / "enroll.token"),
    }
    env.update(over)
    return env


def test_a_start_with_neither_identity_nor_token_file_refuses(tmp_path):
    with pytest.raises(config.ConfigError, match="enrollment"):
        config.load_config(_env(tmp_path))


def test_a_start_with_an_empty_token_file_refuses(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "enroll.token").write_text("  \n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="enrollment"):
        config.load_config(_env(tmp_path))


def test_a_start_with_a_token_file_is_accepted(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "enroll.token").write_text("tok-abcdefgh\n", encoding="utf-8")
    cfg = config.load_config(_env(tmp_path))
    assert cfg.read_token() == "tok-abcdefgh"
    assert cfg.has_identity() is False


def test_a_start_with_a_persisted_identity_needs_no_token_file(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "identity.json").write_text(
        json.dumps({"device_id": DEVICE_ID}), encoding="utf-8"
    )
    (tmp_path / "state" / "device.key").write_text("pem", encoding="utf-8")
    assert config.load_config(_env(tmp_path)).has_identity() is True


def test_an_identity_file_without_its_key_is_not_an_identity(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "identity.json").write_text(
        json.dumps({"device_id": DEVICE_ID}), encoding="utf-8"
    )
    with pytest.raises(config.ConfigError):
        config.load_config(_env(tmp_path))


def test_a_start_without_a_broker_url_refuses(tmp_path):
    env = _env(tmp_path)
    del env["PAGENTOS_CLOUD_BROKER_URL"]
    with pytest.raises(config.ConfigError, match="BROKER_URL"):
        config.load_config(env)


def test_a_broker_url_that_is_not_http_refuses(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(_env(tmp_path, PAGENTOS_CLOUD_BROKER_URL="ftp://api"))


def test_the_websocket_url_is_the_devices_connect_route(tmp_path):
    assert _cfg(tmp_path).ws_url == "ws://api:8000/v1/devices/connect"
    assert (
        _cfg(tmp_path, broker_http_url="https://core.example/").ws_url
        == "wss://core.example/v1/devices/connect"
    )


def test_main_exits_2_when_the_enrollment_material_is_missing(tmp_path, monkeypatch):
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    assert main([]) == 2


def test_the_module_run_the_way_the_container_runs_it_refuses_and_says_why(tmp_path):
    """The image's ENTRYPOINT is ``python -m browser_agent.cloud``. The first real build
    (Cloud Core, 2026-09-30) found that this did NOTHING and exited 0: ``main`` was
    defined and never called, and every test called ``main()`` itself. This one starts
    the module as a process, the way the container does."""
    import os
    import subprocess
    import sys

    env = {**os.environ, **_env(tmp_path), "PYTHONPATH": str(Path(policy.__file__).parents[1])}
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "browser_agent.cloud"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert done.returncode == 2, (done.returncode, done.stderr[-400:])
    assert "cloud worker refuses to start" in done.stderr
    assert "no enrollment material" in done.stderr


# --------------------------------------------------------------------------- #
# the enrollment and hello the module sends are the contract's
# --------------------------------------------------------------------------- #


def test_the_enroll_body_names_the_device_bulut_of_platform_cloud():
    body = broker.enroll_body("tok-abcdefgh", "SPKI" * 12)
    assert body["name"] == "bulut"
    assert body["platform"] == "cloud"
    assert body["token"] == "tok-abcdefgh"


def test_the_enroll_body_carries_exactly_the_fields_the_api_accepts():
    body = broker.enroll_body("tok-abcdefgh", "SPKI" * 12)
    source = API_ROUTES.read_text(encoding="utf-8")
    block = source.split("class EnrollRequest", 1)[1].split("@router", 1)[0]
    api_fields = set(re.findall(r"^    (\w+): ", block, flags=re.MULTILINE))
    assert set(body) == api_fields


def test_the_enroll_body_advertises_the_workers_capabilities_and_all_are_valid_names():
    body = broker.enroll_body("tok-abcdefgh", "SPKI" * 12)
    assert body["capabilities"] == list(policy.CAPABILITIES)
    pattern = re.search(r'CAPABILITY_PATTERN = r"([^"]+)"', API_FRAMES.read_text("utf-8"))
    assert pattern is not None
    assert all(re.match(pattern.group(1), cap) for cap in body["capabilities"])


def test_the_platform_fits_the_apis_sixty_four_character_bound_and_the_name_its_two_hundred():
    assert 1 <= len(config.DEVICE_PLATFORM) <= 64
    assert 1 <= len(config.DEVICE_NAME) <= 200


def test_the_hello_is_protocol_version_one_with_the_device_id_and_capabilities():
    hello = broker.hello_frame(DEVICE_ID)
    assert hello["type"] == "hello"
    assert hello["protocol_version"] == 1
    assert hello["device_id"] == DEVICE_ID
    assert hello["capabilities"] == list(policy.CAPABILITIES)
    assert len(hello["software_version"]) <= 64


def test_the_protocol_version_matches_the_one_the_broker_requires():
    match = re.search(r"^PROTOCOL_VERSION = (\d+)", API_FRAMES.read_text("utf-8"), re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) == broker.PROTOCOL_VERSION


def test_every_error_class_the_worker_can_answer_with_is_one_the_broker_knows():
    frames_src = API_FRAMES.read_text("utf-8")
    known = set(re.findall(r'^    "(\w+)",$', frames_src.split("ERROR_CLASSES = (")[1], re.M))
    for member in ErrorClass:
        assert str(member.value) in known, member


def test_the_signature_verifies_the_way_the_broker_verifies_it():
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    hashes = pytest.importorskip("cryptography.hazmat.primitives.hashes")
    ser = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    pem, spki_b64 = broker.generate_identity()
    nonce = b"n" * 32
    signature_b64 = broker.sign_challenge(pem, base64.b64encode(nonce).decode(), DEVICE_ID)
    public = ser.load_der_public_key(base64.b64decode(spki_b64))
    public.verify(
        base64.b64decode(signature_b64), nonce + DEVICE_ID.encode(), ec.ECDSA(hashes.SHA256())
    )


def test_a_signature_over_a_different_device_id_does_not_verify():
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    hashes = pytest.importorskip("cryptography.hazmat.primitives.hashes")
    ser = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    from cryptography.exceptions import InvalidSignature

    pem, spki_b64 = broker.generate_identity()
    nonce = b"n" * 32
    signature_b64 = broker.sign_challenge(pem, base64.b64encode(nonce).decode(), DEVICE_ID)
    public = ser.load_der_public_key(base64.b64decode(spki_b64))
    with pytest.raises(InvalidSignature):
        public.verify(
            base64.b64decode(signature_b64), nonce + b"other", ec.ECDSA(hashes.SHA256())
        )


# --------------------------------------------------------------------------- #
# the bridge, against a fake socket and a fake worker
# --------------------------------------------------------------------------- #


class FakeSocket:
    """Plays the broker: scripted inbound frames, recorded outbound frames."""

    def __init__(self, handshake: list[dict], after: list[dict], linger_s: float = 0.0):
        self._handshake = [json.dumps(f) for f in handshake]
        self._after = [json.dumps(f) for f in after]
        self._linger_s = linger_s
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def recv(self) -> str:
        return self._handshake.pop(0)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for text in self._after:
            yield text
        if self._linger_s:
            await asyncio.sleep(self._linger_s)


class FakeWorker:
    def __init__(self, envelope: dict | None = None, delay_s: float = 0.0):
        self.calls: list[tuple[str, dict]] = []
        self.cancelled: list[str] = []
        self._envelope = envelope
        self._delay_s = delay_s

    async def exec(self, request_id: str, capability: str, payload: dict, timeout_ms: int):
        self.calls.append((capability, payload))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return self._envelope or {"type": "result", "request_id": request_id, "ok": True,
                                  "result": {"title": "fixture"}}

    def cancel(self, request_id: str) -> None:
        self.cancelled.append(request_id)


def _command(capability="browser.navigate", cid="cmd-1", key="key-1", payload=None,
             expires_in_s=60):
    from datetime import UTC, datetime, timedelta

    when = (datetime.now(UTC) + timedelta(seconds=expires_in_s)).isoformat()
    return {
        "type": "command",
        "command": {
            "command_id": cid,
            "idempotency_key": key,
            "capability": capability,
            "payload": payload if payload is not None else {"url": "https://example.org/"},
            "expires_at": when.replace("+00:00", "Z"),
            "trace_id": "t",
        },
    }


def _serve(socket: FakeSocket, worker: FakeWorker) -> broker.CloudBridge:
    pem = b"unused-in-fake"
    bridge = broker.CloudBridge(
        device_id=DEVICE_ID,
        signer=lambda nonce_b64: "SIG(" + nonce_b64 + ")",
        worker=worker,
        max_concurrent=2,
    )
    del pem
    asyncio.run(bridge.serve(socket))
    return bridge


HANDSHAKE = [
    {"type": "challenge", "nonce": "bm9uY2U="},
    {"type": "welcome", "session_id": "s", "heartbeat_interval_s": 3600},
]


def _acks(socket: FakeSocket, command_id: str) -> list[str]:
    return [f["status"] for f in socket.sent
            if f["type"] == "command_ack" and f["command_id"] == command_id]


def test_the_handshake_sends_hello_then_the_signed_challenge():
    socket = FakeSocket(HANDSHAKE, [])
    _serve(socket, FakeWorker())
    assert [f["type"] for f in socket.sent[:2]] == ["hello", "auth"]
    assert socket.sent[1]["signature"] == "SIG(bm9uY2U=)"


def test_a_command_is_acked_accepted_then_running_then_succeeded_with_the_workers_result():
    socket = FakeSocket(HANDSHAKE, [_command()])
    worker = FakeWorker()
    _serve(socket, worker)
    assert _acks(socket, "cmd-1") == ["accepted", "running", "succeeded"]
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert final["result"] == {"title": "fixture"}
    assert worker.calls == [("browser.navigate", {"url": "https://example.org/"})]


def test_a_worker_failure_is_acked_failed_with_the_workers_error_class():
    envelope = {"type": "result", "request_id": "x", "ok": False,
                "error": {"class": "timeout", "message": "slow", "retryable": True}}
    socket = FakeSocket(HANDSHAKE, [_command()])
    _serve(socket, FakeWorker(envelope))
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert final["status"] == "failed"
    assert final["error"]["class"] == "timeout"


def test_an_error_class_the_broker_does_not_know_is_acked_as_internal_bug():
    envelope = {"type": "result", "request_id": "x", "ok": False,
                "error": {"class": "brand_new", "message": "?"}}
    socket = FakeSocket(HANDSHAKE, [_command()])
    _serve(socket, FakeWorker(envelope))
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert final["error"]["class"] == "internal_bug"


def test_the_same_idempotency_key_twice_runs_the_worker_once_and_re_acks_the_outcome():
    socket = FakeSocket(HANDSHAKE, [_command(cid="a", key="k"), _command(cid="b", key="k")])
    worker = FakeWorker()
    _serve(socket, worker)
    assert len(worker.calls) == 1
    assert _acks(socket, "b")[-1] == "succeeded"
    assert "running" not in _acks(socket, "b")


def test_a_different_idempotency_key_runs_the_worker_again():
    socket = FakeSocket(HANDSHAKE, [_command(cid="a", key="k1"), _command(cid="b", key="k2")])
    worker = FakeWorker()
    _serve(socket, worker)
    assert len(worker.calls) == 2


def test_an_expired_command_is_failed_command_expired_and_never_reaches_the_worker():
    socket = FakeSocket(HANDSHAKE, [_command(expires_in_s=-5)])
    worker = FakeWorker()
    _serve(socket, worker)
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert (final["status"], final["error"]["class"]) == ("failed", "command_expired")
    assert worker.calls == []


def test_a_session_open_asking_for_more_than_read_and_navigate_fails_without_reaching_the_worker():
    wide = {"policy": {"allowed_risk_classes": ["READ", "NAVIGATE", "REVERSIBLE_WRITE"]}}
    socket = FakeSocket(HANDSHAKE, [_command("browser.session_open", payload=wide)])
    worker = FakeWorker()
    _serve(socket, worker)
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert (final["status"], final["error"]["class"]) == ("failed", "security_scope_error")
    assert worker.calls == []


def test_a_session_open_reaches_the_worker_with_read_and_navigate_filled_in():
    socket = FakeSocket(HANDSHAKE, [_command("browser.session_open", payload={})])
    worker = FakeWorker()
    _serve(socket, worker)
    assert sorted(worker.calls[0][1]["policy"]["allowed_risk_classes"]) == ["NAVIGATE", "READ"]


def test_a_capability_the_worker_does_not_advertise_is_refused_before_the_worker():
    socket = FakeSocket(HANDSHAKE, [_command("shell.run")])
    worker = FakeWorker()
    _serve(socket, worker)
    final = [f for f in socket.sent if f["type"] == "command_ack"][-1]
    assert final["error"]["class"] == "capability_missing"
    assert worker.calls == []


def test_a_cancel_frame_is_forwarded_to_the_worker_by_command_id():
    socket = FakeSocket(HANDSHAKE, [_command(cid="c9"), {"type": "cancel", "command_id": "c9"}])
    worker = FakeWorker(delay_s=0.05)
    _serve(socket, worker)
    assert worker.cancelled == ["c9"]


def test_heartbeats_are_sent_at_the_interval_the_welcome_named_with_rising_seq():
    handshake = [HANDSHAKE[0], {"type": "welcome", "session_id": "s", "heartbeat_interval_s": 0.01}]
    socket = FakeSocket(handshake, [], linger_s=0.08)
    _serve(socket, FakeWorker())
    seqs = [f["seq"] for f in socket.sent if f["type"] == "heartbeat"]
    assert len(seqs) >= 2
    assert seqs == sorted(set(seqs))


def test_an_auth_refusal_from_the_broker_ends_the_session_with_an_error():
    socket = FakeSocket(
        [HANDSHAKE[0], {"type": "error", "error": {"class": "auth_error", "message": "no"}}], []
    )
    with pytest.raises(broker.AuthRefused):
        _serve(socket, FakeWorker())


def test_the_healthcheck_passes_while_the_marker_is_fresh_and_fails_when_it_is_stale(tmp_path):
    cfg = _cfg(tmp_path)
    assert healthcheck.is_healthy(cfg) is False
    healthcheck.touch(cfg)
    assert healthcheck.is_healthy(cfg) is True
    assert healthcheck.is_healthy(cfg, now=time.time() + healthcheck.MAX_AGE_S + 5) is False


def test_the_bridge_touches_the_liveness_hook_on_welcome_and_on_each_heartbeat():
    beats: list[int] = []
    handshake = [HANDSHAKE[0], {"type": "welcome", "session_id": "s", "heartbeat_interval_s": 0.01}]
    socket = FakeSocket(handshake, [], linger_s=0.08)
    bridge = broker.CloudBridge(
        device_id=DEVICE_ID, signer=lambda n: "S", worker=FakeWorker(),
        on_alive=lambda: beats.append(1),
    )
    asyncio.run(bridge.serve(socket))
    assert len(beats) >= 3


# --------------------------------------------------------------------------- #
# the container definition
# --------------------------------------------------------------------------- #


def _service() -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = doc["services"]
    assert list(services) == ["cloud-browser"]
    return services["cloud-browser"]


def test_the_container_is_capped_at_two_gigabytes_of_memory_and_swap():
    svc = _service()
    assert svc["mem_limit"] == "2g"
    assert svc["memswap_limit"] == "2g"


def test_the_container_has_two_cpus_a_two_gigabyte_shm_and_an_init_process():
    svc = _service()
    assert float(svc["cpus"]) == 2
    assert svc["shm_size"] == "2gb"
    assert svc["init"] is True


def test_the_container_restarts_on_failure_and_has_a_healthcheck():
    svc = _service()
    assert svc["restart"] in {"unless-stopped", "on-failure", "always"}
    assert svc["healthcheck"]["test"]
    assert svc["healthcheck"]["interval"]


def test_the_container_publishes_no_ports_and_mounts_no_docker_socket():
    svc = _service()
    assert "ports" not in svc
    assert not any("docker.sock" in str(v) for v in svc.get("volumes", []))
    assert svc.get("privileged", False) is False


def test_the_container_carries_no_secret_in_its_environment():
    svc = _service()
    env = svc.get("environment", {})
    keys = list(env) if isinstance(env, dict) else [e.split("=", 1)[0] for e in env]
    assert not [k for k in keys if re.search(r"TOKEN(?!_FILE)|SECRET|KEY|PASSWORD", k)]


def test_the_memory_evidence_is_either_not_run_with_a_command_or_real_with_its_numbers():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["status"] in {"NOT_RUN", "PROVEN_REAL"}
    if evidence["status"] == "NOT_RUN":
        assert evidence["peak_rss_bytes"] is None
        commands = evidence["command_for_the_lead_on_the_cloud_core"]
        assert any("measure-memory.sh" in c for c in commands)
    else:
        for field in ("machine", "date_utc", "image_digest", "peak_rss_bytes"):
            assert evidence[field]

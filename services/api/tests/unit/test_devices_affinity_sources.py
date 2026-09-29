"""ADR-0208: where a session is - the address rules, the id list, the settings that guard them.

The rules here are the ones a wrong answer turns into "the command ran on the employer's
machine": the transport peer of a request is the nginx edge, so the caller's address is a
header, and a header is only as good as the peer that sent it.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices import affinity

EDGE = affinity.trusted_networks(["172.18.0.0/16"])
OFFICE_IP = "100.80.20.54"


def _headers(**kv: str) -> dict[str, str]:
    return {k.replace("_", "-").lower(): v for k, v in kv.items()}


# ------------------------------------------------------------------ client_ip


def test_the_edge_peer_yields_the_address_the_edge_reported() -> None:
    assert affinity.client_ip("172.18.0.4", _headers(x_real_ip=OFFICE_IP), EDGE) == OFFICE_IP


def test_the_edge_peer_without_the_header_yields_nobody() -> None:
    # The peer is the proxy, and the proxy is not the caller.
    assert affinity.client_ip("172.18.0.4", {}, EDGE) is None


@pytest.mark.parametrize(
    "junk", ["", "not an ip", "100.80.20.54, 100.1.1.1", "999.1.1.1", "office"]
)
def test_the_edge_peer_with_a_malformed_header_yields_nobody(junk: str) -> None:
    assert affinity.client_ip("172.18.0.4", _headers(x_real_ip=junk), EDGE) is None


def test_a_header_from_a_peer_that_is_not_trusted_is_ignored() -> None:
    # Anyone can write X-Real-IP; only the edge's is a fact. The peer's own address is used.
    assert (
        affinity.client_ip("100.99.99.99", _headers(x_real_ip=OFFICE_IP), EDGE) == "100.99.99.99"
    )


def test_with_no_trusted_proxy_configured_no_header_is_ever_believed() -> None:
    assert affinity.client_ip("172.18.0.4", _headers(x_real_ip=OFFICE_IP), ()) == "172.18.0.4"


def test_x_forwarded_for_is_never_read() -> None:
    headers = _headers(x_forwarded_for="1.2.3.4, 100.80.20.54")
    assert affinity.client_ip("172.18.0.4", headers, EDGE) is None
    assert affinity.client_ip("100.99.99.99", headers, EDGE) == "100.99.99.99"


@pytest.mark.parametrize("peer", [None, "", "testclient", "localhost", "unix:/run/x.sock"])
def test_a_peer_that_is_not_an_address_yields_nobody(peer) -> None:
    assert affinity.client_ip(peer, _headers(x_real_ip=OFFICE_IP), EDGE) is None


def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_address() -> None:
    assert affinity.client_ip("::ffff:100.80.20.54", {}, EDGE) == OFFICE_IP
    assert (
        affinity.client_ip("172.18.0.4", _headers(x_real_ip="::ffff:100.80.20.54"), EDGE)
        == OFFICE_IP
    )


def test_a_malformed_configured_network_is_skipped_never_trusted() -> None:
    assert affinity.trusted_networks(["nonsense", "172.18.0.0/16"]) == (
        affinity.ipaddress.ip_network("172.18.0.0/16"),
    )


# ------------------------------------------------------------------- id list


def test_the_candidate_order_is_declared_then_bound_then_source() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert affinity.session_device_ids(declared=str(a), bound=b, source=c) == (a, b, c)


def test_repeats_and_non_ids_are_dropped() -> None:
    a = uuid.uuid4()
    assert affinity.session_device_ids(declared=None, bound=str(a), source=a) == (a,)
    assert affinity.session_device_ids(declared="nope", bound=42, source=None) == ()
    assert affinity.session_device_ids() == ()


# --------------------------------------------------- the broker's address -> device map


def _connect(runtime: BrokerRuntime, ip: str | None) -> uuid.UUID:
    device_id = uuid.uuid4()
    runtime.register_connection(
        DeviceConnection(
            device_id=device_id, session_id=uuid.uuid4(), websocket=object(), peer_ip=ip
        )
    )
    return device_id


def test_one_connected_device_at_an_address_is_the_device_at_that_address() -> None:
    runtime = BrokerRuntime(Settings(_env_file=None))
    office = _connect(runtime, OFFICE_IP)
    _connect(runtime, "100.92.148.30")
    assert runtime.device_id_for_peer_ip(OFFICE_IP) == office
    assert runtime.device_id_for_peer_ip("100.1.1.1") is None


def test_two_devices_at_one_address_are_no_answer() -> None:
    runtime = BrokerRuntime(Settings(_env_file=None))
    _connect(runtime, OFFICE_IP)
    _connect(runtime, OFFICE_IP)
    assert runtime.device_id_for_peer_ip(OFFICE_IP) is None


def test_a_device_whose_address_was_never_told_matches_nothing() -> None:
    runtime = BrokerRuntime(Settings(_env_file=None))
    _connect(runtime, None)
    assert runtime.device_id_for_peer_ip("None") is None


def test_the_address_leaves_with_the_connection() -> None:
    runtime = BrokerRuntime(Settings(_env_file=None))
    device_id = _connect(runtime, OFFICE_IP)
    runtime.unregister_connection(runtime.connections[device_id])
    assert runtime.device_id_for_peer_ip(OFFICE_IP) is None


# ---------------------------------------------------------- source_device_id + settings


class _Broker:
    def __init__(self, mapping: dict[str, uuid.UUID]) -> None:
        self.mapping = mapping

    def device_id_for_peer_ip(self, ip: str) -> uuid.UUID | None:
        return self.mapping.get(ip)


def test_the_source_address_path_is_off_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.device_affinity_by_source_ip is False
    assert settings.trusted_proxy_cidrs == ()
    office = uuid.uuid4()
    assert (
        affinity.source_device_id(
            peer_host=OFFICE_IP, headers={}, broker=_Broker({OFFICE_IP: office}), settings=settings
        )
        is None
    )


def test_the_source_address_path_answers_only_when_switched_on() -> None:
    office = uuid.uuid4()
    settings = Settings(
        _env_file=None, device_affinity_by_source_ip=True, trusted_proxy_cidrs=("172.18.0.0/16",)
    )
    got = affinity.source_device_id(
        peer_host="172.18.0.4",
        headers=_headers(x_real_ip=OFFICE_IP),
        broker=_Broker({OFFICE_IP: office}),
        settings=settings,
    )
    assert got == office


def test_no_broker_is_no_answer() -> None:
    settings = Settings(_env_file=None, device_affinity_by_source_ip=True)
    assert (
        affinity.source_device_id(peer_host=OFFICE_IP, headers={}, broker=None, settings=settings)
        is None
    )


def test_the_trusted_proxy_setting_reads_json_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_TRUSTED_PROXY_CIDRS", '["172.18.0.0/16", "10.0.0.5/32"]')
    monkeypatch.setenv("PAGENTOS_DEVICE_AFFINITY_BY_SOURCE_IP", "true")
    settings = Settings(_env_file=None)
    assert settings.trusted_proxy_cidrs == ("172.18.0.0/16", "10.0.0.5/32")
    assert settings.device_affinity_by_source_ip is True


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "::/0", "not-a-cidr", "300.1.1.1/8"])
def test_a_wildcard_or_malformed_trusted_proxy_is_refused_at_start_up(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_cidrs=(bad,))

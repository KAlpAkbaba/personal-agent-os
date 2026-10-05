"""The watch's cloud reader (watch-engine): where a reading may run, and where it may not.

A reading is a SCHEDULED job: the execution rule is asked, its chain is the cloud alone, and
``acting`` is False. A home machine that is online is never a fallback - when the cloud is
down the reading is ``unreadable`` and no command leaves for anyone. The ADR-0257 setting
(``routines_execution_rule_enabled``) is a routine's switch, not the watch's: flipping it
changes nothing here. A destination is checked again at every reading (DNS rebinding).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.devices.commands import CommandFailed, CommandSucceeded
from app.devices.types import DeviceView
from app.execution import rule
from app.research.browser_gateway import (
    BrowserDispatchError,
    DeviceBrowserGateway,
    PageDigest,
    reset_known_open_sessions,
)
from app.watch import compare, reader
from app.watch.reader import CloudReader

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
URL = "https://www.home-assistant.io/blog/"
WATCH_ID = uuid.UUID(int=42)
CLOUD_ID = uuid.UUID(int=7)
HOME_ID = uuid.UUID(int=8)
CAPS = ("browser.fetch_evidence", "browser.session_open", "browser.chrome")


@pytest.fixture(autouse=True)
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    table = {"www.home-assistant.io": ["93.184.216.34"]}
    monkeypatch.setattr(
        "app.research.destination.resolve_hostname", lambda host: table.get(host, ["93.184.216.34"])
    )
    reset_known_open_sessions()
    return table


def _view(device_id: uuid.UUID, platform: str, presence: str, **kw) -> DeviceView:
    return DeviceView(
        id=device_id,
        name=kw.pop("name", platform),
        platform=platform,
        status="enrolled",
        presence=presence,
        capabilities=CAPS,
        enrolled_at=NOON,
        last_seen_at=NOON,
        **kw,
    )


def cloud(presence: str = "online") -> DeviceView:
    return _view(CLOUD_ID, "cloud", presence, name="pagentos-cloud-browser")


def home(presence: str = "online") -> DeviceView:
    return _view(HOME_ID, "windows", presence, name="ev", labels=("owner_chrome",))


class FakeGateway:
    def __init__(self, digest: PageDigest | Exception) -> None:
        self.digest = digest
        self.calls: list[tuple[str, str | None]] = []
        self.closed = 0

    def fetch_page_digest(self, url: str, *, selector: str | None = None) -> PageDigest:
        self.calls.append((url, selector))
        if isinstance(self.digest, Exception):
            raise self.digest
        return self.digest

    def close_session(self) -> None:
        self.closed += 1


def digest(text: str = "Home Assistant 2026.10", **kw) -> PageDigest:
    fields = {
        "url": URL,
        "final_url": URL,
        "excerpt": text,
        "text_sha256": compare.text_sha256(text),
        "selector_matched": None,
        "page_kind": "ok",
        "http_status": 200,
    }
    fields.update(kw)
    return PageDigest(**fields)


class Recorder:
    """The gateway factory: which device a gateway was built for."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.built: list[uuid.UUID] = []

    def __call__(self, device_id: uuid.UUID, task_id: str):
        self.built.append(device_id)
        return self.gateway


def _read(views, factory, url: str = URL, selector: str | None = None):
    r = CloudReader(views=lambda db: list(views), gateway_factory=factory)
    return r.read(None, url=url, selector=selector, watch_id=WATCH_ID)


# ------------------------------------------------------------------ the rule, as a scheduled job


def test_the_rule_is_asked_as_a_scheduled_read_only_job(monkeypatch) -> None:
    asked: list[rule.ExecutionRequest] = []
    real = rule.decide

    def spy(request: rule.ExecutionRequest) -> rule.Decision:
        asked.append(request)
        return real(request)

    monkeypatch.setattr(reader.rule, "decide", spy)
    gateway = FakeGateway(digest())
    factory = Recorder(gateway)
    observation = _read([cloud(), home()], factory)
    assert observation.ok is True
    assert observation.text_sha256 == compare.text_sha256("Home Assistant 2026.10")
    assert len(asked) == 1
    assert asked[0].job_kind is rule.JobKind.SCHEDULED
    assert asked[0].acting is False
    assert asked[0].url == URL
    assert factory.built == [CLOUD_ID]
    assert gateway.calls == [(URL, None)]
    assert gateway.closed == 1


def test_cloud_offline_with_a_home_machine_online_is_unreadable_and_no_command_leaves() -> None:
    sent: list[tuple[uuid.UUID, str]] = []

    class Client:
        def run(self, *, device_id, capability, **kw):
            sent.append((device_id, capability))
            raise AssertionError("no command may leave")

    def real_factory(device_id: uuid.UUID, task_id: str) -> DeviceBrowserGateway:
        return DeviceBrowserGateway(Client(), device_id=device_id, task_id=task_id)

    observation = _read([cloud("offline"), home("online")], real_factory)
    assert observation.ok is False
    assert observation.reason_tr == "Bulut şu anda çevrimiçi değil."
    assert sent == []


def test_no_cloud_device_at_all_is_the_same_refusal() -> None:
    factory = Recorder(FakeGateway(digest()))
    observation = _read([home("online")], factory)
    assert (observation.ok, observation.reason_tr) == (False, "Bulut şu anda çevrimiçi değil.")
    assert factory.built == []


def test_the_adr_0257_setting_changes_nothing(monkeypatch) -> None:
    results = []
    for flag in (False, True):
        settings = Settings(_env_file=None, routines_execution_rule_enabled=flag)
        monkeypatch.setattr("app.config.get_settings", lambda s=settings: s)
        monkeypatch.setattr("app.routines.dispatch.get_settings", lambda s=settings: s)
        for views in ([cloud(), home()], [cloud("offline"), home()]):
            factory = Recorder(FakeGateway(digest()))
            observation = _read(views, factory)
            results.append((flag, observation.ok, observation.reason_tr, tuple(factory.built)))
    off = [r[1:] for r in results if r[0] is False]
    on = [r[1:] for r in results if r[0] is True]
    assert off == on
    assert off == [(True, "", (CLOUD_ID,)), (False, "Bulut şu anda çevrimiçi değil.", ())]


# ------------------------------------------------------------------ destinations


@pytest.mark.parametrize(
    "url",
    [
        "http://100.90.158.26:8001/v1/system/health",
        "http://100.64.0.1/",
        "http://127.0.0.1:8000/",
        "ftp://example.com/file",
    ],
)
def test_a_forbidden_destination_is_refused_at_reading(url: str) -> None:
    factory = Recorder(FakeGateway(digest()))
    observation = _read([cloud()], factory, url=url)
    assert observation.ok is False
    assert observation.error_class == "security_scope_error"
    assert observation.reason_tr and " " in observation.reason_tr
    assert factory.built == []


def test_a_name_that_now_resolves_into_the_tailnet_is_refused_at_reading(dns) -> None:
    # Public when the watch was made; repointed since (DNS rebinding).
    dns["www.home-assistant.io"] = ["100.90.158.26"]
    factory = Recorder(FakeGateway(digest()))
    observation = _read([cloud()], factory)
    assert (observation.ok, observation.error_class) == (False, "security_scope_error")
    assert factory.built == []


def test_a_redirect_into_a_forbidden_destination_from_the_worker_is_a_failure() -> None:
    error = BrowserDispatchError("security_scope_error", "redirect hop refused", False)
    gateway = FakeGateway(error)
    observation = _read([cloud()], Recorder(gateway))
    assert (observation.ok, observation.error_class) == (False, "security_scope_error")
    assert observation.text == ""
    assert gateway.closed == 1


# ------------------------------------------------------------------ what the page said


@pytest.mark.parametrize(
    ("kw", "word"),
    [
        ({"page_kind": "auth_wall"}, "giriş"),
        ({"page_kind": "captcha"}, "doğrulama"),
        ({"page_kind": "blocked"}, "engel"),
        ({"http_status": 404}, "404"),
        ({"selector_matched": False, "text_sha256": None, "excerpt": ""}, "seçici"),
    ],
)
def test_a_page_that_cannot_be_read_says_why_in_turkish(kw, word: str) -> None:
    observation = _read([cloud()], Recorder(FakeGateway(digest(**kw))), selector=".p")
    assert observation.ok is False
    assert word in observation.reason_tr.casefold()


def test_the_selector_reaches_the_gateway() -> None:
    gateway = FakeGateway(digest("19.499 TL", selector_matched=True))
    observation = _read([cloud()], Recorder(gateway), selector=".price")
    assert gateway.calls == [(URL, ".price")]
    assert observation.text == "19.499 TL"


# ------------------------------------------------------------------ the gateway's digest


class ScriptedClient:
    def __init__(self, result: dict | CommandFailed) -> None:
        self.result = result
        self.calls: list[tuple[str, dict, str]] = []

    def run(self, *, device_id, capability, payload, idempotency_key, **kw):
        self.calls.append((capability, payload, idempotency_key))
        if capability == "browser.fetch_evidence" and isinstance(self.result, CommandFailed):
            return self.result
        body = self.result if capability == "browser.fetch_evidence" else {"created": True}
        return CommandSucceeded(command_id=uuid.uuid4(), result=body)


def test_fetch_page_digest_sends_the_selector_and_reads_the_contract() -> None:
    sha = compare.text_sha256("19.499 TL")
    client = ScriptedClient(
        {
            "url": URL,
            "final_url": URL,
            "excerpt": "19.499 TL",
            "text_sha256": sha,
            "selector_matched": True,
            "page_kind": "ok",
            "http_status": 200,
        }
    )
    gateway = DeviceBrowserGateway(client, device_id=CLOUD_ID, task_id="watch-1")
    page = gateway.fetch_page_digest(URL, selector=".price")
    fetch = [c for c in client.calls if c[0] == "browser.fetch_evidence"]
    assert len(fetch) == 1 and fetch[0][1]["selector"] == ".price"
    assert fetch[0][1]["url"] == URL
    assert (page.text_sha256, page.selector_matched, page.excerpt) == (sha, True, "19.499 TL")
    opened = [c for c in client.calls if c[0] == "browser.session_open"]
    assert opened and opened[0][1]["profile"] == "research"


def test_fetch_page_digest_refuses_a_long_selector_and_a_forbidden_url_before_sending() -> None:
    client = ScriptedClient({})
    gateway = DeviceBrowserGateway(client, device_id=CLOUD_ID, task_id="watch-2")
    with pytest.raises(BrowserDispatchError) as long_one:
        gateway.fetch_page_digest(URL, selector="x" * 201)
    assert long_one.value.error_class == "validation_error"
    with pytest.raises(BrowserDispatchError) as forbidden:
        gateway.fetch_page_digest("http://100.64.0.1/")
    assert forbidden.value.error_class == "security_scope_error"
    assert client.calls == []


def test_fetch_page_digest_drops_a_malformed_hash() -> None:
    client = ScriptedClient({"url": URL, "excerpt": "x", "text_sha256": "not-a-hash"})
    gateway = DeviceBrowserGateway(client, device_id=CLOUD_ID, task_id="watch-3")
    assert gateway.fetch_page_digest(URL).text_sha256 is None


def test_fetch_page_digest_refuses_a_final_url_in_the_tailnet(dns) -> None:
    dns["evil.example"] = ["100.90.158.26"]
    client = ScriptedClient(
        {"url": URL, "final_url": "https://evil.example/", "excerpt": "x", "text_sha256": "a" * 64}
    )
    gateway = DeviceBrowserGateway(client, device_id=CLOUD_ID, task_id="watch-4")
    with pytest.raises(BrowserDispatchError) as refused:
        gateway.fetch_page_digest(URL)
    assert refused.value.error_class == "security_scope_error"


def test_a_worker_refusal_is_raised_typed() -> None:
    client = ScriptedClient(
        CommandFailed(
            error_class="security_scope_error",
            message="redirect",
            retryable=False,
        )
    )
    gateway = DeviceBrowserGateway(client, device_id=CLOUD_ID, task_id="watch-5")
    with pytest.raises(BrowserDispatchError) as refused:
        gateway.fetch_page_digest(URL)
    assert refused.value.error_class == "security_scope_error"

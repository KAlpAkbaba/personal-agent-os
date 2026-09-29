"""ADR-0210: a research report opened from a device that cannot ``file.fetch`` opens as a new
tab in the owner's own Chrome.

2026-09-29: the owner finished a research from the office PC and heard "cihazda açamadım".
The office PC (GMKADIRAKBABA) is enrolled WITHOUT the Digital Operator on purpose (ADR-0203),
so it advertises no ``file.fetch`` - the documents family rides the Operator flag - and the
only thing that could open a report there is the browser worker, which drives the owner's
own Chrome through the enrolment they made (ADR-0113, ADR-0183).

Everything here goes through the REAL application object, the REAL relay and tools, and the
REAL ``BrokerDeviceAction`` selecting among REAL enrolled device rows (ADR-0208 session
affinity included). Only the last hop, the command client that would talk to a socket, is a
recording fake - a fake DEVICE PORT would prove nothing about which machine a command goes to.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.routines.dispatch as dispatch_mod
from app.artifacts import render_store
from app.artifacts import service as artifact_service
from app.artifacts.models import Artifact, ArtifactVersion
from app.artifacts.render_view_store import (
    RenderViewStore,
    get_render_view_store,
    set_render_view_store,
)
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices.commands import CommandFailed, CommandSucceeded
from app.devices.status import get_status_registry
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.object_store import InMemoryObjectStore
from app.operator.models import ObjectFocusRow
from app.operator.service import OperatorService
from app.routines.dispatch import BrokerDeviceAction
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES
from tests.unit.test_voice_research_followup import (
    RESEARCH_TABLES,
    _complete_a_research,
)

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"
#: What the operator sets (PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN): the origin the office PC's
#: browser can reach the edge at.
EDGE = "http://100.90.158.26:8001"

#: The home PC (MAIL) is installed with -Operator, so it advertises the documents family; the
#: office PC (GMKADIRAKBABA) is not, and advertises the browser family (ADR-0203, ADR-0205).
HOME_CAPABILITIES = [
    "app.launch",
    "file.fetch",
    "file.open",
    "desktop.open_application",
    "desktop.open_artifact",
    "browser.chrome",
]
OFFICE_CAPABILITIES = [
    "desktop.open_application",
    "desktop.open_artifact",
    "desktop.display_wake",
    "desktop.notify",
    "browser.chrome",
]
NO_BROWSER_CAPABILITIES = ["desktop.open_application", "desktop.open_artifact"]

REPORT_BODY = "# Rapor\n\nBirinci bulgu: bağlam penceresi büyüdü.\n"
TOPIC = "Ofis konusu"
BROWSER_STEPS = [
    "browser.session_open",
    "browser.tab_new",
    "browser.inspect",
    "browser.session_close",
]


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


class _RecordingCommands:
    """The last hop only: what the broker would put on a socket, recorded and answered the
    way each device answers it (``file.fetch`` the way the companion's fetcher does, the
    browser family the way the worker's contract - BROWSER_CAPABILITIES.md - says)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        #: capability -> a failure to answer instead of the happy result.
        self.fail: dict[str, CommandFailed] = {}
        #: capability -> something that happens right after that command was answered.
        self.after: dict[str, Any] = {}
        self.page_kind = "ok"
        self.inspect_title = "Araştırma raporu"

    def run(
        self,
        *,
        device_id: uuid.UUID,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
        trace_id: str,
        heartbeat: Any = None,
    ) -> CommandSucceeded | CommandFailed:
        self.calls.append(
            {
                "device_id": device_id,
                "capability": capability,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
            }
        )
        result = self._answer(capability, payload)
        hook = self.after.get(capability)
        if hook is not None:
            hook()
        return result

    def _answer(self, capability: str, payload: dict[str, Any]) -> CommandSucceeded | CommandFailed:
        if capability in self.fail:
            return self.fail[capability]
        if capability == "file.fetch":
            name = str(payload.get("name") or "rapor.html")
            return CommandSucceeded(
                {
                    "path": f"C:/Users/owner/Downloads/{name}",
                    "verified": True,
                    "opened": {
                        "opened": True,
                        "observed": {"window": {"title": name}, "window_appeared": True},
                    },
                }
            )
        if capability == "browser.session_open":
            return CommandSucceeded(
                {"session_id": payload["session_id"], "created": True, "profile": "owner"}
            )
        if capability == "browser.tab_new":
            self._last_url = payload.get("url")
            return CommandSucceeded({"index": 1})
        if capability == "browser.inspect":
            return CommandSucceeded(
                {
                    "url": self._last_url,
                    "title": self.inspect_title,
                    "tab_index": 1,
                    "tab_count": 2,
                    "page_kind": self.page_kind,
                }
            )
        return CommandSucceeded({})

    _last_url: str | None = None

    def capabilities(self) -> list[str]:
        return [c["capability"] for c in self.calls]

    def devices(self) -> set[uuid.UUID]:
        return {c["device_id"] for c in self.calls}

    def payload_for(self, capability: str) -> dict[str, Any]:
        for call in self.calls:
            if call["capability"] == capability:
                return call["payload"]
        raise AssertionError(f"{capability} was never sent: {self.capabilities()}")


class _World:
    def __init__(self, client, app, factory, runtime, artifacts, commands, ids, broker) -> None:
        self.client = client
        self.app = app
        self.broker = broker
        self.factory = factory
        self.runtime = runtime
        self.artifacts = artifacts
        self.commands: _RecordingCommands = commands
        self.ids: dict[str, uuid.UUID] = ids


class _Clock:
    """A settable instant, so a token's lifetime is asserted without waiting."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture()
def clock() -> Iterator[_Clock]:
    """A view store on a controllable clock for the length of one test, restored after."""
    original = get_render_view_store()
    fake = _Clock()
    set_render_view_store(RenderViewStore(clock=fake))
    try:
        yield fake
    finally:
        set_render_view_store(original)


def _wired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    devices: dict[str, dict[str, Any]],
    *,
    download_origin: str = EDGE,
) -> _World:
    """``devices``: name -> {"capabilities": [...], "aliases": [...], "online": bool}.

    A FILE database with a real pool: the real ``BrokerDeviceAction`` opens and closes its own
    sessions per command, which on one shared connection would roll back the relay's
    uncommitted tool-call row (see ``test_operator_open_application_fallback``)."""
    settings = Settings(
        _env_file=None,
        voice_openai_api_key=VENDOR_KEY,
        artifact_download_origin=download_origin,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'owner-chrome.db'}", connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(engine)
    tables = {
        t.name: t
        for t in (
            RealtimeSessionRow.__table__,
            RealtimeToolCall.__table__,
            AuditEvent.__table__,
            VoiceProfile.__table__,
            NarrationSession.__table__,
            PronunciationEntry.__table__,
            Artifact.__table__,
            ArtifactVersion.__table__,
            ActivityEventRow.__table__,
            PendingBriefingRow.__table__,
            EnrollmentToken.__table__,
            ObjectFocusRow.__table__,
            Device.__table__,
            DeviceSession.__table__,
            DeviceCommand.__table__,
            *RESEARCH_TABLES,
        )
    }
    for table in tables.values():
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = factory
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    artifacts._store = InMemoryObjectStore()
    app.state.artifacts = artifacts
    sim = SimulatedRealtimeProvider()
    runtime = RealtimeVoiceRuntime(
        settings,
        engine=engine,
        providers={sim.name: sim},
        sideband=RecordingSideband(deliver=True),
        broker=broker,
        artifacts=artifacts,
    )
    app.state.voice_realtime = runtime

    commands = _RecordingCommands()
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: broker)
    action = BrokerDeviceAction(session_factory=factory, command_client=commands)
    runtime.register_live(operator=OperatorService(), device_action=action)

    ids: dict[str, uuid.UUID] = {}
    for name, spec in devices.items():
        with broker.session() as db:
            enrolled = broker_service.enroll_device(
                db,
                name=name,
                platform="windows",
                public_key_spki_b64=_spki(),
                capabilities=list(spec["capabilities"]),
                trace_id=None,
            )
            if spec.get("aliases"):
                broker_service.update_device_metadata(
                    db, enrolled.id, aliases=list(spec["aliases"]), trace_id=None
                )
        ids[name] = enrolled.id
        if spec.get("online", True):
            broker.connections[enrolled.id] = DeviceConnection(
                device_id=enrolled.id, session_id=uuid.uuid4(), websocket=object()
            )
    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    return _World(client, app, factory, runtime, artifacts, commands, ids, broker)


def _both(monkeypatch, tmp_path, **kwargs) -> _World:
    return _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]},
        },
        **kwargs,
    )


def _office_only(monkeypatch, tmp_path, **kwargs) -> _World:
    return _wired(
        monkeypatch,
        tmp_path,
        {"GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]}},
        **kwargs,
    )


def _create(client, device_id: uuid.UUID | None = None) -> str:
    body = {"device_id": str(device_id)} if device_id is not None else {}
    response = client.post("/v1/voice/realtime/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _say(client, sid: str, text: str) -> None:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}]},
    )
    assert response.status_code == 200, response.text


def _tool(client, sid: str, name: str, arguments: dict, call_id: str | None = None) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id or f"c-{uuid.uuid4()}", "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _give_the_report_renders(
    world: _World, artifact_id: str, formats: tuple[str, ...] = ("html",), body: str = REPORT_BODY
) -> None:
    """What ``research_compose`` + ``research_render`` leave behind for a real run."""
    with world.runtime.session() as db:
        aid = uuid.UUID(artifact_id)
        version = artifact_service.add_artifact_version(
            db,
            artifact_id=aid,
            canonical_body=body,
            content_hash=uuid.uuid4().hex * 2,
            source_manifest=[],
        )
        artifact = artifact_service.get_artifact(db, aid)
        render_store.ensure_renders(
            db, world.artifacts.store, version=version, title=artifact.title, formats=formats
        )
        db.commit()


def _a_research(world: _World, formats: tuple[str, ...] = ("html",)) -> str:
    """One completed research with a rendered report; returns the report's artifact id.

    Starting a research needs an online browser device (``research.start`` selects on
    ``browser.chrome``). A world that has none - the point of some tests - gets a scratch one
    for the length of the start, taken away again before anything is opened."""
    scratch: uuid.UUID | None = None
    if not any(
        world.broker.connections.get(device_id) is not None
        and "browser.chrome" in _capabilities_of(world, device_id)
        for device_id in world.ids.values()
    ):
        with world.broker.session() as db:
            enrolled = broker_service.enroll_device(
                db,
                name="SCRATCH",
                platform="windows",
                public_key_spki_b64=_spki(),
                capabilities=["browser.chrome"],
                trace_id=None,
            )
        scratch = enrolled.id
        world.broker.connections[scratch] = DeviceConnection(
            device_id=scratch, session_id=uuid.uuid4(), websocket=object()
        )
    try:
        _task, artifact_id = _complete_a_research(
            world.client, world.runtime, world.artifacts, topic=TOPIC
        )
    finally:
        if scratch is not None:
            world.broker.connections.pop(scratch, None)
    _give_the_report_renders(world, artifact_id, formats)
    return artifact_id


def _capabilities_of(world: _World, device_id: uuid.UUID) -> list[str]:
    with world.broker.session() as db:
        return list(db.get(Device, device_id).capabilities_json or [])


def _open_research(world: _World, session_id: str) -> dict:
    _say(world.client, session_id, "Son araştırmayı aç.")
    call = _tool(world.client, session_id, "research.open", {})
    assert call["status"] == "succeeded", call
    return call["result"]


def _view_url(world: _World) -> str:
    url = world.commands.payload_for("browser.tab_new").get("url")
    assert isinstance(url, str), url
    return url


def _redeem(world: _World, url: str, **kwargs):
    """The browser's GET: no owner session, no header - only what is in the URL."""
    parts = urlsplit(url)
    path = f"{parts.path}?{parts.query}" if parts.query else parts.path
    with TestClient(world.app) as anonymous:
        return anonymous.get(path, **kwargs)


# ------------------------------------------------- the office event, end to end


def test_an_office_session_opens_the_report_in_the_owners_chrome_with_the_home_pc_online(
    monkeypatch, tmp_path, clock
) -> None:
    """2026-09-29, verbatim: the research finished on the office PC, the home PC is also
    online (and CAN fetch files), the owner says "Son araştırmayı aç"."""
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    sid = _create(world.client, world.ids["GMKADIRAKBABA"])

    body = _open_research(world, sid)

    assert world.commands.capabilities() == BROWSER_STEPS, body
    assert world.commands.devices() == {world.ids["GMKADIRAKBABA"]}
    assert body["opened"] is True
    assert body["open_receipt"]["artifact_id"] == artifact_id
    assert "tarayıcıda açtım" in body["speech"]
    assert "ofis" in body["speech"].lower()
    assert "açamadım" not in body["speech"]


def test_the_session_is_the_owners_own_chrome_and_only_reads_and_navigates(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    opened = world.commands.payload_for("browser.session_open")
    # The owner's attached browser, never a fallback profile; the DEFAULT session kind, which
    # is the one the worker gates on the enrolment's owner_authorized_for_research.
    assert opened["profile"] == "owner"
    assert "session_kind" not in opened
    assert opened["policy"] == {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True}
    assert opened["channel"] == "chrome"
    # A NEW tab, before anything else touches the browser: the owner's own tab is not ours.
    order = world.commands.capabilities()
    assert order.index("browser.tab_new") == order.index("browser.session_open") + 1
    # One session id for the whole open, and it is closed again (the tab stays).
    session_ids = {
        c["payload"].get("session_id")
        for c in world.commands.calls
        if c["capability"] != "browser.session_open" or c["payload"].get("session_id")
    }
    assert len(session_ids) == 1
    assert world.commands.calls[-1]["capability"] == "browser.session_close"


def test_the_tab_is_sent_only_to_a_url_this_open_minted(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    parts = urlsplit(_view_url(world))
    assert f"{parts.scheme}://{parts.netloc}" == EDGE
    assert parts.path == "/v1/artifacts/renders/view"
    token = parse_qs(parts.query)["t"]
    assert len(token) == 1 and len(token[0]) >= 43
    assert parts.fragment == ""
    # No other URL is ever sent anywhere in the family.
    for call in world.commands.calls:
        for key in ("url", "target", "href"):
            if call["capability"] != "browser.tab_new":
                assert key not in call["payload"], call


def test_the_page_the_browser_loads_is_the_report_and_only_the_report(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    page = _redeem(world, _view_url(world))
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "bağlam penceresi büyüdü" in page.text
    # A document the owner reads, never one that can run, phone home or be framed.
    policy = page.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src" not in policy or "'unsafe" not in policy.split("script-src", 1)[1]
    assert "sandbox" in policy and "frame-ancestors 'none'" in policy
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"


def test_the_receipt_and_the_ledger_say_it_was_the_browser_and_on_which_device(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    receipt = body["open_receipt"]
    # The registered tool's name (ADR-0114/0116: only a registered name resolves in the
    # self-model); what actually ran is in the server observation.
    assert receipt["capability"] == "artifact.open"
    assert receipt["execution_status"] == "executed"
    assert receipt["terminal_status"] == "verified"
    server = receipt["observed_after"]["server"]
    assert server["path"] == "browser.tab_new"
    assert server["via"] == "browser"
    assert server["device_id"] == str(world.ids["GMKADIRAKBABA"])
    assert server["device"] == "GMKADIRAKBABA"
    assert server["format"] == "html"
    with world.factory() as db:
        rows = list(
            db.execute(select(ActivityEventRow).where(ActivityEventRow.action == "artifact.open"))
            .scalars()
            .all()
        )
    # Two rows are written, as on every open: the receipt (what the tool observed) and the
    # ledger event (what happened, for the owner's timeline). Both say it.
    receipts = [r for r in rows if "observed_after" in (r.detail_json or {})]
    events = [r for r in rows if "observed_after" not in (r.detail_json or {})]
    assert len(receipts) == 1 and len(events) == 1
    assert receipts[0].detail_json["observed_after"]["server"]["via"] == "browser"
    assert receipts[0].detail_json["observed_after"]["server"]["device"] == "GMKADIRAKBABA"
    assert "browser" in events[0].factual_summary and "GMKADIRAKBABA" in events[0].factual_summary
    assert events[0].detail_json["via"] == "browser"
    assert events[0].detail_json["device_id"] == str(world.ids["GMKADIRAKBABA"])


def test_the_bearer_url_is_in_no_receipt_no_ledger_row_and_no_log(
    monkeypatch, tmp_path, clock, capsys
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    token = parse_qs(urlsplit(_view_url(world)).query)["t"][0]

    assert token not in repr(body)
    with world.factory() as db:
        for row in db.execute(select(ActivityEventRow)).scalars().all():
            assert token not in repr(row.detail_json) + str(row.factual_summary)
    logged = capsys.readouterr()
    assert token not in logged.out + logged.err


def test_a_page_the_browser_could_not_confirm_is_not_reported_as_opened(
    monkeypatch, tmp_path, clock
) -> None:
    """``browser.tab_new`` succeeding means a tab exists. ``inspect`` saying the page is not
    ``ok`` means the owner is looking at an error page: not "açtım"."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.page_kind = "error_page"
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert body["opened"] is False
    assert body["open_receipt"]["terminal_status"] != "verified"
    assert "doğrulayamadım" in body["speech"]
    assert "tarayıcıda açtım" not in body["speech"]


# --------------------------------------------------------- the home PC is untouched


def test_a_home_session_still_fetches_the_file_on_the_home_pc(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["MAIL"]))

    assert world.commands.capabilities() == ["file.fetch"], body
    assert world.commands.devices() == {world.ids["MAIL"]}
    fetch = world.commands.payload_for("file.fetch")
    assert fetch["url"].startswith(f"{EDGE}/v1/artifacts/renders/fetch/")
    assert fetch["open"] is True
    assert body["open_receipt"]["artifact_id"] == artifact_id
    assert body["open_receipt"]["observed_after"]["server"].get("via") is None
    assert "tarayıcıda" not in body["speech"]
    assert get_render_view_store().size() == 0, "no browser token for a file.fetch open"


def test_an_unbound_session_with_both_machines_online_keeps_the_file_fetch_path(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    _open_research(world, _create(world.client))

    assert world.commands.capabilities() == ["file.fetch"]
    assert world.commands.devices() == {world.ids["MAIL"]}


def test_the_fetch_path_is_unchanged_when_the_only_device_has_both(
    monkeypatch, tmp_path, clock
) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {"MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]}},
    )
    _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["MAIL"]))
    assert world.commands.capabilities() == ["file.fetch"]
    assert body["opened"] is True


def test_with_nothing_that_can_fetch_a_file_an_unbound_session_uses_the_browser_device(
    monkeypatch, tmp_path, clock
) -> None:
    world = _office_only(monkeypatch, tmp_path)
    _a_research(world)
    body = _open_research(world, _create(world.client))

    assert world.commands.capabilities() == BROWSER_STEPS
    assert world.commands.devices() == {world.ids["GMKADIRAKBABA"]}
    assert body["opened"] is True


def test_artifact_open_by_voice_takes_the_same_route(monkeypatch, tmp_path, clock) -> None:
    """The route lives in the one place both ``research.open`` and ``artifact.open`` open
    an artifact by id, so "Bunu aç" cannot disagree with "Son araştırmayı aç"."""
    from app.operator import focus as operator_focus
    from app.operator.models import FOCUS_KIND_ARTIFACT

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    with world.runtime.session() as db:
        operator_focus.set_focus(db, FOCUS_KIND_ARTIFACT, artifact_id, label="Rapor", source="test")
        db.commit()
    sid = _create(world.client, world.ids["GMKADIRAKBABA"])
    _say(world.client, sid, "Bunu aç.")
    call = _tool(world.client, sid, "artifact.open", {})

    assert call["status"] == "succeeded", call
    assert world.commands.capabilities() == BROWSER_STEPS
    assert world.commands.devices() == {world.ids["GMKADIRAKBABA"]}
    assert "tarayıcıda açtım" in call["result"]["speech"]


# ------------------------------------------------------------------------ refusals


def test_a_browser_that_is_not_authorised_for_research_is_refused_and_no_other_is_used(
    monkeypatch, tmp_path, clock
) -> None:
    """The enrolment record has owner_authorized_for_research=false: the worker answers
    ``security_scope_error``. The owner is told, in Turkish, and NO other profile is tried."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.session_open"] = CommandFailed(
        "security_scope_error",
        "enrollment 'x' is not authorized for autonomous research use",
        False,
    )
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert world.commands.capabilities() == ["browser.session_open"], "no second profile"
    assert body["opened"] is False
    assert body["open_error_class"] == "owner_browser_not_authorized"
    assert "yetkilendirilmemiş" in body["speech"]
    assert "cihazda açamadım" in body["speech"]
    assert world.commands.payload_for("browser.session_open")["profile"] == "owner"


def test_no_enrolled_browser_is_refused_and_never_replaced_by_an_isolated_one(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.session_open"] = CommandFailed(
        "capability_missing", "no cdp_loopback enrollment", False
    )
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert world.commands.capabilities() == ["browser.session_open"]
    assert body["open_error_class"] == "owner_browser_missing"
    assert "bağlı değilim" in body["speech"]
    assert all(c["payload"].get("profile") != "isolated" for c in world.commands.calls)


def test_a_chrome_that_is_gone_says_so(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.session_open"] = CommandFailed(
        "dependency_unavailable", "cdp endpoint refused the connection", True
    )
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert world.commands.capabilities() == ["browser.session_open"]
    assert body["open_error_class"] == "owner_browser_unreachable"
    assert "ulaşamadım" in body["speech"]


def test_a_pdf_only_report_is_refused_and_nothing_is_opened_or_minted(
    monkeypatch, tmp_path, clock
) -> None:
    """A browser would DOWNLOAD a PDF, and this route does not download files through the
    browser: nothing is opened and no token exists."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world, formats=("pdf", "docx"))
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert world.commands.calls == []
    assert body["opened"] is False
    assert body["open_error_class"] == "no_html_render"
    assert "HTML" in body["speech"]
    assert get_render_view_store().size() == 0


def test_a_named_non_html_format_is_refused_on_the_browser_route(
    monkeypatch, tmp_path, clock
) -> None:
    from app.operator import focus as operator_focus
    from app.operator.models import FOCUS_KIND_ARTIFACT

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world, formats=("html", "pdf"))
    with world.runtime.session() as db:
        operator_focus.set_focus(db, FOCUS_KIND_ARTIFACT, artifact_id, label="Rapor", source="test")
        db.commit()
    sid = _create(world.client, world.ids["GMKADIRAKBABA"])
    _say(world.client, sid, "Bunu PDF olarak aç.")
    call = _tool(world.client, sid, "artifact.open", {"format": "pdf"})

    assert world.commands.calls == []
    assert call["result"]["error_class"] == "no_html_render"


def test_a_destination_the_worker_refuses_is_said_and_the_session_is_closed(
    monkeypatch, tmp_path, clock, capsys
) -> None:
    """The worker's own URL policy (BROWSER_CAPABILITIES.md section 5a) refuses a private or
    tailnet destination. That refusal is honoured, never worked around, and named: it is a
    fact about how the device is set up, not a failure of the report."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.tab_new"] = CommandFailed(
        "security_scope_error", "tab_new: destination refused (non-public IP address)", False
    )
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert world.commands.capabilities() == [
        "browser.session_open",
        "browser.tab_new",
        "browser.session_close",
    ]
    assert body["opened"] is False
    assert body["open_error_class"] == "destination_refused"
    assert "reddetti" in body["speech"]
    logged = capsys.readouterr()
    assert "research_open_device_open_failed" in logged.out + logged.err


def test_a_failed_tab_leaves_no_session_behind(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.tab_new"] = CommandFailed("timeout", "navigation timeout", True)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert world.commands.capabilities()[-1] == "browser.session_close"


def test_a_device_message_never_carries_the_token_into_the_record(
    monkeypatch, tmp_path, clock, capsys
) -> None:
    """Whatever a device echoes back (a worker's message may repeat the URL's path) is
    scrubbed of the token before it reaches a receipt or a log."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)

    real_run = world.commands.run

    def echo(**kwargs):
        if kwargs["capability"] == "browser.tab_new":
            url = kwargs["payload"]["url"]
            world.commands.calls.append({**kwargs, "payload": dict(kwargs["payload"])})
            return CommandFailed("timeout", f"navigation to {url} timed out", True)
        return real_run(**kwargs)

    world.commands.run = echo  # type: ignore[method-assign]
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    token = parse_qs(urlsplit(_view_url(world)).query)["t"][0]

    assert token not in repr(body)
    logged = capsys.readouterr()
    assert token not in logged.out + logged.err


def test_no_origin_for_the_browser_is_said_and_nothing_is_minted_or_sent(
    monkeypatch, tmp_path, clock
) -> None:
    """No configured origin and no recorded dial origin: there is no address to send a
    browser to. Said, not guessed, and not a relative URL."""
    world = _both(monkeypatch, tmp_path, download_origin="")
    _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert world.commands.calls == []
    assert body["open_error_class"] == "no_origin"
    assert get_render_view_store().size() == 0


def test_without_a_configured_origin_the_origin_the_device_dialled_is_used(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path, download_origin="")
    _a_research(world)
    get_status_registry().record_dial_origin(
        world.ids["GMKADIRAKBABA"], "http://100.90.158.26:8001"
    )
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))

    assert body["opened"] is True
    assert _view_url(world).startswith("http://100.90.158.26:8001/v1/artifacts/renders/view?t=")


def test_the_configured_origin_wins_over_a_recorded_dial_origin(
    monkeypatch, tmp_path, clock
) -> None:
    """A dial origin is the Host header of the WebSocket handshake, and the edge's ``$host``
    drops the port. The operator's explicit setting is the one to believe."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    get_status_registry().record_dial_origin(world.ids["GMKADIRAKBABA"], "http://100.90.158.26")
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert _view_url(world).startswith(f"{EDGE}/v1/artifacts/renders/view?t=")


# ------------------------------------------- nothing opens on the other machine


def test_nothing_is_sent_to_the_home_pc_when_the_office_session_opens_a_report(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert world.ids["MAIL"] not in world.commands.devices()


def test_a_refusal_on_the_office_pc_is_not_retried_on_the_home_pc(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    world.commands.fail["browser.session_open"] = CommandFailed(
        "security_scope_error", "not authorized", False
    )
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert world.commands.devices() == {world.ids["GMKADIRAKBABA"]}
    assert "file.fetch" not in world.commands.capabilities()


def test_a_device_that_drops_out_mid_open_is_not_replaced_by_the_other_machine(
    monkeypatch, tmp_path, clock
) -> None:
    """The office PC goes offline right after the session opened. The ordinary rule would now
    pick the home PC - which also carries a browser worker - for the next command, and the tab
    would open THERE while the owner sits at the office. Every step re-checks its device."""
    world = _both(monkeypatch, tmp_path)
    _a_research(world)
    office = world.ids["GMKADIRAKBABA"]
    world.commands.after["browser.session_open"] = lambda: world.broker.connections.pop(
        office, None
    )
    body = _open_research(world, _create(world.client, office))

    assert world.commands.capabilities() == ["browser.session_open"]
    assert world.commands.devices() == {office}
    assert body["opened"] is False
    assert body["open_error_class"] == "device_changed"
    assert "başka bir cihaza" in body["speech"]


def test_the_office_pc_without_a_browser_worker_falls_to_the_ordinary_rule(
    monkeypatch, tmp_path, clock
) -> None:
    """An office device that advertises no browser family cannot do this, so the open is what
    it always was (ADR-0208: a session's device that cannot act changes nothing)."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            "GMKADIRAKBABA": {"capabilities": NO_BROWSER_CAPABILITIES, "aliases": ["ofis"]},
        },
    )
    _a_research(world)
    _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert world.commands.capabilities() == ["file.fetch"]


def test_neither_capability_anywhere_is_todays_refusal(monkeypatch, tmp_path, clock) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {"KIOSK": {"capabilities": NO_BROWSER_CAPABILITIES, "aliases": []}},
    )
    _a_research(world)
    body = _open_research(world, _create(world.client))

    assert body["opened"] is False
    assert body["open_error_class"] == "capability_missing"
    assert "dosya getiremiyor" in body["speech"]
    # The old path ran and was refused at selection: nothing was sent to any device.
    assert world.commands.calls == []
    assert get_render_view_store().size() == 0


def test_an_offline_office_pc_is_not_a_candidate(monkeypatch, tmp_path, clock) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {"GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "online": False}},
    )
    _a_research(world)
    body = _open_research(world, _create(world.client, world.ids["GMKADIRAKBABA"]))
    assert body["opened"] is False
    assert all(c["capability"] == "file.fetch" for c in world.commands.calls)


# ------------------------------------------------------------------------ the gate


def test_the_step_up_tier_is_still_decided_by_the_tool_name() -> None:
    from app.security.step_up import TIER_OPEN, TIER_SENSITIVE, tier_of

    # The browser route lives INSIDE these two tools, so it is reached at exactly the tier the
    # file path was: neither lowered nor raised by this change.
    assert tier_of("artifact.open") == TIER_SENSITIVE
    assert tier_of("research.open") == TIER_OPEN


# ------------------------------------------------- the route the browser redeems


def _mint(world: _World, *, fmt: str = "html", artifact_id: str | None = None):
    """Mint a view token directly, for the route's own tests."""
    aid = uuid.UUID(artifact_id) if artifact_id else uuid.uuid4()
    with world.runtime.session() as db:
        version = artifact_service.get_current_version(db, aid)
        row = artifact_service.get_render(db, version.id, fmt) if version else None
    content_hash = row.content_hash if row is not None else "0" * 64
    return get_render_view_store().put(artifact_id=aid, fmt=fmt, content_hash=content_hash)


def test_the_view_route_serves_the_report_and_needs_no_owner_session(
    monkeypatch, tmp_path, clock
) -> None:
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    handle = _mint(world, artifact_id=artifact_id)
    with TestClient(world.app) as anonymous:
        response = anonymous.get(handle.path())
    assert response.status_code == 200
    assert "bağlam penceresi büyüdü" in response.text


def test_a_view_token_can_be_read_more_than_once_within_its_lifetime(
    monkeypatch, tmp_path, clock
) -> None:
    """A reload, a session restore and a back/forward are all more GETs of the same URL."""
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    handle = _mint(world, artifact_id=artifact_id)
    for _ in range(3):
        assert _redeem(world, handle.path()).status_code == 200


def test_a_view_token_stops_working_after_its_reads_are_spent(monkeypatch, tmp_path, clock) -> None:
    from app.artifacts.render_view_store import MAX_READS

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    handle = _mint(world, artifact_id=artifact_id)
    statuses = [_redeem(world, handle.path()).status_code for _ in range(MAX_READS + 1)]
    assert statuses == [200] * MAX_READS + [404]


def test_a_view_token_is_dead_after_its_lifetime_even_with_reads_left(
    monkeypatch, tmp_path, clock
) -> None:
    from app.artifacts.render_view_store import DEFAULT_TTL_S

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    handle = _mint(world, artifact_id=artifact_id)
    assert _redeem(world, handle.path()).status_code == 200
    clock.advance(seconds=DEFAULT_TTL_S + 1)
    replay = _redeem(world, handle.path())
    assert replay.status_code == 404
    assert replay.content == _redeem(world, "/v1/artifacts/renders/view?t=" + "A" * 43).content


def test_every_refusal_of_the_view_route_is_the_same_bare_404(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    good = _mint(world, artifact_id=artifact_id)
    expired = _mint(world, artifact_id=artifact_id)
    # Bad tokens of every shape: unknown, empty, absent, over-long, wrong parameter.
    paths = [
        "/v1/artifacts/renders/view?t=" + "A" * 43,
        "/v1/artifacts/renders/view?t=",
        "/v1/artifacts/renders/view",
        "/v1/artifacts/renders/view?t=" + "A" * 4000,
        "/v1/artifacts/renders/view?token=" + good.token,
        "/v1/artifacts/renders/view/" + good.token,
    ]
    clock.advance(seconds=10_000)
    paths.append(expired.path())
    bodies = set()
    for path in paths:
        response = _redeem(world, path)
        assert response.status_code == 404, path
        bodies.add(response.content)
    # One shape for every reason, and none of them names an artifact.
    assert len(bodies) == 1
    assert artifact_id.encode() not in next(iter(bodies))


def test_a_token_for_a_render_that_changed_is_refused(monkeypatch, tmp_path, clock) -> None:
    """The hash is pinned at mint time: a report re-rendered underneath the token is not
    served under it."""
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    handle = _mint(world, artifact_id=artifact_id)
    _give_the_report_renders(world, artifact_id, body="# Başka\n\nDeğişen içerik.\n")
    assert _redeem(world, handle.path()).status_code == 404


def test_a_token_serves_its_own_artifact_and_never_another(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    first = _a_research(world)
    with world.runtime.session() as db:
        other = artifact_service.get_or_create_artifact_for_task(
            db, task_id=None, title="Gizli başka rapor", kind="research_report"
        )
        db.commit()
        other_id = str(other.id)
    _give_the_report_renders(world, other_id, body="# Gizli\n\nBAŞKA-RAPORUN-METNİ\n")
    handle = _mint(world, artifact_id=first)
    served = _redeem(world, handle.path())
    assert served.status_code == 200
    assert "BAŞKA-RAPORUN-METNİ" not in served.text
    assert "Gizli başka rapor" not in served.text


def test_the_view_route_lists_nothing(monkeypatch, tmp_path, clock) -> None:
    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    _mint(world, artifact_id=artifact_id)
    for path in (
        "/v1/artifacts/renders",
        "/v1/artifacts/renders/",
        "/v1/artifacts/renders/view/",
        "/v1/artifacts/renders/view?list=1",
        "/v1/artifacts/renders/view?t=*",
        "/v1/artifacts/renders/view?t=../",
    ):
        response = _redeem(world, path)
        # Never a 200 and never a body that names anything: the routes at these paths are
        # either absent (404), method-bound (405) or the owner-gated artifact routes (401).
        assert response.status_code in (401, 404, 405), path
        assert artifact_id not in response.text


def test_a_fetch_token_is_not_a_view_token_and_the_reverse(monkeypatch, tmp_path, clock) -> None:
    """The two stores are separate: neither route honours the other's tokens, and a wrong
    guess at one does not spend the other."""
    from app.artifacts.render_fetch_store import get_render_fetch_store

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world)
    view = _mint(world, artifact_id=artifact_id)
    with world.runtime.session() as db:
        version = artifact_service.get_current_version(db, uuid.UUID(artifact_id))
        row = artifact_service.get_render(db, version.id, "html")
    fetch = get_render_fetch_store().put(
        artifact_id=uuid.UUID(artifact_id), fmt="html", content_hash=row.content_hash
    )
    assert _redeem(world, f"/v1/artifacts/renders/view?t={fetch.token}").status_code == 404
    assert _redeem(world, f"/v1/artifacts/renders/fetch/{view.token}").status_code == 404
    # Neither was spent by the wrong-route attempt.
    assert _redeem(world, view.path()).status_code == 200
    assert _redeem(world, f"/v1/artifacts/renders/fetch/{fetch.token}").status_code == 200


def test_a_view_token_for_a_non_html_render_cannot_be_minted(monkeypatch, tmp_path, clock) -> None:
    from app.artifacts.render_view_store import FormatNotViewableError

    world = _both(monkeypatch, tmp_path)
    artifact_id = _a_research(world, formats=("html", "pdf"))
    with pytest.raises(FormatNotViewableError):
        _mint(world, fmt="pdf", artifact_id=artifact_id)
    assert get_render_view_store().size() == 0

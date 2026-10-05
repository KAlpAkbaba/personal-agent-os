"""jarvis-calls-owner: JARVIS phones the owner and tells him, in Turkish, what happened.

Pinned here, each one RED under its own mutation:

* a call to any number but the configured owner number is refused before Twilio is asked;
* the TwiML Twilio receives plays OUR Turkish audio from a one-time url, and that url stops
  working after one fetch or after ten minutes; with our TTS down it is Twilio's own
  ``<Say language="tr-TR">``;
* not answered -> one retry after five minutes -> then a notification, never a third call;
* every call is a ledger row with its reason, Twilio's call SID and the status;
* the important notifications ring once each, routine ones never;
* the Account SID and the auth token never appear in a route's answer or a log line;
* the Twilio REST request itself (form fields, basic auth, the error that carries no secret).

No real network, no real Twilio, no wall clock.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import re
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms.audio_store import AudioStore
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.notifications import events as notification_events
from app.notifications import service as notifications
from app.notifications.models import NotificationRow
from app.telephony import policy
from app.telephony import routes as telephony_routes
from app.telephony.provider import PlacedCall, TelephonyError
from app.telephony.service import AUDIO_TTL, CallRefused, OwnerCaller
from app.telephony.twilio import TwilioTelephony
from tests.identity_support import authenticate

OWNER = "+905551112233"
TWILIO_NUMBER = "+15005550006"
BASE = "https://core.test"
ACCOUNT_SID = "AC" + "0123456789abcdef" * 2
AUTH_TOKEN = "tok" + "9f8e7d6c5b4a" * 2 + "zz"
#: 14:00 in Istanbul - outside quiet hours.
T0 = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt fake-turkish-speech"


class FakeTelephony:
    """Twilio as the caller sees it: it accepts a call and later answers a status."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.status: dict[str, str] = {}

    def configured(self) -> bool:
        return True

    def place_call(self, *, to: str, from_: str, twiml: str) -> PlacedCall:
        sid = f"CA{len(self.calls) + 1:032d}"
        self.calls.append({"to": to, "from": from_, "twiml": twiml, "sid": sid})
        self.status[sid] = "queued"
        return PlacedCall(sid=sid, status="queued")

    def call_status(self, sid: str) -> str:
        return self.status[sid]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    NotificationRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_scope(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        with factory() as db:
            yield db

    return scope


def _caller(
    session_scope: Any,
    *,
    provider: Any = None,
    speak: Any = None,
    clock: Clock | None = None,
    store: AudioStore | None = None,
) -> OwnerCaller:
    return OwnerCaller(
        provider=provider or FakeTelephony(),
        owner_number=OWNER,
        from_number=TWILIO_NUMBER,
        public_base_url=BASE,
        audio_store=store or AudioStore(ttl_s=int(AUDIO_TTL.total_seconds())),
        speak=speak if speak is not None else (lambda text: (WAV, "audio/wav")),
        session_scope=session_scope,
        clock=clock or Clock(T0),
    )


def _ledger(session_scope: Any) -> list[ActivityEventRow]:
    with session_scope() as db:
        return list(
            db.execute(select(ActivityEventRow).order_by(ActivityEventRow.recorded_at))
            .scalars()
            .all()
        )


def _token_in(twiml: str) -> str:
    match = re.search(r"<Play>https://core\.test/v1/telephony/audio/([^<]+)</Play>", twiml)
    assert match, twiml
    return match.group(1)


# ------------------------------------------------------------------ the owner only


def test_a_call_to_any_number_but_the_owners_is_refused(session_scope: Any) -> None:
    provider = FakeTelephony()
    caller = _caller(session_scope, provider=provider)

    with pytest.raises(CallRefused):
        caller.call(policy.KIND_TEST_CALL, "Merhaba", to="+905550000000")

    assert provider.calls == []
    refused = [row for row in _ledger(session_scope) if row.event_type == "telephony.call_refused"]
    assert len(refused) == 1
    assert "+905550000000" not in str(refused[0].detail_json)


def test_the_owner_number_spelled_with_spaces_is_still_the_owner(session_scope: Any) -> None:
    provider = FakeTelephony()
    caller = _caller(session_scope, provider=provider)

    outcome = caller.call(policy.KIND_TEST_CALL, "Merhaba", to="+90 555 111 22 33")

    assert outcome.status == "placed"
    assert provider.calls[0]["to"] == OWNER
    assert provider.calls[0]["from"] == TWILIO_NUMBER


# ------------------------------------------------------------------ what Twilio receives


def test_the_twiml_plays_our_turkish_audio_from_a_one_time_url(
    session_scope: Any, engine: Any
) -> None:
    store = AudioStore(ttl_s=int(AUDIO_TTL.total_seconds()))
    provider = FakeTelephony()
    spoken: list[str] = []

    def speak(text: str) -> tuple[bytes, str]:
        spoken.append(text)
        return WAV, "audio/wav"

    # The route redeems on the wall clock, so the token is minted on it too: a critical
    # call, which no quiet hour stops whenever this test runs.
    caller = _caller(
        session_scope, provider=provider, speak=speak, store=store, clock=Clock(datetime.now(UTC))
    )
    outcome = caller.call(policy.KIND_SECURITY_CRITICAL, "Yeni sürüm açılamadı, eskisine döndüm.")

    assert outcome.spoken == "audio"
    assert len(spoken) == 1 and spoken[0].startswith("Merhaba, ben JARVIS.")
    assert spoken[0].endswith("Yeni sürüm açılamadı, eskisine döndüm.")
    twiml = provider.calls[0]["twiml"]
    assert twiml.startswith("<?xml") and "<Response>" in twiml and "<Say" not in twiml
    token = _token_in(twiml)
    assert len(token) >= 40  # 256 bits, url-safe

    app = FastAPI()
    app.include_router(telephony_routes.audio_router)
    app.state.telephony_audio_store = store
    client = TestClient(app)
    first = client.get(f"/v1/telephony/audio/{token}")
    second = client.get(f"/v1/telephony/audio/{token}")

    assert first.status_code == 200
    assert first.content == WAV
    assert first.headers["content-type"].startswith("audio/wav")
    assert second.status_code == 404


def test_the_audio_url_stops_working_after_ten_minutes() -> None:
    store = AudioStore(ttl_s=int(AUDIO_TTL.total_seconds()))
    early = store.put(WAV, now=T0)
    late = store.put(WAV, now=T0)

    assert AUDIO_TTL == timedelta(minutes=10)
    assert store.take(early.token, now=T0 + timedelta(minutes=9, seconds=59)) is not None
    assert store.take(late.token, now=T0 + timedelta(minutes=10, seconds=1)) is None


def test_with_our_tts_down_twilio_says_it_in_turkish(session_scope: Any) -> None:
    provider = FakeTelephony()

    def broken(text: str) -> tuple[bytes, str]:
        raise RuntimeError("tts down")

    caller = _caller(session_scope, provider=provider, speak=broken)
    outcome = caller.call(policy.KIND_RELEASE_FAILED, "Yedek & sürüm <bozuk>")

    assert outcome.spoken == "say"
    twiml = provider.calls[0]["twiml"]
    assert '<Say language="tr-TR"' in twiml
    assert "Yedek &amp; sürüm &lt;bozuk&gt;" in twiml
    assert "<Play>" not in twiml


@pytest.mark.parametrize(
    "base",
    [
        "https://pagentos-core.tail1234.ts.net",
        "https://100.101.102.103",
        "https://localhost:8000",
        "https://192.168.1.20",
    ],
)
def test_a_tailnet_or_private_origin_is_not_one_twilio_can_reach(
    session_scope: Any, base: str
) -> None:
    """Return 4: the Cloud Core is tailnet-only (`tailscale serve`, never Funnel), so Twilio
    on the internet cannot fetch from it. A base url that only the tailnet can reach would
    give the owner a call that says "an application error has occurred" - so it counts as
    no public origin, and Twilio's own Turkish voice speaks instead."""
    provider = FakeTelephony()
    caller = OwnerCaller(
        provider=provider,
        owner_number=OWNER,
        from_number=TWILIO_NUMBER,
        public_base_url=base,
        audio_store=AudioStore(ttl_s=int(AUDIO_TTL.total_seconds())),
        speak=lambda text: (WAV, "audio/wav"),
        session_scope=session_scope,
        clock=Clock(T0),
    )

    assert caller.speaks_with == "say"
    outcome = caller.call(policy.KIND_RELEASE_FAILED, "Yeni sürüm açılamadı.")
    assert outcome.spoken == "say"
    assert "<Play>" not in provider.calls[0]["twiml"]


# ------------------------------------------------------------------ the ledger and the retry


def test_every_call_is_a_ledger_row_with_reason_sid_and_status(session_scope: Any) -> None:
    provider = FakeTelephony()
    clock = Clock(T0)
    caller = _caller(session_scope, provider=provider, clock=clock)

    outcome = caller.call(policy.KIND_SECURITY_CRITICAL, "Bilinmeyen bir cihaz girmeye çalıştı.")
    provider.status[outcome.call_sid] = "completed"
    clock.now = T0 + timedelta(minutes=5)
    caller.sweep()

    rows = _ledger(session_scope)
    placed = [row for row in rows if row.event_type == "telephony.call_placed"]
    ended = [row for row in rows if row.event_type == "telephony.call_ended"]
    assert len(placed) == 1 and len(ended) == 1
    assert placed[0].subsystem == "telephony"
    assert placed[0].detail_json["reason"] == policy.KIND_SECURITY_CRITICAL
    assert placed[0].detail_json["call_sid"] == outcome.call_sid
    assert ended[0].detail_json["call_status"] == "completed"
    assert ended[0].status == "completed"
    # answered: no second call
    assert len(provider.calls) == 1


def test_not_answered_is_retried_once_after_five_minutes_then_a_notification(
    session_scope: Any,
) -> None:
    provider = FakeTelephony()
    clock = Clock(T0)
    caller = _caller(session_scope, provider=provider, clock=clock)

    first = caller.call(policy.KIND_RELEASE_FAILED, "Yeni sürüm açılamadı.")
    provider.status[first.call_sid] = "no-answer"

    clock.now = T0 + timedelta(minutes=4)
    caller.sweep()
    assert len(provider.calls) == 1, "retried before five minutes"

    clock.now = T0 + timedelta(minutes=5)
    caller.sweep()
    assert len(provider.calls) == 2, "not retried after five minutes"
    retry_sid = provider.calls[1]["sid"]
    provider.status[retry_sid] = "busy"

    clock.now = T0 + timedelta(minutes=10)
    caller.sweep()
    clock.now = T0 + timedelta(minutes=30)
    caller.sweep()

    assert len(provider.calls) == 2, "a third call"
    with session_scope() as db:
        notices = (
            db.execute(
                select(NotificationRow).where(
                    NotificationRow.kind == policy.UNANSWERED_NOTIFICATION_KIND
                )
            )
            .scalars()
            .all()
        )
    assert len(notices) == 1
    assert "Yeni sürüm açılamadı." in notices[0].body
    ended = [
        row.detail_json["call_status"]
        for row in _ledger(session_scope)
        if row.event_type == "telephony.call_ended"
    ]
    assert ended == ["no-answer", "busy"]


def test_the_retry_obeys_the_hourly_cap(session_scope: Any) -> None:
    """Return 1 (inspector, 2026-10-05): three unanswered calls were each retried at +5 min
    - six calls in five minutes with a cap of three. The retry is a call like any other: the
    cap stops it, the ledger says so, and the owner gets the notification instead."""
    provider = FakeTelephony()
    clock = Clock(T0)
    caller = _caller(session_scope, provider=provider, clock=clock)
    for _ in range(3):
        sid = caller.call(policy.KIND_SECURITY_CRITICAL, "Uyarı").call_sid
        provider.status[sid] = "no-answer"

    clock.now = T0 + timedelta(minutes=5)
    counts = caller.sweep()

    assert len(provider.calls) == 3, "a retry rang past the hourly cap"
    assert counts["retried"] == 0
    assert counts["notified"] == 3
    skipped = [r for r in _ledger(session_scope) if r.event_type == "telephony.call_skipped"]
    assert [r.detail_json["decision"] for r in skipped] == ["hourly_cap"] * 3
    assert all(r.detail_json["attempt"] == 2 for r in skipped)


def test_the_retry_of_a_non_critical_call_waits_out_quiet_hours(session_scope: Any) -> None:
    """Return 2: a release failure at 22:57 Istanbul rang, went unanswered, and its retry
    rang at 23:02 - inside quiet hours, for a kind that may not ring then. The retry is
    skipped and becomes the notification; a critical one still rings."""
    provider = FakeTelephony()
    before_quiet = datetime(2026, 10, 5, 19, 57, tzinfo=UTC)  # 22:57 Istanbul
    clock = Clock(before_quiet)
    caller = _caller(session_scope, provider=provider, clock=clock)
    routine = caller.call(policy.KIND_RELEASE_FAILED, "Yeni sürüm açılamadı.")
    critical = caller.call(policy.KIND_SECURITY_CRITICAL, "Biri girmeye çalışıyor.")
    provider.status[routine.call_sid] = "no-answer"
    provider.status[critical.call_sid] = "no-answer"

    clock.now = before_quiet + timedelta(minutes=5)  # 23:02 Istanbul
    counts = caller.sweep()

    assert len(provider.calls) == 3, "the routine retry rang in quiet hours"
    assert "Biri girmeye çalışıyor." in caller.last_message
    assert counts == {"answered": 0, "retried": 1, "notified": 1, "unknown": 0}
    skipped = [r for r in _ledger(session_scope) if r.event_type == "telephony.call_skipped"]
    assert [(r.detail_json["reason"], r.detail_json["decision"]) for r in skipped] == [
        (policy.KIND_RELEASE_FAILED, "quiet_hours")
    ]
    with session_scope() as db:
        notices = db.execute(select(NotificationRow)).scalars().all()
    assert [n.body for n in notices] == ["Yeni sürüm açılamadı."]


def test_the_hourly_cap_is_counted_from_the_ledger(session_scope: Any) -> None:
    """A restart must not reset the cap: the count is the ledger's, not the process's."""
    provider = FakeTelephony()
    for _ in range(3):
        _caller(session_scope, provider=provider).call(policy.KIND_SECURITY_CRITICAL, "Uyarı")

    fourth = _caller(session_scope, provider=provider).call(policy.KIND_SECURITY_CRITICAL, "Uyarı")

    assert fourth.status == "skipped"
    assert fourth.reason == "hourly_cap"
    assert len(provider.calls) == 3
    skipped = [r for r in _ledger(session_scope) if r.event_type == "telephony.call_skipped"]
    assert skipped and skipped[0].detail_json["decision"] == "hourly_cap"


# ------------------------------------------------------------------ the events that ring


def test_an_important_notification_rings_once_and_a_routine_one_never(
    session_scope: Any,
) -> None:
    provider = FakeTelephony()
    clock = Clock(T0)
    caller = _caller(session_scope, provider=provider, clock=clock)
    with session_scope() as db:
        notification_events.task_completed(db, task_id="t1", what="Rapor", now=T0)
        notifications.record(
            db,
            kind=notification_events.RECOVERY_ALERT,
            title="Sürüm geri alındı",
            body="Yeni sürüm sağlıksızdı, eskisine döndüm.",
            priority="urgent",
            now=T0,
        )

    caller.call_for_notifications()
    clock.now = T0 + timedelta(seconds=30)
    caller.call_for_notifications()

    assert len(provider.calls) == 1
    assert "eskisine döndüm" in caller.last_message


# ------------------------------------------------------------------ the secrets


def test_the_twilio_request_is_the_rest_call_with_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "CA" + "1" * 32, "status": "queued"})

    twilio = TwilioTelephony(ACCOUNT_SID, AUTH_TOKEN, transport=httpx.MockTransport(handler))
    placed = twilio.place_call(to=OWNER, from_=TWILIO_NUMBER, twiml="<Response/>")

    assert placed.sid == "CA" + "1" * 32
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json"
    )
    form = urllib.parse.parse_qs(request.content.decode())
    assert form["To"] == [OWNER]
    assert form["From"] == [TWILIO_NUMBER]
    assert form["Twiml"] == ["<Response/>"]
    expected = base64.b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert AUTH_TOKEN not in repr(twilio) and ACCOUNT_SID not in repr(twilio)


def test_a_twilio_error_carries_no_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})

    twilio = TwilioTelephony(ACCOUNT_SID, AUTH_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(TelephonyError) as caught:
        twilio.place_call(to=OWNER, from_=TWILIO_NUMBER, twiml="<Response/>")

    assert "401" in str(caught.value)
    assert ACCOUNT_SID not in str(caught.value) and AUTH_TOKEN not in str(caught.value)


def _route_app(engine: Any, session_scope: Any, settings: Settings, provider: Any) -> TestClient:
    from app.artifacts.runtime import ArtifactRuntime
    from app.telephony.service import build_owner_caller

    app = FastAPI()
    app.include_router(telephony_routes.router)
    app.include_router(telephony_routes.audio_router)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    store = AudioStore(ttl_s=int(AUDIO_TTL.total_seconds()))
    app.state.telephony_audio_store = store
    app.state.telephony = build_owner_caller(
        settings,
        session_scope=session_scope,
        audio_store=store,
        provider=provider,
        speak=lambda text: (WAV, "audio/wav"),
    )
    client = TestClient(app)
    authenticate(app, client, settings=Settings(_env_file=None))
    return client


def test_the_settings_routes_say_connected_and_never_return_or_log_a_secret(
    engine: Any, session_scope: Any, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        _env_file=None,
        telephony_twilio_account_sid=ACCOUNT_SID,
        telephony_twilio_auth_token=AUTH_TOKEN,
        telephony_owner_number=OWNER,
        telephony_from_number=TWILIO_NUMBER,
        telephony_public_base_url=BASE,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "CA" + "2" * 32, "status": "queued"})

    provider = TwilioTelephony(
        settings.telephony_twilio_account_sid.get_secret_value(),
        settings.telephony_twilio_auth_token.get_secret_value(),
        transport=httpx.MockTransport(handler),
    )
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as structured:
        client = _route_app(engine, session_scope, settings, provider)
        status = client.get("/v1/telephony/status")
        test_call = client.post("/v1/telephony/test-call")

    assert status.status_code == 200, status.text
    assert status.json()["connected"] is True
    assert "trial" in status.json()["trial_note"].lower()
    assert test_call.status_code == 200, test_call.text
    assert test_call.json()["status"] == "placed"
    assert test_call.json()["call_sid"] == "CA" + "2" * 32
    assert len(seen) == 1
    everything = " ".join(
        [status.text, test_call.text, caplog.text, repr(structured), repr(settings)]
        + [repr(row.detail_json) + (row.factual_summary or "") for row in _ledger(session_scope)]
    )
    assert structured, "nothing was logged - the scan would pass vacuously"
    assert AUTH_TOKEN not in everything
    assert ACCOUNT_SID not in everything


def test_the_settings_page_says_not_connected_without_the_secrets(
    engine: Any, session_scope: Any
) -> None:
    client = _route_app(engine, session_scope, Settings(_env_file=None), None)

    status = client.get("/v1/telephony/status")
    test_call = client.post("/v1/telephony/test-call")

    assert status.json()["connected"] is False
    assert test_call.status_code == 409


def test_the_settings_routes_need_the_owner(engine: Any, session_scope: Any) -> None:
    client = _route_app(engine, session_scope, Settings(_env_file=None), None)
    client.headers.pop("Authorization")

    assert client.get("/v1/telephony/status").status_code == 401
    assert client.post("/v1/telephony/test-call").status_code == 401


# ------------------------------------------------------------------ wired into the app


def test_the_application_carries_the_caller_and_its_routes() -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    client = TestClient(app)

    # Owner-gated routes that exist answer 401, not 404; the audio route is open and says
    # its own 404 for a token it does not know.
    assert client.get("/v1/telephony/status").status_code == 401
    assert client.post("/v1/telephony/test-call").status_code == 401
    audio = client.get("/v1/telephony/audio/unknown-token")
    assert audio.status_code == 404 and audio.json()["detail"] == "audio not available"
    assert isinstance(app.state.telephony, OwnerCaller)
    assert app.state.telephony_audio_store is not None
    assert app.state.telephony.configured is False


def test_the_call_loop_is_started_by_the_lifespan() -> None:
    """The retry and the event calls are driven by ``telephony_loop``. Started in the
    lifespan it must also be reported by health (test_bounded_delivery /
    test_health_endpoint guard sets) - those two files are outside this task's area."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
    lifespan = source.split("async def lifespan", 1)[1]

    assert "await telephony_loop.start()" in lifespan

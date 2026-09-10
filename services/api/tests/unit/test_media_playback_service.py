"""Owner-requested playback (ADR-0112): what actually reaches the device, and what
is claimed about it afterwards.

NO BROWSER IS LAUNCHED HERE: a scripted fake stands in for the device port, the same
``DeviceActionPort`` discipline ``test_news_playback_service.py`` uses for the sibling
news surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.media.models import (
    PLAYBACK_STATUS_CLOSED,
    PLAYBACK_STATUS_FAILED,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
    OwnerMediaPlaybackRow,
)
from app.media.playback_service import (
    CAPABILITY_MEDIA_PLAY,
    CAPABILITY_MEDIA_STOP,
    CAPABILITY_SEARCH,
    CAPABILITY_SESSION_OPEN,
    CAPABILITY_TAB_NEW,
    ERROR_NO_DEVICE,
    ERROR_NO_VIDEO_FOUND,
    ERROR_NOTHING_PLAYING,
    ERROR_NOTHING_REQUESTED,
    ERROR_PLAYBACK_UNVERIFIED,
    ERROR_SEARCH_FAILED,
    OWNER_ATTACHED_PROFILE,
    OWNER_MEDIA_PROFILE,
    OWNER_MEDIA_SESSION_KIND,
    SESSION_PREFIX,
    play_request,
    stop_playback,
)
from app.routines.dispatch import DeviceRunResult

VIDEO = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"
SPOKEN = "Doğum günün kutlu olsun Kadir"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    OwnerMediaPlaybackRow.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@dataclass
class FakeDeviceAction:
    results: dict[str, DeviceRunResult | Callable[[dict[str, Any]], DeviceRunResult]] = field(
        default_factory=dict
    )
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append((capability, dict(payload)))
        scripted = self.results.get(capability)
        if scripted is None:
            return DeviceRunResult(True, result={})
        if callable(scripted):
            return scripted(payload)
        return scripted

    def capabilities(self) -> list[str]:
        return [capability for capability, _ in self.calls]


def _found(url: str = WATCH) -> DeviceRunResult:
    return DeviceRunResult(
        True,
        result={
            "results": [
                {"rank": 1, "url": "https://example.com/lyrics", "title": "sözler"},
                {"rank": 2, "url": url, "title": "Doğum Günün Kutlu Olsun"},
            ]
        },
    )


def _playing(verified: bool = True, title: str = "Doğum Günün Kutlu Olsun") -> DeviceRunResult:
    return DeviceRunResult(
        True, result={"playing": True, "verified": verified, "title": title, "url": WATCH}
    )


def _device(**scripted: Any) -> FakeDeviceAction:
    return FakeDeviceAction(
        results={
            CAPABILITY_SEARCH: scripted.get("search", _found()),
            CAPABILITY_MEDIA_PLAY: scripted.get("play", _playing()),
            **{k: v for k, v in scripted.items() if k.startswith("browser.")},
        }
    )


# ------------------------------------------------------------------- the wire


def test_it_asks_for_the_owners_own_browser_first(db: Session) -> None:
    """ADR-0113. The owner asked for their own Chrome -- signed in, their tabs --
    and the point of the whole change is that this is the FIRST thing tried, not a
    setting somebody has to remember to turn on."""
    device = _device()

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.ok
    assert outcome.profile == OWNER_ATTACHED_PROFILE == "owner"
    capability, payload = device.calls[0]
    assert capability == CAPABILITY_SESSION_OPEN
    assert payload["profile"] == "owner"
    assert payload["session_kind"] == OWNER_MEDIA_SESSION_KIND == "media"
    assert payload["session_id"].startswith(SESSION_PREFIX)
    # Narrower than the owner profile's own default, and deliberately: playing a
    # video needs nothing more, and a session may always be narrowed. The wider
    # grant belongs to the tools that actually click and type.
    assert payload["policy"]["allowed_risk_classes"] == ["READ", "NAVIGATE"]
    assert payload["policy"]["visible"] is True


def test_when_no_browser_is_enrolled_it_falls_back_AND_says_so(db: Session) -> None:
    """A blank browser is signed into nothing, so the owner meets a consent wall
    instead of their own YouTube. Playing it anyway and saying nothing would make
    them diagnose that themselves."""
    device = FakeDeviceAction(
        results={
            CAPABILITY_SESSION_OPEN: lambda payload: (
                DeviceRunResult(False, error_class="capability_missing", message="not enrolled")
                if payload["profile"] == "owner"
                else DeviceRunResult(True, result={"opened": True})
            ),
            CAPABILITY_SEARCH: _found(),
            CAPABILITY_MEDIA_PLAY: _playing(),
        }
    )

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.ok
    assert outcome.profile == OWNER_MEDIA_PROFILE == "isolated"
    opens = [p["profile"] for c, p in device.calls if c == CAPABILITY_SESSION_OPEN]
    assert opens == ["owner", "isolated"]
    assert "Kendi tarayıcınıza bağlı değilim" in outcome.speech


def test_a_real_attach_failure_is_not_worked_around(db: Session) -> None:
    """``capability_missing`` means "nothing is enrolled" -- a configuration fact.
    Anything else (a Chrome that died, a revoked endpoint, a port that stopped
    answering) is real, and quietly opening a different browser instead would tell
    the owner a song is playing somewhere they never opened."""
    device = FakeDeviceAction(
        results={
            CAPABILITY_SESSION_OPEN: DeviceRunResult(
                False, error_class="browser_unavailable", message="connection refused"
            )
        }
    )

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.ok is False
    opens = [p["profile"] for c, p in device.calls if c == CAPABILITY_SESSION_OPEN]
    assert opens == ["owner"], "it must not silently retry in another browser"


def test_search_then_play_on_the_same_session(db: Session) -> None:
    device = _device()

    play_request(db, device, request_text=SPOKEN)

    assert device.capabilities() == [
        CAPABILITY_SESSION_OPEN,
        CAPABILITY_TAB_NEW,
        CAPABILITY_SEARCH,
        CAPABILITY_MEDIA_PLAY,
    ]
    sessions = {payload["session_id"] for _, payload in device.calls}
    assert len(sessions) == 1, "one session does the search AND the playback"
    _, search_payload = device.calls[2]
    assert search_payload["query"] == f"{SPOKEN} youtube"
    _, play_payload = device.calls[3]
    assert play_payload["url"] == WATCH


# ----------------------------------------------------------------- the claims


def test_verified_playback_is_the_only_thing_called_playing(db: Session) -> None:
    outcome = play_request(db, _device(play=_playing(verified=True)), request_text=SPOKEN)

    assert outcome.ok is True
    assert outcome.status == PLAYBACK_STATUS_PLAYING
    assert outcome.video_id == VIDEO
    # It says WHAT is playing: the owner named a song from memory, and hearing the
    # title back is how they learn the machine heard the same one.
    assert "Doğum Günün Kutlu Olsun" in outcome.speech
    row = db.execute(select(OwnerMediaPlaybackRow)).scalars().one()
    assert row.status == PLAYBACK_STATUS_PLAYING
    assert row.error_class is None


def test_an_unverified_play_is_never_reported_as_playing(db: Session) -> None:
    """The device opened a browser and could not prove the element advanced. Saying
    "çalıyor" on that evidence is the exact dishonesty the 2026-09-09 typing defect
    committed in the other direction."""
    outcome = play_request(db, _device(play=_playing(verified=False)), request_text=SPOKEN)

    assert outcome.ok is False
    assert outcome.status == PLAYBACK_STATUS_UNVERIFIED
    assert outcome.error_class == ERROR_PLAYBACK_UNVERIFIED
    assert "doğrulayamadım" in outcome.speech
    row = db.execute(select(OwnerMediaPlaybackRow)).scalars().one()
    assert row.status == PLAYBACK_STATUS_UNVERIFIED
    # ...and the video is still recorded, because something may well be on screen.
    assert row.video_id == VIDEO


def test_no_video_in_the_results_says_so_and_plays_nothing(db: Session) -> None:
    device = _device(
        search=DeviceRunResult(True, result={"results": [{"rank": 1, "url": "https://a.example"}]})
    )

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.error_class == ERROR_NO_VIDEO_FOUND
    assert CAPABILITY_MEDIA_PLAY not in device.capabilities()
    # the session it opened is closed again rather than left running
    assert "browser.session_close" in device.capabilities()


def test_a_failed_search_is_a_search_failure_not_a_playback_failure(db: Session) -> None:
    device = _device(search=DeviceRunResult(False, error_class="timeout", message="slow"))

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.error_class == ERROR_SEARCH_FAILED
    assert CAPABILITY_MEDIA_PLAY not in device.capabilities()
    row = db.execute(select(OwnerMediaPlaybackRow)).scalars().one()
    assert row.status == PLAYBACK_STATUS_FAILED
    assert row.error_message == "slow"


def test_no_device_at_all_refuses_before_writing_a_row(db: Session) -> None:
    outcome = play_request(db, None, request_text=SPOKEN)

    assert outcome.error_class == ERROR_NO_DEVICE
    assert db.execute(select(OwnerMediaPlaybackRow)).scalars().all() == []


def test_an_empty_request_asks_rather_than_searching_for_nothing(db: Session) -> None:
    device = _device()

    outcome = play_request(db, device, request_text="   ")

    assert outcome.error_class == ERROR_NOTHING_REQUESTED
    assert device.calls == []


# ------------------------------------------------------------------ stopping


def test_stop_closes_the_session_this_service_opened(db: Session) -> None:
    device = _device()
    play_request(db, device, request_text=SPOKEN)
    opened_session = device.calls[0][1]["session_id"]
    device.calls.clear()

    outcome = stop_playback(db, device)

    assert outcome.ok
    capability, payload = device.calls[0]
    assert capability == CAPABILITY_MEDIA_STOP
    assert payload["session_id"] == opened_session
    row = db.execute(select(OwnerMediaPlaybackRow)).scalars().one()
    assert row.status == PLAYBACK_STATUS_CLOSED


def test_stopping_when_nothing_is_playing_says_so(db: Session) -> None:
    device = _device()

    outcome = stop_playback(db, device)

    assert outcome.error_class == ERROR_NOTHING_PLAYING
    assert device.calls == []


def test_a_second_request_stops_the_first_rather_than_stacking(db: Session) -> None:
    """Two songs at once is not a thing anyone asked for, and an isolated Chrome
    context left behind on every request is how a machine ends up with nine."""
    device = _device()
    play_request(db, device, request_text=SPOKEN)
    first_session = device.calls[0][1]["session_id"]
    device.calls.clear()

    play_request(db, device, request_text="Sezen Aksu Gülümse")

    stopped = [p["session_id"] for c, p in device.calls if c == CAPABILITY_MEDIA_STOP]
    assert stopped == [first_session]
    rows = db.execute(
        select(OwnerMediaPlaybackRow).order_by(OwnerMediaPlaybackRow.created_at)
    ).scalars()
    statuses = [row.status for row in rows]
    assert statuses == [PLAYBACK_STATUS_CLOSED, PLAYBACK_STATUS_PLAYING]


# --------------------------------- the owner's own tabs (ADR-0113, found 2026-09-10)


def test_it_opens_a_new_tab_before_touching_the_owners_browser(db: Session) -> None:
    """Attaching hands the session the browser's FIRST EXISTING tab.

    ``ExistingSessionBackend`` takes ``context.pages[0]``, so without this the search
    would drive whatever the owner has open in tab one to Google and then to YouTube --
    their work, gone, to play a song. Caught by reading the backend before the first
    real run, not by a user losing a tab.
    """
    device = _device()

    play_request(db, device, request_text=SPOKEN)

    ordered = device.capabilities()
    assert ordered == [
        CAPABILITY_SESSION_OPEN,
        CAPABILITY_TAB_NEW,
        CAPABILITY_SEARCH,
        CAPABILITY_MEDIA_PLAY,
    ]
    assert ordered.index(CAPABILITY_TAB_NEW) < ordered.index(CAPABILITY_SEARCH)


def test_the_blank_browser_needs_no_new_tab(db: Session) -> None:
    """The isolated context is ours and starts empty; a tab there would just be litter."""
    device = FakeDeviceAction(
        results={
            CAPABILITY_SESSION_OPEN: lambda payload: (
                DeviceRunResult(False, error_class="capability_missing", message="not enrolled")
                if payload["profile"] == "owner"
                else DeviceRunResult(True, result={"opened": True})
            ),
            CAPABILITY_SEARCH: _found(),
            CAPABILITY_MEDIA_PLAY: _playing(),
        }
    )

    play_request(db, device, request_text=SPOKEN)

    assert CAPABILITY_TAB_NEW not in device.capabilities()


def test_a_failed_new_tab_refuses_rather_than_using_the_owners_tab(db: Session) -> None:
    device = FakeDeviceAction(
        results={
            CAPABILITY_SESSION_OPEN: DeviceRunResult(True, result={"opened": True}),
            CAPABILITY_TAB_NEW: DeviceRunResult(
                False, error_class="browser_lifecycle_violation", message="max tabs"
            ),
            CAPABILITY_SEARCH: _found(),
            CAPABILITY_MEDIA_PLAY: _playing(),
        }
    )

    outcome = play_request(db, device, request_text=SPOKEN)

    assert outcome.ok is False
    assert CAPABILITY_SEARCH not in device.capabilities()
    assert CAPABILITY_MEDIA_PLAY not in device.capabilities()

"""M18.3 alarm media operations (BROWSER_CAPABILITIES.md §1/§2/§3, contract v1.2).

NO BROWSER IS LAUNCHED HERE, by construction: every test drives a real
:class:`~browser_agent.worker.Worker` in-process against a fake ``Page`` /
``BrowserSession`` (the style ``tests/unit/test_evidence.py`` uses for the page
driver), and the two ``session_open`` tests replace ``ManagedBackend`` with a
recording double that never touches Playwright. ``tests/test_test_isolation_guards.py``
holds the owner's standing rule that this stays true.

What is asserted, in the order the alarm actually needs it:

- the dedicated alarm profile is a real, separate directory and can never be
  the research profile or a path inside the owner's own Chrome;
- ``--autoplay-policy=no-user-gesture-required`` reaches Chrome for a
  ``session_kind="media"`` launch and for NOTHING else;
- the in-page ramp script (a pure generator) cancels its predecessor, steps
  every 250 ms and lands on the level it was asked for;
- every ``media_play`` result field, and every failure reason in the contract's
  enumeration, produced by a page that really behaves that way;
- nothing here clicks, retries, or reads cookies/storage.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_agent import media, policy
from browser_agent.backends import ManagedBackend
from browser_agent.detect import BrowserInfo
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import SessionState, Worker, build_arg_parser

# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

_EMPTY_RAW: dict[str, Any] = {
    "title": "",
    "heading_text": "",
    "has_password_field": False,
    "html_lang": None,
    "canonical_url": None,
    "description": None,
    "og_site_name": None,
    "article_published_time": None,
    "article_modified_time": None,
    "meta_date": None,
    "meta_author": None,
    "time_datetime": None,
    "links": [],
    "json_ld_raw": [],
}


class _FakeVideo:
    """A ``<video>`` that behaves like one, without a browser."""

    def __init__(
        self,
        *,
        volume: float = 1.0,
        muted: bool = True,
        paused: bool = True,
        ended: bool = False,
        current_time: float = 0.0,
        duration: float | None = 210.0,
        play_error: str | None = None,
        advance_per_read: float = 1.0,
    ) -> None:
        self.volume = volume
        self.muted = muted
        self.paused = paused
        self.ended = ended
        self.current_time = current_time
        self.duration = duration
        self.play_error = play_error
        self.advance_per_read = advance_per_read

    def read(self) -> dict[str, Any]:
        if not self.paused and not self.ended:
            self.current_time += self.advance_per_read
        return {
            "present": True,
            "paused": self.paused,
            "ended": self.ended,
            "current_time": self.current_time,
            "duration": self.duration,
            "volume": self.volume,
            "muted": self.muted,
        }


class _FakeResponse:
    def __init__(self, status: int | None) -> None:
        self.status = status


class _FakeTab:
    index = 0
    url = "https://example.com"
    title = ""
    is_current = True


class _FakePage:
    """The narrow slice of Playwright's ``Page`` the media path touches.

    Anything the media surface must NEVER do (click, type, press) raises here,
    so "no bypass" is enforced by the double rather than only reviewed.
    """

    def __init__(
        self,
        *,
        url: str = "https://example.com/watch",
        title: str = "A song",
        body_text: str = "a perfectly ordinary page with a video on it",
        video: _FakeVideo | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        self.url = url
        self._title = title
        self.body_text = body_text
        self.video = video
        self._raw = {**_EMPTY_RAW, "title": title, **(raw or {})}
        self.calls: list[tuple[str, Any]] = []
        self.ramp_scripts: list[str] = []

    async def title(self) -> str:  # type: ignore[override]
        return self._title

    async def wait_for_selector(self, selector: str, *, timeout: float, state: str) -> object:
        self.calls.append(("wait_for_selector", (selector, timeout, state)))
        if self.video is None:
            raise PlaywrightTimeoutError(f"Timeout {timeout}ms waiting for {selector}")
        return object()

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if script == media.MEDIA_PLAY_JS:
            return self._play(arg)
        if script == media.MEDIA_READ_JS:
            self.calls.append(("read", None))
            return {"present": False} if self.video is None else self.video.read()
        if script == media.MEDIA_PAUSE_JS:
            return self._pause()
        if media.RAMP_HANDLE_PROPERTY in script:
            return self._ramp(script)
        self.calls.append(("extract", None))
        return dict(self._raw)

    def _play(self, volume: Any) -> dict[str, Any]:
        if self.video is None:
            self.calls.append(("play", None))
            return {"present": False}
        # Mirrors the script's own order, which the string test below pins.
        self.calls.append(("volume", volume))
        self.video.volume = float(volume)
        self.video.muted = False
        started_at = self.video.current_time
        self.calls.append(("play", None))
        if self.video.play_error is not None:
            return {
                "present": True,
                "played": False,
                "error_name": self.video.play_error,
                "error_message": "blocked",
                "current_time": self.video.current_time,
                "duration": self.video.duration,
                "volume": self.video.volume,
                "muted": self.video.muted,
                "paused": self.video.paused,
            }
        self.video.paused = False
        return {
            "present": True,
            "played": True,
            "started_at": started_at,
            "current_time": self.video.current_time,
            "duration": self.video.duration,
            "volume": self.video.volume,
            "muted": self.video.muted,
            "paused": self.video.paused,
        }

    def _pause(self) -> dict[str, Any]:
        self.calls.append(("pause", None))
        if self.video is None:
            return {"present": False}
        was_playing = not self.video.paused and not self.video.ended
        self.video.paused = True
        return {"present": True, "was_playing": was_playing, "paused": True}

    def _ramp(self, script: str) -> dict[str, Any]:
        self.ramp_scripts.append(script)
        self.calls.append(("ramp", None))
        if self.video is None:
            return {"applied": False, "level_from": None, "steps": 0}
        return {"applied": True, "level_from": self.video.volume, "steps": 1}

    # --- everything the media surface may never do ------------------------ #

    async def click(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the media surface must never click anything")

    async def fill(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the media surface must never type anything")

    async def context(self) -> None:
        raise AssertionError("the media surface must never reach for cookies/storage")


class _FakeBackend:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.main_pid: int | None = None
        self.last_launch_kind = "clean"
        self.launch_lock_name: str | None = None
        self.job_object_assigned = False

    @property
    def current_page(self) -> _FakePage:
        return self._page

    async def is_alive(self) -> bool:
        return True


class _FakeBrowserSession:
    def __init__(
        self,
        page: _FakePage,
        *,
        http_status: int | None = 200,
        navigate_error: BrowserError | None = None,
        final_url: str | None = None,
    ) -> None:
        self._page = page
        self.backend = _FakeBackend(page)
        self._http_status = http_status
        self._navigate_error = navigate_error
        self._final_url = final_url
        self.navigations: list[str] = []
        self.closed = False

    async def navigate(self, url: str, *, timeout_ms: float = 15_000) -> _FakeResponse:
        self.navigations.append(url)
        if self._navigate_error is not None:
            raise self._navigate_error
        self._page.url = self._final_url or url
        return _FakeResponse(self._http_status)

    async def page_text(self, *, timeout_ms: float = 5_000) -> str:
        return self._page.body_text

    async def list_tabs(self) -> list[_FakeTab]:
        return [_FakeTab()]

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# worker/session helpers
# --------------------------------------------------------------------------- #


def _make_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **extra: str) -> Worker:
    argv = ["--data-dir", str(tmp_path / "data"), "--channel", "chrome", "--headless"]
    for key, value in extra.items():
        argv.extend([f"--{key.replace('_', '-')}", value])
    worker = Worker(build_arg_parser().parse_args(argv))

    async def fake_detect(_channel):
        return BrowserInfo(
            channel="chrome", available=True, version="999.0.0.0", executable_path="/fake/chrome"
        )

    monkeypatch.setattr("browser_agent.worker.detect_browser", fake_detect)
    return worker


def _register_session(
    worker: Worker,
    browser_session: _FakeBrowserSession,
    *,
    session_id: str = "alarm-abc",
    session_kind: str = media.MEDIA_SESSION_KIND,
    profile: str = media.ALARM_PROFILE,
    allowed: frozenset[policy.RiskClass] | None = None,
) -> SessionState:
    state = SessionState(
        session_id=session_id,
        browser_session=browser_session,  # type: ignore[arg-type]
        backend=browser_session.backend,  # type: ignore[arg-type]
        policy_allowed=allowed if allowed is not None else policy.MEDIA_SESSION_CLASSES,
        visible=True,
        channel="chrome",
        browser_version="999.0.0.0",
        profile=profile,
        session_uid="uid-1",
        profile_dir=None,  # keeps _await_browser_pid_exit off the OS process list
        session_kind=session_kind,
        last_used=0.0,
    )
    worker._sessions[session_id] = state
    return state


@pytest.fixture()
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the media path's wall-clock waits instead of spending them."""
    recorded: list[float] = []

    async def fake_wait(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("browser_agent.worker._media_wait", fake_wait)
    return recorded


# --------------------------------------------------------------------------- #
# 1. the dedicated alarm profile
# --------------------------------------------------------------------------- #


class TestAlarmProfileIsolation:
    def test_the_alarm_profile_is_a_sibling_of_the_research_profile_never_it(self) -> None:
        research = Path("C:/data/pagentos/browser/profile")
        alarm = media.alarm_profile_dir_for(research)
        assert alarm != research
        assert alarm.parent == research.parent
        assert alarm.name == "profile-alarm"

    def test_a_worker_resolves_two_distinct_persistent_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        research = worker._persistent_profile_dir(media.RESEARCH_PROFILE)
        alarm = worker._persistent_profile_dir(media.ALARM_PROFILE)
        assert research is not None and alarm is not None
        assert research != alarm
        assert not alarm.is_relative_to(research)
        # "isolated" owns no directory at all.
        assert worker._persistent_profile_dir(media.ISOLATED_PROFILE) is None

    def test_an_alarm_profile_equal_to_or_inside_the_research_profile_is_refused(self) -> None:
        research = Path("C:/data/profile")
        for bad in (research, research / "nested"):
            with pytest.raises(BrowserError) as exc:
                media.require_distinct_alarm_profile(bad, research)
            assert exc.value.error_class == ErrorClass.VALIDATION_ERROR

    def test_configuring_the_alarm_profile_onto_the_research_one_fails_at_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile"
        with pytest.raises(BrowserError) as exc:
            _make_worker(
                tmp_path,
                monkeypatch,
                profile_dir=str(profile),
                alarm_profile_dir=str(profile),
            )
        assert "separate from the research profile" in exc.value.message

    def test_the_alarm_profile_can_never_be_the_owners_own_chrome(self) -> None:
        """The existing real-profile guard covers the alarm directory too — an
        alarm profile placed anywhere inside the owner's Chrome tree is refused
        by ManagedBackend before any launch is attempted."""
        owner_chrome = Path(r"C:\Users\owner\AppData\Local\Google\Chrome\User Data")
        with pytest.raises(BrowserError) as exc:
            ManagedBackend(profile_dir=owner_chrome / "alarm")
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "real browser profile tree" in exc.value.message


# --------------------------------------------------------------------------- #
# 2. the autoplay preference
# --------------------------------------------------------------------------- #


class TestAutoplayFlag:
    def test_the_flag_is_exactly_the_documented_switch(self) -> None:
        assert media.AUTOPLAY_POLICY_ARG == "--autoplay-policy=no-user-gesture-required"

    def test_only_a_media_session_gets_it(self) -> None:
        assert media.media_launch_args(media.MEDIA_SESSION_KIND) == [media.AUTOPLAY_POLICY_ARG]
        assert media.media_launch_args(media.RESEARCH_SESSION_KIND) == []
        assert media.media_launch_args("anything-else") == []

    def test_it_is_documented_as_a_preference_and_not_an_anti_bot_measure(self) -> None:
        source = Path(media.__file__).read_text(encoding="utf-8")
        assert "is not an anti-bot measure" in source
        assert "defeats no site protection" in source


class _RecordingBackend:
    """Stands in for ManagedBackend at session_open: records how it was
    constructed and never goes anywhere near Playwright."""

    instances: list[_RecordingBackend] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.page = _FakePage()
        self.main_pid = None
        self.last_launch_kind = "clean"
        self.launch_lock_name = None
        self.job_object_assigned = False
        self.native_browser = None
        self.breaker = None
        self.connected = False
        _RecordingBackend.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def is_alive(self) -> bool:
        return True

    @property
    def current_page(self) -> _FakePage:
        return self.page

    async def list_tabs(self) -> list[_FakeTab]:
        return [_FakeTab()]

    def on_popup(self, handler: Any) -> None:
        self.popup_handler = handler

    async def close(self) -> None:
        return None


@pytest.fixture()
def recording_backend(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingBackend]:
    _RecordingBackend.instances = []
    monkeypatch.setattr("browser_agent.worker.ManagedBackend", _RecordingBackend)
    return _RecordingBackend


class TestSessionOpen:
    async def test_a_media_session_launches_on_the_alarm_profile_with_the_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        result = await worker._execute(
            "browser.session_open",
            {"session_id": "alarm-1", "profile": "alarm", "session_kind": "media"},
        )
        assert result["created"] is True
        assert result["profile"] == "alarm"
        assert result["session_kind"] == "media"
        backend = recording_backend.instances[-1]
        assert backend.kwargs["browser_args"] == [media.AUTOPLAY_POLICY_ARG]
        assert backend.kwargs["profile_dir"] == worker._alarm_profile_dir
        assert backend.kwargs["profile_dir"] != worker._profile_dir
        # A visible window even though this worker was started --headless.
        assert backend.kwargs["headless"] is False
        assert result["policy"]["visible"] is True
        assert result["policy"]["allowed_risk_classes"] == ["NAVIGATE", "READ"]

    async def test_a_research_session_is_launched_exactly_as_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute("browser.session_open", {"session_id": "task-1"})
        backend = recording_backend.instances[-1]
        assert backend.kwargs["browser_args"] is None
        assert backend.kwargs["profile_dir"] == worker._profile_dir
        assert backend.kwargs["headless"] is True

    async def test_an_explicit_visible_false_still_wins_for_a_media_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute(
            "browser.session_open",
            {
                "session_id": "alarm-1",
                "profile": "alarm",
                "session_kind": "media",
                "policy": {"visible": False},
            },
        )
        assert recording_backend.instances[-1].kwargs["headless"] is True

    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ({"profile": "alarm"}, "requires session_kind 'media'"),
            (
                {"profile": "research", "session_kind": "media"},
                "may not use the research profile",
            ),
            # "owner" became a REAL profile in v1.4 (ADR-0113); an unknown one still
            # has to be refused, so the case keeps its point with a name that is
            # genuinely not in the vocabulary.
            ({"profile": "karaoke"}, "profile must be one of"),
            ({"session_kind": "karaoke"}, "session_kind must be one of"),
        ],
    )
    async def test_the_alarm_profile_and_the_media_kind_are_one_thing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        recording_backend,
        payload: dict[str, Any],
        fragment: str,
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        with pytest.raises(BrowserError) as exc:
            await worker._execute("browser.session_open", {"session_id": "s1", **payload})
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert fragment in exc.value.message
        assert recording_backend.instances == [], "nothing may be launched by a refused open"

    # ------------------------------------------------- the owner's own browser (v1.4)

    async def test_the_owner_profile_is_refused_when_the_worker_has_no_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """ADR-0113. Attaching to the owner's signed-in Chrome is the most powerful
        thing this worker can do, so the default is that it cannot: a worker started
        without ``--owner-enrollment-file`` has no way to reach it at all."""
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()

        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open", {"session_id": "owner-media-1", "profile": "owner"}
            )

        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "--owner-enrollment-file" in exc.value.message
        assert recording_backend.instances == [], "nothing may be launched by a refused open"

    async def test_the_owner_profile_is_refused_until_the_owner_enrolls_a_browser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """Passing the PATH grants nothing. The FILE is the grant, and the refusal
        names the script that creates it rather than leaving the owner to guess."""
        registry = tmp_path / "owner-enrollment.json"
        worker = _make_worker(tmp_path, monkeypatch, owner_enrollment_file=str(registry))
        await worker._print_hello()

        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open", {"session_id": "owner-media-1", "profile": "owner"}
            )

        assert exc.value.error_class == ErrorClass.CAPABILITY_MISSING
        assert "enroll-owner-chrome" in exc.value.message
        assert recording_backend.instances == [], "nothing may be launched by a refused open"

    async def test_the_owner_profile_never_launches_a_browser_it_attaches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """The owner's Chrome is not ours to start -- and, on close, not ours to kill.
        A launch here would be a SECOND Chrome, which is the whole thing this profile
        exists to avoid."""
        import json

        registry = tmp_path / "owner-enrollment.json"
        registry.write_text(
            json.dumps(
                {
                    "enrollments": [
                        {
                            "id": "e1",
                            "name": "owner-chrome",
                            "transport": "cdp_loopback",
                            "endpoint": "http://127.0.0.1:19123",
                            "capability_overrides": {},
                            "created_at": "2026-09-10T18:00:00+00:00",
                            "owner_authorized_for_research": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        worker = _make_worker(tmp_path, monkeypatch, owner_enrollment_file=str(registry))
        await worker._print_hello()

        # Nothing is listening on that port, so the attach fails -- which is the
        # point: it FAILED TRYING TO CONNECT, and never once tried to launch.
        with pytest.raises(BrowserError):
            await worker._execute(
                "browser.session_open",
                {"session_id": "owner-media-1", "profile": "owner", "session_kind": "media"},
            )

        assert recording_backend.instances == [], "attach must never launch a browser"

    @staticmethod
    def _owner_registry(tmp_path: Path, *, research: bool) -> Path:
        import json

        registry = tmp_path / "owner-enrollment.json"
        registry.write_text(
            json.dumps(
                {
                    "enrollments": [
                        {
                            "id": "e1",
                            "name": "owner-chrome",
                            "transport": "cdp_loopback",
                            "endpoint": "http://127.0.0.1:19123",
                            "capability_overrides": {},
                            "created_at": "2026-09-10T18:00:00+00:00",
                            "owner_authorized_for_research": research,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return registry

    async def test_autonomous_research_may_not_drive_the_owners_chrome_without_the_grant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """ADR-0035 vs ADR-0113. The owner put their Chrome at their own disposal (media,
        operator actions) and the enrollment script records owner_authorized_for_research
        as false. A research session - the DEFAULT kind, so a bare {profile: owner} is one -
        must not attach to it. Refused as a scope decision, before any connection."""
        registry = self._owner_registry(tmp_path, research=False)
        worker = _make_worker(tmp_path, monkeypatch, owner_enrollment_file=str(registry))
        await worker._print_hello()
        connects: list[str] = []
        monkeypatch.setattr(
            "browser_agent.backends.ExistingSessionBackend.connect",
            lambda self: connects.append("connect"),
        )

        for payload in (
            {"session_id": "research-1", "profile": "owner"},
            {"session_id": "research-2", "profile": "owner", "session_kind": "research"},
        ):
            with pytest.raises(BrowserError) as exc:
                await worker._execute("browser.session_open", payload)
            assert exc.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
            assert "owner_authorized_for_research" in exc.value.message

        assert connects == [], "a refused scope never reaches the owner's browser"
        assert recording_backend.instances == []

    async def test_with_the_grant_a_research_session_attaches_like_any_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        registry = self._owner_registry(tmp_path, research=True)
        worker = _make_worker(tmp_path, monkeypatch, owner_enrollment_file=str(registry))
        await worker._print_hello()

        # Nothing listens on the port: the attach is ATTEMPTED (a connection error), which
        # is what proves the grant let it through.
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open", {"session_id": "research-1", "profile": "owner"}
            )
        assert exc.value.error_class != ErrorClass.SECURITY_SCOPE_ERROR
        assert recording_backend.instances == []

    async def test_a_non_loopback_enrollment_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """ADR-0019's rule, still enforced on the path that finally uses it: a CDP
        endpoint reachable from off this machine is not an owner browser, it is an
        open door."""
        import json

        registry = tmp_path / "owner-enrollment.json"
        registry.write_text(
            json.dumps(
                {
                    "enrollments": [
                        {
                            "id": "e1",
                            "name": "somewhere-else",
                            "transport": "cdp_loopback",
                            "endpoint": "http://10.0.0.5:19123",
                            "capability_overrides": {},
                            "created_at": "2026-09-10T18:00:00+00:00",
                            "owner_authorized_for_research": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        worker = _make_worker(tmp_path, monkeypatch, owner_enrollment_file=str(registry))
        await worker._print_hello()

        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "owner-media-1", "profile": "owner", "session_kind": "media"},
            )

        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "loopback" in exc.value.message
        assert recording_backend.instances == []

    async def test_a_reopen_may_not_turn_a_research_session_into_a_media_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute("browser.session_open", {"session_id": "task-1"})
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "task-1", "profile": "alarm", "session_kind": "media"},
            )
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert len(recording_backend.instances) == 1

    async def test_research_and_alarm_browsers_coexist_but_each_has_one_owner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute("browser.session_open", {"session_id": "task-1"})
        await worker._execute(
            "browser.session_open",
            {"session_id": "alarm-1", "profile": "alarm", "session_kind": "media"},
        )
        assert worker._persistent_profile_owner == {"research": "task-1", "alarm": "alarm-1"}
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "alarm-2", "profile": "alarm", "session_kind": "media"},
            )
        assert exc.value.error_class == ErrorClass.BROWSER_LIFECYCLE_VIOLATION
        assert exc.value.evidence["profile"] == "alarm"
        # ... and the research browser is untouched by any of it.
        assert worker._persistent_profile_owner["research"] == "task-1"


# --------------------------------------------------------------------------- #
# 3. the in-page ramp script (pure generation)
# --------------------------------------------------------------------------- #


class TestRampScript:
    def test_step_count_is_250ms_steps(self) -> None:
        assert media.RAMP_STEP_MS == 250
        assert media.ramp_step_count(20) == 80
        assert media.ramp_step_count(1) == 4
        assert media.ramp_step_count(0.1) == 1
        assert media.ramp_step_count(0) == 0

    def test_the_script_is_an_arrow_function_playwright_will_call(self) -> None:
        script = media.build_volume_ramp_script(0.6, 20).strip()
        assert script.startswith("() => {")
        assert script.endswith("}")
        assert script.count("{") == script.count("}")

    def test_it_steps_the_media_elements_own_volume_toward_the_level(self) -> None:
        script = media.build_volume_ramp_script(0.6, 20)
        assert "document.querySelector('video')" in script
        assert "const from = v.volume;" in script, "the page reads its OWN current level"
        assert "const to = 0.6;" in script
        assert "const steps = 80;" in script
        assert "}, 250);" in script
        assert "setInterval" in script
        # every write is clamped into 0..1
        assert "Math.min(1, Math.max(0, x))" in script
        assert "v.volume = clamp(to);" in script, "the ramp lands exactly on the level"

    def test_a_new_ramp_cancels_the_running_one_before_arming_its_own(self) -> None:
        script = media.build_volume_ramp_script(0.15, 1)
        handle = f'window["{media.RAMP_HANDLE_PROPERTY}"]'
        assert handle in script
        first_clear = script.index("clearInterval")
        first_set = script.index("setInterval")
        assert first_clear < first_set, "cancel the previous ramp before arming a new one"
        assert script.count("clearInterval") == 2, "cancel on entry, and again when done"

    def test_a_zero_second_ramp_sets_the_level_at_once_and_arms_nothing(self) -> None:
        script = media.build_volume_ramp_script(0.4, 0)
        assert "const steps = 0;" in script
        assert "if (steps <= 0)" in script
        # the immediate branch precedes the interval, so nothing is scheduled
        assert script.index("if (steps <= 0)") < script.index("setInterval")

    def test_no_media_element_means_the_script_reports_it_rather_than_throwing(self) -> None:
        script = media.build_volume_ramp_script(0.5, 5)
        assert "{applied: false, level_from: null, steps: 0}" in script

    def test_only_validated_numbers_are_interpolated(self) -> None:
        """Every value the generator embeds is a JSON literal of a bounded
        number — the generated source carries nothing caller-shaped."""
        script = media.build_volume_ramp_script(0.123456789, 3.5)
        assert "const to = 0.123457;" in script  # rounded, JSON-encoded
        assert json.loads("0.123457") == pytest.approx(0.123457)
        assert "const steps = 14;" in script


# --------------------------------------------------------------------------- #
# 4. media_play
# --------------------------------------------------------------------------- #


def _play_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "session_id": "alarm-abc",
        "url": "https://www.youtube.com/watch?v=abc",
        "volume": 0.15,
        "verify_seconds": 3,
    }
    payload.update(overrides)
    return payload


class TestMediaPlay:
    async def test_the_script_sets_the_volume_before_it_calls_play(self) -> None:
        """Spec §4 is explicit about the order: the owner must never be hit by
        the page's default volume for the instant before the ramp starts."""
        script = media.MEDIA_PLAY_JS
        assert script.index("v.volume = volume;") < script.index("await v.play();")

    async def test_a_verified_play_answers_every_contract_field(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(
            url="https://www.youtube.com/watch?v=abc",
            title="Hans Zimmer - Time",
            video=_FakeVideo(current_time=0.0, duration=250.0, advance_per_read=1.5),
        )
        session = _FakeBrowserSession(page)
        _register_session(worker, session)

        result = await worker._execute("browser.media_play", _play_payload())

        assert result["playing"] is True
        assert result["verified"] is True
        assert result["reason"] is None
        assert result["url"] == "https://www.youtube.com/watch?v=abc"
        assert result["final_url"] == "https://www.youtube.com/watch?v=abc"
        assert result["title"] == "Hans Zimmer - Time"
        assert result["current_time_s"] == pytest.approx(1.5)
        assert result["duration_s"] == pytest.approx(250.0)
        assert result["volume"] == pytest.approx(0.15)
        assert result["muted"] is False
        assert set(result) == {
            "playing",
            "verified",
            "url",
            "final_url",
            "title",
            "current_time_s",
            "duration_s",
            "volume",
            "muted",
            "reason",
            "lifecycle",
        }
        # the element was reached the way the contract says, and only that way
        assert ("volume", 0.15) in page.calls
        assert page.calls.index(("volume", 0.15)) < page.calls.index(("play", None))
        assert waits == [3], "verification waits exactly verify_seconds"
        assert session.navigations == ["https://www.youtube.com/watch?v=abc"]

    async def test_it_waits_at_most_ten_seconds_for_the_media_element(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo())
        _register_session(worker, _FakeBrowserSession(page))
        await worker._execute("browser.media_play", _play_payload())
        selector, timeout, state = next(
            args for name, args in page.calls if name == "wait_for_selector"
        )
        assert (selector, timeout, state) == ("video", media.VIDEO_WAIT_TIMEOUT_MS, "attached")
        assert media.VIDEO_WAIT_TIMEOUT_MS == 10_000

    async def test_no_media_element(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=None, body_text="an article with no video anywhere on it")
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "no_media_element"
        assert result["playing"] is False and result["verified"] is False
        assert result["current_time_s"] == 0.0
        assert result["duration_s"] is None
        assert result["volume"] == pytest.approx(0.15), "the requested level, not an invention"
        assert waits == [], "nothing is waited on when there is nothing to verify"

    async def test_autoplay_blocked(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(play_error=media.AUTOPLAY_BLOCKED_ERROR_NAME))
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "autoplay_blocked"
        assert result["playing"] is False and result["verified"] is False
        assert waits == [], "a refusal is never waited out and never retried"
        assert page.calls.count(("play", None)) == 1, "NO retry"

    async def test_any_other_play_rejection_is_a_plain_error(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(play_error="AbortError"))
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "error"

    @pytest.mark.parametrize(
        ("body", "raw", "status", "expected"),
        [
            ("please verify you are human to continue", {}, 200, "challenge"),
            ("sign in to continue", {"has_password_field": True}, 200, "challenge"),
            ("unusual traffic from your computer network", {}, 200, "challenge"),
            ("nothing here", {}, 503, "navigation_failed"),
        ],
    )
    async def test_a_wall_is_named_and_never_opened(
        self, waits, body: str, raw: dict[str, Any], status: int, expected: str
    ) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(body_text=body, raw=raw, video=_FakeVideo())
        _register_session(worker, _FakeBrowserSession(page, http_status=status))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == expected
        assert result["playing"] is False and result["verified"] is False
        assert ("play", None) not in page.calls, "a wall is reported, never played through"
        assert ("wait_for_selector", ("video", media.VIDEO_WAIT_TIMEOUT_MS, "attached")) not in [
            (n, a) for n, a in page.calls
        ]

    async def test_a_consent_host_is_a_consent_wall_not_a_challenge(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(body_text="Before you continue to YouTube")
        session = _FakeBrowserSession(page, final_url="https://consent.youtube.com/m?continue=x")
        _register_session(worker, session)
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "consent_wall"
        assert result["final_url"] == "https://consent.youtube.com/m?continue=x"

    async def test_a_page_that_never_yields_a_video_behind_consent_wording(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(
            video=None,
            body_text="Before you continue to YouTube. Accept all. Reject all.",
        )
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "consent_wall"

    async def test_consent_wording_alone_over_a_playing_video_is_not_a_wall(self, waits) -> None:
        """A cookie banner floating over a video that plays anyway is not a
        consent wall and must not be reported as one."""
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(
            body_text="Accept all cookies? — and the video below plays regardless",
            video=_FakeVideo(advance_per_read=2.0),
        )
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] is None and result["verified"] is True

    async def test_a_transport_failure_is_navigation_failed_not_a_retryable_error(
        self, waits
    ) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage()
        session = _FakeBrowserSession(
            page,
            navigate_error=BrowserError(
                ErrorClass.DEPENDENCY_UNAVAILABLE, "net::ERR_NAME_NOT_RESOLVED", retryable=True
            ),
        )
        _register_session(worker, session)
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["reason"] == "navigation_failed"
        assert result["final_url"] is None
        assert result["title"] == ""

    async def test_a_policy_refusal_stays_a_hard_error(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage()))
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.media_play", _play_payload(url="http://127.0.0.1:8080/x")
            )
        assert exc.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR

    async def test_a_video_that_does_not_move_is_an_honest_error(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(advance_per_read=0.1))
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_play", _play_payload())
        assert result["verified"] is False
        assert result["reason"] == "error"
        assert result["playing"] is True, "it says it is playing; it just did not move"

    async def test_the_advance_threshold_is_half_a_second(self, waits) -> None:
        assert media.VERIFY_MIN_ADVANCE_S == 0.5
        assert media.verified_from_readings(started_at=0.0, current_time=0.5, paused=False)
        assert not media.verified_from_readings(started_at=0.0, current_time=0.49, paused=False)
        assert not media.verified_from_readings(started_at=0.0, current_time=9.0, paused=True)
        assert not media.verified_from_readings(started_at=None, current_time=9.0, paused=False)

    @pytest.mark.parametrize(
        "payload",
        [
            {"url": ""},
            {"volume": 1.5},
            {"volume": -0.1},
            {"volume": "loud"},
            {"volume": True},
            {"verify_seconds": 0},
            {"verify_seconds": 11},
            {"verify_seconds": 2.5},
        ],
    )
    async def test_payload_bounds(self, waits, payload: dict[str, Any]) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=_FakeVideo())))
        with pytest.raises(BrowserError) as exc:
            await worker._execute("browser.media_play", _play_payload(**payload))
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR

    async def test_every_reason_the_contract_names_is_reachable(self) -> None:
        assert set(media.MEDIA_FAILURE_REASONS) == {
            "no_media_element",
            "autoplay_blocked",
            "challenge",
            "consent_wall",
            "navigation_failed",
            "error",
        }


# --------------------------------------------------------------------------- #
# 5. media_volume
# --------------------------------------------------------------------------- #


class TestMediaVolume:
    async def test_a_long_ramp_returns_as_soon_as_the_page_started_it(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(volume=0.15))
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute(
            "browser.media_volume",
            {"session_id": "alarm-abc", "level": 0.6, "ramp_seconds": 20},
        )
        assert result["applied"] is True
        assert result["level_from"] == pytest.approx(0.15)
        assert result["level_to"] == pytest.approx(0.6)
        assert result["ramp_seconds"] == pytest.approx(20)
        assert set(result) == {"applied", "level_from", "level_to", "ramp_seconds", "lifecycle"}
        assert waits == [], "a 20 s ramp does not hold the command open for 20 s"
        assert "setInterval" in page.ramp_scripts[0]

    async def test_a_short_ramp_is_awaited_so_the_greeting_can_follow(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=_FakeVideo(volume=0.6))))
        result = await worker._execute(
            "browser.media_volume",
            {"session_id": "alarm-abc", "level": 0.15, "ramp_seconds": 1},
        )
        assert result["applied"] is True
        assert waits == [1], "the duck completes before the op returns"
        assert media.RAMP_AWAIT_CEILING_S == 2.0

    async def test_no_media_element_is_reported_not_raised(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=None)))
        result = await worker._execute(
            "browser.media_volume",
            {"session_id": "alarm-abc", "level": 0.6, "ramp_seconds": 20},
        )
        assert result["applied"] is False
        assert result["level_from"] is None
        assert result["level_to"] == pytest.approx(0.6)

    @pytest.mark.parametrize(
        "payload",
        [
            {"level": 1.01},
            {"level": None},
            {"level": "half"},
            {"level": 0.5, "ramp_seconds": -1},
            {"level": 0.5, "ramp_seconds": 121},
            {"level": 0.5, "ramp_seconds": "slow"},
        ],
    )
    async def test_payload_bounds(self, waits, payload: dict[str, Any]) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=_FakeVideo())))
        with pytest.raises(BrowserError) as exc:
            await worker._execute("browser.media_volume", {"session_id": "alarm-abc", **payload})
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert media.MAX_RAMP_SECONDS == 120.0


# --------------------------------------------------------------------------- #
# 6. media_status
# --------------------------------------------------------------------------- #


class TestMediaStatus:
    async def test_it_reads_and_changes_nothing(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        video = _FakeVideo(volume=0.6, muted=False, paused=False, current_time=12.0, duration=250.0)
        page = _FakePage(title="Hans Zimmer - Time", video=video)
        _register_session(worker, _FakeBrowserSession(page))
        result = await worker._execute("browser.media_status", {"session_id": "alarm-abc"})
        assert result["present"] is True
        assert result["playing"] is True
        assert result["paused"] is False
        assert result["ended"] is False
        assert result["current_time_s"] == pytest.approx(13.0)
        assert result["duration_s"] == pytest.approx(250.0)
        assert result["volume"] == pytest.approx(0.6)
        assert result["muted"] is False
        assert result["title"] == "Hans Zimmer - Time"
        assert result["url"] == "https://example.com/watch"
        assert ("pause", None) not in page.calls
        assert ("play", None) not in page.calls

    async def test_an_ended_video_is_not_playing(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        video = _FakeVideo(paused=True, ended=True, current_time=250.0, advance_per_read=0.0)
        _register_session(worker, _FakeBrowserSession(_FakePage(video=video)))
        result = await worker._execute("browser.media_status", {"session_id": "alarm-abc"})
        assert result["ended"] is True and result["playing"] is False

    async def test_no_element_reports_absence_rather_than_a_fabricated_reading(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=None)))
        result = await worker._execute("browser.media_status", {"session_id": "alarm-abc"})
        assert result["present"] is False
        assert result["playing"] is False
        assert result["paused"] is True
        assert result["volume"] is None
        assert result["duration_s"] is None

    async def test_status_is_a_read_risk_class(self) -> None:
        assert policy.CAPABILITY_RISK_CLASS["browser.media_status"] == policy.RiskClass.READ


# --------------------------------------------------------------------------- #
# 7. media_stop
# --------------------------------------------------------------------------- #


class TestMediaStop:
    async def test_it_pauses_then_closes_the_session(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(paused=False))
        session = _FakeBrowserSession(page)
        _register_session(worker, session)
        result = await worker._execute("browser.media_stop", {"session_id": "alarm-abc"})
        assert result["stopped"] is True
        assert result["was_playing"] is True
        assert result["browser_pid_exited"] is True
        assert ("pause", None) in page.calls
        assert session.closed is True
        assert "alarm-abc" not in worker._sessions

    async def test_stopping_a_paused_session_says_so(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        session = _FakeBrowserSession(_FakePage(video=_FakeVideo(paused=True)))
        _register_session(worker, session)
        result = await worker._execute("browser.media_stop", {"session_id": "alarm-abc"})
        assert result["stopped"] is True and result["was_playing"] is False

    async def test_it_is_idempotent_for_a_session_that_is_already_gone(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        result = await worker._execute("browser.media_stop", {"session_id": "alarm-nope"})
        assert result == {
            "stopped": True,
            "was_playing": False,
            "session_id": "alarm-nope",
            "browser_pid_exited": True,
        }

    async def test_a_dead_page_still_gets_its_session_closed(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(paused=False))

        async def exploding_evaluate(script: str, arg: Any = None) -> Any:
            raise RuntimeError("Target page, context or browser has been closed")

        page.evaluate = exploding_evaluate  # type: ignore[assignment]
        session = _FakeBrowserSession(page)
        _register_session(worker, session)
        result = await worker._execute("browser.media_stop", {"session_id": "alarm-abc"})
        assert result["stopped"] is True and result["was_playing"] is False
        assert session.closed is True

    async def test_it_releases_the_alarm_profile_for_the_next_alarm(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        _register_session(worker, _FakeBrowserSession(_FakePage(video=_FakeVideo())))
        worker._persistent_profile_owner[media.ALARM_PROFILE] = "alarm-abc"
        await worker._execute("browser.media_stop", {"session_id": "alarm-abc"})
        assert media.ALARM_PROFILE not in worker._persistent_profile_owner


# --------------------------------------------------------------------------- #
# 8. the boundaries the whole family shares
# --------------------------------------------------------------------------- #


class TestMediaBoundaries:
    @pytest.mark.parametrize(
        ("capability", "payload"),
        [
            ("browser.media_play", {"url": "https://example.com/v", "volume": 0.2}),
            ("browser.media_volume", {"level": 0.5, "ramp_seconds": 1}),
            ("browser.media_status", {}),
            ("browser.media_stop", {}),
        ],
    )
    async def test_no_media_operation_runs_on_a_research_session(
        self, waits, capability: str, payload: dict[str, Any]
    ) -> None:
        """The structural half of "alarm audio never touches the owner's
        browsing": a media op aimed at a research session is refused before it
        can reach the page."""
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo())
        session = _FakeBrowserSession(page)
        _register_session(
            worker,
            session,
            session_id="task-1",
            session_kind=media.RESEARCH_SESSION_KIND,
            profile=media.RESEARCH_PROFILE,
        )
        with pytest.raises(BrowserError) as exc:
            await worker._execute(capability, {"session_id": "task-1", **payload})
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "session_kind='media'" in exc.value.message
        assert session.closed is False
        assert page.calls == []

    async def test_the_four_names_are_advertised_with_their_risk_classes(self) -> None:
        expected = {
            "browser.media_play": policy.RiskClass.NAVIGATE,
            "browser.media_volume": policy.RiskClass.NAVIGATE,
            "browser.media_status": policy.RiskClass.READ,
            "browser.media_stop": policy.RiskClass.NAVIGATE,
        }
        for name, risk in expected.items():
            assert name in policy.CAPABILITIES
            assert policy.CAPABILITY_RISK_CLASS[name] == risk
        assert policy.MEDIA_SESSION_CLASSES == frozenset(
            {policy.RiskClass.READ, policy.RiskClass.NAVIGATE}
        )

    async def test_a_session_without_navigate_may_not_play_or_stop(self, waits) -> None:
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        session = _FakeBrowserSession(_FakePage(video=_FakeVideo()))
        _register_session(worker, session, allowed=frozenset({policy.RiskClass.READ}))
        for capability, payload in (
            ("browser.media_play", {"url": "https://example.com/v", "volume": 0.2}),
            ("browser.media_stop", {}),
        ):
            with pytest.raises(BrowserError) as exc:
                await worker._execute(capability, {"session_id": "alarm-abc", **payload})
            assert exc.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
        assert session.closed is False

    async def test_no_media_result_can_carry_session_material(self, waits) -> None:
        """The forbidden-key scan (contract §6) applies unchanged; asserted on
        the real results rather than trusted."""
        from browser_agent.worker import redact_forbidden_keys

        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(advance_per_read=2.0))
        _register_session(worker, _FakeBrowserSession(page))
        results = [
            await worker._execute("browser.media_play", _play_payload()),
            await worker._execute(
                "browser.media_volume",
                {"session_id": "alarm-abc", "level": 0.6, "ramp_seconds": 20},
            ),
            await worker._execute("browser.media_status", {"session_id": "alarm-abc"}),
            await worker._execute("browser.media_stop", {"session_id": "alarm-abc"}),
        ]
        for result in results:
            _redacted, found = redact_forbidden_keys(result)
            assert found == []

    async def test_the_media_path_never_clicks_types_or_reads_storage(self, waits) -> None:
        """The fake page raises on click/fill/context, so this passing at all
        is the assertion — plus the source carries no click of its own."""
        worker = Worker(build_arg_parser().parse_args(["--data-dir", "."]))
        page = _FakePage(video=_FakeVideo(advance_per_read=2.0))
        _register_session(worker, _FakeBrowserSession(page))
        await worker._execute("browser.media_play", _play_payload())
        await worker._execute("browser.media_status", {"session_id": "alarm-abc"})
        for name, _args in page.calls:
            assert name in {"wait_for_selector", "extract", "volume", "play", "read"}
        source = Path(media.__file__).read_text(encoding="utf-8")
        for forbidden in (".click(", "document.cookie", "localStorage", "sessionStorage"):
            assert forbidden not in source


# ---------------- the two backends must answer the same surface (2026-09-10)


def test_both_backends_answer_every_attribute_the_worker_asks_a_backend_for() -> None:
    """``session_open`` treats a backend polymorphically; only one of them was complete.

    ``native_browser`` was defined on ``ManagedBackend`` alone, and the worker reads it
    right after ``connect()`` to report the browser version. The first REAL attach to the
    owner's Chrome therefore died with
    ``AttributeError: 'ExistingSessionBackend' object has no attribute 'native_browser'``
    -- in production, on the owner's machine, after four green worker tests.

    Those tests could not catch it: their attach fails at ``connect()`` on a port with
    nothing behind it, so the line after ``connect()`` was never executed. And a fake
    carrying the attribute would have been worse than useless, passing while the real
    class failed.

    So the list of attributes is READ OUT OF THE WORKER'S OWN SOURCE rather than restated
    here -- a name the worker starts using tomorrow is checked tomorrow, without anyone
    remembering to add it.
    """
    import ast

    from browser_agent import worker as worker_module
    from browser_agent.backends import ExistingSessionBackend, ManagedBackend

    tree = ast.parse(pathlib.Path(worker_module.__file__).read_text(encoding="utf-8"))
    asked: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "backend"
        ):
            asked.add(node.attr)
    assert asked, "no backend.<attr> access found; this test would prove nothing"

    # Three names belong to LAUNCHING and the worker only touches them on the branch
    # that launched something. An attached browser was not started by this worker, owns
    # no profile directory to relaunch and joins no Windows job object, so it answers
    # none of them and does not need to.
    launch_only = {"breaker", "job_object_assigned", "last_launch_kind"}
    # INSTANCES, not classes: several of these are set in __init__, so a class-level
    # hasattr would report them missing on both and prove nothing about either.
    from browser_agent.enrollment import BrowserEnrollment

    backends = (
        (ManagedBackend(), asked),
        (
            ExistingSessionBackend(
                BrowserEnrollment.cdp_loopback("http://127.0.0.1:19222", name="surface-test")
            ),
            asked - launch_only,
        ),
    )
    for backend, expected in backends:
        missing = sorted(name for name in expected if not hasattr(backend, name))
        assert not missing, (
            f"{type(backend).__name__} cannot answer {missing}, "
            "which worker.py asks a backend for"
        )


def test_the_attached_backend_reports_its_browser_the_way_session_open_reads_it() -> None:
    """The exact expression that crashed, on the real class, before it is connected:
    ``backend.native_browser`` must be readable and simply None."""
    from browser_agent.backends import ExistingSessionBackend
    from browser_agent.enrollment import BrowserEnrollment

    backend = ExistingSessionBackend(
        BrowserEnrollment.cdp_loopback("http://127.0.0.1:19222", name="test")
    )

    assert backend.native_browser is None

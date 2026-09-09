"""Latest News Mode's dedicated browser profile (v1.3, BROWSER_CAPABILITIES.md §1/§2).

News playback needs its OWN context, the same way M18.3 gave the wake alarm its own
(``tests/unit/test_media_ops.py``'s ``TestAlarmProfileIsolation``) — a news video must
never be able to land in the research browser (profile contention with a live research
run) and must never be able to interrupt or replace the owner's wake song (the alarm
profile). This file pins the THIRD profile the same way that one pins the second: a
derived sibling directory, checked against both other persistent profiles at startup,
and a real ``session_open`` that reaches it with ``session_kind="media"`` — the same
verified ``media_play``/``media_status``/``media_stop`` surface the alarm already has,
reused rather than re-invented (media.py's own docstring: playback is verified, never
assumed).

NO BROWSER IS LAUNCHED HERE: a recording double stands in for ``ManagedBackend``, the
same discipline ``test_media_ops.py`` uses and ``tests/test_test_isolation_guards.py``
holds as the owner's standing rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from browser_agent import media
from browser_agent.detect import BrowserInfo
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import Worker, build_arg_parser


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


class _FakeTab:
    index = 0
    url = "https://example.com"
    title = ""
    is_current = True


class _FakePage:
    url = "https://www.youtube.com/watch?v=abc123"
    title = ""


class _RecordingBackend:
    """Stands in for ManagedBackend at session_open (test_media_ops.py's own double)."""

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


# --------------------------------------------------------------------------- #
# 1. profile derivation and isolation (pure)
# --------------------------------------------------------------------------- #


class TestNewsProfileIsolation:
    def test_the_news_profile_is_a_sibling_of_the_research_profile_never_it(self) -> None:
        research = Path("C:/data/pagentos/browser/profile")
        news = media.news_profile_dir_for(research)
        assert news != research
        assert news.parent == research.parent
        assert news.name == "profile-news"

    def test_the_news_profile_is_distinct_from_the_alarm_profile_too(self) -> None:
        research = Path("C:/data/pagentos/browser/profile")
        news = media.news_profile_dir_for(research)
        alarm = media.alarm_profile_dir_for(research)
        assert news != alarm

    def test_a_worker_resolves_three_distinct_persistent_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        research = worker._persistent_profile_dir(media.RESEARCH_PROFILE)
        alarm = worker._persistent_profile_dir(media.ALARM_PROFILE)
        news = worker._persistent_profile_dir(media.NEWS_PROFILE)
        assert research is not None and alarm is not None and news is not None
        assert len({research, alarm, news}) == 3
        assert not news.is_relative_to(research)
        assert not news.is_relative_to(alarm)

    def test_a_news_profile_equal_to_or_inside_the_research_profile_is_refused(self) -> None:
        research = Path("C:/data/profile")
        alarm = Path("C:/data/profile-alarm")
        for bad in (research, research / "nested"):
            with pytest.raises(BrowserError) as exc:
                media.require_distinct_news_profile(bad, research, alarm)
            assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
            assert "separate from the research profile" in exc.value.message

    def test_a_news_profile_equal_to_or_inside_the_alarm_profile_is_refused(self) -> None:
        research = Path("C:/data/profile")
        alarm = Path("C:/data/profile-alarm")
        for bad in (alarm, alarm / "nested"):
            with pytest.raises(BrowserError) as exc:
                media.require_distinct_news_profile(bad, research, alarm)
            assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
            assert "separate from the alarm profile" in exc.value.message

    def test_configuring_the_news_profile_onto_the_research_one_fails_at_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile"
        with pytest.raises(BrowserError) as exc:
            _make_worker(
                tmp_path, monkeypatch, profile_dir=str(profile), news_profile_dir=str(profile)
            )
        assert "separate from the research profile" in exc.value.message

    def test_configuring_the_news_profile_onto_the_alarm_one_fails_at_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alarm = tmp_path / "profile-alarm"
        with pytest.raises(BrowserError) as exc:
            _make_worker(
                tmp_path, monkeypatch, alarm_profile_dir=str(alarm), news_profile_dir=str(alarm)
            )
        assert "separate from the alarm profile" in exc.value.message

    def test_the_news_profile_can_never_be_the_owners_own_chrome(self) -> None:
        """Same real-profile guard the alarm profile already gets."""
        from browser_agent.backends import ManagedBackend

        owner_chrome = Path(r"C:\Users\owner\AppData\Local\Google\Chrome\User Data")
        with pytest.raises(BrowserError) as exc:
            ManagedBackend(profile_dir=owner_chrome / "news")
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "real browser profile tree" in exc.value.message


# --------------------------------------------------------------------------- #
# 2. session_open with profile="news"
# --------------------------------------------------------------------------- #


class TestNewsSessionOpen:
    async def test_a_media_session_launches_on_the_news_profile_with_the_autoplay_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        result = await worker._execute(
            "browser.session_open",
            {"session_id": "news-ctx-1", "profile": "news", "session_kind": "media"},
        )
        assert result["created"] is True
        assert result["profile"] == "news"
        assert result["session_kind"] == "media"
        backend = recording_backend.instances[-1]
        assert media.AUTOPLAY_POLICY_ARG in (backend.kwargs.get("browser_args") or [])
        # The news profile directory is neither the research nor the alarm one.
        news_dir = backend.kwargs.get("profile_dir")
        assert news_dir is not None
        assert Path(news_dir).name == "profile-news"

    async def test_the_news_and_alarm_profiles_coexist_as_separate_owned_browsers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """The M13 one-owner-per-persistent-profile guard is per PROFILE, not global: an
        open alarm session must never block a news session (and vice versa) — only a
        SECOND session on the SAME profile is refused."""
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        alarm_result = await worker._execute(
            "browser.session_open",
            {"session_id": "alarm-1", "profile": "alarm", "session_kind": "media"},
        )
        news_result = await worker._execute(
            "browser.session_open",
            {"session_id": "news-ctx-1", "profile": "news", "session_kind": "media"},
        )
        assert alarm_result["created"] is True
        assert news_result["created"] is True
        assert len(recording_backend.instances) == 2

    async def test_a_second_news_session_id_while_one_owns_the_news_profile_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute(
            "browser.session_open",
            {"session_id": "news-ctx-1", "profile": "news", "session_kind": "media"},
        )
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "news-ctx-2", "profile": "news", "session_kind": "media"},
            )
        assert exc.value.error_class == ErrorClass.BROWSER_LIFECYCLE_VIOLATION

    async def test_a_research_session_may_not_use_the_news_profile_for_media(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """The news profile is for the media surface, exactly like alarm — a media
        session may still never land on the research profile (existing guard, unchanged)."""
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "r-1", "profile": "research", "session_kind": "media"},
            )
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "may not use the research profile" in exc.value.message

    async def test_a_news_session_must_be_a_media_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """The security review's M3, at the door rather than one step past it.

        This test used to open ``{profile: "news", session_kind: "research"}``
        SUCCESSFULLY and only check that a later media_play refused — which meant a
        caller could hold a general-purpose, persistent browsing session on the
        dedicated news Chrome profile, while `packages/protocol/BROWSER_CAPABILITIES.md`
        promised that profile was "for Latest News Mode playback only". Nothing that
        ships did it (`open_latest_news` always sends "media"), but a governance claim
        nobody enforces stops being true quietly.

        `alarm` has had this bidirectional guard since M18.3; `news` has it now, and the
        unimplemented discovery tiers can widen it deliberately when they are built."""
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.session_open",
                {"session_id": "news-research-1", "profile": "news", "session_kind": "research"},
            )
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "playback only" in exc.value.message

    async def test_media_ops_name_news_as_a_fix_not_only_alarm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_backend
    ) -> None:
        """The half of the old test that is still worth having: a media op aimed at a
        non-media session names BOTH profiles that can carry one, so a Cloud Core that
        forgot ``session_kind`` on a news session gets a contract error it can read
        rather than a message that only mentions alarms."""
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        await worker._execute(
            "browser.session_open",
            {"session_id": "iso-research-1", "profile": "isolated", "session_kind": "research"},
        )
        with pytest.raises(BrowserError) as exc:
            await worker._execute(
                "browser.media_play",
                {"session_id": "iso-research-1", "url": "https://www.youtube.com/watch?v=x"},
            )
        assert exc.value.error_class == ErrorClass.VALIDATION_ERROR
        assert "'alarm' or 'news'" in exc.value.message

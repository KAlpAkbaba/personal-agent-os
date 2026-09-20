"""The owner's own tabs are not the agent's tab budget (ADR-0187).

Production 2026-09-20, the first run whose search actually worked: 236 candidates
discovered, and then EVERY page failed with ``browser_lifecycle_violation`` —
``fetch_evidence: open tab count 7 exceeds max_tabs=6``. The tabs it counted were the
owner's: since ADR-0183 the research session is ATTACHED to the browser they are using, and
the lifecycle budget — written when the worker launched its own empty Chrome, where every
tab really was its own — counted all of them.

No browser is launched here: a fake session answers ``list_tabs`` with the owner's tabs.
"""

from __future__ import annotations

from typing import Any

import pytest

from browser_agent import media, policy
from browser_agent.errors import BrowserError
from browser_agent.worker import SessionState, Worker, build_arg_parser

_RAW: dict[str, Any] = {
    "title": "Yapay zeka haberleri",
    "heading_text": "Yapay zeka haberleri",
    "has_password_field": False,
    "html_lang": "tr",
    "canonical_url": "https://ornek.com/haber",
    "description": "Yapay zeka haberleri",
    "og_site_name": "Ornek",
    "article_published_time": "2026-09-20T12:00:00Z",
    "article_modified_time": None,
    "meta_date": None,
    "meta_author": None,
    "time_datetime": None,
    "links": [],
    "json_ld_raw": [],
}


class _FakeTab:
    def __init__(self, index: int, *, is_current: bool = False) -> None:
        self.index = index
        self.is_current = is_current
        self.url = f"https://owner-tab-{index}.example"
        self.title = f"Sekme {index}"


class _FakePage:
    url = "https://ornek.com/haber"

    async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeBackend:
    current_page = _FakePage()


class _FakeResponse:
    status = 200


class _FakeBrowserSession:
    """The owner's Chrome: it already has tabs, and it keeps count of ours."""

    def __init__(self, owner_tabs: int) -> None:
        self._owner_tabs = owner_tabs
        self._extra = 0
        self.backend = _FakeBackend()
        self.opened = 0
        self.closed = 0

    async def list_tabs(self) -> list[_FakeTab]:
        total = self._owner_tabs + self._extra
        return [_FakeTab(i, is_current=i == 0) for i in range(total)]

    async def new_tab(self, _url: str | None = None) -> int:
        self.opened += 1
        self._extra += 1
        return self._owner_tabs + self._extra - 1

    async def close_tab(self, _index: int) -> None:
        self.closed += 1
        self._extra = max(0, self._extra - 1)

    async def select_tab(self, _index: int) -> None:
        return None

    async def navigate(self, _url: str, *, timeout_ms: int = 0) -> _FakeResponse:
        return _FakeResponse()

    async def page_text(self) -> str:
        return "Yapay zeka haberleri. " * 40


@pytest.fixture()
def worker(tmp_path) -> Worker:
    return Worker(build_arg_parser().parse_args(["--data-dir", str(tmp_path), "--headless"]))


def _register(
    worker: Worker,
    session: _FakeBrowserSession,
    *,
    profile: str,
    session_id: str = "research-1",
    max_tabs: int = 6,
) -> SessionState:
    state = SessionState(
        session_id=session_id,
        browser_session=session,  # type: ignore[arg-type]
        backend=session.backend,  # type: ignore[arg-type]
        policy_allowed=frozenset({policy.RiskClass.READ, policy.RiskClass.NAVIGATE}),
        visible=True,
        channel="chrome",
        browser_version="153.0.0.0",
        profile=profile,
        session_uid="uid-1",
        profile_dir=None,
        last_used=0.0,
        max_tabs=max_tabs,
    )
    worker._sessions[session_id] = state
    return state


@pytest.fixture(autouse=True)
def _page_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page-reading helpers are not what this file is about."""
    from browser_agent import worker as worker_module

    async def _raw(_page: Any) -> Any:
        from browser_agent.extraction import RawPageData

        return RawPageData(**_RAW)

    async def _primary(_page: Any) -> str:
        return "Yapay zeka haberleri. " * 40

    monkeypatch.setattr(worker_module, "read_raw_page_data", _raw)
    monkeypatch.setattr(worker_module, "read_primary_text", _primary)
    # The destination guard is a different subject: this file is about tab counting, and
    # the fake host has no DNS.
    from browser_agent import destination

    monkeypatch.setattr(destination, "_default_resolver", lambda _host: ["93.184.216.34"])


@pytest.mark.asyncio
async def test_the_owners_open_tabs_do_not_spend_the_agents_budget(worker: Worker) -> None:
    session = _FakeBrowserSession(owner_tabs=9)
    _register(worker, session, profile=media.OWNER_PROFILE)

    result = await worker._op_fetch_evidence(
        worker._sessions["research-1"],
        {"url": "https://ornek.com/haber", "tab": "new", "query": "q", "source_class": "news"},
    )

    assert result["url"] == "https://ornek.com/haber"
    assert session.opened == 1 and session.closed == 1, "our own tab is opened and closed again"


@pytest.mark.asyncio
async def test_the_agents_own_tabs_are_still_budgeted_on_its_own_browser(worker: Worker) -> None:
    """On the worker's OWN profile every tab really is its own, and the guard is unchanged."""
    session = _FakeBrowserSession(owner_tabs=9)
    _register(worker, session, profile=media.RESEARCH_PROFILE, session_id="research-2")

    with pytest.raises(BrowserError) as excinfo:
        await worker._op_fetch_evidence(
            worker._sessions["research-2"],
            {"url": "https://ornek.com/haber", "tab": "new", "query": "q", "source_class": "news"},
        )

    assert "max_tabs" in str(excinfo.value)
    assert session.opened == 0, "a refused fetch must not open anything"


@pytest.mark.asyncio
async def test_an_attached_session_still_refuses_to_hoard_its_own_tabs(worker: Worker) -> None:
    """The budget is not removed for an attached session - it counts OUR tabs. A session
    that somehow holds max_tabs of its own refuses the next one, exactly as before."""
    session = _FakeBrowserSession(owner_tabs=2)
    state = _register(worker, session, profile=media.OWNER_PROFILE, session_id="research-3")
    state.own_tabs = state.max_tabs

    with pytest.raises(BrowserError) as excinfo:
        await worker._op_fetch_evidence(
            state,
            {"url": "https://ornek.com/haber", "tab": "new", "query": "q", "source_class": "news"},
        )

    assert "max_tabs" in str(excinfo.value)


@pytest.mark.asyncio
async def test_opening_a_tab_by_hand_is_counted_and_giving_it_back_is_too(worker: Worker) -> None:
    """``browser.tab_new`` is the other way this session can hold a tab, so it keeps the
    same books - otherwise the budget would be spent by fetches alone."""
    session = _FakeBrowserSession(owner_tabs=5)
    state = _register(worker, session, profile=media.OWNER_PROFILE, session_id="research-4")

    for _ in range(state.max_tabs):
        await worker._op_tab_new(state, {"url": None})
    assert state.own_tabs == state.max_tabs

    with pytest.raises(BrowserError):
        await worker._op_tab_new(state, {"url": None})

    await worker._op_tab_close(state, {"index": 0})
    assert state.own_tabs == state.max_tabs - 1
    await worker._op_tab_new(state, {"url": None})  # the freed slot is usable again

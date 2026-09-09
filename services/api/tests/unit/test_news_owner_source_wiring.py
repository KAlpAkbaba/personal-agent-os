"""Configuring the owner's real news source, and the three defects that surfaced doing it.

The owner gave `https://www.youtube.com/showanahaber` — a legacy custom URL, not a handle
and not a `/channel/UC…` link, so the only honest way to bind it is to ask YouTube what
channel it is. Doing that for real found three things this file now holds:

1. **Nothing ever passed a real `fetch_page`.** The seam existed from the start and the REST
   routes did not fill it, so every handle and custom URL an owner could give was left
   `needs_identity` forever. Wired now, on the owner's own configuration call only.
2. **The channel feed answers 404 to an honest client under rate limiting**, then succeeds
   seconds later — measured against two different channels. Retried, bounded; NOT worked
   around with a browser User-Agent, which would be evading an anti-bot control.
3. **The `live` marker's description promised something the configuration did not keep.**
   It reads "never run by default CI", and CI ran `-m "not integration"`, which does not
   exclude it — so every CI run made a real YouTube call against an endpoint we had just
   measured to be intermittently 404. A claim in a docstring is not a control.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.news import provider as provider_module
from app.news.page_fetch import MAX_PAGE_BYTES, USER_AGENT, PageFetchError, fetch_channel_page
from app.news.provider import (
    FEED_ATTEMPTS,
    FEED_RETRY_BACKOFF_S,
    MAX_FEED_RETRY_DELAY_S,
    ProviderUnavailableError,
    fetch_feed_bytes,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "workflows" / "ci.yml").is_file():
            return parent
    raise AssertionError("the CI workflow was not found above this test")


# ------------------------------------------------- the seam nothing ever filled


def test_the_rest_routes_pass_a_real_fetcher() -> None:
    """Identity resolution is the ONE thing this family refuses to guess — which meant that
    until something passed a real `fetch_page`, it refused to resolve anything an owner
    could realistically paste. Read from the source: both the create and update routes must
    hand one over."""
    from app.news import routes

    text = Path(routes.__file__).read_text(encoding="utf-8")
    assert text.count("fetch_page=fetch_channel_page") == 2, (
        "create and update must both resolve identities for real; a route that omits the "
        "fetcher silently leaves every handle and custom URL needs_identity"
    )


def test_the_fetcher_is_not_dressed_up_as_a_browser() -> None:
    """A User-Agent chosen to look like Chrome would be evading an anti-bot control, which
    the owner's directive forbids and which is not even necessary: an honest client gets a
    200 on the retry. This is worth a test because the temptation appears exactly when a
    fetch starts failing."""
    assert "Mozilla" not in USER_AGENT
    assert "Chrome" not in USER_AGENT
    assert "PersonalAgentOS" in USER_AGENT


def test_a_redirect_off_youtube_is_refused_after_the_fact(monkeypatch) -> None:
    """The host is checked BEFORE the call; this is the check after. A redirect can land
    anywhere, and the first check said nothing about where — that is the SSRF the M26 news
    review named when the host test was still a substring match."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.youtube.com":
            return httpx.Response(302, headers={"Location": "https://evil.example/page"})
        return httpx.Response(200, text="<html>attacker</html>")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs.pop("max_redirects", None)
        return real_client(transport=transport, follow_redirects=True, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)

    with pytest.raises(PageFetchError, match="redirected off YouTube"):
        fetch_channel_page("https://www.youtube.com/showanahaber")


def test_the_page_fetch_is_bounded() -> None:
    """A channel page is large — the owner's real one measured 2.1 MB — but not unbounded."""
    assert 0 < MAX_PAGE_BYTES <= 16 * 1024 * 1024


# ---------------------------------------------- the feed's transient 404, retried


def test_a_transient_feed_failure_is_retried_and_then_succeeds(monkeypatch) -> None:
    """Measured live: the provider's own unspoofed request 404'd once and returned 15
    candidates on the next attempt, seconds later. Without a retry the owner is told there
    is no news because a rate limiter said 404 once."""
    monkeypatch.setattr(provider_module, "FEED_RETRY_BACKOFF_S", 0.0)
    calls: list[int] = []

    def flaky(url: str, *, timeout_s: float, max_bytes: int) -> bytes:
        calls.append(1)
        if len(calls) < 2:
            raise ProviderUnavailableError("channel feed request failed: 404 Not Found")
        return b"<feed/>"

    monkeypatch.setattr(provider_module, "_fetch_feed_once", flaky)
    assert (
        fetch_feed_bytes("https://example.invalid/f", timeout_s=1.0, max_bytes=1024) == b"<feed/>"
    )
    assert len(calls) == 2


def test_a_persistent_feed_failure_is_still_reported_honestly(monkeypatch) -> None:
    """The other side: a channel that genuinely is not there must NOT be retried for ever,
    and must still fail. A bounded retry is a courtesy to a rate limiter, not a way to
    pretend a wrong channel id is right."""
    monkeypatch.setattr(provider_module, "FEED_RETRY_BACKOFF_S", 0.0)
    calls: list[int] = []

    def always_404(url: str, *, timeout_s: float, max_bytes: int) -> bytes:
        calls.append(1)
        raise ProviderUnavailableError("channel feed request failed: 404 Not Found")

    monkeypatch.setattr(provider_module, "_fetch_feed_once", always_404)
    with pytest.raises(ProviderUnavailableError):
        fetch_feed_bytes("https://example.invalid/f", timeout_s=1.0, max_bytes=1024)
    assert len(calls) == FEED_ATTEMPTS


# ------------------------------- the retry budget, held to a bound a voice turn can absorb


def test_the_retry_budget_is_bounded_by_what_a_spoken_answer_can_wait_for() -> None:
    """The declared bound and the schedule that produces it must be the same number.

    `MAX_FEED_RETRY_DELAY_S` is what the comment above `FEED_ATTEMPTS` promises the owner:
    at most this many seconds of silence added to a voice turn before the news answer or
    the honest refusal. Raising the attempts without raising the bound - or raising the
    bound past what a spoken turn can absorb - should fail here rather than in the owner's
    ears.
    """
    scheduled = sum(FEED_RETRY_BACKOFF_S * n for n in range(1, FEED_ATTEMPTS))
    assert scheduled == MAX_FEED_RETRY_DELAY_S
    assert MAX_FEED_RETRY_DELAY_S <= 4.0


def test_the_budget_survives_the_flap_that_was_actually_measured(monkeypatch) -> None:
    """Two refusals then an answer - the shape the endpoint produced on 2026-09-09.

    The previous budget was two attempts, which gives up exactly one request before this
    succeeds. That is not a hypothetical: the production run reported the provider
    unavailable, and asking again seconds later returned fifteen candidates.
    """
    monkeypatch.setattr(provider_module, "FEED_RETRY_BACKOFF_S", 0.0)
    answers = [
        ProviderUnavailableError("channel feed request failed: 404 Not Found"),
        ProviderUnavailableError("channel feed request failed: 500 Internal Server Error"),
        b"<feed/>",
    ]
    calls: list[int] = []

    def flapping(url: str, *, timeout_s: float, max_bytes: int) -> bytes:
        calls.append(1)
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(provider_module, "_fetch_feed_once", flapping)
    assert (
        fetch_feed_bytes("https://example.invalid/f", timeout_s=1.0, max_bytes=1024) == b"<feed/>"
    )
    assert len(calls) == 3


# ------------------------------------- the marker whose description CI did not keep


def test_ci_excludes_the_live_marker_its_own_description_promises_to_exclude() -> None:
    """`pyproject.toml` says of the `live` marker: "never run by default CI". CI ran
    `-m "not integration"`, which does not exclude it — so every CI run made a real call to
    a third-party endpoint that we measured returning intermittent 404s. A claim in a
    docstring is not a control; this test is the control.
    """
    root = _repo_root()
    pyproject = (root / "services" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    assert "never run by default CI" in pyproject, (
        "the marker no longer makes that promise - update this test with the new contract"
    )

    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    default_runs = re.findall(r'uv run pytest -m "([^"]+)" -q', workflow)
    assert default_runs, "no marker-filtered pytest invocation found in CI - has it moved?"
    unit_runs = [expr for expr in default_runs if expr.startswith("not integration")]
    assert unit_runs, f"no default unit run among {default_runs}"
    for expr in unit_runs:
        assert "not live" in expr, (
            f'CI runs -m "{expr}", which still selects the live tests the marker says are '
            f"never run by default - a real third-party call on every push"
        )

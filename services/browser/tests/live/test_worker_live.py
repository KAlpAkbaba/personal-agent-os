"""M13 live qualification: real Chrome, real network (opt-in, ``-m live``).

Not part of the default/CI run (excluded by pyproject ``addopts``). These hit
real public sites and a real search engine through the real installed Google
Chrome (channel ``chrome``) — inherently non-deterministic (site markup,
CAPTCHA/rate-limit walls, network conditions can all change outcomes), so the
task is to run them and report the actual outcome honestly, not to make them
pass at any cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_agent.worker import Worker, build_arg_parser

pytestmark = pytest.mark.live


@pytest.fixture()
async def live_worker(tmp_path: Path):
    data_dir = tmp_path / "live-worker-data"
    data_dir.mkdir()
    args = build_arg_parser().parse_args(
        # Headful is the production posture (contract §2 `visible: true`) and the one
        # that works: on 2026-09-03 the same public newsroom answered headless Chrome
        # with 403 and headful Chrome with 200, and every engine served results headful.
        ["--data-dir", str(data_dir), "--channel", "chrome", "--visible"]
    )
    w = Worker(args)
    await w._print_hello()
    try:
        yield w
    finally:
        await w._close_all_sessions()


async def test_fetch_evidence_openai_news_has_metadata(live_worker: Worker) -> None:
    opened = await live_worker._execute(
        "browser.session_open",
        {
            "session_id": "live1",
            "profile": "research",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
        },
    )
    assert opened["created"] is True

    result = await live_worker._execute(
        "browser.fetch_evidence",
        {
            "session_id": "live1",
            "url": "https://openai.com/news/",
            "query": "AI agents",
            "source_class": "official",
            "timeout_ms": 30000,
        },
    )
    print(
        f"\n[live] fetch_evidence page_kind={result['page_kind']} "
        f"http_status={result['http_status']} text_chars={result['text_chars']} "
        f"metadata={result['metadata']}"
    )

    assert result["page_kind"] == "ok"
    assert result["text_chars"] > 0
    assert result["excerpt"]
    # At least one metadata field should be populated on a real, well-formed
    # publisher page (never guessed — whichever the page actually declares).
    metadata = result["metadata"]
    assert any(metadata[k] is not None for k in ("publisher", "language", "canonical_url"))

    await live_worker._execute("browser.session_close", {"session_id": "live1"})


async def test_search_ai_agents_engine_auto_returns_results(live_worker: Worker) -> None:
    opened = await live_worker._execute(
        "browser.session_open",
        {
            "session_id": "live2",
            "profile": "research",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
        },
    )
    assert opened["created"] is True

    outcome = await live_worker._execute(
        "browser.search",
        {"session_id": "live2", "query": "AI agents", "engine": "auto", "max_results": 10},
    )
    print(
        f"\n[live] search engine={outcome['engine']} page_kind={outcome['page_kind']} "
        f"result_count={len(outcome['results'])}"
    )
    for r in outcome["results"][:5]:
        print(f"  - {r['title']!r} {r['url']}")

    assert len(outcome["results"]) >= 3
    for r in outcome["results"]:
        assert r["url"].startswith("http")
        assert r["title"]

    await live_worker._execute("browser.session_close", {"session_id": "live2"})


async def test_google_search_ui_handoff_mode_reports_shape_honestly(live_worker: Worker) -> None:
    """Real Google, through the real UI, in owner-handoff mode (contract §3a).

    Google may answer this address with its unusual-traffic interstitial —
    that is a valid, expected outcome here (``state=waiting_for_owner_
    verification``), not a test failure: this asserts the RESULT SHAPE only,
    then reports what actually happened. A clean result (``state=ok``,
    ``path=google_ui``) is just as valid an outcome to report.
    """
    opened = await live_worker._execute(
        "browser.session_open",
        {
            "session_id": "live3",
            "profile": "research",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
        },
    )
    assert opened["created"] is True

    outcome = await live_worker._execute(
        "browser.search",
        {
            "session_id": "live3",
            "query": "AI agents",
            "engine": "google",
            "max_results": 10,
            "interstitial": "handoff",
        },
    )
    print(
        f"\n[live] google UI handoff: state={outcome['state']} path={outcome['path']} "
        f"provider={outcome['provider']} page_kind={outcome['page_kind']} "
        f"result_count={outcome['result_count']}"
    )

    assert outcome["schema_version"] == 3
    assert outcome["requested_provider"] == "google"
    assert outcome["state"] in ("ok", "waiting_for_owner_verification")
    if outcome["state"] == "waiting_for_owner_verification":
        assert outcome["path"] == "handoff_pending"
        assert outcome["provider"] is None
        assert outcome["results"] == []
        assert outcome["page_kind"] in ("captcha", "consent")
        assert outcome["verification_url"]
        print(f"[live] owner verification would be needed at {outcome['verification_url']}")
    else:
        assert outcome["path"] in ("google_ui", "google_url")
        assert outcome["provider"] == "google"
        for r in outcome["results"]:
            assert r["url"].startswith("http")
            assert r["title"]

    await live_worker._execute("browser.session_close", {"session_id": "live3"})

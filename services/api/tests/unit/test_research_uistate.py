"""Regression: a research run is visible on the UI-state bus while it runs.

Until 2026-09-05 the only research event on the bus was published at the ranking
stage, with a constant ``intensity=0.7``. So the Core showed nothing at all while
a real job spent minutes discovering and fetching sources, and the one number it
did send was not a measurement of anything (ADR-0056 addendum 1 #2).

These tests assert the events off a real publisher rather than asserting a
function was called, for the same reason ``test_experience_uistate_signal.py``
does: a mock would have passed against the silent version too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.research import browser_activities as ba
from app.research import destination
from app.research.models import (
    STAGE_DISCOVERING,
    STAGE_FETCHING,
    STAGE_RANKING,
    STAGE_SYNTHESIZING,
)
from app.uistate import UiState, UiStatePublisher, set_publisher
from app.uistate.publisher import get_publisher
from tests.unit import test_research_browser_activities as activity_tests

# Re-bound rather than imported: `from ... import db_url` collides with the
# fixture argument of the same name in every test below (ruff F811). The DB and
# seeding fixtures are the ones the real activity suite already uses, so this
# module exercises the same shapes rather than a convenient parallel set.
_insert_candidate = activity_tests._insert_candidate
_seed_evidence = activity_tests._seed_evidence
_permissive_destination = activity_tests._permissive_destination
_usable_evidence = activity_tests._usable_evidence
_rank_ok = activity_tests._rank_ok
_window_ok = activity_tests._window_ok
TOPIC = activity_tests.TOPIC
db_url = activity_tests.db_url
task_id = activity_tests.task_id

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
PUBLIC_IP = "93.184.216.34"


@pytest.fixture()
def bus():
    previous = get_publisher()
    publisher = UiStatePublisher()
    set_publisher(publisher)
    yield publisher
    set_publisher(previous)


def _research_events(publisher: UiStatePublisher):
    return [e for e in publisher.tail() if e.state is UiState.RESEARCHING]


def test_discovery_reaches_the_bus(monkeypatch, db_url, task_id: str, bus) -> None:
    from app.research import discovery

    monkeypatch.setattr(
        discovery,
        "fetch_hn",
        lambda query, *, window_start, max_results=10, timeout_s=10.0: [
            discovery.DiscoveredCandidate(
                url="https://news.ycombinator.com/item?id=1",
                title="t",
                publisher="Hacker News",
                discovered_by="hn",
                query_id="technical:0",
            )
        ],
    )
    ba.discover_activity(
        task_id, str(uuid.uuid4()), "technical:0", "ai agents", "technical", NOW.isoformat()
    )

    (event,) = _research_events(bus)
    assert event.subsystem == "research"
    assert event.status == STAGE_DISCOVERING
    assert event.task_id == task_id
    # Counted, not estimated: one candidate was inserted and one now exists.
    assert event.metadata == {"candidates": 1, "added": 1}
    # Nothing knows the length of a discovery pass, so no progress is claimed.
    assert event.progress is None


def test_fetch_targets_publishes_the_counts_it_actually_has(
    monkeypatch, db_url, task_id: str, bus
) -> None:
    _insert_candidate(db_url, task_id, url="https://public.example.com/a")
    _insert_candidate(db_url, task_id, url="https://public.example.com/b")
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: [PUBLIC_IP])

    ba.fetch_targets_activity(task_id, 1, "ai agents")

    (event,) = _research_events(bus)
    assert event.status == STAGE_FETCHING
    # M18.2 (ADR-0068, owner rule 7): the bus carries coarse, fixed Turkish labels
    # only - never the raw topic text. Nothing has actually been fetched at this
    # point in the stage (fetch_targets_activity only plans the wave), so no coarse
    # phrase applies yet; fetch_activity publishes "N güvenilir kaynak incelendi"
    # itself once a source actually completes (see test_research_browser_activities).
    assert event.label is None
    # One target, because the owner's budget was one - and two candidates, because
    # two were found. Both are the real figures, and they differ on purpose.
    assert event.metadata == {"targets": 1, "candidates": 2}
    assert event.progress is None


def test_a_rejected_target_is_not_counted_as_one(monkeypatch, db_url, task_id: str, bus) -> None:
    _insert_candidate(db_url, task_id, url="https://internal.example.com/b")
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: ["127.0.0.1"])

    ba.fetch_targets_activity(task_id, 10)

    (event,) = _research_events(bus)
    assert event.metadata["targets"] == 0
    assert event.metadata["candidates"] == 1


def test_discovery_label_is_the_coarse_turkish_phrase(
    monkeypatch, db_url, task_id: str, bus
) -> None:
    """M18.2 (ADR-0068, owner rule 7): the bus never carries the raw topic or
    crawler vocabulary - just one of a fixed, small set of Turkish phrases."""
    from app.research import discovery

    monkeypatch.setattr(
        discovery,
        "fetch_hn",
        lambda query, *, window_start, max_results=10, timeout_s=10.0: [],
    )
    ba.discover_activity(
        task_id, str(uuid.uuid4()), "technical:0", "ai agents", "technical", NOW.isoformat()
    )
    (event,) = _research_events(bus)
    assert event.label == "Kaynaklar aranıyor"


def test_fetch_activity_publishes_the_coarse_fetching_label(
    monkeypatch, db_url, task_id: str, bus
) -> None:
    """The fetching-stage label increments per completed source ("N güvenilir
    kaynak incelendi") - never the raw topic, published from fetch_activity itself
    since fetch_targets_activity runs before anything has actually been fetched."""
    from app.devices.commands import CommandSucceeded
    from tests.device_command_support import FakeDeviceCommandClient

    fake = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "url": "https://a.example.com/x",
                "excerpt": "yapay zeka ajanları hakkında ayrıntılı bir bulgu " * 5,
                "fetched_at": NOW.isoformat(),
                "extraction_method": "dom_text",
            }
        )
    )
    monkeypatch.setattr(ba, "_command_client", lambda: fake)
    ba.fetch_activity(task_id, str(uuid.uuid4()), "https://a.example.com/x", "q", "news")

    (event,) = [e for e in _research_events(bus) if e.status == STAGE_FETCHING]
    assert event.label == "1 güvenilir kaynak incelendi"
    assert event.metadata == {"fetched": 1}


def test_ranking_label_is_the_coarse_turkish_phrase(db_url, task_id: str, bus) -> None:
    _seed_evidence(db_url, task_id, _usable_evidence())
    window_start = (NOW - timedelta(days=3)).isoformat()
    ba.rank_activity(task_id, TOPIC, window_start, NOW.isoformat())

    (event,) = [e for e in _research_events(bus) if e.status == STAGE_RANKING]
    assert event.label == "Bulgular doğrulanıyor"


def test_synthesize_label_is_the_coarse_turkish_phrase(db_url, task_id: str, bus) -> None:
    _seed_evidence(db_url, task_id, _usable_evidence())
    _rank_ok(task_id)
    ba.synthesize_activity(task_id, TOPIC, _window_ok(), "deterministic")

    (event,) = [e for e in _research_events(bus) if e.status == STAGE_SYNTHESIZING]
    assert event.label == "Sonuç hazırlanıyor"


def test_ranking_no_longer_sends_a_constant_intensity(db_url, task_id: str, bus) -> None:
    """The one number the bus used to carry for research was invented.

    Real-shaped evidence, because the quality gate refuses anything else before
    ranking sees it - the same fixture shape `test_rank_activity_assigns_ids_and_ranks`
    uses.
    """
    _seed_evidence(
        db_url,
        task_id,
        [
            {
                "url": "https://a.example.com/ai-agent-launch",
                "title": "OpenAI yeni yapay zeka ajanı platformunu duyurdu",
                "excerpt": (
                    "OpenAI, geliştiricilerin kendi yapay zeka ajanlarını kurmasına olanak "
                    "tanıyan yeni bir platform duyurdu. Ajanlar araç kullanımı, hafıza ve "
                    "çok adımlı görev planlaması yapabiliyor; şirket, kurumsal müşteriler "
                    "için erişimin bu hafta açılacağını belirtti. Duyuru, agentic AI "
                    "alanındaki rekabetin hızlandığı bir döneme denk geliyor."
                ),
                "fetched_at": NOW.isoformat(),
                "published_at": NOW.isoformat(),
                "extraction_method": "dom_text",
                "source_class": "official",
            },
            {
                "url": "https://b.example.com/agent-framework",
                "title": "Açık kaynak AI agent çerçevesi 2.0 yayınlandı",
                "excerpt": (
                    "Popüler açık kaynak yapay zeka ajanı çerçevesinin 2.0 sürümü yayınlandı. "
                    "Yeni sürüm, araç çağırma protokolü desteği, daha iyi hafıza yönetimi ve "
                    "çok ajanlı iş akışları için bir planlayıcı içeriyor. Geliştiriciler, "
                    "otonom ajanların üretim ortamında çalıştırılmasının kolaylaştığını "
                    "söylüyor."
                ),
                "fetched_at": NOW.isoformat(),
                "published_at": NOW.isoformat(),
                "extraction_method": "dom_text",
                "source_class": "community",
            },
        ],
    )
    window_start = (NOW - timedelta(days=3)).isoformat()
    ba.rank_activity(task_id, "yapay zeka ajanları", window_start, NOW.isoformat())

    (event,) = [e for e in _research_events(bus) if e.status == STAGE_RANKING]
    assert event.intensity is None
    assert event.metadata == {"candidates": 2, "kept": 2}

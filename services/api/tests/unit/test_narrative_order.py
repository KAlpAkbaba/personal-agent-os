"""Unit test: failures are told newest first (ADR-0216, the inspector's live mutant)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ledger.vocabulary import (
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_RESEARCH,
)
from app.narrative.collector import collect
from app.narrative.narrator import RuleNarrator
from tests.unit.test_narrative_collector import NOW, add_row, make_session


def _seeded():
    s = make_session()
    # inserted out of time order on purpose: the order must come from the timestamp, not the insert.
    for ref, hour, what in (
        ("b", 9, "orta hata"),
        ("c", 11, "en yeni hata"),
        ("a", 7, "en eski hata"),
    ):
        add_row(
            s,
            ref,
            datetime(2026, 9, 30, hour, 0, tzinfo=UTC),
            EVENT_TYPE_RESEARCH_FAILED,
            SUBSYSTEM_RESEARCH,
            STATUS_FAILED,
            what,
        )
    for ref, hour in (("x", 6), ("z", 10), ("y", 8)):
        add_row(
            s,
            ref,
            datetime(2026, 9, 30, hour, 0, tzinfo=UTC),
            EVENT_TYPE_RESEARCH_COMPLETED,
            SUBSYSTEM_RESEARCH,
            STATUS_COMPLETED,
            f"bitti {hour}",
        )
    return s


def test_failures_are_listed_newest_first():
    facts = collect(_seeded(), "bugün", now=NOW)
    assert [e.summary for e in facts.failed] == ["en yeni hata", "orta hata", "en eski hata"]


def test_completed_events_of_a_subsystem_are_listed_newest_first():
    facts = collect(_seeded(), "bugün", now=NOW)
    _, events = facts.completed[0]
    assert [e.summary for e in events] == ["bitti 10", "bitti 8", "bitti 6"]


def test_the_spoken_text_names_the_newest_failure_before_the_oldest():
    text = RuleNarrator().tell(collect(_seeded(), "bugün", now=NOW))
    assert text.index("en yeni hata") < text.index("orta hata") < text.index("en eski hata")

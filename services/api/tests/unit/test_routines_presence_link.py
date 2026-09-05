"""The seam between the Routine Engine and the Presence Engine.

Before this seam, ``RoutineConditionContext.owner_present`` was whatever the HTTP caller
put in the request body — so a presence-gated routine could fire on a client's assertion
about a room the client cannot see. These tests pin the three things that fixes:

1. when the caller says nothing, the answer comes from the fusion engine;
2. a stale assertion resolves to *unknown*, never to a boolean in either direction;
3. whichever way it was answered, the firing record says which.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.presence.engine import PresenceFusionEngine, set_engine
from app.presence.states import PresenceAssertion, PresenceState
from app.routines.conditions import RoutineConditionContext, evaluate_conditions
from app.routines.presence_link import (
    SOURCE_CALLER,
    SOURCE_NEVER_OBSERVED,
    SOURCE_PRESENCE_ENGINE,
    SOURCE_STALE,
    resolve_owner_present,
)
from app.routines.routes import ConditionContextIn

NOW = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


class _FixedEngine(PresenceFusionEngine):
    """A fusion engine holding one assertion, so these tests are about the seam."""

    def __init__(self, assertion: PresenceAssertion | None) -> None:
        self._assertion = assertion

    def current(self) -> PresenceAssertion | None:  # type: ignore[override]
        return self._assertion


def _assertion(state: PresenceState, *, observed_at: datetime, ttl_s: float = 300.0):
    return PresenceAssertion(
        state=state,
        confidence=0.9,
        signals=(),
        observed_at=observed_at,
        state_started_at=observed_at,
        stale_after_s=ttl_s,
    )


@pytest.fixture()
def engine_holding():
    installed: list[PresenceFusionEngine] = []

    def install(assertion: PresenceAssertion | None) -> None:
        engine = _FixedEngine(assertion)
        installed.append(engine)
        set_engine(engine)

    yield install
    set_engine(PresenceFusionEngine())


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (PresenceState.PRESENT, True),
        (PresenceState.RETURNED, True),
        (PresenceState.AWAKE, True),
        # Resting and likely-asleep mean the owner IS in the room. Whether it is
        # acceptable to act on that is the quiet-hours condition's question, not this one.
        (PresenceState.RESTING, True),
        (PresenceState.LIKELY_ASLEEP, True),
        (PresenceState.AWAY, False),
    ],
)
def test_presence_states_map_to_in_the_room(engine_holding, state, expected) -> None:
    engine_holding(_assertion(state, observed_at=NOW))
    present, source = resolve_owner_present(now=NOW)
    assert present is expected
    assert source == SOURCE_PRESENCE_ENGINE


def test_a_stale_assertion_is_unknown_not_a_boolean(engine_holding) -> None:
    engine_holding(_assertion(PresenceState.PRESENT, observed_at=NOW, ttl_s=300.0))

    present, source = resolve_owner_present(now=NOW + timedelta(seconds=301))
    assert present is None  # not False, and emphatically not True
    assert source == SOURCE_STALE


def test_nothing_ever_observed_is_its_own_answer(engine_holding) -> None:
    engine_holding(None)
    present, source = resolve_owner_present(now=NOW)
    assert present is None
    # A camera that was never enabled is a different situation from one that stopped
    # reporting, and a routine's skip reason should be able to say which.
    assert source == SOURCE_NEVER_OBSERVED
    assert source != SOURCE_STALE


def test_the_route_resolves_presence_when_the_caller_says_nothing(engine_holding) -> None:
    engine_holding(_assertion(PresenceState.PRESENT, observed_at=datetime.now(UTC)))

    context = ConditionContextIn().to_context()
    assert context.owner_present is True
    assert context.owner_present_source == SOURCE_PRESENCE_ENGINE


def test_a_caller_may_still_assert_but_it_is_labelled_as_theirs(engine_holding) -> None:
    engine_holding(_assertion(PresenceState.AWAY, observed_at=datetime.now(UTC)))

    context = ConditionContextIn(owner_present=True).to_context()
    assert context.owner_present is True
    # The engine says away; the caller says present. The caller wins - some callers know
    # something the camera does not - but the record shows the claim was unverified.
    assert context.owner_present_source == SOURCE_CALLER


def test_the_skip_reason_names_where_the_fact_came_from() -> None:
    conditions = [{"kind": "owner_present", "detail": {"required": True}}]

    passed, results = evaluate_conditions(
        conditions,
        RoutineConditionContext(owner_present=True, owner_present_source=SOURCE_PRESENCE_ENGINE),
    )
    assert passed
    assert SOURCE_PRESENCE_ENGINE in results[0]["reason"]

    passed, results = evaluate_conditions(
        conditions,
        RoutineConditionContext(owner_present=True, owner_present_source=SOURCE_CALLER),
    )
    assert passed
    assert SOURCE_CALLER in results[0]["reason"]


def test_an_unknown_presence_does_not_fire_a_presence_gated_routine() -> None:
    passed, results = evaluate_conditions(
        [{"kind": "owner_present", "detail": {"required": True}}],
        RoutineConditionContext(owner_present=None, owner_present_source=SOURCE_STALE),
    )
    assert not passed
    assert "owner_presence_unknown" in results[0]["reason"]
    assert SOURCE_STALE in results[0]["reason"]

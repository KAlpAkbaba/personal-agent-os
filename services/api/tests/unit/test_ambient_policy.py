"""Unit tests: app.ambient.policy.decide (M18.3 spec §3.9, §11).

Pure, so every line of the decision table is reachable from a plain call with no fixtures.
The property the whole file exists to prove is one sentence from spec §1.4:

    **Uncertain means ON.** UNKNOWN, stale, low confidence, the eye disabled, the camera
    failed, the display state unreadable — none of these ever produces a display-off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ambient.holdoff import (
    SOURCE_ALARM_WAKE,
    SOURCE_INPUT,
    SOURCE_OWNER_COMMAND,
    SOURCE_OWNER_RETURN,
    Holdoff,
)
from app.ambient.policy import (
    ACTION_DISPLAY_OFF,
    ACTION_NONE,
    ALARM_ARMED_CONTEXT_S,
    REASON_ALARM_CONTEXT,
    REASON_DISPLAY_NOT_ON,
    REASON_NO_CONDITION_MET,
    REASON_NO_PERCEPTION,
    REASON_NOT_HELD_LONG_ENOUGH,
    REASON_OWNER_AWAY,
    REASON_OWNER_LIKELY_ASLEEP,
    REASON_POLICY_DISABLED,
    REASON_UNCERTAIN,
    AmbientInputs,
    AmbientPolicy,
    decide,
)
from app.presence.states import PresenceState

NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)

#: The policy the owner has turned ON — every "uncertain means ON" test below runs against
#: THIS, so a pass can never be an accident of the feature being disabled.
ENABLED = AmbientPolicy(auto_off_enabled=True)


def _inputs(**overrides) -> AmbientInputs:
    """A world in which a display-off WOULD be correct, so each test changes one thing."""
    base = {
        "display_on": True,
        "presence_state": PresenceState.AWAY,
        "presence_confidence": 0.9,
        "presence_held_s": 1200.0,
        "presence_stale": False,
        "eye_enabled": True,
        "alarm_active": False,
        "next_alarm_at": None,
        "holdoffs": (),
        # ADR-0079 §3: the camera delivered five seconds ago - part of a world in which an
        # off is legitimate; a silent camera is one of the uncertainties below.
        "perception_age_s": 5.0,
    }
    base.update(overrides)
    return AmbientInputs(**base)


def test_the_baseline_world_really_does_produce_a_display_off() -> None:
    """A guard on the fixture: if this ever stopped acting, every test below would pass
    vacuously."""
    decision = decide(_inputs(), ENABLED, NOW)
    assert decision.action == ACTION_DISPLAY_OFF
    assert decision.reason == REASON_OWNER_AWAY


# ------------------------------------------------------- uncertain means ON (spec §1.4)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        pytest.param({"display_on": None}, REASON_DISPLAY_NOT_ON, id="display_unknown"),
        pytest.param({"display_on": False}, REASON_DISPLAY_NOT_ON, id="display_already_off"),
        pytest.param(
            {"presence_state": PresenceState.UNKNOWN}, REASON_UNCERTAIN, id="presence_unknown"
        ),
        pytest.param({"presence_stale": True}, REASON_UNCERTAIN, id="presence_stale"),
        pytest.param({"eye_enabled": False}, REASON_NO_PERCEPTION, id="eye_disabled"),
    ],
)
def test_uncertainty_never_produces_a_display_off(overrides: dict, reason: str) -> None:
    decision = decide(_inputs(**overrides), ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == reason


def test_a_stale_asleep_assertion_cannot_darken_a_screen() -> None:
    """The C qualification's own sentence: "stale AWAY/ASLEEP cannot re-darken them"."""
    decision = decide(
        _inputs(
            presence_state=PresenceState.LIKELY_ASLEEP,
            presence_confidence=0.95,
            presence_held_s=99999.0,
            presence_stale=True,
        ),
        ENABLED,
        NOW,
    )
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_UNCERTAIN


def test_a_camera_failure_is_never_read_as_a_sleeping_owner() -> None:
    """Spec §1.4: "Camera failure never implies sleep"."""
    decision = decide(
        _inputs(
            eye_enabled=False,
            presence_state=PresenceState.LIKELY_ASLEEP,
            presence_confidence=1.0,
            presence_held_s=99999.0,
        ),
        ENABLED,
        NOW,
    )
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NO_PERCEPTION


def test_decide_never_returns_display_off_from_any_uncertain_world() -> None:
    """A sweep rather than a list: every combination of the uncertainty axes, against the
    ENABLED policy, must be ``none``."""
    for display_on in (None, False):
        for state in (PresenceState.UNKNOWN, PresenceState.AWAY, PresenceState.LIKELY_ASLEEP):
            for stale in (True, False):
                for eye in (True, False):
                    decision = decide(
                        _inputs(
                            display_on=display_on,
                            presence_state=state,
                            presence_stale=stale,
                            eye_enabled=eye,
                        ),
                        ENABLED,
                        NOW,
                    )
                    assert decision.action == ACTION_NONE
    for state in (PresenceState.UNKNOWN,):
        assert decide(_inputs(presence_state=state), ENABLED, NOW).action == ACTION_NONE
    for eye in (False,):
        assert decide(_inputs(eye_enabled=eye), ENABLED, NOW).action == ACTION_NONE


# ------------------------------------------------------------------- the other guards


def test_the_policy_being_off_is_its_own_reason() -> None:
    decision = decide(_inputs(), AmbientPolicy(auto_off_enabled=False), NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_POLICY_DISABLED


def test_an_alarm_that_is_ringing_holds_the_screens() -> None:
    decision = decide(_inputs(alarm_active=True), ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_ALARM_CONTEXT


def test_an_alarm_armed_within_the_window_holds_the_screens() -> None:
    """Darkening the screens ninety seconds before a wake alarm would be undone by the
    alarm itself (spec §3.9's ``alarm_context``)."""
    soon = NOW + timedelta(seconds=ALARM_ARMED_CONTEXT_S - 60)
    assert decide(_inputs(next_alarm_at=soon), ENABLED, NOW).reason == REASON_ALARM_CONTEXT

    later = NOW + timedelta(seconds=ALARM_ARMED_CONTEXT_S + 60)
    assert decide(_inputs(next_alarm_at=later), ENABLED, NOW).action == ACTION_DISPLAY_OFF


@pytest.mark.parametrize(
    "source", [SOURCE_INPUT, SOURCE_OWNER_COMMAND, SOURCE_ALARM_WAKE, SOURCE_OWNER_RETURN]
)
def test_any_active_holdoff_blocks_a_display_off(source: str) -> None:
    holdoff = Holdoff(source=source, until=NOW + timedelta(minutes=5))
    decision = decide(_inputs(holdoffs=(holdoff,)), ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == f"holdoff:{source}"


def test_the_holdoff_outranks_the_policy_and_the_presence() -> None:
    """Spec §1.3: physical owner input outranks passive inference. The holdoff check runs
    BEFORE the policy and the presence checks, so the reason the owner is told is the
    holdoff — which is the true one."""
    holdoff = Holdoff(source=SOURCE_INPUT, until=NOW + timedelta(minutes=5))
    decision = decide(
        _inputs(
            holdoffs=(holdoff,),
            presence_state=PresenceState.LIKELY_ASLEEP,
            presence_confidence=1.0,
            presence_held_s=99999.0,
        ),
        ENABLED,
        NOW,
    )
    assert decision.reason == f"holdoff:{SOURCE_INPUT}"


# ------------------------------------------------------------------ the two off paths


def test_away_must_be_sustained() -> None:
    short = decide(_inputs(presence_held_s=60.0), ENABLED, NOW)
    assert short.action == ACTION_NONE
    assert short.reason == REASON_NOT_HELD_LONG_ENOUGH
    assert short.evidence["needed_s"] == ENABLED.away_after_s

    held = decide(_inputs(presence_held_s=ENABLED.away_after_s), ENABLED, NOW)
    assert held.action == ACTION_DISPLAY_OFF
    assert held.reason == REASON_OWNER_AWAY


def test_away_is_not_acted_on_when_the_owner_turned_that_half_off() -> None:
    policy = AmbientPolicy(auto_off_enabled=True, off_when_away=False)
    decision = decide(_inputs(), policy, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NO_CONDITION_MET


def test_likely_asleep_needs_both_duration_and_confidence() -> None:
    """"The system says LIKELY_ASLEEP confidence=0.86, never OWNER_IS_ASLEEP" — so both
    halves of the claim have to clear their bar."""
    asleep = {
        "presence_state": PresenceState.LIKELY_ASLEEP,
        "presence_held_s": ENABLED.asleep_after_s,
        "presence_confidence": ENABLED.asleep_min_confidence,
    }
    assert decide(_inputs(**asleep), ENABLED, NOW).action == ACTION_DISPLAY_OFF
    assert decide(_inputs(**asleep, ), ENABLED, NOW).reason == REASON_OWNER_LIKELY_ASLEEP

    not_long = decide(_inputs(**{**asleep, "presence_held_s": 10.0}), ENABLED, NOW)
    assert not_long.action == ACTION_NONE
    assert not_long.reason == REASON_NOT_HELD_LONG_ENOUGH

    not_confident = decide(
        _inputs(**{**asleep, "presence_confidence": ENABLED.asleep_min_confidence - 0.01}),
        ENABLED,
        NOW,
    )
    assert not_confident.action == ACTION_NONE
    assert not_confident.reason == REASON_NOT_HELD_LONG_ENOUGH


def test_asleep_is_not_acted_on_when_the_owner_turned_that_half_off() -> None:
    policy = AmbientPolicy(auto_off_enabled=True, off_when_asleep=False)
    decision = decide(
        _inputs(
            presence_state=PresenceState.LIKELY_ASLEEP,
            presence_held_s=99999.0,
            presence_confidence=1.0,
        ),
        policy,
        NOW,
    )
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NO_CONDITION_MET


@pytest.mark.parametrize(
    "state",
    [PresenceState.PRESENT, PresenceState.RETURNED, PresenceState.AWAKE, PresenceState.RESTING],
)
def test_no_other_presence_state_ever_darkens_a_screen(state: PresenceState) -> None:
    """RESTING is deliberately in this list: resting is not asleep, and only the sustained,
    confident LIKELY_ASLEEP is allowed to act."""
    decision = decide(
        _inputs(presence_state=state, presence_held_s=99999.0, presence_confidence=1.0),
        ENABLED,
        NOW,
    )
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NO_CONDITION_MET


# --------------------------------------------------------------------- the evidence


def test_every_decision_carries_the_evidence_it_was_made_on() -> None:
    """"Why are my screens still on?" is answered from the decision itself."""
    decision = decide(_inputs(presence_held_s=60.0), ENABLED, NOW)
    assert decision.evidence["presence"] == "away"
    assert decision.evidence["held_s"] == 60.0
    assert decision.evidence["eye_enabled"] is True
    assert decision.evidence["display_on"] is True
    assert decision.as_dict()["reason"] == REASON_NOT_HELD_LONG_ENOUGH


def test_the_first_true_reason_wins() -> None:
    """The order of the checks is the order of certainty, so the reason reported is the
    cheapest true one rather than the last one evaluated."""
    decision = decide(
        _inputs(
            display_on=False,
            alarm_active=True,
            holdoffs=(Holdoff(source=SOURCE_INPUT, until=NOW + timedelta(minutes=5)),),
        ),
        AmbientPolicy(auto_off_enabled=False),
        NOW,
    )
    assert decision.reason == REASON_DISPLAY_NOT_ON


def test_the_policy_round_trips_through_its_dict() -> None:
    assert AmbientPolicy(**{
        k: v for k, v in ENABLED.as_dict().items()
    }) == ENABLED

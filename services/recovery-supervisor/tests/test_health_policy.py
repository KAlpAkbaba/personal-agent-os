"""Health-policy state machine: transient vs sustained failure."""

import pytest

from recovery_supervisor.health import HealthPolicy


def test_single_transient_failure_never_unhealthy() -> None:
    policy = HealthPolicy(failure_threshold=3, window_s=30.0)
    assert policy.record(True, now=0.0) == HealthPolicy.HEALTHY
    assert policy.record(False, now=1.0) == HealthPolicy.DEGRADED
    assert policy.record(True, now=2.0) == HealthPolicy.HEALTHY
    assert policy.consecutive_failures == 0


def test_success_resets_consecutive_failures() -> None:
    policy = HealthPolicy(failure_threshold=3, window_s=30.0)
    policy.record(False, now=1.0)
    policy.record(False, now=2.0)
    policy.record(True, now=3.0)  # breaks the run
    assert policy.record(False, now=4.0) == HealthPolicy.DEGRADED
    assert policy.record(False, now=5.0) == HealthPolicy.DEGRADED
    assert policy.record(False, now=6.0) == HealthPolicy.UNHEALTHY


def test_sustained_consecutive_failures_within_window_unhealthy() -> None:
    policy = HealthPolicy(failure_threshold=3, window_s=30.0)
    assert policy.record(False, now=1.0) == HealthPolicy.DEGRADED
    assert policy.record(False, now=2.0) == HealthPolicy.DEGRADED
    assert policy.record(False, now=3.0) == HealthPolicy.UNHEALTHY


def test_failures_outside_window_do_not_count() -> None:
    policy = HealthPolicy(failure_threshold=3, window_s=10.0)
    policy.record(False, now=0.0)
    policy.record(False, now=1.0)
    # Third failure arrives long after: the first two aged out of the window.
    assert policy.record(False, now=100.0) == HealthPolicy.DEGRADED
    assert policy.consecutive_failures == 1


def test_threshold_below_two_rejected() -> None:
    with pytest.raises(ValueError):
        HealthPolicy(failure_threshold=1)
    with pytest.raises(ValueError):
        HealthPolicy(window_s=0.0)


def test_reset_clears_state() -> None:
    policy = HealthPolicy(failure_threshold=2, window_s=30.0)
    policy.record(False, now=1.0)
    policy.reset()
    assert policy.record(False, now=2.0) == HealthPolicy.DEGRADED

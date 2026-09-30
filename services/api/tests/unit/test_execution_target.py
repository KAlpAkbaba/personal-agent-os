"""The execution_target rule: where a job runs, decided by pure functions (ADR-0213).

Every rule has its hit and a near miss beside it. Nothing here touches a device, a
database or a model.
"""

from __future__ import annotations

import json

import pytest

from app.execution import (
    Availability,
    ExecutionRequest,
    JobKind,
    Target,
    decide,
    events,
    forced_target_of,
)
from app.execution import vocabulary as vocab
from app.webtask.sites import DENYLIST_PATH

ALL_UP = Availability(
    cloud_online=True,
    owner_chrome_enrolled=True,
    owner_chrome_device_online=True,
    device_online=True,
)
CLOUD_DOWN = Availability(
    cloud_online=False,
    owner_chrome_enrolled=True,
    owner_chrome_device_online=True,
    device_online=True,
)
CLOUD_ONLY = Availability(cloud_online=True)


def req(kind: JobKind, availability: Availability = ALL_UP, **kw) -> ExecutionRequest:
    return ExecutionRequest(job_kind=kind, availability=availability, **kw)


def denylisted_url() -> str:
    domain = json.loads(DENYLIST_PATH.read_text("utf-8"))["categories"]["bank"]["domains"][0]
    return f"https://www.{domain}/login"


# --- scheduled ------------------------------------------------------------------------


def test_a_scheduled_job_runs_in_the_cloud():
    d = decide(req(JobKind.SCHEDULED))
    assert (d.outcome, d.target) == ("selected", Target.CLOUD)


def test_a_scheduled_job_is_refused_when_the_cloud_is_offline_and_never_reaches_owner_chrome():
    d = decide(req(JobKind.SCHEDULED, CLOUD_DOWN))
    assert d.outcome == "refused" and d.target is None
    assert Target.OWNER_CHROME not in d.chain and Target.DEVICE not in d.chain
    assert [s.reason for s in d.skipped] == ["cloud_offline"]


def test_a_research_job_with_the_cloud_offline_does_fall_to_owner_chrome():
    d = decide(req(JobKind.RESEARCH, CLOUD_DOWN))
    assert d.target is Target.OWNER_CHROME


# --- research / browser task ----------------------------------------------------------


def test_research_prefers_the_cloud_then_owner_chrome_then_the_device():
    assert decide(req(JobKind.RESEARCH)).chain == (
        Target.CLOUD,
        Target.OWNER_CHROME,
        Target.DEVICE,
    )
    assert decide(req(JobKind.RESEARCH)).target is Target.CLOUD


def test_research_reaches_the_device_when_cloud_and_owner_chrome_are_both_down():
    a = Availability(cloud_online=False, owner_chrome_enrolled=False, device_online=True)
    d = decide(req(JobKind.RESEARCH, a))
    assert d.target is Target.DEVICE
    assert [(s.target, s.reason) for s in d.skipped] == [
        (Target.CLOUD, "cloud_offline"),
        (Target.OWNER_CHROME, "owner_chrome_not_enrolled"),
    ]


def test_an_enrolled_owner_chrome_whose_device_is_offline_is_skipped_with_its_own_reason():
    a = Availability(cloud_online=False, owner_chrome_enrolled=True, device_online=True)
    d = decide(req(JobKind.RESEARCH, a))
    assert d.skipped[1].reason == "owner_chrome_device_offline"
    assert d.target is Target.DEVICE


# --- signed-in session ----------------------------------------------------------------


def test_a_job_that_needs_a_signed_in_session_defaults_to_owner_chrome():
    d = decide(req(JobKind.BROWSER_TASK, needs_signed_in_session=True))
    assert d.target is Target.OWNER_CHROME
    assert Target.CLOUD not in d.chain


def test_a_signed_in_job_is_refused_rather_than_sent_to_the_cloud_when_chrome_and_device_are_down():
    a = Availability(cloud_online=True, owner_chrome_enrolled=False, device_online=False)
    d = decide(req(JobKind.RESEARCH, a, needs_signed_in_session=True))
    assert d.outcome == "refused" and d.reason == "no_target_available"
    assert Target.CLOUD not in d.chain


def test_a_signed_in_job_forced_to_the_cloud_is_refused_as_not_allowed():
    d = decide(req(JobKind.BROWSER_TASK, needs_signed_in_session=True, spoken_target="bulutta"))
    assert d.outcome == "refused" and d.reason == "forced_target_not_allowed"


def test_a_job_that_does_not_need_a_session_may_still_be_forced_to_the_cloud():
    d = decide(req(JobKind.BROWSER_TASK, spoken_target="bulutta"))
    assert d.target is Target.CLOUD and d.forced


def test_a_scheduled_job_that_needs_a_signed_in_session_has_no_eligible_target():
    d = decide(req(JobKind.SCHEDULED, needs_signed_in_session=True))
    assert d.outcome == "refused" and d.reason == "no_eligible_target" and d.chain == ()


# --- desktop / compute ----------------------------------------------------------------


def test_a_desktop_job_runs_on_the_device_and_never_in_the_cloud():
    d = decide(req(JobKind.DESKTOP))
    assert d.target is Target.DEVICE and d.chain == (Target.DEVICE,)


def test_a_desktop_job_with_the_device_offline_is_refused_not_moved_to_the_cloud():
    d = decide(req(JobKind.DESKTOP, CLOUD_ONLY))
    assert d.outcome == "refused" and Target.CLOUD not in d.chain


def test_compute_runs_in_the_cloud_only():
    assert decide(req(JobKind.COMPUTE)).chain == (Target.CLOUD,)
    d = decide(req(JobKind.COMPUTE, CLOUD_DOWN))
    assert d.outcome == "refused" and d.target is None


# --- forced targets -------------------------------------------------------------------


@pytest.mark.parametrize("word", ["bulutta", "bulut", "Bulutta", "BULUT.", " bulutta "])
def test_the_cloud_words_force_the_cloud(word):
    assert forced_target_of(word) is Target.CLOUD


@pytest.mark.parametrize("word", ["bulutlu", "bulutbank", "bulutlar", "oncebulut"])
def test_a_word_that_merely_contains_bulut_is_a_device_alias_not_the_cloud(word):
    assert forced_target_of(word) is Target.DEVICE


def test_a_site_name_containing_bulut_forces_nothing_without_a_spoken_target():
    d = decide(req(JobKind.RESEARCH, url="https://bulut.example.com/haber"))
    assert not d.forced and d.target is Target.CLOUD
    assert forced_target_of(None) is None and forced_target_of("  ") is None


def test_a_forced_cloud_that_is_offline_is_a_refusal_not_a_fallback():
    d = decide(req(JobKind.RESEARCH, CLOUD_DOWN, spoken_target="bulutta"))
    assert d.outcome == "refused" and d.reason == "forced_target_unavailable"
    assert d.target is None and d.forced


def test_a_named_device_forces_the_device_and_needs_it_online():
    d = decide(req(JobKind.RESEARCH, spoken_target="ofis", resolved_device="office-pc"))
    assert d.target is Target.DEVICE and d.forced
    off = Availability(cloud_online=True, device_online=False)
    d = decide(req(JobKind.RESEARCH, off, spoken_target="ofis", resolved_device="office-pc"))
    assert d.reason == "forced_target_unavailable"
    assert d.skipped[0].reason == "device_offline"


def test_a_device_alias_that_resolves_to_no_device_is_forced_target_unavailable():
    d = decide(req(JobKind.RESEARCH, spoken_target="depo", resolved_device=None))
    assert d.outcome == "refused" and d.reason == "forced_target_unavailable"
    assert d.skipped[0].reason == "device_unresolved"


def test_a_named_device_is_not_allowed_for_a_compute_or_scheduled_job():
    for kind in (JobKind.COMPUTE, JobKind.SCHEDULED):
        d = decide(req(kind, spoken_target="ofis", resolved_device="office-pc"))
        assert d.reason == "forced_target_not_allowed"


def test_a_forced_cloud_is_not_allowed_for_a_desktop_job():
    d = decide(req(JobKind.DESKTOP, spoken_target="bulutta"))
    assert d.reason == "forced_target_not_allowed"


# --- blockers, payment, deny-list -----------------------------------------------------


@pytest.mark.parametrize("blocker", ["auth_wall", "captcha", "challenge"])
def test_a_cloud_run_that_meets_a_wall_asks_the_owner_and_does_not_fall_back(blocker):
    d = decide(req(JobKind.BROWSER_TASK, cloud_blocker=blocker))
    assert d.outcome == "ask_owner" and d.reason == "ask_owner"
    assert d.target is Target.CLOUD and d.skipped == ()
    (event,) = events(d)
    assert event["event_type"] == vocab.EXECUTION_REFUSED and event["ask_owner"] is True


def test_an_unrecognised_cloud_blocker_word_is_not_a_wall():
    d = decide(req(JobKind.BROWSER_TASK, cloud_blocker="slow_page"))
    assert d.outcome == "selected"


def test_payment_is_refused_on_every_kind_and_every_target():
    for kind in JobKind:
        assert decide(req(kind, involves_payment=True)).reason == "payment_out_of_scope"
    d = decide(req(JobKind.BROWSER_TASK, involves_payment=True, spoken_target="bulutta"))
    assert d.reason == "payment_out_of_scope"


def test_acting_on_a_deny_listed_site_is_refused_on_every_target():
    url = denylisted_url()
    assert decide(req(JobKind.BROWSER_TASK, url=url)).reason == "deny_listed_site"
    assert decide(req(JobKind.BROWSER_TASK, url=url, spoken_target="bulutta")).outcome == "refused"
    assert decide(req(JobKind.BROWSER_TASK, url=url, needs_signed_in_session=True)).outcome == (
        "refused"
    )
    assert decide(
        req(JobKind.BROWSER_TASK, url=url, spoken_target="ofis", resolved_device="p")
    ).reason == ("deny_listed_site")


def test_reading_a_deny_listed_site_is_not_refused():
    d = decide(req(JobKind.RESEARCH, url=denylisted_url(), acting=False))
    assert d.outcome == "selected"


def test_an_ordinary_site_is_not_refused_for_acting():
    d = decide(req(JobKind.BROWSER_TASK, url="https://example.org/form"))
    assert d.outcome == "selected"


# --- the chain and the events ---------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "chain"),
    [
        (JobKind.RESEARCH, (Target.CLOUD, Target.OWNER_CHROME, Target.DEVICE)),
        (JobKind.BROWSER_TASK, (Target.CLOUD, Target.OWNER_CHROME, Target.DEVICE)),
        (JobKind.DESKTOP, (Target.DEVICE,)),
        (JobKind.SCHEDULED, (Target.CLOUD,)),
        (JobKind.COMPUTE, (Target.CLOUD,)),
    ],
)
def test_the_fallback_chain_is_ordered_and_complete_for_each_job_kind(kind, chain):
    assert decide(req(kind)).chain == chain
    nothing = Availability(cloud_online=False)
    refused = decide(req(kind, nothing))
    assert refused.chain == chain and [s.target for s in refused.skipped] == list(chain)
    assert all(s.reason for s in refused.skipped)


def test_a_selection_after_skips_writes_one_fallback_per_skip_then_one_selected():
    a = Availability(cloud_online=False, owner_chrome_enrolled=False, device_online=True)
    out = events(decide(req(JobKind.RESEARCH, a)))
    assert [e["event_type"] for e in out] == [
        vocab.EXECUTION_FALLBACK,
        vocab.EXECUTION_FALLBACK,
        vocab.EXECUTION_SELECTED,
    ]
    assert [e["reason"] for e in out[:2]] == ["cloud_offline", "owner_chrome_not_enrolled"]
    assert out[2]["target"] == "device"


def test_a_refusal_writes_one_refused_event_plus_one_fallback_per_skipped_target():
    out = events(decide(req(JobKind.SCHEDULED, CLOUD_DOWN)))
    types = [e["event_type"] for e in out]
    assert types == [vocab.EXECUTION_FALLBACK, vocab.EXECUTION_REFUSED]
    assert out[1]["reason"] == "no_target_available"


def test_every_decision_yields_exactly_one_selected_or_refused_and_every_event_has_a_reason():
    cases = [
        req(JobKind.RESEARCH),
        req(JobKind.RESEARCH, CLOUD_DOWN),
        req(JobKind.DESKTOP, CLOUD_ONLY),
        req(JobKind.BROWSER_TASK, involves_payment=True),
        req(JobKind.BROWSER_TASK, spoken_target="bulutta"),
        req(JobKind.BROWSER_TASK, cloud_blocker="captcha"),
    ]
    for c in cases:
        d = decide(c)
        out = events(d)
        finals = [e for e in out if e["event_type"] != vocab.EXECUTION_FALLBACK]
        assert len(finals) == 1
        assert sum(e["event_type"] == vocab.EXECUTION_FALLBACK for e in out) == len(d.skipped)
        assert all(e["reason"] for e in out)


def test_the_three_event_types_are_the_documented_strings():
    assert vocab.EXECUTION_EVENT_TYPES == (
        "execution.selected",
        "execution.fallback",
        "execution.refused",
    )

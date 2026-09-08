---
name: m26-executive-autonomy-review
description: M26 Executive Autonomy security review (main, 2026-09-09). HIGH (live PoC) - an exception run_step_activity could not classify escaped the activity and asyncio.gather let it terminate the whole Temporal execution; because the workflow holds no state the row stayed `running` forever and the owner was told the work was still in progress, one of two run slots was held indefinitely, and the only escape (cancel) ran compensations across already-VERIFIED sibling steps. Trigger is ordinary - a research summary over ArtifactSpec's 20,000-char bound raises pydantic ValidationError, not StepError. MEDIUM (live PoC) - the <=2 active-run bound counted then planned then inserted with nothing holding the count still; two threads on separate connections both committed, leaving three. LOW - test_worker_restart_mid_run_resumes_from_history awaits the workflow to completion BEFORE re-deriving a handle, so mid-run reattachment is unproven and ADR-0089 addendum 1 decision 5 overstated the coverage. INFORMATIONAL - a run is never observable as `planned` on this build (start_run_db constructs with STATE_RUNNING), refuting an earlier claim. Verified sound - no path to send/pay/delete/publish (high_risk mapped to zero kinds, asserted positively), validate_graph refuses same-or-later refs including through amend, planner cannot set its own risk_class/compensation/evidence, _evidence_meets_minimum never satisfied by an empty comparison, both cross-file contract guards confirmed to bite, all REST routes owner-gated and listed, migration expand-only, planner reads only the owner's own directive so fetched content cannot decide steps, no dangerouslySetInnerHTML anywhere in the web.
metadata:
  type: project
---

Reviewed the merged M26 milestone before its gate: `services/api/app/executive/*`,
`app/uistate/contract.py`, `app/voice/intents.py` and `realtime_sessions/*`, migration
`0033_executive_runs`, and the web's `uistate`/`cockpit` halves, against
`docs/M26_EXECUTIVE_AUTONOMY_SPEC.md` and ADR-0089 (+ addendum 1). All 298 targeted unit
tests and all 120 `executive` corpus cases were executed live, not merely collected, with
zero forbidden side effects.

The reviewer could not write this file (the security-reviewer agent has no Write tool), so
the findings were transcribed here by the integrator, with the fixes recorded beside them.

**HIGH — an unclassified exception killed the run, and the owner kept being told it was
working.** Two live PoCs: `_artifact_spec_from_text` raising pydantic's `ValidationError`
for a >20,000-character summary, and a real `ExecutiveWorkflow` in the time-skipping
environment where one plain `ValueError` in `s1` terminated the whole execution while an
independent sibling `s2` in the same batch had already succeeded. Fixed in two layers: the
activity classifies what it cannot name (`ERROR_INTERNAL`, never retryable, carrying the
exception's type), and the workflow settles a step whose activity died rather than letting
it escape the loop. Regression: `tests/unit/test_executive_review_findings.py`.

**MEDIUM — the two-run bound was a TOCTOU race**, proven with two threads on separate
connections. Fixed by re-checking the count after the row is in the transaction. The first
regression test for this was itself vacuous — in-memory SQLite pools per thread, so the
racers had separate databases and it passed with the fix removed. It uses a file database
now and was verified to go red without the fix.

**The HIGH's first fix was itself a defect, and it hung the suite.** The new
`except Exception` branch went in between `except StepError: error = exc` and the retry
decision that followed it inside that block, leaving the StepError branch with no `break`
and no attempt counter: every classified step failure re-dispatched the step in an unbounded
hot loop. `test_executive_activities.py` caught it by HANGING - no message, no line number,
suite stopped. The retry decision now lives after the try/except and serves both branches,
and `test_executive_review_findings.py` asserts the bound directly through `asyncio.wait_for`
so the same shape fails in ten seconds instead of stalling. A test that catches a bug by
hanging has not caught it.

**LOW — the restart-mid-run proof is narrower than claimed.** ADR-0089 addendum 2 narrows
decision 5 accordingly rather than leaving the framing to stand.

**INFORMATIONAL** — the `planned` window the integrator claimed to have measured does not
exist on this build; recorded and corrected in addendum 2.

The full "verified sound" list is in ADR-0089 addendum 2 and is what the Stage 24 gate
cites. Related: [[m24-capability-genesis-review]], [[m25-creative-3d-review]] — this is the
third consecutive milestone whose HIGH was found at exactly this point, and all three were
real.

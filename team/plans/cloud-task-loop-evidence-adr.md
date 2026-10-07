# ADR (number at merge): the cloud task loop's evidence run - three choices (card cloud-task-loop-evidence)

**Context.** The card asks for a repeatable integration test that starts the api, the api's
Temporal worker and the cloud companion itself, then runs T1/T2/T4 with the real planner model
on real sites, and for a script that runs the same tasks on dev/staging and writes
`docs/evidence/cloud-task-loop-t1-t2-t4.{json,md}`.

**Decision 1 - opt-in, then no skip.** `tests/integration/test_webtask_cloud_companion.py` runs
its tasks only with `PAGENTOS_TEST_CLOUD_COMPANION=process|container`; without it the module's
`stack` fixture skips. The quality gate runs every `-m integration` test, and this one spends
model money and reaches public sites on each run. Once opted in nothing skips: a companion that
does not start, a missing model key or a worker that exits FAILS with the reason. The two cheap
tests (the script's production refusal, the writer's secret refusal) run in every gate.

**Decision 2 - T2 on a public test form, not the PR-B fixture.** The cloud worker refuses every
loopback/private destination (`browser_agent/destination.py`; the companion never passes
`--allow-private-destinations`), so a local fixture server cannot be reached from it, whatever
name it is given; a hosts entry would need an elevated edit of the owner's machine. T2 uses
`https://httpbin.org/forms/post`, a public form that exists to be filled; `httpbin.org` is added
to the DEV allow-list for the run and removed afterwards (and in cleanup whatever happened).

**Decision 3 - T4's clock from two observations.** `browser.media_status` runs only on a media
session (profile `alarm`/`news`, `session_kind=media`), and the cloud worker opens only its
`research` profile (`browser_agent/cloud/policy.py`), so currentTime cannot be read through the
media operations in the cloud. The test reads the player's "elapsed / length" clock from two
`browser.observe` calls on the task's own session, 8 s apart. If that clock cannot be read the
test fails (it is not counted as playing).

**Consequences.** One schema: the test imports the script's writer. The evidence files are
written only when `PAGENTOS_EVIDENCE_OUT` is set (the card's run sets `docs/evidence`); a gate
run never dirties the tree.

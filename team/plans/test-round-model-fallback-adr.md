# ADR draft — the test round lowers a limited model as the cycle does

- Task: `test-round-model-fallback` (cycle d20261007)
- Status: proposed (the lead numbers it and moves it into `docs/DECISIONS.md`)
- Relates to: ADR-0214 addendum 7 (the model policy and its fallback chain), `team/plans/test-team-adr.md`

## Context

2026-10-07 07:10, round `t-r10070710` on staging `b1f8ef94`: "Test PY plan yazmadı ... tur başlamadı".
The test lead's plan run ended at once with `api_error` 429 `model_requires_usage_credits`
("You're out of usage credits ...") on `claude-fable-5-1`. `scripts/testteam/test-round.ps1`
started the test lead on the lead's configured model and the testers on the worker's,
unconditionally - while the cycle knew Fable was limited (status `limits.fable` = limited until
2026-10-12) and lowered its own runs. Every round would fail the same way until the reset.

## Decision

The test round chooses a run's model with the cycle's own rules, not a copy of them:

1. **What is limited:** `team/limits.json` (`Read-TeamLimitedModels`), plus the cycle's live
   status `limits` - `team/status.json` in file mode, `GET /v1/team/queue/status` in API mode.
   `fable: limited` closes `claude-fable-5-1`, `all: limited` closes every model; only a DATED
   status limit is taken (an undated one in an old status would bar a model for ever). A status
   that cannot be read is said and ignored: the run will say the limit itself.
2. **Which model:** `Get-TeamRunModel` with the role's configured model (`team/models.json`,
   test lead = `lead`, tester = `worker`) and its `fallback` (default on): the next open model
   DOWN the chain. A lowering is said once a round on the console and posted from the test
   lead's seat: `model düşürüldü: <configured> -> <model> (limit, <role>)`.
3. **A run that returns the usage limit** (read by `Read-TeamRunResult`, so the tool's own error
   shape only - a run that merely quotes the words is not one) closes its model for the round
   (`Set-TeamModelClosed`) and is started ONCE more on the next open model - the plan, or the
   same tester job. A second limit gives up: the plan run ends the round with
   "Test PY plan yazmadı ... - kullanım limiti: ..."; a tester job's card becomes `environment`
   (nothing forwarded) and its seat is told `sonuç: error - <job> - model limiti, iletilmedi`.
4. **Every model limited:** before anything starts the round stops with ONE line
   ("TUR BAŞLAMADI: modellerin hepsi limitte (<role>: <chain>); en erken sıfırlanma <time>"),
   exit 1, no run, no seat note. Mid-round the remaining cards stay `planned` with one line
   "TUR DURDU: ...".

The round never writes `team/limits.json`: what it learns lives for the round (the cycle owns
that file).

## Consequences

- With Fable limited the test team keeps working on Opus; with everything limited it says so
  once instead of starting runs that fail at once.
- `sonuç: error` is used for a limit-ended job because the Ofis' Test odası
  (`officeTestRoom.tsx`) ends a seat's job only on `passed|failed|broke|error`. The existing
  `sonuç: environment` note (dead staging session) is NOT recognised there either - its seat stays
  "test ediyor"; follow-up card (outside this task's area).
- In API mode the model SETTING is still read from `team/models.json` only (as before); reading
  `GET /v1/team/queue/models` like the cycle is a possible follow-up.

## Evidence

`scripts/tests/testteam-model-fallback.tests.ps1` (11 cases, fake `claude` answering the real
t-r10070710 result line), each behaviour proven RED by a mutation restored byte-for-byte. The
inspector's return (2026-10-07) added three: "once" on a three-model tester chain (Fable, Opus,
never Sonnet), a dated status `all: limited` closing every model, and an undated status limit
barring nothing.

The local gate (`scripts/quality-gate.ps1`) lists the test-team suites by name and does not run
this one yet; CI takes it by glob. Adding it there is outside this task's area (lead's call).

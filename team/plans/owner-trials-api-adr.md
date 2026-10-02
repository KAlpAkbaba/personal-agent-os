# ADR (owner-trials-api): the owner's trials - the third gate on the server side

Status: accepted (worker, cycle d20261003; the lead numbers it at merge)
Proposal: team/proposals/2026-10-01-deneme-listesi.md (owner approved 2026-10-01)

## Decision
- `owner_trials` items become objects `{id, sentence, machine, expect, verdict, said, at}`
  (`verdict` null | "oldu" | "olmadi"; `said`, `at` null until decided). The old plain-string
  form stays valid. Both schema copies hold it as `$defs/owner_trial` WITHOUT a `type`
  keyword: `required` / `properties` / `additionalProperties` apply to an object only, so a
  string passes and an object is fully checked - no `anyOf`, which neither validator
  (store.py, test_team_queue_schema.py) knows. Cost: a number or null item is not refused by
  the schema; `trials.trial_objects` ignores anything that is not an object.
- `GET /v1/team/approvals` gains `trials`: `[{task_id, title, sha, trial}]` for every
  object trial with `verdict` null on a task in `released` / `awaiting_real_evidence`.
  Old string trials are not listed (no id to decide on); the lead converts them.
- `POST /v1/team/trials/decision {task_id, trial_id, verdict, said}` (owner session):
  `said` is required for "olmadi" (422 `said_required`), at most 500 characters; a decided
  trial is 409 `already_decided`; unknown task / trial 404; a task not in the two states 409
  `not_on_trial`. Same lock rule as the approvals (refused on the file store while a cycle
  runs; taken on the database store), same write lock, ledger event first.
- "oldu" records verdict, words, time; the state is never changed and PROVEN_REAL is never
  claimed. When no trial is left open and all said "oldu", `reason` =
  `sahip denedi: oldu (<at>) - PROVEN_REAL satırını lead yazar`.
- "olmadi" opens `fix-<task_id>-<n>` (first free n; the task id is shortened so the whole
  fits 64 characters) in `approved` with `area: []`, `reason: "alan: lead belirler"`, the
  original's `roadmap_row` and budget, title `Düzelt: <sentence>`, the goal quoting the
  sentence, machine, expectation, the owner's words and the released sha. The original task
  is written first (its conditional write is the race guard), then the fix task.

## For the lead at merge
- Vocabulary: add `team.trial.passed` and `team.trial.failed` (`trials.EVENT_TRIAL_PASSED`,
  `trials.EVENT_TRIAL_FAILED`); the tests monkeypatch them until then.
- Inspector role text: READY_FOR_OWNER lines become `owner_trials` objects.
- Release step: a released task with open trials -> `awaiting_real_evidence` (the route
  accepts decisions in both states, so the order does not matter).
- `scripts/lib/TeamRun.ps1:443` (cycle report "Sahibin gerçek cihazda deneyecekleri") still
  reads the items as strings; it must read `.sentence` / `.machine` / `.expect` of an object.
- Enter 38.3-38.5 as the first open trials.

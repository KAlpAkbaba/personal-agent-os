# ADR (no number yet): the cycle splits an approved proposal itself

Status: accepted by the worker of `cycle-lead-run`; the lead numbers it and moves it into
`docs/DECISIONS.md` at merge time. Serves ADR-0214 addendum 2 (TEAM_PROTOCOL 3a).

## Context

A proposal that serves a roadmap row is approved in advance, but turning it into tasks (area,
goal, acceptance) was done by a person, so the nightly cycle stopped at every proposal.

## Decision

`cycle.ps1` has a split phase between the researcher and the task loop (skipped by
`-ResearchOnly`, which touches no task). For every task with a `proposal`, an empty `area`
and the state `approved`, or `proposed` with a non-empty `roadmap_row`:

1. ONE fresh `lead` run, tools = the role file's minus `Bash`, `Edit` and `Agent` (left out of
   `--allowedTools` and named in `--disallowedTools`). Its card names the proposal, the areas
   that are taken and the shape of the file; it writes only
   `team/plans/<cycle>-split-<id>.json`, a list of task objects.
2. THE SCRIPT judges (`Test-TeamSplit`), the model does not. Refused, whole: a missing field
   (id, title, roadmap_row, goal, acceptance, evidence_expected, area); an id in the queue or
   used twice; an area outside the repository; more than 25 area entries; a shared file
   (HANDOFF, DECISIONS, BUILD_STATE, THIRD_PARTY, queue.json, lock.json) or a directory that
   holds one; an area overlapping a task that is `approved`, `assigned`, `in_progress`,
   `inspecting` or `returned`, or another task of the same split that does not `depends_on` it;
   a branch `main` / `hand-gestures`, an area of hand-gestures; an unknown dependency.
3. A sound split is appended as `approved` tasks (only the fields the script knows; no branch,
   worktree or assignee - the cycle names them), traced by `proposal`, and the queue is
   re-checked with `Test-TeamQueue` before it is saved. They run in the same cycle.
4. The proposal becomes `done` with `reason: "bölündü: <ids>"` (the schema has no field for
   it and this task may not change the schema). A refused or failed split leaves it where it
   was and is a line under "Açık riskler" (`bölme reddedildi: <id>: <reasons>`); the next cycle
   asks again, once.
5. A proposal with an empty `roadmap_row` stays with the owner: `proposed` still moves to
   `awaiting_owner`. `Get-TeamNextRole` says `rest` for a proposal that awaits its split, so
   it is never handed to a worker without an area.

## Consequences

- The lead's role file is unchanged; the split card carries the split-only instructions.
- Approved tasks that overlap a proposed split are counted as taken (stricter than
  `Test-TeamQueue`, which checks only tasks being worked on), because the cycle starts them
  together and two of them would then break the queue.
- Evidence class: PROVEN_AUTOMATED (fake model). A real lead run against the real model is
  NOT_RUN; the first nightly cycle with a roadmap-serving proposal is its proof.

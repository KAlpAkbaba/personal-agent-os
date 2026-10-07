# ADR draft - every test plan job names the JARVIS row it tests (test-plan-names-roadmap-rows)

Status: draft (round 1: red tests only; implementation waits for the area request below)

## Context

2026-10-07 11:45: round t-w10071102 on staging d74a8daa passed scenarios, but no job in its
plan.json had a `roadmap_row` and the round's log had no proof line. So the Ofis strip showed
"staging'de kanıtlı %0". `Get-RoundProof` (test-round.ps1) counts a card under the row its job
names, and otherwise falls back to `why`/`family`. The Cloud Core then files those under
"satır dışı". The role file never asked for the field. The schema did not require it, and
test-round.ps1 reads the plan with plain `Read-TeamJson`, not the schema reader.

## Decision

- `scripts/testteam/schema/plan.json`: `jobs[].roadmap_row` is required. It is a `string` with
  `"values_from": "docs/ROADMAP.md#What JARVIS does"`. That adds no new type, so
  testteam-dry-round's type list holds. The valid values are the counted rows of that table,
  read the way `app.team.progress.parse_jarvis` reads them: the first cell with `**` removed
  and whitespace collapsed, with no NEVER row. A row's full cell and its bold title (when the
  cell starts bold) are both accepted. The value is read back as the bold title, which is the
  canonical form. The Core's `resolve_row` resolves both forms.
- `TestTeamSchema.ps1`: the reader checks `values_from` like an enum (trimmed, whitespace
  collapsed). If the value is absent or not a row, it is Missing, and the record's Why names
  the wrong value and every valid title. `New-TestTeamSchemaSample` writes the first valid
  title, so the generated half documents stay valid.
- `test-round.ps1`: the plan goes through `Read-TestTeamPlan`. An unreadable plan stops the
  round before cards or testers start (exit 1, the Why printed, and a board note when the
  board is on). The cards are made from the shaped `Value`.
- Both test-lead.md copies (kept byte-equal) ask for the field. They point at the JARVIS table
  ("What JARVIS does") and give an example that carries a real title.

## Consequences

- Every passing scenario of a valid plan moves its row on the strip. A plan that would prove
  nothing is refused loudly instead of passing silently.
- A ROADMAP.md row rename refuses old plans. That is intended: the plan is written fresh each
  round.
- Old plan fixtures in the existing suites need a `roadmap_row`. The 3d430def fixture is kept
  word for word, and its test expects the refusal by name.

## Area request (round 1)

Turning the red tests green needs these files outside the card's area:
scripts/testteam/test-round.ps1 (the plan check), scripts/tests/testteam-dry-round.tests.ps1
(its pinned required set {jobs, jobs[0].family}, the 'yeni-aile' plan, and the 3d430def fixture
read as readable), scripts/tests/testteam.tests.ps1 and
scripts/tests/testteam-board-notes.tests.ps1 (their plans have no roadmap_row and would be
refused).

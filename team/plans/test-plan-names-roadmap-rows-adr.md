# ADR draft - every test plan job names the JARVIS row it tests (test-plan-names-roadmap-rows)

Status: draft, implemented in round 3 (the area widened). One step is left for the lead:
`.claude/agents/test-lead.md` must be copied from `scripts/testteam/roles/test-lead.md`
(byte-equal). The worker's write under `.claude/agents/` was refused by the permission layer.

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
  board is on). The cards are made from the shaped `Value`. `Get-RoundProof` reads the plan
  the same way, so the proof names the row by its bold title even when the plan wrote the
  whole cell. An older plan, which `-PostProof` may re-post, is still read as written.
- The match is exact: whitespace is collapsed and the comparison is ordinal. The half
  documents gain an `enum-disi:jobs[0].roadmap_row` case, generated from `values_from`.
- Both test-lead.md copies (kept byte-equal) ask for the field. They point at the JARVIS table
  ("What JARVIS does") and give an example that carries a real title.

## Consequences

- Every passing scenario of a valid plan moves its row on the strip. A plan that would prove
  nothing is refused loudly instead of passing silently.
- A ROADMAP.md row rename refuses old plans. That is intended: the plan is written fresh each
  round.
- The old plan fixtures in the existing suites now carry a real `roadmap_row`. The 3d430def
  fixture keeps its killing shape (a job with no `scenario`) and also gains a row.

- The plan check is stricter than the Core on purpose. `app.team.progress.resolve_row` still
  resolves a loose wording to a row (a prefix of 3+ words, `_head`, `ROW_ALIASES`: "Proactive",
  "Repairs and improve itself"). The plan refuses those so that what the test lead writes is the
  row exactly; the Core's tolerance stays for the other writers (trials, `why`).
- The refusal's Why names all ~16 titles (about 1 KB). The console and the round log carry it
  whole. A board note is cut to the board's 280 characters: the field, the wrong value and
  "geçerli başlıklar: docs/ROADMAP.md What JARVIS does".

## Area (round 3)

In round 3 the area was widened to test-round.ps1 and the three testteam suites, and all of
them are done. The one file left is the installed copy `.claude/agents/test-lead.md`. Writing
it was refused, so it still differs from its source. Until the lead copies it, three checks
stay red: plan-rows, dry-round's byte-equal check and testteam's sha check.

# ADR draft: the Ofis panel shows the last movement apart from the last commit

Task: office-panel-last-activity (cycle d20261006). Number: the lead's.

## Context

The owner, 2026-10-06: "Çalışan 2 ve denetleyicinin son değişiklik süreleri neden bu kadar uzun?"
The seat panel's "Son değişiklik: N dk önce" was the run's `progress.last_change_at` - the newest
COMMIT on the task branch. A worker editing uncommitted files looked idle, and an inspector (who
never commits) showed the worker's commit as its own (migration-rechain-on-merge: 09:02 at 17:15).
The status already carries the measured liveness per run (`last_activity_at`, `idle_minutes`,
`stuck` - pm-stuck-run-check).

## Decision

- `panelProgress` (officeModel.ts) returns `movement` from the seat's `last_activity_at`
  ("N dk önce" / "az önce"; "—" when absent or unparseable) and keeps `lastChange` from
  `last_change_at`.
- The panel shows "Son hareket: ..." first and, smaller (`<small>` in a muted line), "Son kayıt: ...".
- An inspector seat (model role `inspector`) shows "Denetliyor" and labels the commit
  "Çalışanın son kaydı: ...", never "Son kayıt".
- A seat the API marks `stuck` (idle_minutes at the cycle's bound) keeps today's wording through
  `seatLiveness`: "takılmış olabilir - N dk iz yok"; the bound stays the API's decision.
- "Son değişiklik" is gone from the panel. No API change: the fields were already sent.

## Consequences

The panel's time now answers "is it working?"; the commit time stays visible as a secondary fact.
`buildPanel` takes an optional `now` (default the clock) so the panel is testable on fixed time.

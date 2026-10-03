# ADR (unnumbered - the lead numbers it): the role half of "alan dışı geri verme"

Card: `area-widen-role-lines` (proposal `team/proposals/2026-10-02-alan-disi-geri-verme.md`,
rules 1 and 3). The parser is `Get-TeamAreaRequest` in `scripts/lib/TeamArea.ps1` (card
`area-widen-rules`).

## Decision

- `.claude/agents/inspector.md`: when an item of a RETURN can only be fixed in a file outside
  the card's area, the report carries `alan_disi: [path, path]` alone on its own line ABOVE
  the verdict; the verdict stays the last line and stays `RETURN (...)`. A defect inside the
  area is never turned into a request; protected paths are never wished for (the finding is
  written and the lead decides).
- `.claude/agents/worker.md`: after the red acceptance test is written, run and committed, if
  turning it green needs a file outside the area, the worker does not implement and does not
  touch that file; it returns at once with `ALAN_ISTEGI: [path, ...]`, the red test's name and
  one sentence of why. "Never touch files outside your area" is unchanged.
- `scripts/tests/team-area.tests.ps1` section `roles` reads both role files from disk, takes
  each file's own example line and parses it with `Get-TeamAreaRequest` for that role; and the
  inspector's example report (request line, then `RETURN (...)`) is read by both
  `Get-TeamVerdict` and `Get-TeamAreaRequest`. The role text and the parser cannot drift.

## Why the worker asks after the red test and before any implementation

- The red test is the evidence that the request is real: it names the behaviour that cannot
  turn green inside the area, so the lead (and later the cycle) judges a concrete failing
  test, not a guess. A request before it would be a widening asked on intuition.
- Before any implementation, because a half-implementation inside the area that only makes
  sense with the outside file is either thrown away or merged incomplete; and touching the
  outside file "just a little" breaks the area rule the inspector enforces. Returning at once
  costs one short run; the committed red test survives into the next round.

## What the lead does with the line until the wiring exists

The roles only write the line; neither role text promises what the cycle does with it. Until
the cycle's wiring card lands, the lead reads the line in the report, resolves it by hand with
the rules of `TeamArea.ps1` (widen if nobody holds the files, wait behind a holder, refuse a
protected path or past the cap), edits the card's area in the store, and re-runs the card.
Whether real inspectors and workers write the line is counted in the next real cycles'
reports - this ADR claims only the contract between the role text and the parser.

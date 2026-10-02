Split file written: `team/plans/d20261002-split-idea-2026-10-02-koruyucu-testler-is-dalinda.json` — two cards. Nothing was run and no other file was touched.

**Tasks**

1. **`branch-guards-runner`** — the list and the runner, usable by hand the day it merges.
   - Area: `team/guards.json`, `scripts/lib/TeamGuards.ps1`, `scripts/team/guards.ps1`, `services/api/tests/unit/test_team_guards_runner.py`, `.claude/agents/lead.md`, its ADR.
   - Holds the proposal's six guards and runs them in a branch's own worktree with no agent.
   - Adds one paragraph to `lead.md`: run the guards on the integration branch before the 80-minute gate.
   - Carries the proposal's measurement duty: seconds per guard, and the list is shortened if the total exceeds 180 s.
2. **`branch-guards-rules`** (depends on the runner) — the pure rule layer, in a new `scripts/lib/TeamGuardRules.ps1` with its own pytest file and ADR.
   - Writes a run's result to the card's `guards` field, builds the inspector's note, the lead's wiring list and the Ofis sentence.
   - Decides "no gate while a merged task has an unresolved row".
   - One test feeds the real runner's output into the rules, so the two halves cannot drift.

**Why this split**

- **The wiring cannot be carded now.** Every file the cycle step needs is held by a card in work: `cycle.ps1`, `TeamRun.ps1`, `TeamQueue.ps1`, `team-cycle.tests.ps1`, `fake-claude.ps1`, `integrate.ps1`, `inspector.md`, `quality-gate.ps1`, the queue schema and the Ofis page. One overlapping path would refuse the whole list, so I used the same shape as the `area-widen` split: new files only, wiring later.
- **Tests are pytest files that drive PowerShell 5.1**, not a new `*.tests.ps1`. A new suite would need a line in `quality-gate.ps1` and `ci.yml`, which is exactly the "written but run by nothing" defect the proposal cites. The precedent is `test_stt_utterance_corpus.py`.
- **The rules card depends on the runner** instead of running in parallel, because the seats are already full and the dependency buys the join test.
- **Two rule choices are mine and stated in the cards:**
  - A red guard changes no task state and the inspector's verdict is not overridden.
  - A card that was never guard-run does not block the gate; this is marked "sahip incelemesi bekliyor".

**Left for the lead**

- The wiring card, `branch-guards-cycle-wiring`, once `cycle-seat-pool`, `researcher-every-cycle`, `model-policy-floor`, `area-widen-role-lines` and `cycle-auto-integrate` free their areas. It covers the step after the area check, the schema field, the inspector paragraph, the integrate step and the Ofis line, with the proposal's three fake-agent scenarios.
- The TEAM_PROTOCOL clause, the ROADMAP "Approved ideas" line and the ADR numbers, at merge.

**Risks**

- The unit suite gains PowerShell-spawning tests; the rules card is held to one process per file.
- The worktree interpreter choice (no `uv sync`, the main checkout's environment) is left to the worker and proven by a scratch-worktree run, not specified by me.

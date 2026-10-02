**Inspector report: `area-widen-rules` (branch `team/d20261002/worker-area-widen-rules` @ `7ee1e0b0`, second inspection)**

The card's acceptance is met and every number in the worker's report reproduces; the findings below are notes for the lead, none a defect against the card.

**Pass 1 — re-run (Windows PowerShell 5.1, `-NoProfile -File`, clean tree)**
- `team-area.tests.ps1`: **99 passed, 0 failed, exit 0**.
- `team-cycle.tests.ps1`: **145 / 0**; `team-feed.tests.ps1`: **61 / 0**; `script-syntax.tests.ps1`: **148 scripts, 0 failed**.
- Diff `main...HEAD` touches exactly the four files of the area; `git status` is clean before and after my runs.
- Red runs reproduced in a scratch copy outside the repo:
  - HEAD suite against the first commit's lib (`c0145539`): **88 passed, 11 failed, exit 1**, as reported.
  - No `TeamArea.ps1` at all: exit 1.
- Gate step: registered after the roadmap feeder, PS 5.1, `Assert-ExitCode`; I ran the step's own command, green.
- NOT_RUN: the full `quality-gate.ps1` (worker branch; the lead runs it on the integration branch).
- PostgreSQL / host snapshot: not applicable — no table, migration, store, container or `scripts/cloud` file is touched.

**My mutations** — 15, all different from the worker's six; backup copy, restored each time to sha256 `6821b1ed…35dc2e`:
- **14 RED:**
  - conflict judged on equal text only: 7 FAIL
  - protected pattern entries off: 6
  - cap `-gt`: 2
  - idempotence guard removed: 1
  - size bound `-ge`: 1
  - a wait counts as a return: 6
  - key matched without case: 1
  - in-work state filter removed: 8
  - deadlock branch off: 1
  - directory holding a protected path not refused: 9
  - a wait also widens the area: 6
  - size counts every asked file: 1
  - first request line wins: 1
  - nothing-to-widen not refused: 1
- **1 SURVIVED:** removing the self-skip (`$otherId -eq $id`) in the conflict loop, 99/0. The code is correct today (asking `src` while holding `src/a.py` gives `widen`), but no case pins it. With the guard gone the card would wait on itself: nothing written, return not counted.

**Pass 2 — adversarial probe (about 110 inputs)**
1. **Protected gaps, for the lead to decide before the wiring card goes live.** These all answer `widen`:
   - `scripts/lib/TeamArea.ps1` (the protected list itself), `scripts/lib/TeamQueue.ps1`, `scripts/team/cycle.ps1`, `scripts/quality-gate.ps1`
   - `.claude/hooks/session-start.ps1`, `.claude/settings.json`
   - `CLAUDE.md`, `PROJECT_CONSTITUTION.md`, `.gitignore`, `.git/hooks/pre-commit`
   - `scripts/cloud/release-cloud-core.ps1`

   The ADR records most of these as the lead's decision and the card's list is met. `.git` and `.gitignore` are not in the ADR.
2. **The ADR says a wildcard is Bad; only a mid-path one is.** Leading and trailing asterisks are stripped as bold first, so `*.py` becomes the file `.py` and widens (one of the two widenings spent, return not counted). `src/*` becomes the directory `src`, which matches the area convention. `docs/*` is refused. No case pins either.
3. **`Resolution.Add` can differ from what is written.** Asking `lib/x` and `lib/x/y.py` together gives Add with both, but the area gains only `lib/x`. With 24 entries that request is refused as 26 though the result would be 25. It errs towards refusal.
4. **Card-specified, worth knowing:** a holder in `merged` does not conflict, so the card is widened into a file whose change is on the integration branch and not yet on main.
5. **For the wiring card, already in the ADR:**
   - A hand-made `widen` naming `docs/HANDOFF.md` is written by `Add-TeamAreaWidening`.
   - `Files` must be resolved together with `Bad`.
   - A second `wait` on the same holder changes nothing and does not count.
6. **Checked and fine:**
   - Parsing: case variants, CRLF, quotes, a full-width colon, a heading prefix, and a prose key line withdrawing an earlier request.
   - Protected paths: `DOCS/HANDOFF.MD`, `::$DATA`, trailing dot, space or tab, `Secrets/local`, `.ENV`, `b.PEM` are all refused.
   - Holders: areas written with a glob, backslashes, upper case or `./` all give `wait`.
   - Hygiene: the Turkish `Why` is intact (U+0130), BOMs are present, the index is LF, and there are no secrets or machine paths in the diff.

**Evidence classes**
- PROVEN_AUTOMATED: the suite counts, both red runs, 14 of my mutations plus the worker's six classes, the gate step, the unchanged suites.
- NOT_RUN: the full gate.
- Nothing is PROVEN_PROXY or READY_FOR_OWNER; nothing is wired.

**Recommended for the wiring card or the lead (not blocking):** one case for the self-skip, one for `*.py`, and the decision on finding 1.

APPROVE

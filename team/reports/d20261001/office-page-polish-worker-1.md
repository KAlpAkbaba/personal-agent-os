## Şu an üzerinde çalışılan
- task `office-page-polish` · area `apps/web/app/core/office`, `apps/web/tests/office`, `team/plans/office-page-polish-adr.md` · machine: the owner's PC, worktree `.claude/worktrees/team/d20261001/worker-office-page-polish`
- DONE: committed and pushed, worktree clean. The lead writes `docs/HANDOFF.md`; I did not touch it.

## Report
- **sha:** `f56f93103c8ac02840307aca23ebf71428fba1d4` on `team/d20261001/worker-office-page-polish` (the remote branch is at the same sha).
- **Files changed:** 4, all inside the area: `OfficeScene.tsx` (+2/−1), `office.css` (+8/−4), `tests/office/layout.test.tsx` (new), `team/plans/office-page-polish-adr.md` (new).

**What changed**
- **Label:** the task-title label now carries the full title in its `title` attribute, only when the seat has a task. It keeps its ellipsis at the cell's width, and the seat has `min-width: 0` so a long title cannot widen the cell.
- **Wrapping:** `.office-floor` is now `repeat(auto-fill, minmax(max(7rem, calc(25% - 0.75rem)), 1fr))`, with the fixed four 10rem columns and `min-width: 43rem` removed. The old floor was 43rem inside a 42rem column, which is what caused the scrollbar and the clipped label.
- **Row sizes:** at most four seats a row (4+4 at 670 px), two a row on a 320 px phone. `.office-scroll` keeps `overflow-x: auto`, which now only acts when a single cell cannot fit.
- **Figure:** `.office-figure` is `width: 100%; max-width: 8rem` so it shrinks with its cell.
- States, top bar, panel, approvals, the model and the 5 s poll are untouched.

**Tests (8 new, in `layout.test.tsx`)**
- **RED before the implementation:** 5 failed, 32 passed. The failures were the missing `title` attribute, the missing seat `min-width: 0`, no `auto-fill`/`minmax`, and the phone-width budget.
- The other 3 new tests passed before the change by design: the panel shows the full title, the scene markup nests scroll box → floor → seats, and the scroll box keeps `overflow-x: auto`.
- **GREEN after:** office suite 5 files, 37/37; the existing 29 stay green.
- **Full web suite:** 119 files, 2049/2049. `tsc --noEmit` exit 0; `oxlint app/core/office tests/office` exit 0.

**Mutation proof** (each restored from a backup copy; sha256 identical before and after: `OfficeScene.tsx` `6392e3b4…5bcd`, `office.css` `0d32660f…487b`; 37/37 again after restore)

| Mutation | Result |
|---|---|
| M1 `title` attribute removed | RED, 2 failed |
| M2 `repeat(4, 10rem)` put back | RED, 2 failed |
| M3 seat `min-width: 0` removed | RED, 1 failed |
| M4 minimum cell 9rem instead of 7rem | RED, 1 failed |

**Evidence class**
- Label markup, `title` attribute and full title in the panel: PROVEN_AUTOMATED (rendered markup).
- Wrapping grid, no fixed width, two a row on a phone: PROVEN_AUTOMATED on the text of `office.css` only. The test reads the stylesheet and does the rem arithmetic; no browser laid the page out.
- Actual rendering at 670 px and at phone width: NOT_RUN. It becomes PROVEN_REAL when the owner looks at `/core/office` during the next cycle.

**Not done, and why**
- The card's title also says "tahmini USD koşu sürerken de güncellensin", but the goal lists only the label and the wrapping, says "nothing else changes", and has no acceptance for USD. I did not touch it; it needs its own card if wanted.

**Open risks**
- In the cell-width range between phone and 670 px the eight seats sit 3+3+2.
- In a narrow phone cell the label shows only a few characters; the full title is in the tooltip and the panel. A touch screen has no hover, so there the panel is the only way to read it.
- The ADR text is in `team/plans/office-page-polish-adr.md`, unnumbered, for the lead to number.

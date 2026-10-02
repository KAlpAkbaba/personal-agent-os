## Şu an üzerinde çalışılan
- Task `office-page-polish` (returned once by the inspector), cycle d20261001, worker, the owner's PC.
- Area: `apps/web/app/core/office`, `apps/web/tests/office`, `team/plans/office-page-polish-adr.md`.
- Branch `team/d20261001/worker-office-page-polish`, worktree clean, pushed.

## Result
- **sha:** `efdd8f3d7d6023267cc474e999f1f525fdb0235b`, on top of `f56f9310`; the remote branch is at the same sha.
- **Files changed:** 2, both inside the area: `apps/web/tests/office/layout.test.tsx` and `team/plans/office-page-polish-adr.md`. `office.css` and the components are unchanged.

## Return point 1: the 25% cap is now tested
- **New test:** "are four a row at the owner's width and never five: eight seats are 4+4, not 5+3". It does auto-fill's arithmetic on the track minimum that `office.css` declares, including the whole `max(7rem, calc(25% - 0.75rem))` term.
- **What it asserts:** exactly 4 seats a row at the owner's 42rem box, and never more than 4 at any width from 17rem to 120rem in 0.5rem steps.
- **Phone test rewritten:** it now uses the same arithmetic and asserts exactly 2 a row at 17rem. The old regex that read only the first rem number is gone.
- **RED-first:** the CSS already existed, so the new test was green on arrival; its RED proof is the mutation below.

| Mutation on `.office-floor` | Result |
|---|---|
| I3: cap removed (`minmax(7rem, 1fr)`) | RED, 1 failed / 37 passed, `expected 5 to be 4` |
| I3b: `25%` changed to `20%` | RED, 1 failed, `expected 5 to be 4` |
| I3c: gap term dropped (`25% - 0rem`) | RED, 1 failed, `expected 3 to be 4` |
| I3d: minimum `7rem` changed to `9rem` | RED, 1 failed (phone test), `expected 1 to be 2` |

- **Restore:** `office.css` sha256 was `0d32660fe0644d53480d014cdf9ba11eba3f63fa502eef1478d5fc72d86f487b` before and after, restored from a backup copy in `%TEMP%`.
- **After restore:** `tests/office` is 5 files, 38/38 passed (37 before plus the new one).

## Return point 2: the USD half (for the lead)
- "Tahmini USD koşu sürerken de güncellensin" is **not delivered** here. It has no acceptance criterion and sits on the data side, not the layout.
- The ADR text now says so under "Not in this change". It needs its own card or removal from the title.

## Fast checks
- `tsc --noEmit`: exit 0.
- `oxlint app/core/office tests/office`: exit 0, no warnings. The whole-app run shows only existing warnings outside the area.
- Full web suite outside `tests/office`: NOT_RUN, because no file outside the office tests changed.

## Evidence classes
- Label markup, `title` attribute, full title in the panel: PROVEN_AUTOMATED (unchanged from `f56f9310`).
- "Four a row at most, never 5+3" and "two a row on a phone": PROVEN_AUTOMATED as arithmetic on the declared CSS.
- The same two claims in a real browser: PROVEN_PROXY from the inspector's headless run. I did not run a browser.
- The owner's look at `/core/office` in the next cycle: READY_FOR_OWNER.

## Open risks
- The test reads two track forms only: `max(Xrem, calc(P% - Yrem))` or a plain `Xrem`. Any other form fails with "a track minimum this test can read", so a rewrite of the track has to update the test too.
- The arithmetic assumes 1rem = 16px and `main`'s 1.5rem padding from `globals.css`; it does not read `globals.css`.
- Unchanged from the inspector's notes: a 320 px desktop window with a classic scrollbar gets one seat per row, and on touch screens the full title is readable only in the panel.

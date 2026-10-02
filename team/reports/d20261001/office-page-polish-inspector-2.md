**Inspector report: office-page-polish, branch `team/d20261001/worker-office-page-polish` @ `efdd8f3d` (remote at the same sha, tree clean)**

**Pass 1: run it**
- **Office tests:** `tests/office` is 5 files, 38/38 passed, matching the worker's number.
- **Whole web suite:** 119 files, 2050/2050 passed. The worker left this NOT_RUN; I ran it.
- **Fast checks:** `tsc --noEmit` exit 0; `oxlint app/core/office tests/office` exit 0.
- **Scope:** the worker's two commits touch only `OfficeScene.tsx`, `office.css`, `layout.test.tsx` and the ADR text, all inside the area. The other files in `git diff main...` come from the lead's merge `69730f8e`.
- **Database / infrastructure:** none touched, so there is no Postgres run to do.

**Real run (headless shell, real `globals.css` + `office.css`, hand-written copy of the scene markup, 8 seats, 90-character titles on 4 of them)**

| Host width | Seats per row | Sideways scroll | Labels outside their cell |
|---|---|---|---|
| 280 px | 1 | no | 0 |
| 320 / 360 / 414 px | 2 | no | 0 |
| 560 px | 4+4 | no | 0 |
| 670 px (owner's) | 4+4 | no | 0 |
| 720–1920 px | 4+4 | no | 0 |

- All four long labels are cut with an ellipsis at every width, with the panel open and closed.
- Control with the old CSS from `69730f8e` on the same harness: sideways scroll at 670 px with 2 seats beyond the box, and 6 beyond it at 320 px. The harness does see the owner's defect.
- The markup was hand-copied from `OfficeScene.tsx`, not rendered by Next, so this is a proxy.

**Mutations (mine, 10, each restored from a backup copy; sha256 of both files checked OK, 38/38 after)**

| Mutation | Result |
|---|---|
| M1: `title` attribute removed (the card's mutation) | RED, 2 failed |
| M2: `title=""` on seats without a task | RED, 1 failed |
| M3: `title` = seat name | RED, 2 failed |
| M4: `repeat(4, 10rem)` back | RED, 3 failed |
| M5: `min-width: 43rem` back on the floor | RED, 1 failed |
| M6: seat `min-width: 0` removed | RED, 1 failed |
| M7: label `max-width: 100%` removed | RED, 1 failed |
| M8: figure `width: 8rem` again | RED, 1 failed |
| M9: `auto-fit` instead of `auto-fill` | RED, 3 failed |
| M10: scroll box `overflow-x: hidden` | RED, 1 failed |

**Pass 2: break it**
- **USD half of the title:** "tahmini USD koşu sürerken de güncellensin" is not delivered. The worker says so and the ADR records it; the card has no acceptance criterion for it. Lead: cut a separate card or drop it from the title before closing this one.
- **Layout tests are text and arithmetic on the CSS, not a browser.** They assume 1rem = 16px and `main`'s 1.5rem padding without reading `globals.css`. My browser run agrees with the arithmetic at every width measured.
- **Below about 300 px:** one seat per row, no scroll. Acceptable; the card asks for two a row at phone width (320 px), which holds.
- **Touch screens:** the `title` tooltip does not show; the full title is readable only in the panel. The card accepts this.
- **HANDOFF:** the worker's commits do not update `docs/HANDOFF.md` (outside the area); that is the lead's at merge.
- **Nothing found on:** secrets, paths, contract drift, privacy, or CPU/memory (CSS only). Rollback is a revert of two commits.

**Evidence classes**
- Label markup, `title` attribute, full title in the panel: PROVEN_AUTOMATED.
- Wrapping grid, at most four a row, two at phone width: PROVEN_AUTOMATED (arithmetic) and PROVEN_PROXY (headless run).
- No sideways scroll and no clipped label at the owner's 670 px: PROVEN_PROXY.
- The owner's look at `/core/office` in the next cycle: READY_FOR_OWNER.
- USD estimate updating during a run: NOT_RUN (not implemented).

`APPROVE`

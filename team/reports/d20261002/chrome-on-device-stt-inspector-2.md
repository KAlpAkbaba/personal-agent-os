**Inspector report (second pass): chrome-on-device-stt @ `993abde4` (cycle d20261002)**

All four returned findings are closed and re-proven on the final tree; one new, low-severity test gap is listed for the lead and does not block.

**Pass 1 — re-run from a clean tree**
- Six local/STT suites: 103/103. Full web suite: 125 files, 2144/2144 on the first run (the worker's one-off `discoverability.test.tsx` failure did not reproduce). PROVEN_AUTOMATED.
- `tsc --noEmit` exit 0; oxlint exit 0, no warnings in the new files (the same three existing `on…=` warnings in `localMode.ts`). PROVEN_AUTOMATED.
- `test_voice_local_mode.py`: 9 passed (the worker left it NOT_RUN). No file under `services/` or `packages/` changed, so last round's check that the server accepts `payload.stt_engine` still stands. PROVEN_AUTOMATED.
- The two worker commits touch 10 files, all inside the area. No table, migration, store, script or infra: the Postgres and host-snapshot rules do not apply.
- NOT_RUN: a real Chrome (the pack exists only in the owner's own Chrome); `quality-gate.ps1` and the PS suites (no API or script file changed). READY_FOR_OWNER: twenty sentences on MAIL in `olc`.

**Mutations** (restored from a backup copy each time; sha256 identical before and after — `localMode.ts` `ae0417a60f8c…`, `sttSetting.ts` `1768ae5adf45…`; tree clean)

| Mutation | Result |
|---|---|
| Mine: `olc` ends the run with `abort()` instead of `stop()` | RED, 2 |
| Mine: same-leg guard removed in `endRunForOtherLeg` | RED, 1 |
| Mine: engine read when the final is handled, not when heard | RED, 2 |
| Mine: UI flag true for any stored value | RED, 1 |
| Mine: `downloading` named `chrome-bulut` | RED, 3 |
| Mine: `install()` without `processLocally` | RED, 1 |
| Mine: phrases not cleared on the other leg | RED, 3 |
| Mine: `leaveDevice` keeps `running` true | RED, 2 |
| Mine: `setting !== "olc"` removed in `endRunForOtherLeg` | **GREEN** on the worker's tests |
| Card: default flipped to `acik` | RED, 6 |
| Card: a "no" still installs | RED, 1 |
| Worker's, re-run on the final tree: `!this.running`; late install / `useDevice` / probe guards; no aliases from the browser sources; `olc` not ending a silent run | RED, 2 / 2 / 2 / 2 / 3 / 3 |

The worker had not re-run mutations after the last `fake.ts` cast and `.toSorted()` edit; the last two rows close that.

**Pass 2 — break attempts** (seven scratch scenarios, 7/7 passed on the real code; scratch file deleted)
- `olc`, silent turn, Chrome's late `end`: a sentence cut by the stop keeps the old run's name, nothing is written until `end`, and there is exactly one restart.
- Stopping the mode while a run is ending: the stale `end` writes nothing.
- A device run refused right after the silent-turn restart: recorded fallback, no further stops, names stay `bilinmiyor`.
- Mixed spoken turns under both `end` timings alternate every time.
- `kapali`, 30 turns: zero writes on the recogniser, zero stops, one `available()`, zero `install()`, no question.

**Findings**
1. **One ADR claim is unpinned (low).** "`acik` never stops the recogniser for this" and "after a yes the device leg begins at the next restart" hold in the product, but no committed test fails when the `olc`-only guard is removed. My scratch case does: `acik` + `downloadable` + `answerPackQuestion(true)` + three silent turns, expecting `stops === 0`, `starts === 1` and no assignments. This path is unreachable today because no shell button calls `answerPackQuestion`. The lead should add this test with the `VoiceControlView` button.
2. **Not a defect, for the lead:** the server line (`service.py`, allow-listed `stt_engine`) is still open. Until it lands the engine name is accepted and dropped, so the owner's `olc` measurement records nothing; it must merge before that READY_FOR_OWNER step.
3. The ADR now records last round's items 5 and 6 and the silent-turn restart cost; I read it against the code and found no drift.
4. No secrets or paths; aliases stay out of the log, snapshot and wire; nothing installs without a click.

**Housekeeping:** my `uv run` created a gitignored `services/api/.venv` in this worktree. Nothing of mine is left running; the `pytest tests/unit` process on the main checkout (started 07:52) is not mine.

APPROVE

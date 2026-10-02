## Şu an üzerinde çalışılan
- Task: pack-question-button (d20261003, ADR-0249 plan D3). Area: VoiceControlView.tsx, VoiceControl.tsx, the new test file and team/plans/pack-question-button-adr.md. Machine: the owner's build PC (Windows), worktree worker-pack-question-button.

## Report
The language-pack question can now be answered from /core. When local mode is on and the mode holds `packQuestion`, the cell shows the question with "Evet, indir" and "Hayır" buttons. "Evet, indir" starts Chrome's `install()` within the same click. Nothing has run in a real Chrome yet.

**Commit:** `1c5a322887818b478ee0218e684b457db9ea8d8b`, pushed to `team/d20261003/worker-pack-question-button`. The worktree is clean.

**Files changed:** 4, all inside the area.
- `apps/web/app/core/VoiceControlView.tsx`:
  - `LocalModeViewProps` gains an optional `onAnswerPack`.
  - A small `PackQuestion` component shows the snapshot's own sentence in `data-local-pack-question`. The view does not write any of the question text itself.
  - The two buttons use `core-chip` and `type="button"`. Each click calls `answer(true)` or `answer(false)` directly, with nothing in between.
  - With no handler, the question is shown with no buttons.
  - The start/stop button and the listening indicator have not moved.
- `apps/web/app/core/VoiceControl.tsx`: a new exported pure function, `buildLocalViewProps(...)`, builds the local props. `onToggle`, `onStart` and `onStop` keep their old bodies, and `onAnswerPack: (yes) => w.answerPackQuestion(yes)` is added. The component now takes `answerPackQuestion` from `useLocalVoiceMode()`.
- `apps/web/tests/uistate/voice-control-pack-question.test.tsx` (new, 9 tests).
- `team/plans/pack-question-button-adr.md`: covers the click-must-stay-synchronous rule and how the test holds it, the optional prop, and what is shown without a handler.

**Tests:** all 9 are in the new file and cover acceptance cases 1–6.
- **Red first (unchanged view):** 6 failed, 3 passed out of 9. The 3 that passed only check that something is absent (no question, switch off, start/stop still there), so they hold trivially on the old view. The others failed because the pack markup and `buildLocalViewProps` did not exist.
- **Green after the change:** 9 of 9 pass.
- **The synchronous rule:** the test builds a real `LocalVoiceMode` from the fakes (setting `acik`, `FakeOnDevice('downloadable')`) and passes it through the real builder and view. It walks the element tree, skipping `next/link`, which uses hooks. On the line right after the yes `onClick`, with no await and no tick, `installCalls` equals `[{langs:['tr-TR'], processLocally:true}]`.

**Mutation RED (backup copy restored; sha256 identical before and after):**

| Mutation | Result |
|---|---|
| M1: yes wrapped in `void Promise.resolve().then(...)` | 3 red, including the install-before-return test |
| M2: both buttons pass `true` | 3 red, including the "no" test and the {true, false} test |
| M3: builder does not pass `answerPackQuestion` | 4 red, including the install test |

- Restored hashes: VoiceControlView.tsx `59820ab6…11c48`, VoiceControl.tsx `bf0f5baa…7aad2`.

**Web gates** (run directly with `node node_modules/...`, not `pnpm exec`):
- **vitest, whole suite:** 126 files and 2173 tests, all passed. `tests/uistate`, `tests/voice` and `tests/pages` were not edited and are green.
- **tsc --noEmit:** exit 0.
- **oxlint (`app` and `tests`):** exit 0, 0 errors, 38 warnings, none in my files.

**Evidence class:**
- PROVEN_AUTOMATED: the red-first run, the gate counts, the 3 mutation REDs and the commit hash.
- PROVEN_PROXY at most for the page itself: it runs on fakes, with no browser.

**NOT_RUN:**
- Chrome's real `install()` from a real click: whether Chrome accepts the activation, and how big the pack is. No real browser has run this.
- Clicking "Evet, indir" would download a language pack to the owner's C: drive, which is his decision, so I did not do it.

**READY_FOR_OWNER, after the release:** only if he decides to set `pagentos.core.localStt` to `acik` on a machine without the pack.
- Open /core on MAIL, turn on "Yerel mod", press "Dinlemeye başla".
- He should see the question with two buttons; pressing "Hayır" should remove it and download nothing.

**Open risks:**
- No CSS was added (`core.css` is outside the area), so the question is an unstyled span and the buttons sit inside the flex cell. The layout has not been checked on screen.

# WORKLOG — B48 row 331 "Ambient policy UI": thresholds and quiet hours editable

Branch: `worktree-agent-abda01a215aa139a6`
Base: `6fa7dfe3d1efe5f52227a8ba4d8b9a87dec231f0` (local `main`)

## What the investigation found before any code was written

`services/api/app/ambient/routes.py`'s `PUT /v1/ambient/policy` already accepted every
threshold and the quiet-hours window as owner-gated, validated fields — `away_after_s`,
`asleep_after_s`, `asleep_min_confidence`, the four holdoffs, `asleep_after_outside_quiet_s`,
`camera_unknown_grace_s`, `quiet_hours`/`clear_quiet_hours` — since ADR-0079
(`8f3e005`, 2026-09-07). `app.ambient.service.set_policy` already ledgers every change
(`EVENT_TYPE_AMBIENT_POLICY_CHANGED`) and starts the owner-command holdoff for it, exactly
like the four switches. `services/api/tests/unit/test_ambient_hardening.py` and
`test_alarms_routes.py` already covered the server side of this.

The gap the matrix row actually named was narrower than the task description assumed: the
**web panel** (Cockpit and Settings) never offered a form for any of it — only the four
switches and the camera-mode chips were wired to a PUT. The voice tool's schema was
(correctly) never given these fields either; that stays true.

Given that, the work done here is:

1. A web-only feature (the thresholds/quiet-hours form), plus
2. One real safety hardening the review surfaced: the four holdoff fields accepted `0`
   through the PUT (`ge=0`), which is now `ge=1`. A zero holdoff is indistinguishable from
   no holdoff at all, and now that an owner can reach these fields from a form rather than
   only a script, that footgun is closed. See `services/api/app/ambient/routes.py`'s new
   comment on `PolicyIn` and the regression test
   `test_a_zero_holdoff_is_refused` in `test_alarms_routes.py`.
3. A voice-tool regression test (`test_ambient_set_policy_schema_stays_five_switches_row_331_did_not_add_to_it`
   in `test_alarms_voice.py`) pinning that the schema stays exactly the five switches.

## Design decisions (autonomous, per docs/DEVELOPMENT_POLICY.md — recorded here for the
## owner/lead to fold into docs/DECISIONS.md; this worktree does not edit that file)

**ADR note — Ambient thresholds form: explicit clear, not blank-means-clear.**

Clearing the quiet-hours window is a separate, explicit checkbox
(`clear_quiet_hours`, mapped straight onto the server's own `PolicyIn.clear_quiet_hours`),
never inferred from both time fields being blank. The alternative — "both blank means
clear" — cannot be told apart from "the form has not finished loading the server's
current window yet," and conflating the two risks silently wiping a quiet-hours window
neither the owner nor the form touched. Every other threshold field keeps the ADR-0079
"a PUT is a PATCH of the fields named" contract: a blank field is left alone. This is a
reversible, UI-only decision; nothing about the server contract changed for it.

**ADR note — the four ambient holdoffs floor at 1 second, not 0.**

`input_holdoff_s`, `command_holdoff_s`, `alarm_holdoff_s`, `return_holdoff_s` in
`PUT /v1/ambient/policy`'s `PolicyIn` now require `ge=1` (was `ge=0`). A holdoff of zero
seconds is indistinguishable from no holdoff at all, and the entire point of ADR-0079's
holdoffs is that the owner's own command, a just-refused keyboard input, or an alarm firing
buys a pause before anything automatic runs again (`app/ambient/policy.py`'s
`OWNER_COMMAND_HOLDOFF_S` docstring documents a real 2026-09-09 incident from exactly this
kind of unguarded zero). This field was reachable only via a raw PUT before; now that the
web panel offers it as a plain number input, the floor closes that footgun. No default
changed and no existing caller sets zero (checked across `services/api/app` and
`services/api/tests`).

## Proposed feature-matrix row 331 (15 pipes, no `|` inside any cell)

Replaces the current row 331 in `docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md`
(not edited by this worktree — for the owner/lead to apply):

```
| 331 | Ambient policy UI | Kokpit ve Ayarlar'da dört anahtar + cihaz kamerası kipi (kapalı / periyodik / sürekli) + eşikler (yokluk, uyku, uyku güveni, dört bekleme, sessiz saat dışı uyku eşiği, kamera tazelik payı) ve sessiz saatler aynı owner-gated PUT ile düzenlenebilir; sıfır bekleme artık reddediliyor; sesli aracın şeması değişmedi | Tam | DONE | PA | P2 | 685 | B48 | CockpitPanels.tsx:AmbientPanel; lib/cockpit/api.ts:updateAmbientThresholds; lib/cockpit/useAmbientThresholds.ts | b48-web.test.tsx · b48-camera-web.test.tsx · b48-thresholds-web.test.tsx · test_alarms_routes.py (eşik/sessiz saat PUT, sıfır bekleme 422) · test_alarms_voice.py (ambient.set_policy şeması değişmedi) · test_camera_b48.py (PUT camera_mode, 422) | tarayıcıda görsel doğrulama | no | Sessiz saatleri kapatmak ayrı bir onay kutusu; boş bırakmak yetmez |
```

(Pipe count: 15, verified with `grep -o '|' <<< "$ROW" | wc -l`.)

## Files touched

Backend:
- `services/api/app/ambient/routes.py` — the four holdoff fields' floor, `ge=0` -> `ge=1`.
- `services/api/tests/unit/test_alarms_routes.py` — `test_a_zero_holdoff_is_refused`
  (parametrised over the four fields) and
  `test_row_331_every_documented_threshold_and_quiet_hours_are_owner_editable`.
- `services/api/tests/unit/test_alarms_voice.py` —
  `test_ambient_set_policy_schema_stays_five_switches_row_331_did_not_add_to_it`.

Web:
- `apps/web/app/lib/cockpit/api.ts` — `AmbientPolicy` gains every threshold field and a
  typed `quiet_hours`; `AMBIENT_THRESHOLD_FIELDS`/`_BOUNDS`/`_LABEL`,
  `validateAmbientThreshold`, `validateAmbientQuietHours`, `updateAmbientThresholds`
  (reuses `describeErrorDetail` from `../voice/api` for the server's owner-facing 422 body).
- `apps/web/app/lib/cockpit/useAmbientThresholds.ts` (new) — the form's pure logic
  (`draftFromPolicy`, `buildThresholdChanges`, `emptyThresholdsDraft`) plus
  `useAmbientThresholdsControl`, following `useRoutineControl.ts`'s
  pure-function-plus-thin-hook shape.
- `apps/web/app/core/panels/CockpitPanels.tsx` — `AmbientPanel` takes an optional
  `thresholds` control prop; renders one bounded number input per field, the quiet-hours
  window, the explicit clear checkbox, a Save button and the server's own message.
- `apps/web/app/core/cockpit/page.tsx`, `apps/web/app/settings/page.tsx` — wire
  `useAmbientThresholdsControl` into both panels that render `AmbientPanel`.
- `apps/web/tests/eye/b48-thresholds-web.test.tsx` (new) — 29 tests: bounds table,
  quiet-hours validation, `buildThresholdChanges`, the write (including both 422 body
  shapes), and the panel's rendering with a hand-built control object.
- `apps/web/tests/eye/b48-camera-web.test.tsx`, `apps/web/tests/uistate/minimal-mode.test.tsx`,
  `apps/web/tests/cockpit/quiet-families.test.tsx` — updated `AmbientPolicy` fixtures for
  the widened type (compile-only changes, no behaviour asserted differently).

## Tests: before / after

Before this task's changes, all suites below were green on `6fa7dfe` (base). After:

- `services/api/.venv` pytest, `-p no:cacheprovider`:
  - `test_ambient_hardening.py`, `test_ambient_policy.py`, `test_ambient_service.py`,
    `test_alarms_routes.py`, `test_alarms_voice.py`, `test_camera_b48.py`,
    `test_presence_b48.py`, `test_presence_eye_invalidation.py`, `test_voice_eye_tools.py`:
    **256 passed**.
  - Voice corpus, ambient-only (`test_owner_utterance_corpus.py -k ambient`): **15 passed**.
  - `ruff check` on every touched backend file: clean.
- `apps/web`, `node node_modules/vitest/vitest.mjs run`: **1948 passed** (108 files),
  up from 1919 before (29 new tests in `b48-thresholds-web.test.tsx`).
- `node node_modules/typescript/bin/tsc --noEmit`: clean.
- `node node_modules/oxlint/dist/cli.js` (the repo's `bin/oxlint` shim is a POSIX script,
  not runnable through `node` directly — invoked its `dist/cli.js` instead): exit 0, only
  pre-existing warnings in unrelated files.

## Mutation RED proofs (sha256-verified backups, restored with a file copy — never
## `git checkout --`)

Backups taken to the session scratchpad before any mutation; sha256 recorded before
mutating and re-checked after restoring (all four matched exactly):

1. `services/api/app/ambient/routes.py`: `command_holdoff_s` bound reverted `ge=1` ->
   `ge=0`. `test_a_zero_holdoff_is_refused[command_holdoff_s]` went RED
   (`assert 200 == 422`, i.e. the PUT silently accepted zero). Restored; suite green again.
2. `services/api/app/voice/realtime_sessions/tools_ambient.py`: added
   `"away_after_s": {"type": "integer"}` to `ambient.set_policy`'s tool schema.
   `test_ambient_set_policy_schema_stays_five_switches_row_331_did_not_add_to_it` went RED.
   Restored; full `test_alarms_voice.py` (75 tests) green again.
3. `apps/web/app/lib/cockpit/useAmbientThresholds.ts`: changed the half-filled
   quiet-hours guard from `start === "" || end === ""` to `start === "" && end === ""`
   (the same condition as the branch above it, so it could never fire). The test "one
   quiet-hours time filled and the other blank is refused, never silently dropped" went
   RED — the mutated code would have sent `{end: ""}` to the server as a real quiet-hours
   object. Restored; the 29-test file green again.
4. `apps/web/app/lib/cockpit/api.ts`: `AMBIENT_THRESHOLD_BOUNDS.input_holdoff_s.min`
   reverted `1` -> `0`. "row 331's own safety rule: the four holdoffs refuse zero" went
   RED. Restored; full web suite (1948 tests) and `tsc --noEmit` green again.

## Not done / left for the owner or a follow-up

- The matrix row and `docs/DECISIONS.md` are not edited by this worktree (out of scope
  per the task); the text above is ready to paste in.
- "Tarayıcıda görsel doğrulama" (a real browser click-through of the new form) is not
  done here — this suite has no jsdom/browser harness (`environment: "node"` in
  `vitest.config.ts`), consistent with how the four switches and camera-mode chips were
  verified in B48 originally.

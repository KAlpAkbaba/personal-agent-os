**Şu an üzerinde çalışılan:** `chrome-on-device-stt` — yerel kipte Chrome'un cihaz içi Türkçe tanıması, ayar arkasında (varsayılan KAPALI). Alan: `apps/web/app/lib/voice`, `apps/web/tests/voice`, `team/plans/chrome-on-device-stt-adr.md`. Makine: MAIL. Durum: commit edildi, push edildi, worktree temiz.

**sha:** `9b4974e240eb949546a0fb3d4935793c90b43dc0` on `team/d20261002/worker-chrome-on-device-stt` (pushed).

**Files changed: 9, all inside the area**
- New: `sttSetting.ts`, `sttPhrases.ts`, `tests/voice/local-stt-engine.test.ts`, `tests/voice/stt-phrases.test.ts`, `team/plans/chrome-on-device-stt-adr.md`.
- Changed: `localMode.ts`, `fake.ts`, `useLocalVoiceMode.ts`, and the one exact-body assertion in `local-mode.test.ts`.

**What it does**
- **Setting:** `localStorage` key `pagentos.core.localStt` takes `kapali` / `acik` / `olc`; anything else, or unreadable storage, is `kapali`. No UI; the flag `pagentos.core.localSttUi` is exported for the shell.
- **`kapali`:** never writes `processLocally` or `phrases`, installs nothing, asks nothing. It runs one read-only `available()` probe, not awaited, so the engine name is honest (plan D1).
- **`acik`:** with the pack available it sets `processLocally`, then the phrase list, before `start()`. With `downloadable` it puts the question in `snapshot.packQuestion`; `install()` runs only from `answerPackQuestion(true)`. Every other case falls back to today's path with a reason in `snapshot.sttFallback` and the log.
- **`olc`:** alternates the two legs between recogniser runs; phrases ride only on the device leg.
- **Engine name:** every utterance carries `payload.stt_engine`, captured when the final arrives.
- **Phrase list:** pure, capped at 64, device aliases first, then application names, then open-verb forms.

**Decisions I took (recorded in the ADR text)**
- The capability list has no application-name field, so a name is what stands before an open verb in its example sentences ("Hesap makinesini aç" → "Hesap makinesini").
- The consent line carries no size, because the plan's "~60 MB" was never measured.
- After a yes, the device leg starts at the recogniser's next restart, not mid-sentence.

**RED → GREEN — PROVEN_AUTOMATED**
- RED before any implementation: 27 tests failed and `stt-phrases.test.ts` could not import its module.
- GREEN: the three local-mode suites pass 68/68. Full web suite: 124 files, 2129 passed.
- `tsc --noEmit` exit 0. oxlint: 0 errors, 38 warnings, none in the new files; the three in `localMode.ts` are existing `on…=` assignments.
- `services/api/tests/unit/test_voice_local_mode.py` (reads `localMode.ts`): 9 passed.

**Mutation RED — PROVEN_AUTOMATED**
Each was restored from a backup copy; sha256 before and after were identical (`sttSetting.ts` 1768ae5a…, `localMode.ts` 17b266dc…).

| Mutation | Tests failed |
|---|---|
| Default flipped to `acik` | 6 |
| Consent guard (`if (!yes)`) removed | 1 |
| Engine read at `turnBody` instead of at the final | 1 |
| Phrases left on the cloud leg | 3 |
| `install()` called as soon as the status is `downloadable` | 3 |

**For the LEAD at merge (outside my area)**
1. **Server:** `services/api/app/voice/realtime_sessions/service.py`, utterance branch (`meta.update` before `_audit(ACTION_INTENT_RESOLVED)`), needs one allow-listed `stt_engine` line plus a test. `_say` in `test_voice_local_mode.py` should post the payload. Until then the value is accepted and dropped: a scratch copy of that test with the payload passed 9/9 on the unchanged server (scratch file deleted).
2. **Shell:** `VoiceControlView.tsx` must draw `packQuestion` with a yes/no button calling `answerPackQuestion`, which the `useLocalVoiceMode` handle now exposes. Until then `acik` on a machine without the pack is only a recorded fallback, so on MAIL today it cannot reach on-device.

**Could not do / NOT_RUN**
- No real Chrome ran any of this. Behaviour comes from the integrator's reading of Chromium main, not from the owner's Chrome 154.
- The turn record does not yet hold the engine (server line above).

**Open risks**
- `downloadable` is named `chrome-bulut`, but Chromium can mask an installed pack as `downloadable` when `tr-TR` is not an accept-language, so the name could be wrong there.
- On today's path a `start()` that throws something other than `InvalidStateError` is still swallowed, as before; I left `kapali` unchanged on purpose.
- With the pack installed, `olc` compares phrases on/off, not cloud versus device (plan D2).

**READY_FOR_OWNER**
1. In DevTools on MAIL: `await SpeechRecognition.available({langs:['tr-TR'], processLocally:true})`.
2. Twenty sentences before any install — the only true cloud baseline.
3. After the lead wires the button: yes to the pack, then twenty sentences in `acik` (or `olc`). Which engine misheard less is the owner's call.

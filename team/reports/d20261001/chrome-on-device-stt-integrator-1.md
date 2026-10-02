# Integrator report — chrome-on-device-stt

**Choice:** ADOPT the browser feature behind the existing `LocalModeDeps` seam. It is Chrome itself, so there is no library, no pin and no new dependency. Plan: `team/plans/chrome-on-device-stt-integration.md` (main checkout, uncommitted). Nothing was installed and no browser binary was run.

**Licence:** Chrome's proprietary Turkish language pack, delivered by Chrome's own updater; nothing enters our tree.

**Versions (primary sources, read 2026-10-02):**
- `available()` / `install()` / `processLocally`: Chrome 139, stable 2025-08-05 (the 2025-01-07 ship intent had named 135). Desktop only.
- `phrases`: 142 per MDN compat data, but the 2025-07-09 ship intent says 140, so the worker must feature-detect.
- `quality`: 150 (ship intent 2026-05-06).
- tr-TR is an on-device language, both in the explainer's Chrome list and in Chromium's pack table.

**Three findings from Chromium source (main `fc2e8e54`) that change the card:**
1. **Once the Turkish pack is installed, Chrome uses it even when `processLocally` is false.** "kapalı" and the "cloud" leg of "ölç" then stop being cloud. This is what issue 521896368 describes.
2. **`install()` needs a click and `processLocally: true` in its options.** A spoken "evet" carries no user activation, and without the option it resolves false (the MDN example omits it).
3. **`phrases` with server recognition is an error** (`phrases-not-supported`), not a silent no-op. Separately, `quality` other than the default selects different, multi-GB models, so the worker must never set it.

**Could NOT be verified:**
- Issue 521896368 needs a Google sign-in; I read the title and a search summary only. Status and any fix are unknown.
- The Turkish pack's size is stated nowhere. The ship thread says about 60 MB per language; one third-party post measured 244 MB for the engine plus an unknown set of packs.
- Whether Turkish is actually offered at run time on the owner's Chrome 154, and how well it hears Turkish.
- The office PC: I ran on MAIL. I found no dedicated enterprise policy; a blocked component updater leaving the status stuck is my inference.
- Whether Chrome later deletes the pack when Live Caption is off, and any cap on phrases.

**Measured on MAIL:** Chrome 154.0.8037.58, no Chrome policy keys, no speech pack folders present, C: 65 GB free (86 % used).

**Footprint (estimated, not measured):** 60–250 MB on C: inside the Chrome profile, not movable to E:. CPU and RAM are unknown until the owner's first run.

**Lead decisions (my recommendation in each):**
- **D1 — honest engine name.** Call it `chrome-bulut` only when the API is missing or the probe says no pack is usable; otherwise `bilinmiyor`. This needs one read-only `available()` probe per start in all three settings, including `kapali`; the recogniser's options stay exactly today's. Without the probe, `kapali` can only ever say `bilinmiyor`.
- **D2 — owner measurement in two phases.** Twenty sentences before any install (the only true cloud baseline), then install, then twenty in `acik`. `olc` after install measures phrases on/off, not cloud versus device. The consent line should say the pack stays in use with the setting off.
- **D3 — the yes-button is outside the area.** It belongs in `apps/web/app/core/VoiceControlView.tsx`. The worker exposes `packQuestion` and `answerPackQuestion`; you wire it at merge or cut a follow-up.
- **D4 — server file at merge.** The contract already allows `payload`, and the key `stt_engine` passes the forbidden-key filter. But the utterance branch of `services/api/app/voice/realtime_sessions/service.py` (meta.update near line 2232) drops the payload, so it needs one allow-listed line plus a test. `test_voice_local_mode.py:164` should gain the payload too.

**Worker pitfalls (plan §6):**
- `available()` and `install()` can hang, so both need a guard timer.
- `listen()` swallows every exception from `start()` today; only `InvalidStateError` may stay swallowed.
- `language-not-supported` fires with no `onend`, so the fallback restart must come from the error handler.
- Capture the engine at the final, not at `turnBody`.
- The exact-body assertion in `local-mode.test.ts` must gain the payload.

**Risks:** installing is hard to undo and changes Chrome's choice for every site; Turkish accuracy is unmeasured; behaviour was read on Chromium main, not on 154. Device safety is fine (no driver, capture or credential access). Only the pack download contacts Google.

**READY_FOR_OWNER:** run `await SpeechRecognition.available({langs:['tr-TR'], processLocally:true})` in DevTools on MAIL and on the office PC, then the two-phase twenty sentences.

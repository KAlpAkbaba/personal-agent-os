# chrome-on-device-stt — integration plan (integrator, cycle d20261001)

**First line for the lead:** the API exists in the owner's Chrome and Turkish is a pack Chromium knows,
but three facts read in Chromium's own source change the card: (1) once the Turkish pack is on disk,
Chrome uses it **even when `processLocally` is false** — so "kapalı" and the "cloud" leg of "ölç" stop
being cloud, and a true cloud-vs-device comparison must be taken BEFORE the pack is installed;
(2) `install()` needs a **click** (transient user activation) and `processLocally: true` in its
options, otherwise it rejects / resolves `false`; (3) `phrases` with server recognition is an error
(`phrases-not-supported`), never a silent no-op.

Everything below was read on **2026-10-02**. Nothing was installed, no browser binary was run, no
dependency was added, no feature code was written.

## 1. Decision

**ADOPT the browser feature (no library, no pin possible — it is Chrome itself), behind the existing
`LocalModeDeps` seam.** No candidate library was considered further: a JS wrapper for three static
calls would be a dependency for nothing, and every STT library that runs in the page (whisper-wasm,
Vosk-wasm, transformers.js) is a 40 MB–1.6 GB download with its own licence and is a different
proposal (`stt-engines-measure` covers local Whisper).

## 2. The API, piece by piece — versions, dates, links

| Piece | Chrome (Windows desktop) | Source and date |
|---|---|---|
| `SpeechRecognition.available()`, `.install()`, `processLocally` | **139**, stable 2025-08-05. The Intent to Ship (2025-01-07) named 135; it shipped in 139. Android/WebView: not supported. | Release notes https://developer.chrome.com/release-notes/139 ; BCD `api/SpeechRecognition.json` (`version_added: "139"` for all three) https://github.com/mdn/browser-compat-data ; I2S https://groups.google.com/a/chromium.org/g/blink-dev/c/VNOok2dbmHM ; chromestatus https://chromestatus.com/feature/6090916291674112 (page is a JS app, not readable by fetch) |
| `phrases` + `SpeechRecognitionPhrase(phrase, boost)` | **142** per BCD; the Intent to Ship (2025-07-09) said "Shipping on desktop 140". The two disagree; treat < 142 as "may be absent" and feature-detect. Boost is a float in [0.0, 10.0], default 1.0; out of range throws `SyntaxError`. | BCD (as above); I2S https://www.mail-archive.com/blink-dev@chromium.org/msg14140.html ; spec https://webaudio.github.io/web-speech-api/ ("Draft Community Group Report, 18 September 2026"); MDN (last modified 2025-09-30) https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/phrases |
| `quality` (`command` default / `dictation` / `conversation`) | **150** per BCD and the Intent to Ship of 2026-05-06 (desktop only). No LGTM was visible in the thread as fetched. | https://groups.google.com/a/chromium.org/g/blink-dev/c/P8P-x7AnC6I ; MDN `available()` (last modified 2026-05-29) https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/available_static |
| Return values of `available()` | `available` / `downloading` / `downloadable` / `unavailable` | MDN, spec, explainer https://github.com/WebAudio/web-speech-api/blob/main/explainers/on-device-speech-recognition.md |

### Read in Chromium source (`chromium/chromium` main at `fc2e8e543698572671a4b6af1410a0ca468addc9`, 2026-10-02)

Files: `third_party/blink/renderer/modules/speech/speech_recognition.cc`,
`content/browser/speech/speech_recognition_manager_impl.cc`,
`chrome/browser/speech/on_device_speech_recognition_impl.cc`, `components/soda/constants.h`,
`components/soda/soda_util.cc`, `components/soda/soda_installer.cc`. This is **main, not the owner's
154 build** — the behaviour is what the code says today; 154 may differ in detail.

1. **Turkish is an on-device language.** `constants.h`: `{LanguageCode::kTrTr, "tr-TR", prefs::kSodaTrTrConfigPath, …}`.
   The explainer's Chrome list also names "tr-TR (Turkish, Turkey)". `install()` additionally requires the
   language to be in `SodaInstaller::GetLiveCaptionEnabledLanguages()` — a run-time list I could not read.
2. **`processLocally=false` silently prefers on-device.** `UseOnDeviceSpeechRecognition` =
   `config.on_device && (config.on_device_available || !config.allow_cloud_fallback)`; Blink passes
   `on_device` = "the permissions policy allows it" and `allow_cloud_fallback = !process_locally_`.
   So with the pack installed, an unmodified recogniser runs on-device. This is what issue 521896368
   is about (see §3).
3. **`phrases` works only on-device.** `OnPhrasesChanged`: "Only on device speech recognition supports
   contextual biasing" → `phrases-not-supported` when `phrases.length > 0 && !processLocally`; the
   browser side sets the same error when a recognition context arrives on the cloud path. The
   contextual-biasing explainer says the same in softer words ("Some user agents (e.g. Chrome) might
   only support on-device contextual biasing").
4. **`install()`**: resolves `false` unless `options.processLocally` is true ("Installation is only
   relevant for on-device processing") — **the MDN example omits it and would resolve false**. When the
   status is `downloadable` it **consumes transient user activation** and rejects `NotAllowedError`
   without one. A relaxed path (sticky activation is enough) applies only when the origin has the
   microphone permission AND `tr-TR` is literally in the profile's accept-languages. Cross-site iframe
   or a blocking `Permissions-Policy: on-device-speech-recognition` → `NotAllowedError`.
5. **`available()`**: `processLocally: false` always resolves `available`; an empty `langs` throws
   `TypeError` (MDN says it resolves `unavailable`); policy-blocked or cross-site iframe → `unavailable`.
   The answer is **masked per origin**: an installed pack reads `downloadable` to an origin that never
   installed it, unless that origin has the microphone permission and the language is an accept-language.
6. **`start()` with `processLocally=true` and no pack** fires `language-not-supported` and returns
   without starting — **no `onend` follows**. Policy-blocked → `NotAllowedError` thrown / `not-allowed`.
   The browser can also answer `service-not-allowed` for a frame not permitted to use on-device.
7. **`quality`**: `dictation` and `conversation` are `unavailable` unless the Finch features
   `kOnDeviceWebSpeechSmallExpertModel` / `kOnDeviceWebSpeechGeminiNano` are on, and then they are NOT
   the SODA pack but optimisation-guide models (TinyGemma / Gemini Nano — the multi-GB family).
   **The worker must not set `quality`** (default `command` = the SODA pack).
8. **Hardware**: SODA needs AVX on x86 (`soda_util.cc`). The home PC's i7-14700KF has it.
9. **Enterprise policy**: no enterprise-policy check appears in the on-device Web Speech files read;
   the only gate is the *Permissions-Policy* header feature `on-device-speech-recognition`.

## 3. What could NOT be verified — plainly

- **Chromium issue 521896368**: the page needs a Google sign-in. I read only its title ("Web Speech
  API: processLocally=false should not silently prefer on-device, and on-device routing bypasses
  available()'s fingerprinting protection") and a search-engine summary. Its first claim I confirmed
  independently in source (§2.2); its status, comments and any planned fix are unknown. Issue 444393111
  (macOS `available()` broken): title only, same reason.
- **Size of the Turkish pack: stated nowhere.** The 2025 Intent to Ship thread says "~60MB" per language
  pack (through a summarising fetch, not a quote I saw raw); one third-party post measured `SODA` +
  `SODALanguagePacks` at 244 MB with an unknown set of languages. On MAIL both folders are **absent**
  today, so there was nothing to measure.
- **Whether Turkish is actually offered at run time** on the owner's 154 (the Live-Caption-enabled
  language list is not in the files read) and **how well it hears Turkish** — no measurement exists
  anywhere I could find.
- **The office PC (GMKADIRAKBABA)**: I ran on MAIL; its Chrome version, policies and proxy were not
  checked. No dedicated enterprise policy for this feature was found in the policy list searches or in
  source. Inference only: the pack arrives through the component updater (stated in brave-browser#55414),
  so `ComponentUpdatesEnabled=false` or a blocked update host would leave the status at
  `downloadable`/`downloading` for ever. `GenAILocalFoundationalModelSettings` governs Gemini Nano, not SODA.
- **Whether Chrome later deletes a pack installed through Web Speech** when Live Caption is off: the
  source has per-language `ScheduledDeletionTime` prefs and `IsAnyFeatureUsingSodaEnabled` counts only
  Live Caption on desktop; I did not follow the path to the end.
- **A cap on the number of phrases**: none documented in spec, MDN or explainer.
- **Edge**: BCD says "mirrors Chrome"; not verified, and Edge has its own speech service.
- chromestatus pages (JS app) could not be read; MDN's `available()` page gives no version table in the fetch.

## 4. Measured on MAIL (read-only: directory names, registry keys, `df`)

Chrome `154.0.8037.58` (directory name; no binary was run). `HKLM`/`HKCU\SOFTWARE\Policies\Google\Chrome`:
absent (unmanaged). `%LOCALAPPDATA%\Google\Chrome\User Data\SODA` and `SODALanguagePacks`: absent.
C: 65 GB free of 447 GB (86 % used), E: 3.3 TB free. Footprint, **estimated, not measured** (measuring
means installing): disk ≈ 60 MB for the pack plus the SODA binary, upper bound ~250 MB, on **C:** inside
the Chrome profile (it cannot be moved to E:); CPU/RAM of recognition unknown — the owner's first run
records it (Chrome Task Manager, "Utility: Speech Recognition Service").

## 5. Consequences for the card (lead decisions, with my recommendation)

- **D1 — the engine name must be honest.** A recogniser started without `processLocally` is cloud only
  while no pack is usable. Rule: `chrome-cihaz-ici` when started with `processLocally=true` and it
  produced the final; `chrome-bulut` when the API is missing, or the last
  `available({langs:['tr-TR'], processLocally:true})` said `unavailable`/`downloadable`; **`bilinmiyor`**
  when it said `available`/`downloading`, threw, or timed out. This needs one read-only `available()`
  probe per `start()` in ALL three settings, including `kapali` (the recogniser's options stay exactly
  today's — the acceptance's RED condition is about `processLocally`/`phrases` on the object). If the
  lead reads "byte for byte" as "no probe either", `kapali` can only ever say `bilinmiyor`.
- **D2 — `olc` does not compare cloud with device once the pack is installed**; both legs run on-device
  and the difference is phrases on/off. The READY_FOR_OWNER step should be two phases: twenty sentences
  with the pack ABSENT (today's state on MAIL; `kapali`, honestly `chrome-bulut`), then install, then
  twenty in `acik`. `olc` stays useful for "do our phrases help". Installing is not undone by the
  setting: after it, `kapali` is on-device too (remove the pack under chrome://settings → Live Caption
  languages / chrome://components to go back — path from a Chrome team post of 2025-08-11, not tried).
  The one-line question should say so: "Türkçe paketi indirilsin mi? (~60 MB, C: sürücüsü; indikten sonra
  Chrome onu ayar kapalıyken de kullanır.)"
- **D3 — the yes must be a click.** A spoken "evet" carries no user activation. The mode can only expose
  the question and an `answer(yes)` method; the line and its button live in
  `apps/web/app/core/VoiceControlView.tsx`, **outside the card's area** — lead wires it at merge or cuts
  a small follow-up. Until then `downloadable` is a recorded fallback and nothing is installed.
- **D4 — server.** `ClientEvent.payload` is already allowed (`additionalProperties: true`,
  `packages/protocol/realtime-session-contract.json`; no contract change) and the key `stt_engine`
  passes `is_forbidden_key`. But the utterance branch of
  **`services/api/app/voice/realtime_sessions/service.py`** (meta built at ~1619, `meta.update` at ~2232,
  `continue` at 2265 before `meta["payload"] = payload` at 2311) never copies the payload: the value is
  accepted and dropped. LEAD at merge: one allow-listed line there
  (`"stt_engine": payload.get("stt_engine") if in the three names else None`) plus a server test.
  `services/api/tests/unit/test_voice_local_mode.py:164` posts "the web client's utterance event,
  exactly as `localMode.ts` posts it" and should gain the payload.

## 6. The seam and the files (worker)

Area: `apps/web/app/lib/voice`, `apps/web/tests/voice`. `usePreferences.ts` is in `app/core` (outside).

- `localMode.ts`
  - `SpeechRecognitionLike`: optional `processLocally?: boolean`, `phrases?: unknown`.
  - `LocalModeDeps`: optional `onDevice?: { available(o): Promise<string>; install(o): Promise<boolean> } | null`
    (null = API absent), `phrase?: (text, boost) => unknown` (null when `SpeechRecognitionPhrase` is
    absent → on-device without phrases, recorded), `sttSetting?: () => 'kapali'|'acik'|'olc'`
    (default `'kapali'`), `phraseSources?: () => Promise<PhraseSources>`.
  - `browserLocalModeDeps`: the one place `SpeechRecognition.available/install` (static, also on the
    `webkit` prefix object — feature-detect `typeof Ctor.available === 'function'`) and
    `window.SpeechRecognitionPhrase` are read.
  - Snapshot: `sttEngine`, `sttFallback: string | null` (reason code), `packQuestion: string | null`;
    methods `answerPackQuestion(yes: boolean)`.
  - Utterance event: `payload: { stt_engine }` on every utterance. The engine is the one of the
    recogniser run that produced THAT final (capture it at `onFinal`, not at `turnBody` — finals queue
    behind speech and `olc` flips in between).
  - Do not move or reword the `api.create({ client_kind: "web", transport: LOCAL_TRANSPORT, … })` line
    or the `LOCAL_TRANSPORT` line: `test_voice_local_mode.py` reads them.
- New, pure: `sttSetting.ts` (parse + read through an injected `{getItem}`; keys
  `pagentos.core.localStt` and the UI flag `pagentos.core.localSttUi`; anything unknown → `kapali`) and
  `sttPhrases.ts` (`buildPhrases(sources, cap)`).
- `fake.ts`: `FakeSpeechRecognition` records `processLocally`/`phrases` as set (leave them `undefined`
  until assigned, so "kapali never touches them" is assertable with `in`/`hasOwnProperty`), plus a
  `FakeOnDevice` with scripted `available`/`install` (resolve, reject, never-resolve).
- Tests: `tests/voice/local-stt-engine.test.ts`, `tests/voice/stt-phrases.test.ts`; the existing
  `local-mode.test.ts` exact-body assertion (`events[0].body toEqual …`) gains the payload — that is
  the one intended change to an existing test.

### Pitfalls the tests must pin
1. `install({ langs: ['tr-TR'], processLocally: true })` — without `processLocally` Chrome resolves false.
   Never pass `quality`.
2. `available()`/`install()` can hang (brave-browser#55414: `install()` pending for ever) → race both
   against `deps.setTimer`; a timeout is a fallback reason, never a blocked `start()`. No wall clock in tests.
3. `listen()` today swallows every exception from `recognizer.start()`. With `processLocally` a
   `NotAllowedError` would leave the mode "listening" and deaf: only `InvalidStateError` may be swallowed.
4. `language-not-supported`, `phrases-not-supported`, and — while `processLocally` is set —
   `service-not-allowed` / `not-allowed` are FALLBACKS (clear `processLocally` and `phrases`, restart,
   record the reason), not the fatal "Mikrofon izni verilmedi." they are today. No `onend` follows
   `language-not-supported`, so the restart must be issued from the error handler. After one fallback the
   session stays on today's path (no retry loop).
5. `phrases` is assigned BEFORE `start()` and only together with `processLocally=true`; boost within
   [0, 10] (propose 2.0 aliases, 1.5 application names, 1.0 verb forms), clamp in the pure function.
6. `olc`: flip only between recogniser runs (after a final, at the restart in `listen()`), never
   mid-run; the first run is today's path. Run the alternation twice and assert the sequence, not a count.
7. Phrase list: sources are injected — device aliases from `/v1/devices` (`DeviceInfo.aliases`,
   `lib/research/model.ts` `aliasesOf`) with the four spoken names the server knows (`ev`, `iş`,
   `laptop`, `ofis`: `services/api/app/devices/aliases.py:34-37`) as the floor; the capability rows'
   `phrases` (`lib/pages/capabilities.ts` — the list has example sentences, no separate application-name
   field: take the names the card means from those sentences or a short constant, and say which in the
   ADR); the open-verb forms (`aç, açsana, açar, açın, açınız`: `intents.py:5309`, `_APP_OPEN_VERB_FORMS`).
   Dedupe with Turkish-aware lower-casing (`toLocaleLowerCase('tr-TR')`), drop empties and anything over
   ~60 chars, aliases first so the cap never cuts them, cap 64. A failed fetch → aliases floor only,
   never a blocked start. The list goes to the recogniser only: never into the log, the snapshot or the payload.
8. Mutations (sha256, restore from a backup copy): default → `acik`; install-without-consent guard
   removed; plus: engine read at `turnBody` instead of at the final; `phrases` attached on the cloud leg.

## 7. Risks

- R1: after install, Chrome's choice is no longer ours (§5 D2) — an owner-visible, hard-to-undo change on
  the device; hence the consent line and default `kapali`.
- R2: Turkish on-device accuracy is unmeasured and may be worse than the cloud's.
- R3: 60–250 MB on a C: that is 86 % full; not movable.
- R4: office PC behaviour unknown; expected worst case is a permanent `downloadable`/`downloading` → fallback.
- R5: behaviour was read on Chromium main, not on 154; feature flags (Finch) can differ per machine.
- R6: privacy — improves when on-device (audio stays in Chrome); nothing new phones home. The pack
  download itself is a request to Google's component servers.
- Device safety: no driver, no capture outside the page's microphone, no credential access.

## 8. Rollback

Setting back to `kapali` (default) restores today's recogniser options. Code: additive behind optional
deps; remove the two new modules and the optional fields. The language pack is removed in Chrome itself
(§5 D2), not by us. Server line (D4) is one allow-listed key.

## 9. THIRD_PARTY_COMPONENTS entry text (lead appends; the integrator does not edit the tree)

```
## Chrome on-device speech recognition (2026-10-02, chrome-on-device-stt) - behind a setting, default OFF

Role: optional recogniser for the free local voice mode (ADR-0173), through `LocalModeDeps` in
`apps/web/app/lib/voice/localMode.ts`. A browser feature, not a dependency: nothing in package.json.
Web Speech API `SpeechRecognition.available()/install()/processLocally` (Chrome 139, 2025-08-05) and
`phrases` (Chrome 142 per MDN compat data); spec: WebAudio CG draft, https://webaudio.github.io/web-speech-api/.
Model: Chrome's SODA Turkish language pack (Google, proprietary, delivered by Chrome's component
updater into the Chrome profile on C:; ~60 MB per the Chromium ship thread, Turkish size unstated).
Downloaded only after the owner clicks yes. Once installed, Chrome may use it for every site and also
when our setting is off. Audio stays in the browser when on-device; the pack download contacts Google.
`quality` is never set (other values select different, larger models).
Upgrade rule: re-read Chromium issue 521896368 and `speech_recognition_manager_impl.cc`
(`UseOnDeviceSpeechRecognition`) before making on-device the default.
```

## 10. For the lead

- This file is written in the main checkout and is uncommitted; hand it to the worker's worktree.
- Decisions: D1 (probe in `kapali`), D2 (two-phase owner measurement instead of alternation as the
  cloud-vs-device proof), D3 (the question's button is outside the area), D4 (`service.py` line at merge).
- READY_FOR_OWNER, in this order: (a) in Chrome DevTools on MAIL:
  `await SpeechRecognition.available({langs:['tr-TR'], processLocally:true})` — the one answer nobody
  else can give; (b) twenty sentences BEFORE any install; (c) yes to the pack, note the size of
  `User Data\SODA*`; (d) twenty sentences in `acik`; (e) the same `available()` line on the office PC.

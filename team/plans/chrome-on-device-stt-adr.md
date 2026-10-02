# ADR (number at merge): Chrome's on-device Turkish recognition in the local mode, behind a setting that is OFF

Task `chrome-on-device-stt`, cycle d20261002. Owner approval 2026-10-01: behind a setting, default
KAPALI; turning it on is a separate decision after measurement. Plan and sources:
`team/plans/chrome-on-device-stt-integration.md`. Builds on ADR-0173 (free local mode).

## Decision

1. **Setting, three values, per browser** (`apps/web/app/lib/voice/sttSetting.ts`): `localStorage`
   key `pagentos.core.localStt` = `kapali` (default) / `acik` / `olc`, beside the "Yerel mod" key
   `usePreferences.ts` already keeps there. Anything that is not literally one of the three, or a
   storage that throws, is `kapali`. There is no switch in the UI; `sttSettingUiEnabled()` reads the
   flag `pagentos.core.localSttUi = "1"` for the shell to use when the owner decides to adopt.
2. **`kapali` writes nothing on the recogniser.** `processLocally` and `phrases` are never assigned,
   nothing is installed, no question is shown, the start does not wait for anything.
3. **Engine name on every utterance** (plan D1, taken as recommended): `payload.stt_engine` of the
   existing `utterance` event, captured when the final ARRIVES (finals queue behind speech).
   `chrome-cihaz-ici` = the run was started with `processLocally = true`; `chrome-bulut` = started
   as always AND no pack can be in use (no `available()` in the browser, or it answered
   `unavailable` / `downloadable`); `bilinmiyor` = started as always while a pack is or may be
   installed (answer `available` / `downloading` / unknown / thrown / timed out / not yet answered,
   or after a yes to the download). For this, ONE read-only
   `available({langs:['tr-TR'], processLocally:true})` runs per start in all three settings; in
   `kapali` it is not awaited.
4. **`acik`**: `available` -> `processLocally = true`, then `phrases`, both written before `start()`.
   `downloadable` -> one line in the snapshot (`packQuestion`) and today's path; `install()` is
   called only from `answerPackQuestion(true)`, synchronously (Chrome needs the click's user
   activation), with `processLocally: true` (without it Chrome resolves false). Everything else -
   `unavailable`, `downloading`, an unknown status, a throw, a hang (3 s guard on the injected
   timer), no API - is today's path with a reason code in `sttFallback` and in the log.
5. **`olc`**: only with a usable pack. First run is today's path; after each final the next
   recogniser run uses the other leg. The leg changes only between runs. Phrases ride only with
   `processLocally` and are cleared (`phrases = []`, then `processLocally = false`) on the other leg.
6. **A device run Chrome refuses falls back once and stays there**: `language-not-supported`,
   `phrases-not-supported`, `service-not-allowed`, `not-allowed` while on the device leg, or a
   `start()` that throws anything but `InvalidStateError`. The restart is issued from the error
   handler (no `onend` follows `language-not-supported`). On today's path the same errors mean what
   they always meant (a denied microphone is still fatal).
7. **Phrase list** (`sttPhrases.ts`, pure, cap 64, each at most 60 characters, Turkish-aware dedupe):
   the session's device aliases (`/v1/devices`) at boost 2.0, then the four spoken names the server
   always knows (`ev`, `iş`, `laptop`, `ofis`); the application names at 1.5; the open-verb forms
   (`aç, açsana, açar, açın, açınız`) at 1.0. `/v1/voice/capabilities` has no application-name field,
   so a name is **what stands before an open verb in the capability list's example sentences**
   ("Hesap makinesini aç" -> "Hesap makinesini"), in the case the owner says it in - no constant
   list to drift. The list goes to the recogniser only: not to the log, the snapshot or the wire.
   `quality` is never set.

## What differs from the plan, and why

- The consent line carries **no size**: "Türkçe paketi indirilsin mi? (C: sürücüsüne iner, boyutu
  ölçülmedi; indikten sonra Chrome onu bu ayar kapalıyken de kullanır.)" The plan's "~60 MB" is a
  figure nobody measured.
- After a yes, the device leg begins at the recogniser's **next restart**, not at once: stopping a
  running recogniser would cut the owner's sentence.
- `olc` + `downloadable` also shows the question (same code path as `acik`).

## Not done here - for the LEAD at merge

- **Server (plan D4)**: `services/api/app/voice/realtime_sessions/service.py`, the utterance branch
  (`meta.update(...)` before `_audit(db, ACTION_INTENT_RESOLVED, ...)`) drops the payload. One
  allow-listed line (`stt_engine` only when it is one of the three names) plus a server test; and
  `services/api/tests/unit/test_voice_local_mode.py` `_say` should post the payload. Until then the
  value is accepted and not recorded: checked ad hoc - that test file with the payload added to
  `_say` passes 9/9 against the unchanged server.
- **Shell (plan D3)**: `apps/web/app/core/VoiceControlView.tsx` must draw `snapshot.packQuestion`
  with a yes/no BUTTON that calls `answerPackQuestion(yes)` (exposed by `useLocalVoiceMode`'s
  handle; `VoiceControl.tsx` passes it down). Until that exists, `acik` on a machine without the
  pack is a recorded fallback and nothing can be installed from our page.

## Known limits

- `downloadable` is named `chrome-bulut`, but Chromium masks an installed pack as `downloadable`
  for an origin that never installed it unless the origin has the microphone permission and `tr-TR`
  is an accept-language. On a Chrome without `tr-TR` in its languages the name could be wrong.
- On today's path a `start()` that throws something other than `InvalidStateError` is still
  swallowed, exactly as before (`kapali` is unchanged on purpose).
- Behaviour was read in Chromium source, not observed in the owner's Chrome 154: no real browser
  ran any of this.

## Rollback

Remove the key (or set `kapali`). The language pack, once installed, is removed in Chrome itself.

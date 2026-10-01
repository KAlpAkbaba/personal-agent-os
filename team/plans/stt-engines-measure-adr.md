## ADR (lead numbers it) — STT engine comparison: the instrument is built, there is nothing to measure yet

**Status.** The INSTRUMENT is delivered (worker, cycle d20261001). **No number exists**: the
repository and this machine hold zero recordings of the owner's speech (plan §4), so nothing was
sent to any engine and no WER is claimed. MEASUREMENT ONLY (owner, 2026-10-01): nothing is adopted,
no default changed, no provider registered, no `Settings` field added, no dependency added, no
account opened.

**Context.** The owner approved measuring "the existing STT recordings on three engines, compared
by numbers; adoption and opening an account are separate approvals". The integrator's plan
(`team/plans/stt-engines-measure-integration.md`) read Soniox's terms (no training on customer
content; real-time requests not stored; US processing by default, DPA unread without an account)
and found that the "existing recordings" are 22 text labels and one recorded confusion - no audio.

**Decision.**
- `app/voice/stt_compare.py`: a folder of WAV files + `manifest.json` (file, reference,
  recorded_where) → one report. Engines are rows keyed by their OWN label (`openai:gpt-4o-transcribe`,
  `openai:whisper-1`, `soniox:stt-rt-v5`, `azure:tr-TR`, `faster-whisper:large-v3-turbo`,
  `chrome-web-speech`), never by `provider.name` (two OpenAI models share one). An engine that
  cannot run stays in the table as `NOT_RUN` with the reason (`not configured`, `not installed`,
  `no file input`, `not selected`, `no usable recording`); one that fails every file is `FAILED`,
  not a rate of 0; one that fails some files keeps its row with both counts.
- **Rates are pooled** (Σ edits / Σ reference words or letters) - the number to rank by; the mean
  of the per-sentence rates is reported beside it (`mean_sentence_wer`), since
  `benchmark.run_stt_benchmark` reports that one. `levenshtein`, `word_error_rate` and
  `char_error_rate` are `benchmark.py`'s own; `benchmark.py` is unchanged.
- **Normalisation** (`normalize_for_compare`): `intents.turkish_casefold` (I→ı, İ→i), apostrophes
  dropped (`Iğdır'ın` = `Iğdırın`), other punctuation to a space. Numbers stay as spoken ("7" vs
  "yedi" is an error) and Turkish letters are kept (ı/i, ü/u are the errors being measured) - so
  neither `normalize_transcript` nor `loopback.normalize_for_comparison` is used.
- **"Intent change" is a signature, not `.intent`.** The acceptance pair ("Ofis bilgisayarımdan
  hesap makinesini aç" / "Ofisü bilgisayarında hesap makinesini açın") resolves to `app_open` /
  `calc` on both sides; what differs is the device named. The signature is: every `ResolvedIntent`
  field a tool acts on (all fields except `normalized_text`, `tokens`, `fillers_removed`,
  `confidence`, `matched`, `route_repair`, `band`, `candidates`) + the device aliases from
  `spoken_device.resolve_without_device_phrase`, string values compared in normalised form. One
  exception found by test: `reference` is filled for EVERY utterance with the sentence's content
  words and made every misheard word an "intent change" ("aç"→"açın" counted); it is compared only
  when `research_class` is set. The transcript is read as heard, not through the ADR-0224 repair
  layer: the number is what the engine did.
- `app/voice/providers_soniox.py`: `SonioxSTTProvider` over `websockets.sync.client` (already
  locked; no vendor SDK). Real-time endpoint only, `audio_format: auto`, `language_hints`, no
  `client_reference_id`. The key travels in the first frame, so with no key or no audio the socket
  is never opened; the key is never in the URL, and a vendor error message is scrubbed of it.
  Every `recv` has a timeout. Exercised against a fake server only.
- `scripts/voice/stt-compare.ps1 -Folder <dir>`: no manifest → writes `manifest.template.json`
  (the twenty sentences) and stops; otherwise loads the keys that exist in the DPAPI store into
  the child's environment, runs, writes `docs/evidence/stt-compare-<date>.json` and a `.md`
  summary (Turkish, numbers and engine names only), clears the keys. A same-day rerun gets a
  timestamped name; evidence is never overwritten.
- **Writes:** the comparison writes exactly the output file it is given; no transcript is printed.
  A manifest entry that resolves outside the folder is refused.

**Choices the lead may reverse.**
- No `-LocalWhisper` switch: installing faster-whisper and a 1.6 GB download on the home PC was
  left as a lead decision and is not in the card's acceptance. The row exists and reads
  `not installed`; if the package is ever importable it runs with `large-v3-turbo`, CPU `int8`.
- An Azure row was added (the adapter and its setting already exist; it reads `not configured`).
- WAV only. Another container is skipped per file (`unsupported container`), never sent.
- Latency is wall time per file sent in one go - processing time, not first-token latency.

**Not done / owner.** READY_FOR_OWNER: twenty WAV recordings (PCM 16-bit, mono, 16 kHz) of the
template's sentences - Windows Voice Recorder writes `.m4a`, so how they are recorded without the
owner becoming an operator is an open follow-up (a `-Record` mode through the companion's capture).
By a SEPARATE approval: a Soniox account (ask for the EU region, read the DPA in the Console),
then `scripts\secret-store.ps1 -Set PAGENTOS_VOICE_SONIOX_API_KEY`. Unverified against the real
service: the model name `stt-rt-v5` and the wire format (read from the documentation only).

**Rollback.** Additive and unwired: delete the two modules, the two test files and the script.

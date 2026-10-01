# stt-engines-measure — integration plan (integrator, cycle d20261001)

**Soniox, first line for the owner:** Soniox's published terms do NOT allow training on customer
audio and the real-time API keeps nothing ("processed transiently and … not stored"); the only
retention is the Async/file API (30 days, deletable), which this plan forbids the adapter to use.
What is NOT clean: processing is in the United States by default (EU region only on request), and the
DPA and the sub-processor list could not be read without an account.

Everything below was read on 2026-10-01. Nothing was installed, no account was opened, no audio left
the machine, no key was read (presence only).

## 1. Decision

| Engine | Decision | Why |
|---|---|---|
| Soniox real-time STT | **ADAPT** — own ~150-line adapter over `websockets` (already locked, 17.1); no Soniox SDK | The seam is one `transcribe(audio, language)`; the SDK would be a new dependency for one config frame and a read loop. |
| OpenAI transcription (`whisper-1`, `gpt-4o-transcribe`) | **REUSE** `OpenAISTTProvider(key, model=…)` | Already in the tree; the only engine configured on this PC today. `gpt-4o-transcribe` is the model the live realtime session transcribes with (`config.py:147`) — it is the one that produced "Ofisü", so it is the baseline that matters. |
| faster-whisper + Whisper large-v3-turbo (local) | **ADOPT FOR MEASUREMENT ONLY, out of tree** — `uv run --with faster-whisper==1.2.1`, behind the script's `-LocalWhisper` switch; nothing in `pyproject.toml` / `uv.lock` | Existing `FasterWhisperSTTProvider` is import-guarded and takes `model_size/device/compute_type`. Lead/owner decide whether the switch may be used (it downloads 1.6 GB once). |
| NVIDIA Parakeet (`parakeet-tdt-0.6b-v3`) | **REJECTED** | No Turkish: the model card lists 25 European languages and Turkish is not among them. Also NeMo + torch, Linux-preferred. Licence CC-BY-4.0 (fine, irrelevant). |
| Chrome Web Speech | **NOT MEASURABLE here** | Browser API on a live microphone, no file input, not behind `STTProvider`. Listed in the table as `NOT_RUN — no file input`, never silently absent. |

No default changes, nothing is registered in `registry.py`, no `Settings` field is added (that is the
adoption step, a separate approval).

## 2. Soniox — terms, with links and dates

| Question | Answer (verbatim where quoted) | Source |
|---|---|---|
| Training on customer audio | "Soniox does not use Customer Content to train, fine-tune, evaluate, benchmark, or improve Soniox models or services." | Terms of Service, effective **2026-06-29** — https://soniox.com/policies/terms-of-service ; same sentence in the Privacy Policy, last updated **2026-06-29** — https://soniox.com/policies/privacy-policy |
| Retention, real-time API | "Real-time API requests are processed transiently and are not stored by Soniox." / "No retention – Soniox does not store your audio or transcript data unless explicitly requested" | Privacy Policy; https://soniox.com/docs/security-and-privacy (undated) |
| Retention, Async/file API | "Audio and transcriptions stored via the Async API are automatically deleted after 30 days." Deletable earlier "via the Soniox Console or API". | security-and-privacy |
| What IS kept | Usage metadata: "request IDs, organization IDs, project IDs, timestamps, duration, token counts, status codes, model identifiers, region …"; "Logs never contain raw audio or transcript content." An optional `client_reference_id` is recorded if sent. | ToS; security-and-privacy |
| Licence the owner grants | "the limited rights necessary to process Customer Content to provide, operate, secure, support, monitor, debug, bill for, and maintain the Services" | ToS |
| Where it is processed | Soniox Inc., 1045 Helm Lane, Foster City, CA 94404, USA; California law. "may process information in the United States and other countries". EU region exists (`stt-rt.eu.soniox.com`, region-specific keys) but "EU region is enabled by request" (support@soniox.com). | ToS; Privacy Policy; https://soniox.com/docs/data-residency ; https://soniox.com/europe |
| DPA | "Signed self-served DPA can be obtained on the Soniox Console" (pre-signed, one-click countersign). **Its text is not public; I could not read it — it can only be read after an account exists.** | https://soniox.com/europe |
| Sub-processors | Mentioned generically ("service providers, subprocessors, vendors"); **no public list found.** | Privacy Policy |
| Certifications claimed | SOC 2 Type 2, ISO/IEC 27001:2022, GDPR, HIPAA (vendor's own page, not verified against an auditor's report). | security-and-privacy |
| Other clauses that touch us | 18+; trial/free access "may be limited, modified, suspended, or terminated at any time"; benchmark results may not be published "in a false, misleading, deceptive, or commercially disparaging manner" (a private evidence file is not publication). | ToS |
| Turkish | Listed (`tr`) among 60 languages for real-time and async. **No Turkish accuracy figure published** — the reason for measuring. | https://soniox.com/docs/stt/concepts/supported-languages |
| Price | Real-time 0.12 USD/hour (2.00 USD per 1M input audio tokens). Twenty sentences ≈ 100 s ≈ **0.004 USD**. Free credit / card requirement: not stated on the page. | https://soniox.com/pricing |

Conclusion: nothing in the terms stops a measurement. The owner's decision is a KVKK one (a new US
processor hears twenty scripted sentences), not a training/retention one. Conditions the plan imposes:
real-time WebSocket only (never the Async/file API), no `client_reference_id`, ask for the EU region
when the account is opened if the owner wants it, read the DPA in the Console before the first run.

## 3. Local candidates — licences, Turkish, footprint on the HOME PC

Home PC as measured today (`wmic`, `nvidia-smi`): Intel i7-14700KF (20 cores / 28 threads), 47.8 GB RAM
(17.5 GB free at the time), NVIDIA RTX 5060 Ti 16 GB (2.2 GB in use), C: 70 GB free, E: 3.3 TB free.

| Item | Licence | Turkish | Facts |
|---|---|---|---|
| `faster-whisper` 1.2.1 (2025-10-31), SYSTRAN | MIT | n/a (runtime) | Not installed here (`faster_whisper`, `ctranslate2` absent from the venv; `onnxruntime`, `tokenizers`, `huggingface_hub` already present). Pulls `ctranslate2` (MIT), `av`/PyAV (BSD; bundles FFmpeg libraries, LGPL builds), `tqdm`. FFmpeg need not be installed. GPU needs CUDA 12 + cuDNN 9 libraries. https://github.com/SYSTRAN/faster-whisper |
| Whisper large-v3-turbo weights, CT2 conversion `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (what `"large-v3-turbo"`/`"turbo"` resolve to in faster-whisper) | MIT (OpenAI model: MIT) | **Yes, verified**: `"tr": "turkish"` in `whisper/tokenizer.py` LANGUAGES | `model.bin` 1,617,884,929 bytes, 809 M parameters, 4 decoder layers instead of 32: "way faster, at the expense of a minor quality degradation" (model card). No Turkish WER on the card. |
| `Systran/faster-whisper-large-v3` (optional second local row) | MIT | Yes | ~3.1 GB; slower; the researcher's (non-comparable) Common Voice numbers favour it over turbo for Turkish, so it is worth one extra row if the GPU path works. |
| `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 | **No** | Rejected, see §1. |

Footprint — **estimated, not measured** (measuring means installing, which the integrator may not do).
Method: weight file size + the upstream benchmark table (large-v2 fp16 on GPU: 4525 MB VRAM; `small`
int8 on CPU: 1477 MB RAM, 13 min of audio in 1m42s on an i7-12700K), scaled by parameter count.
- large-v3-turbo, GPU `float16`: ~2–2.5 GB VRAM (fits beside the 2.2 GB in use), well under 1 s per sentence.
- large-v3-turbo, CPU `int8`: ~1.5–2 GB RAM, a few seconds per 5 s sentence on 20 cores.
- Disk: 1.6 GB (turbo) — **must go to E:** (`download_root` / `HF_HOME` on E:; C: is 85 % full).
- The worker records the REAL peak RSS / VRAM and seconds in the evidence file when `-LocalWhisper` runs.
Risks: (a) RTX 50-series + CTranslate2 CUDA build is unverified — the script must fall back to CPU `int8`
and say which device ran; (b) first use downloads from Hugging Face (one network call, then
`HF_HUB_OFFLINE=1`; set `HF_HUB_DISABLE_TELEMETRY=1`); (c) nothing else phones home. Device safety:
user-space only, no driver, no capture, no credential access.

## 4. Which recordings exist — the honest inventory

**None. Zero audio files, zero seconds of owner speech.**

| Place searched | Result |
|---|---|
| Tracked files (`git ls-files`, wav/mp3/flac/ogg/opus/webm/m4a/pcm/aac) | 0 |
| Untracked files in the checkout (same extensions, excluding node_modules/.venv/bin/obj) | 0 |
| `%LOCALAPPDATA%\PagentOS` (names only) | 0 audio files |
| Voice profile enrolment | Embeddings only — `speaker.py`: "This is what gets persisted (never raw audio)"; the device keyword enrolment zeroes its frames (`EnrollmentRecorder.cs`). |
| K66 material | Text notes of two sessions (2026-09-02, 2026-09-03) in `docs/VOICE_OWNER_FEEDBACK.md`: timings and false-start counts, no audio. |
| `docs/evidence` (259 files) | 0 audio. Ten `tts-loopback-*.json` hold metrics of TTS-synthesised audio that was never stored. |
| `app/voice/datasets.py` `STT_CASES` | 22 **text** labels; their "audio" is `synthesize_wav`, a sine tone carrying the text bytes — only `FakeSTTProvider` can read it, a real engine hears a hum. |
| `tests/voice_corpus` | Text sentences only. |
| `stt-confusions.json` | **1** entry (`ofisü` → `ofis`, 2026-09-30). The card's "list proposed from the confusion cases of 2026-09-30" therefore has one real case to start from; the other nineteen sentences below are chosen to provoke the same class of error. |

Engines configured on this machine today: **OpenAI only** (`PAGENTOS_VOICE_OPENAI_API_KEY` is in the
DPAPI secret store; no Soniox key, no Azure key, faster-whisper not installed). So step (4) of the card
has engines but no audio: the worker's report is **"nothing to measure: 0 recordings; 22 text labels and
1 recorded confusion"**. Do not spend money on synthetic-voice numbers and call them Turkish WER.

### Twenty sentences for the owner to record (reference text = exactly this, numbers in words)
1. Ofis bilgisayarımdan hesap makinesini aç.
2. Ofis bilgisayarında not defterini aç.
3. Ev bilgisayarında müziği durdur.
4. Bunu unutma, yarın sabah hatırlat.
5. Az önce söylediğimi unut.
6. Yarın sabah yediye alarm kur.
7. Alarmı on dakika ertele.
8. İkinci maddeye geç.
9. Isıtıcıyı kapat, ışığı aç.
10. Iğdır'ın hava durumu nasıl?
11. İstanbul'da yarın yağmur var mı?
12. Yapay zeka son gelişmelerini araştır.
13. Raporu bana PDF olarak gönder.
14. Chrome'da YouTube'u aç ve sesi kıs.
15. Son e-postayı oku.
16. Toplantıyı perşembe saat üçe al.
17. Şu an ne üzerinde çalışıyorsun?
18. Ekibin durumunu özetle.
19. Bundan sonra cevapları kısa tut.
20. Görüşürüz, dinlemeyi bırak.

Each once at the desk microphone; if the owner will, the same twenty again from across the room (the
manifest's `recorded_where` separates them). **Format: WAV, PCM 16-bit, mono, 16 kHz.** See risk R3.

## 5. The seams — exact

- `STTProvider` (`providers.py:309`): `name`, `capabilities()`, `transcribe(audio: bytes, *, language) -> STTResult`. The Soniox adapter implements exactly this; `stt_compare` takes a list of *(label, provider)*.
- **Labels, not `provider.name`:** `OpenAISTTProvider("…", model="whisper-1")` and `(…, model="gpt-4o-transcribe")` both report `name == "openai-whisper"`, and `benchmark.run_stt_benchmark` keys its aggregate by name — two models of one vendor would be summed into one row. `stt_compare` keys by its own engine label (`openai:whisper-1`, `openai:gpt-4o-transcribe`, `soniox:stt-rt-v5`, `faster-whisper:large-v3-turbo`).
- Reuse from `benchmark.py`: `levenshtein`, `word_error_rate`, `char_error_rate`. Do not fork them. `run_stt_benchmark` itself is not reusable (it synthesises its own audio and demands ≥ 2 providers); add nothing to it unless a helper is genuinely shared.
- Normalisation: `app.voice.intents.turkish_casefold` (the repo's dotted/dotless rule, NFC first) + punctuation and case folded. **Not** `normalize_transcript` (it expands numerals — "2." → "ikinci" — and drops fillers; the card says numbers are left as spoken) and **not** `loopback.normalize_for_comparison` (it strips diacritics, which would hide exactly the `ı/i`, `ü/u` errors being measured).
- Secret path: the DPAPI store. `scripts/lib/SecretStore.ps1` → `Get-SecretStorePath -Name`, `Get-StoredSecretValue -Name`; the pattern to copy is `scripts/voice/tts-loopback-qualification.ps1:127-164` (load into the child's environment, remove afterwards). New name: `PAGENTOS_VOICE_SONIOX_API_KEY`. `app/config.py` is outside the area, so the adapter takes `api_key` in its constructor like every other adapter and `stt_compare` reads the environment variable, the way `loopback_cli.KEY_ENV` does.
- Errors: `VoiceError(VoiceErrorClass.PROVIDER_AUTH_MISSING | DEPENDENCY_UNAVAILABLE | TIMEOUT | VALIDATION_ERROR | OPTIONAL_DEPENDENCY_MISSING)`.

### Soniox WebSocket API as read (https://soniox.com/docs/stt/api-reference/websocket-api)
- Endpoint `wss://stt-rt.soniox.com/transcribe-websocket` (EU: `wss://stt-rt.eu.soniox.com/…`; make the host a constructor parameter).
- First frame, **text JSON**: `{"api_key", "model", "audio_format"}` required; optional `sample_rate`, `num_channels`, `language_hints`, `language_hints_strict`, `enable_endpoint_detection`, `client_reference_id`, … Use `model` = constructor parameter (the docs' example is `stt-rt-v5`; confirm the current name at the owner's first run), `audio_format: "auto"` for a WAV container (auto-detected: aac, aiff, amr, asf, flac, mp3, ogg, wav, webm) or `"pcm_s16le"` + `sample_rate: 16000` + `num_channels: 1`, `language_hints: ["tr"]`. Leave endpoint detection off, send no `client_reference_id`.
- Then audio as **binary** frames; end the stream with an **empty** frame.
- Responses: `{"tokens":[{"text","start_ms","end_ms","confidence","is_final"}], "final_audio_proc_ms", "total_audio_proc_ms"}`; the last one has `"finished": true`. Errors: `{"error_code","error_type","error_message","request_id"}`.
- Transcript = the `text` of the `is_final` tokens concatenated in order with NO separator (tokens are sub-word and carry their own spaces), then stripped. The fake server must emit sub-word tokens and a non-final token that is later replaced, so a `" ".join` or a "count non-final too" bug goes RED.
- **"Sends nothing before the first audio chunk":** the API key travels in the first frame, so the adapter must not even open the socket until it holds a non-empty chunk. Order in `transcribe`: key check (no key → `PROVIDER_AUTH_MISSING`, zero connections) → empty audio → `VALIDATION_ERROR`, zero connections → connect → config frame → audio. The key is never in the URL, a log line or a `VoiceError` message.
- `websockets.sync.client.connect` fits the synchronous seam; lazy import (it is a runtime transitive of `uvicorn[standard]` and a dev dependency — `OPTIONAL_DEPENDENCY_MISSING` if absent). Every `recv` has a timeout: a silent server is a `TIMEOUT`, never a hang.

## 6. Files to touch (the card's area, unchanged)
- `services/api/app/voice/stt_compare.py` — manifest loader, engine table, metrics, report; `python -m app.voice.stt_compare --folder --out [--engines]`.
- `services/api/app/voice/providers_soniox.py` — `SonioxSTTProvider`.
- `services/api/app/voice/benchmark.py` — only if a shared helper is needed (e.g. a pooled-rate function). Prefer no change.
- `services/api/tests/unit/test_stt_compare.py`, `test_providers_soniox.py`.
- `scripts/voice/stt-compare.ps1` — writes `manifest.template.json` when the folder has none and stops; otherwise loads the keys that exist, runs, writes `docs/evidence/stt-compare-<date>.json` + the Turkish summary, names the engines that received audio. `-LocalWhisper` adds `--with faster-whisper==1.2.1`.
- `team/plans/stt-engines-measure-adr.md` (worker).

## 7. Tests and pitfalls for the worker
1. **The acceptance pair does NOT differ in `Intent`.** Measured today: both `Ofis bilgisayarımdan hesap makinesini aç` and `Ofisü bilgisayarında hesap makinesini açın` resolve to `app_open`, `application == "calc"`. What differs is the named device: `app.devices.aliases.extract_aliases` returns `('ofis',)` for the first and `()` for the second. A count built on `resolve_intent(...).intent` alone is **0**, and the test would fail for the right reason. Define the compared signature as *(intent, the action fields — `application`, `native_target`, `target_index`, `alarm_minutes`, … —, the named device aliases)* via `app.voice.spoken_device.resolve_without_device_phrase(text, resolve_intent)`; never compare whole `ResolvedIntent` objects (they carry `normalized_text`/`tokens`, so every pair would differ, including the punctuation-only one, which today compares equal only because the normaliser strips punctuation). Read the raw transcript, NOT through the ADR-0224 repair layer (`understanding.normalize` / confusions) — the number is what the engine did, not what the repair hides.
2. WER/CER expected values written by hand in the test (memory: both sides from one source). State in the report whether the table shows the pooled rate (Σ edits / Σ reference words) or the mean of per-sentence rates; pooled is the one to rank by, the existing benchmark's is the mean. Include a pair such as reference `ISI İL` vs hypothesis `ısı il` → 0 errors; with `str.lower()` in place of `turkish_casefold` it is not 0 — that is the I-folding mutation.
3. A missing engine is a row: `status: "NOT_RUN"`, `reason: "not configured"` (no key) / `"not installed"` (faster-whisper) / `"no file input"` (Chrome Web Speech); the run completes and exits 0. Mutation: filter the row out → RED.
4. An engine that fails on ONE file keeps its row: the error is counted per file and the rates are computed over the files that ran, with both counts shown.
5. Latency: wall time per file, p50/p95 over the set — but the files are sent unpaced, so for Soniox this is processing time, not streaming first-token latency; say so in the summary and record `audio_ms` beside it. No wall-clock ceilings as assertions (memory: a stopwatch is not an assertion); inject the clock.
6. "Never writes audio or transcripts outside the output file": snapshot `tmp_path` and the cwd before/after; no transcript in logs or on stdout beyond the summary the script asks for; a manifest `file` that resolves outside the folder is refused.
7. Fake Soniox server: `websockets.sync.server.serve("127.0.0.1", 0)` in a thread, recording frames in order; assert frame 1 is the text config, frame 2 is binary, last is empty; assert zero connections for no-key and for empty audio.
8. Mutations with sha256, restored from a backup copy (never `git checkout --`).

## 8. Risks
- **R1 (privacy):** `docs/evidence/stt-compare-<date>.json` holds the transcripts of the owner's sentences and goes into a git repository hosted on GitHub. With the scripted twenty that is harmless; the summary must still say so, and free speech must never be pointed at this script.
- **R2 (KVKK):** Soniox is a new processor in the US. OpenAI already receives the owner's voice; this task did not re-read OpenAI's retention terms.
- **R3 (owner effort):** Windows 10's Voice Recorder writes `.m4a`. `OpenAISTTProvider.build_request` (outside the area) labels anything that is not WAV/MP3 as `speech.bin`, which OpenAI refuses, and `m4a` is not in Soniox's auto-detected list. The harness must accept WAV (validated with `providers.wav_info`) and report another container as a per-file `unsupported container`, not crash. How the owner produces twenty WAV files without becoming an operator is unsolved in this card — recommended follow-up: a `-Record` mode that reads the manifest and records each sentence through the companion's capture.
- **R4:** the DPA and sub-processor list are unread (account needed). **R5:** RTX 50-series/CTranslate2 unverified → CPU fallback. **R6:** the Soniox model name comes from a docs example.

## 9. Rollback
Additive and unwired: delete the two modules, two tests and the script. Soniox: remove
`PAGENTOS_VOICE_SONIOX_API_KEY` from the secret store and delete the project/account in the Console.
Local Whisper: delete the model directory on E: (the `uv run --with` overlay leaves nothing in the lock).

## 10. THIRD_PARTY_COMPONENTS entry text (lead appends; the integrator does not edit the tree)

```
## STT engines under measurement (2026-10-01, stt-engines-measure) - measured, NOT adopted

Role: candidates compared on the owner's own recordings by `app/voice/stt_compare.py`
(`scripts/voice/stt-compare.ps1`). No default changed; none is registered in `registry.py`.

- Soniox real-time STT (service; Soniox Inc., Foster City, CA, USA). Own adapter
  `app/voice/providers_soniox.py` over `websockets` (BSD-3-Clause, already locked) - no vendor SDK.
  Terms of Service and Privacy Policy effective 2026-06-29, read 2026-10-01: customer content is not
  used to train or improve models; real-time requests are "processed transiently and are not stored";
  the Async/file API stores for 30 days and is NOT used. Usage metadata is logged. US processing by
  default, EU region on request; DPA self-served in the Console (not read - no account). Key:
  `PAGENTOS_VOICE_SONIOX_API_KEY` in the DPAPI secret store; without it the adapter does no I/O.
  Opening the account is a separate owner approval. Phones home: it IS a remote call - the audio of
  each measured file goes to Soniox and the summary names it.
- `faster-whisper` 1.2.1 (MIT; SYSTRAN, https://github.com/SYSTRAN/faster-whisper) with
  `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (MIT; 1.6 GB; Turkish in Whisper's language list).
  Not in `pyproject.toml` / `uv.lock`: run only through `uv run --with faster-whisper==1.2.1` behind
  the script's `-LocalWhisper` switch, on the home PC. Pulls ctranslate2 (MIT) and PyAV (BSD, bundled
  FFmpeg libraries). One model download from Hugging Face, then offline; no telemetry.
- NVIDIA `parakeet-tdt-0.6b-v3` (CC-BY-4.0) - considered and rejected: no Turkish.

Upgrade rule: re-read the Soniox terms (date above) before any adoption; adoption needs its own ADR,
a `Settings` field, a pin and the owner's approval.
```

## 11. For the lead
- This file is written in the main checkout and is uncommitted; hand it to the worker's worktree.
- Lead decisions: (1) may `-LocalWhisper` exist in the script (out-of-tree install + 1.6 GB download on
  the home PC)? (2) R3 — who records the WAV files; (3) the intent-change signature in §7.1 is a design
  choice the acceptance text depends on.
- READY_FOR_OWNER: twenty WAV recordings (§4); by a separate approval a Soniox account (ask for the EU
  region, read the DPA in the Console), then `scripts\secret-store.ps1 -Set PAGENTOS_VOICE_SONIOX_API_KEY`.

## Şu an üzerinde çalışılan
`stt-engines-measure` — STT ölçüm aracı (yalnız ölçüm) · alan: `services/api/app/voice/{stt_compare,providers_soniox}.py`, iki test dosyası, `scripts/voice/stt-compare.ps1`, `team/plans/stt-engines-measure-*` · makine: ev PC'si, worktree `worker-stt-engines-measure`. Durum: bitti, commit + push yapıldı, ağaç temiz.

**Result: nothing to measure — 0 recordings of the owner's speech exist** (0 tracked audio files; 22 text labels and 1 recorded confusion). The instrument is built; no audio was sent to any engine, no account opened, no default changed, no dependency added.

- **sha:** `77b530e6f3f8fc5f3246b2a26dde708409b41bf8`, pushed to `origin/team/d20261001/worker-stt-engines-measure`.
- **Files:** 7 added, all inside the area. `benchmark.py` is unchanged; its `levenshtein` / `word_error_rate` / `char_error_rate` are reused. The integrator's plan was untracked in the main checkout; I copied it in unmodified.

**Tests (38 new: 28 + 10)**
- **RED → GREEN:** first run was a collection error (modules absent); now 44 passed with `test_voice_benchmark`.
- **Hand-computed values:** `ISI İL`/`ısı il` → 0 edits; `aç`→`açın` → 1/3 words, 2/17 letters; the Ofis pair → 3/5 words, 5/36 letters; pooled 4/10 = 0.4 against a per-sentence mean of 0.3111.
- **Intent change:** 1 for the Ofis pair, 0 for the punctuation-only pair.
- **Soniox, against a fake WebSocket server:** returns the transcript from sub-word tokens with a replaced non-final one; frame order is config, audio, empty frame; zero connections with no key or with empty audio.
- **Wider run:** all `test_voice_*` plus every tree-scanning unit test: 2811 passed, 1 skipped. Ruff check and format clean; script-syntax suite (PS 5.1) 142 checked, 0 failed.

**Mutation RED** (each restored from a backup copy, sha256 identical before and after; `stt_compare.py` `c19e7a26…b12e`, `providers_soniox.py` `03f3a3ce…5f0d`)
- Turkish I-folding removed → 4 failed.
- Missing engine dropped from the table → 3 failed.
- Intent compared on `.intent` alone → 3 failed.
- Transcripts printed to stdout → 1 failed.
- Soniox: space-joined tokens, non-final tokens counted, connect without audio, key check removed → 1 failed each.

**Evidence class**
- **Harness and adapter:** PROVEN_AUTOMATED.
- **`stt-compare.ps1`:** run for real in a scratch folder with an empty scratch secret store, not automated (a test file for it is outside the area). Four paths passed: template written; fake stored key loaded and cleared from the environment; one WAV with all engines NOT_RUN writes the report and `.md`; bad manifest exits 2.
- **Numbers on fixtures (PROVEN_PROXY):** NOT_RUN — the fixtures are sine tones no real engine can read.
- **Real OpenAI / Soniox call:** NOT_RUN. Only the OpenAI key is in the store, presence checked, never read.
- **Full unit suite:** NOT_RUN. I stopped my own run at 20 % with no failures; it would have hit its timeout near 45 %.

**Decisions for the lead** (ADR text in `team/plans/stt-engines-measure-adr.md`)
1. **Intent signature differs from plan §7.1.** `reference` is filled for every utterance with the sentence's content words, so `aç`→`açın` counted as an intent change. It is now compared only when `research_class` is set.
2. **No `-LocalWhisper` switch.** The faster-whisper row reads "not installed", and would run if the package ever became importable.
3. **An Azure row was added** (the adapter and setting already existed); it reads "not configured".
4. **WAV only;** another container is skipped per file and sent nowhere.
5. **The script also writes a `.md` summary** beside the JSON (numbers and engine names, no transcripts).

**Open risks**
- The Soniox wire format and the model name `stt-rt-v5` are verified against documentation and a fake server only.
- Who records twenty WAV files is unsolved (Windows Voice Recorder writes `.m4a`).
- Template sentence 9 ("Isıtıcıyı kapat, ışığı aç.") routes to `media_play` today — a router quirk outside the area that will colour that sentence's intent count.
- Another session's `pytest tests/unit` run was live on the machine at the same time; I left it alone.

**READY_FOR_OWNER**
- Twenty WAV recordings (PCM 16-bit, mono, 16 kHz) of the template's sentences, then `.\scripts\voice\stt-compare.ps1 -Folder <klasör>`.
- By a separate approval: a Soniox account (ask for the EU region, read the DPA in the Console), then `scripts\secret-store.ps1 -Set PAGENTOS_VOICE_SONIOX_API_KEY`.

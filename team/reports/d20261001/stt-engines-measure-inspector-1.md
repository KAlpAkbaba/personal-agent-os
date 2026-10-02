# Inspector report — stt-engines-measure (`77b530e6`, cycle d20261001)

**Pass 1 — run**
- **Claimed tests:** 44 passed (28 + 10 + 6 `test_voice_benchmark`) in 11.3 s on a freshly built worktree venv. sha256 of both modules matches the worker's (`c19e7a26…b12e`, `03f3a3ce…5f0d`), before and after my mutations; tree clean.
- **Wider run:** all `test_voice_*` plus the 24 tree-scanning unit files: 2142 passed, 0 failed. Ruff check and format clean. Script-syntax suite: 142 checked, 0 failed.
- **Full unit suite:** NOT_RUN. I stopped it at 7 % after 10 minutes (no failure so far); the lead's gate runs it.
- **mypy:** not runnable here (`uv run mypy` fails to spawn).
- **My mutations (9, different from the worker's, restored from backup copies):** 8 RED, 1 survived.
  - RED: apostrophe kept (2 failed); pooled WER swapped for the mean (1); NOT_RUN rows listed as "audio sent" (2); manifest path outside the folder allowed (1); non-WAV sent to engines (1); key not scrubbed from the vendor error (1); `client_reference_id` sent (1); `recv` without timeout (1).
  - Survived: manifest read as `utf-8` instead of `utf-8-sig` — 28 passed. The BOM handling works (see the real run) but no test holds it.
- **Real run of `stt-compare.ps1`** (scratch folder and evidence dir, PS 5.1, both `-File` and typed-name invocation):
  - No manifest: template written, exit 0, no evidence dir created.
  - Empty scratch store: 6 NOT_RUN rows, "Ses hiçbir motora gönderilmedi", `.json` and `.md` written, exit 0.
  - Real store, real OpenAI call: I sent two locally synthesised English sentences (SAPI Zira, manifest `en-US`, BOM manifest) to OpenAI through the owner's stored key. Both `openai:gpt-4o-transcribe` and `openai:whisper-1` RAN, 2/2 files, WER 0.0000; p50/p95 953/6140 ms and 2339/3000 ms. The key was gone from the environment afterwards and is not in the report or summary. The summary names OpenAI as the only receiver.
  - This is a pipeline proof, not a Turkish number: the PC has no Turkish voice and no owner recordings exist.
- **Intent count:** each of the 20 template sentences against four re-punctuated or re-cased renderings of itself gave 0 false intent changes. `unutma`→`unut` counts as a change; `on`→`10` does not (it is a word error only).
- **PostgreSQL / host snapshot:** not applicable. The diff touches no table, migration, store, broker, container or `scripts/cloud`; the HANDOFF and QUALIFICATION lines in `main...HEAD` come from the base commit, not the worker.
- **Unwired, as claimed:** nothing else in `app/` imports either module; `benchmark.py` is unchanged; all 7 files are inside the area.

**Pass 2 — break**
1. **A non-`VoiceError` exception aborts the whole run with no report.** `run_comparison` catches only `VoiceError`. A scripted engine raising `RuntimeError` after another engine had already received the audio left no report. The real routes are a faster-whisper model load or download failure (that row runs as soon as the package is importable) and a non-JSON 200 body in `resp.json()`. This breaks "the run still completes" and "the summary names which engines received it".
2. **The output file is written only after every engine has run.** `--out` in a missing directory raised `FileNotFoundError` at the end, after the audio had been sent. The ps1 creates the directory, so only the direct CLI is exposed.
3. **Same-day reruns can overwrite evidence.** Two runs in the same second both got `stt-compare-2026-10-02-013535.json` and the second replaced the first; the timestamped name is not checked for existence. This contradicts the script's "never overwritten".
4. **`--write-template` overwrites an edited `manifest.template.json`.** A rerun before the rename loses the owner's edits.
5. **WAV is checked by header only.** A truncated RIFF/WAVE stub counted as usable with `audio_ms` 0; PCM 16-bit, mono, 16 kHz is not validated although the plan named `wav_info`.
6. **Research sentences count spelling variants as intent changes.** `zeka`→`zekâ` on template sentence 12 is counted, because `reference` is compared whenever `research_class` is set.
7. **The faster-whisper row would download 1.6 GB to the default Hugging Face cache on C:** if the package ever became importable; the plan says it must go to E:. Not reachable today (not installed).
8. **`--soniox-url` accepts any host, including `ws://`,** and the key goes there in the first frame. It is a CLI parameter only, and the summary names the host.
- No secrets or machine paths in code beyond the existing winget uv lookup. Soniox terms, licences and the recordings inventory (zero audio) are in the plan with links and dates.

**Evidence classes**
- Harness and Soniox adapter (fake server): PROVEN_AUTOMATED.
- OpenAI path through `stt-compare.ps1`, both models: PROVEN_PROXY (synthetic English audio, real API).
- Turkish WER/CER/intent numbers: NOT_RUN — nothing to measure: 0 recordings.
- Soniox against the real service (wire format, model `stt-rt-v5`): NOT_RUN — no account, separate approval.
- READY_FOR_OWNER: twenty WAV recordings; by separate approval, a Soniox account.

RETURN (1. catch any exception per file in `run_comparison` as a per-file error so the row and the report survive, with a test; 2. check the `--out` parent before any engine is called; 3. never overwrite an existing evidence file when the timestamped name exists; 4. add a BOM-manifest test; items 4–8 of Pass 2 for the lead to accept or queue)

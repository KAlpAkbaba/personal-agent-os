# Inspector report — stt-engines-measure (`c8424e25`, cycle d20261002, second pass)

**Pass 1 — run**
- **Claimed tests:** 50 passed (34 + 10 + 6 `test_voice_benchmark`) in 10.7 s. Wider run (`test_voice_*`, the two task files, `test_ci_covers_every_suite`): 903 passed in 288 s. These match the worker's numbers.
- **Hashes:** `stt_compare.py` `3ab456da…30d5` and `stt-compare.ps1` `4e3604e2…4180`, before and after my mutations; tree clean at the end.
- **Lint:** ruff check and format clean; script-syntax suite 142 checked, 0 failed.
- **mypy:** NOT_RUN — not installed in either venv (`uv run --no-sync mypy`: "Failed to spawn"). Full unit suite: NOT_RUN (the lead's gate).
- **The four returned points are fixed** and each is held by a test, except the script's name loop (see Pass 2, item 2).
- **My mutations (8, different from the worker's and from pass 1; restored from a backup copy):** 8 RED.
  - `continue`→`break` after a per-file exception: 3 failed.
  - FAILED engine left out of `audio_sent_to`: 1 failed.
  - Named devices dropped from the intent signature: 2 failed.
  - `--out` `OSError` no longer caught: 1 failed.
  - `VoiceError` class replaced by the type name: 1 failed.
  - Percentile floor instead of ceil: 1 failed.
  - Unselected engines dropped from the table: 1 failed.
  - `reference` always compared: 2 failed.
- **Script mutation** (the `.md` check removed from the name loop): the real run replaced an earlier `.md`. RED by real run only.
- **Real runs of `stt-compare.ps1`** (PS 5.1, scratch folder and evidence dir, BOM manifest):
  - Only the day's `.md` present: exit 0, timestamped name chosen, the `.md` untouched.
  - Three runs at once: one report, two exit 2 with "already exists", nothing replaced.
  - Real store, real OpenAI call, typed-name invocation: two synthetic English sentences (SAPI Zira) and one header-only WAV. Both `openai:gpt-4o-transcribe` and `openai:whisper-1` RAN with 2 measured and 1 failed (`dependency_unavailable` on the stub), WER 0.0000, p50/p95 1246/3074 ms and 1490/1905 ms. The report and a correctly encoded Turkish `.md` were written, the summary names OpenAI only, the key was absent from the environment before and after, and no `sk-` appears in the report.
  - This is a pipeline proof on `c8424e25`, not a Turkish number: the PC has no Turkish voice and there are no owner recordings.
- **PostgreSQL / host snapshot:** not applicable — the diff touches no table, migration, store, broker, container or `scripts/cloud`. All 7 files are inside the area; `benchmark.py` is unchanged; nothing else in `app/` imports either module.

**Pass 2 — break**
1. **A failed final write still loses the report after the audio was sent.** A transcript holding a lone surrogate made `write_text` raise `UnicodeEncodeError`: traceback, the reserved name deleted, both engines had received both files, no report. Ctrl+C and `SystemExit` in an engine end the same way. The worker disclosed the disk-full variant; this is a second, reproducible route, though it needs a malformed vendor string.
2. **No automated test holds the script's name loop.** The JSON half is held by the exclusive create (worker's M3); the `.md` half only by a real run.
3. **A letter error inside a free-text field a tool carries counts as an intent change.** Besides `zeka`→`zekâ` (pass 1, item 6), `media_query` behaves the same: "Isıtıcıyı kapat, ışığı aç" heard with a dotted `i` is counted. The intent count will overstate on such sentences.
4. **`-Engines` relabels `chrome-web-speech` as "not selected"** instead of "no file input". Cosmetic.
5. **Pass 1 items 4–8 are unchanged**, as the ADR addendum says: template overwrite, WAV header-only check (my stub counted as usable and was sent), HF cache on C:, `--soniox-url` accepting any host. They are the lead's to accept or queue.
6. **The worker's report JSON records two denied PowerShell calls** for its four-run proof. I cannot tell how it obtained those runs; my own runs reproduce the behaviour it describes.
- A kill mid-run leaves a 0-byte placeholder in the evidence dir; later runs step past it.
- No secrets or new paths in code; rollback is deleting the five files.

**Evidence classes**
- Harness, per-file failure handling, exclusive `--out`, Soniox adapter (fake server): PROVEN_AUTOMATED.
- Script name choice and the race; OpenAI path through the script, both models: PROVEN_PROXY.
- Turkish WER/CER/intent numbers: NOT_RUN — nothing to measure: 0 recordings.
- Soniox against the real service (wire format, model `stt-rt-v5`): NOT_RUN — no account, separate approval.
- mypy and the full unit suite: NOT_RUN on this machine; the lead's gate runs them before the merge.
- READY_FOR_OWNER: twenty WAV recordings; by separate approval, a Soniox account.

Items 1–5 above are follow-ups for the lead to queue, not merge blockers: the acceptance criteria and the four returned points hold.

APPROVE

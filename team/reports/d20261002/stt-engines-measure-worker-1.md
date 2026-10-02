## Şu an üzerinde çalışılan
- **Task:** `stt-engines-measure` (the inspector's return, 4 points), cycle d20261002
- **Area:** `services/api/app/voice/stt_compare.py`, `tests/unit/test_stt_compare.py`, `scripts/voice/stt-compare.ps1`, `team/plans/stt-engines-measure-adr.md`
- **Machine:** the owner's home PC, worktree `worker-stt-engines-measure`
- **State:** done, committed and pushed; tree clean; HANDOFF not touched (lead writes it).

## Report
**sha:** `c8424e256dfa1d5de1ba2d1bcfd803d926b24ab6` (on top of `77b530e6`, pushed). 4 files changed, all inside the area; `benchmark.py` and `providers_soniox.py` are unchanged.

**The four returned points**
1. **Any per-file exception is that file's error.** The file is recorded as `unexpected: <ExceptionType>` (type only, the message is not kept). The row keeps both counts, and an engine that raises on every file is `FAILED` and still named in `audio_sent_to`.
2. **`--out` is settled before any engine is called.** `main` creates the file exclusively first; a missing folder is exit 2 with nothing sent.
3. **A report is never overwritten.** An existing `--out` is exit 2 before any engine is called; the script now picks the day's name, then the second's, then `-2`, `-3`, and checks the `.md` beside it.
4. **BOM manifest test added.**

One behaviour change for the lead: the direct CLI now refuses an `--out` that already exists.

**RED → GREEN:** 6 tests added. Against the unchanged module: 5 failed, 29 passed (the BOM test passed, as that behaviour already worked; mutation M5 holds it). After the change: 34 passed; with the Soniox and benchmark files, 50 passed.

**Mutation RED** (restored from a backup copy; `stt_compare.py` sha256 `3ab456da…30d5` before and after, 34 passed once restored):
- M1 catch only `VoiceError`: 2 failed
- M2 name not taken before the engines: 3 failed
- M3 exclusive create changed to overwrite: 1 failed
- M4 held name not given back on bad input: 2 failed
- M5 manifest read as `utf-8`: 1 failed
- M6 exception message kept in the report: 2 failed
- M7 Turkish I-folding removed: 5 failed
- M8 missing engine dropped from the table: 3 failed
- Script mutation (pre-fix name choice put back, sha256 `4e3604e2…4180` before and after): with 241 timestamped names pre-taken, runs 3 and 4 exit 2 with "already exists"; no placeholder was replaced.

**Real run of `stt-compare.ps1`** (scratch folder and evidence dir, BOM manifest, one synthetic WAV, `-Engines chrome-web-speech`, dummy keys so the secret store was not read and no audio was sent):
- Fixed script, four `-File` runs: exit 0 each, names `…-10-02.json`, `…-024649.json`, `…-024650-2.json`, `…-024651-2.json`; 241 of 241 placeholders intact.
- Typed-name invocation: exit 0, six NOT_RUN rows, "Ses hiçbir motora gönderilmedi".

**Checks:** ruff check and format clean; script-syntax suite 142 checked, 0 failed; `test_voice_*` plus the two task files plus `test_ci_covers_every_suite`: 903 passed.

**Evidence classes**
- Harness (points 1–4): PROVEN_AUTOMATED.
- Script name choice: PROVEN_PROXY (real script, scratch dirs, no engine).
- Turkish WER/CER/intent numbers: NOT_RUN — nothing to measure: 0 recordings.
- Soniox against the real service: NOT_RUN — no account, separate approval.
- Full unit suite and mypy: NOT_RUN (the lead's gate).
- No real engine call this pass; the inspector's OpenAI pipeline proof stands for `77b530e6` only.
- READY_FOR_OWNER: twenty WAV recordings; by separate approval, a Soniox account.

**Not done:** inspector Pass 2 items 4–8 are untouched and listed in the ADR addendum for the lead to accept or queue.

**Open risks**
- There is no automated test of the script's name loop; it is held only by the real run above.
- If the final write fails (disk full) after engines ran, the reserved name is deleted and no report remains.

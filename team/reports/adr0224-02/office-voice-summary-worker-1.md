**Şu an üzerinde çalışılan:** office-voice-summary — `services/api/app/team/`, `app/voice/`, ses derlemi (corpus) — bu makine. (HANDOFF.md'yi lead yazar.)

**sha:** `c7793882e161ede989388d298078513dee562c92` (branch pushed, worktree clean). 7 files, all inside the area.

**What was built**
- `app/team/speech.py::office_paragraph(view)`:
  - Pure and deterministic, about 60 words at most.
  - Reads "Altı kişiden iki çalışan çalışıyor: A ve B. Bir görev geri döndü. Bir onayınız bekliyor."
  - No task ids. Titles are cut at the first clause, six words at most, three titles at most.
  - Usage limit is named only when it is not ok. The cost is always written "Tahmini maliyet … dolar".
  - Nothing running gives exactly "Ekip şu an çalışmıyor efendim.", with the returned-task and approval sentences appended when they apply.
- `app/voice/intents.py`: new `Intent.TEAM_STATUS`, a query class mapped to the `team.status` tool. The matcher uses the exact words "ekip" and "ajanlar", never the stem "ajan", which would catch "ajanda". "ofiste" counts only together with "kim" and "çalış".
- `tools_team.py`: `team.status`, empty argument object, returns `{status, speech}`. It reads `ctx.live["team_store"]`/`["team_root"]`. If those are absent it falls back to a FileStore on the repository `team/`, as the route does.
- Corpus: 4 sentence rows and 2 neighbour rows (`team.status.1-4`, `team.neighbour.device`, `team.neighbour.narrative`).
- ADR text is in `team/plans/office-voice-summary-adr.md`.

**Tests (evidence PROVEN_AUTOMATED)**
- Red first: `test_team_speech.py` failed at collection (`No module named app.team.speech`). The router tests were not seen red before the matcher existed; the mutation below is their red proof.
- Green: 7 speech tests and 11 router tests. They cover the busy paragraph (three facts, no id, 60 words or fewer), the idle sentence, the four sentences, the two neighbours, the lookalikes ("Ajandada ne var?", "Ekip toplantısını takvime ekle.") and the tool through the real handler from a FileStore fixture.
- Owner Utterance Suite: 2774 passed with `team.status` temporarily registered. That run included the new corpus rows and my 18 tests, and took 858 s.
- Registration was done only in a temporary edit of `tools.py` and `step_up.py`. Both were restored from a backup copy, and sha256 matches the original (`a6d1f897…`, `386a5f28…`).
- Mutation 1: dropping the approvals sentence went RED with 2 failures. `speech.py` sha256 `e34edd72…` before and after.
- Mutation 2: disabling the router branch went RED with 4 failures. `intents.py` sha256 `9fb6dae4…` before and after.
- ruff check and format pass on the files I touched.

**Findings**
- "Ofiste ne yaptın?" resolves to `artifact_list` today, not the narrative. I did not change that; the test asserts only that it is not TEAM_STATUS, and the corpus row records `artifact_list`.
- Until the lead's registry line exists, the corpus rows for `team.status` fail in the harness. They all passed in my run with the temporary registration.

**For the lead at merge**
1. `app/voice/realtime_sessions/tools.py`: add `from app.voice.realtime_sessions.tools_team import register_team_tools` near line 59, and `register_team_tools(reg)` after `register_briefing_tools(reg)` (line ~2600).
2. `app/security/step_up.py`: add `"team.status": TIER_OPEN` after `"briefing.overnight_work"`. Otherwise shadow mode logs it as `sensitive`.
3. Voice service: put `app.state.team_store` and `team_root` into `ctx.live`. Without them the FileStore fallback is wrong in database mode, where the queue is not in `team/`.
4. `docs/DECISIONS.md`: number the ADR text. `tests/voice_corpus/routing.py` (outside my area) can take a TEAM_STATUS row if wanted.

**Not run:** the full unit suite beyond the corpus and the weather/briefing intent tests. PROVEN_REAL needs the owner to ask by voice during a cycle.

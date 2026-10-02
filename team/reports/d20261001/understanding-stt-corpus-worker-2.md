## Şu an üzerinde çalışılan
`understanding-stt-corpus` (dönüş, döngü d20261001) — alan: `stt_harness.py`, `test_stt_utterance_corpus.py`, `collect-stt-corpus.ps1`, ADR metni — makine: sahibin geliştirme PC'si, worktree `worker-understanding-stt-corpus`.

**Return points 1–3 are fixed and pushed; the ≥ 95 % acceptance is still unmet at 68.9 % (73/106), which is point 4 and the lead's call.**

- **sha:** `1f81f8bd75a311a442687ca00f28b6b6bd15b33e`, pushed, tree clean.
- **Files:** 4 changed, all inside the area (`stt_corpus.py` untouched).

**What changed**
1. **Collector, whole renderings:** it now reads the corpus's own tables (3 real, 103 derived) and matches letter for letter. A real rendering is skipped; a sentence equal to a derived one is proposed with `confirms_derived_case` set to that case's id. If the parsed counts differ from the corpus lines, the tool stops.
2. **Collector, paths and output:** `-OutPath` equal to `-DumpPath` is refused in three spellings and the dump's sha is unchanged. Empty `candidates` is `[]`.
3. **Report:** `wrong_device_observable_cases` = 11 sits beside `wrong_device_actions` = 0, in the run and in `understanding.stt_corpus`.
4. **Lead decision:** the ADR status now reads "instrument delivered, target NOT MET" and says re-carding is the lead's. The target test stays a strict xfail.

**Beyond the return, same file**
- Proposal keys no longer fold case (`[ordered]@{}` merged "Aç" and "aç").
- Relative paths resolve from the shell's location, checked by hand with the script typed by name.
- An `-OutPath` inside the repository but outside `state/reports` is refused, with a test (inspector's KVKK note 7). Drop this guard if the lead wants proposals elsewhere in the tree.

**RED → GREEN (PROVEN_AUTOMATED)**
- RED before the fix: 7 failed, 2 passed. Examples: `KeyError: 'corpus_seen'`; `already_in_corpus` 6 where 1 was expected; `-OutPath` = dump exited 0.
- GREEN after: the same selection, 9 passed.
- Full STT suite in nightly order, after the owner suite, one report file: 135 passed, 1 xfailed.
- Owner Utterance Suite: 2756 passed, report 2754/2754 HEALTHY. The merged file holds 1.0 and 0.6887.

**Mutation RED** (restored from a backup copy; sha256 same before and after: `stt_harness.py` `e1afdd4f…`, `collect-stt-corpus.ps1` `55249003…`)

| Mutation | Failed |
|---|---|
| M1 judge wrong-device rule removed | 4 |
| M2 observable count = every case | 2 |
| M3 substring match again | 2 |
| M4 derived counts as already there | 1 |
| M5 dump-path refusal removed | 1 |
| M6 candidates from the if-expression | 2 |
| M7 case-folding keys | 1 |
| M8 tracked-tree guard removed | 1 |

**Fast checks:** ruff check and format clean on the three Python files; the ps1 parses with 0 errors, ASCII, LF.

**NOT_RUN**
- Collector against production (the lead runs it read-only over SSH).
- The `-ShowQuery` SELECT on dev Postgres (query unchanged since the inspector's run).
- Full `quality-gate.ps1`.
- Layer-2 engine measurement.

**Open risks**
- With M1, the run-level `test_stt_corpus_has_no_wrong_device_action` stays green; only the judge unit tests go RED. No product path produces a wrong device today, as the inspector found.
- "0 wrong-device" covers 11 cases; running all acting cases over two devices is a follow-up.
- The collector reads the corpus by regex over ruff-formatted double-quoted literals; another shape stops the tool rather than matching less.
- Not addressed: the harness still imports private helpers from `test_operator_open_application_fallback.py`, and the `invented_suffix` band inconsistency (inspector 8) stands.

**For the lead at merge**
- Run the STT file after the owner file, no xdist.
- `voice-routing-qualification.ps1` should print `understanding.stt_corpus`, including `wrong_device_observable_cases`.
- Number the ADR in `team/plans/understanding-stt-corpus-adr.md`.
- Decide point 4, and cut the product tasks that shrink `KNOWN_GAPS`: polite forms for every verb table, a fused-word split, exact rules that can be contested.

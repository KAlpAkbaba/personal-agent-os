# ADR-0258 addendum — the owner's trials wired: the report, the inspector's form, the release step

Status: proposed (worker, cycle d20261003, task owner-trials-wiring; the lead numbers it as an
ADR-0258 addendum at merge)

**Decision.**
- **The cycle report** (`scripts/lib/TeamRun.ps1`, `Format-TeamOwnerTrial`): under "Sahibin
  gerçek cihazda deneyecekleri" a trial object is one line
  `<task>: "<sentence>" — makine: <machine> — beklenen: <expect> — durum: denenmedi | oldu | olmadı (<said>)`;
  the old plain-string form still prints as `<task>: <sentence>`. The section lists
  `awaiting_real_evidence` tasks as before (unchanged).
- **The inspector's form**: four `key: value` lines, ASCII keys (no mojibake risk in a
  console): `deneme:` (the id, `^[a-z0-9][a-z0-9-]{0,63}$` as in the schema), `cumle:`,
  `makine:`, `beklenen:`. Reader: `trials.parse_inspector_trials(report) -> list[trial]`, in
  `services/api/app/team/trials.py` (not the cycle's PowerShell reader: the lead's merge step
  and the API both run Python, and the schema check is there). A block opens at `deneme:`; a
  line missing, empty, written twice, an id the schema refuses or a sentence over
  `SENTENCE_MAX_CHARS` = 300 skips the block - never guessed, never raised.
  The schema has no length bound on `sentence` (only `minLength: 1`); 300 is the reader's
  own bound (a sentence the owner says aloud), recorded here, not a schema change.
- **Released with open trials**: `trials.released_state(task)` -> `awaiting_real_evidence` when
  any trial is not "oldu" (an old plain sentence has no verdict, so it counts as open), else
  `released`. Pure: it reads the task and returns a state; the lead's release step writes it.
- **The vocabulary**: the list EXISTS - `app/ledger/vocabulary.py` `EVENT_TYPES` (where
  `team.task.approved` / `team.task.rejected` live as `EVENT_TYPE_TEAM_TASK_*`). It is outside
  this card's area, and adding the two names also breaks
  `tests/unit/test_team_trials.py::test_a_ledger_that_refuses_the_event_leaves_the_queue_untouched`
  (it relies on "the vocabulary as it is today" refusing the event) - that file is to stay
  unedited by this card. Not added; area request below. A strict xfail in
  `test_team_trials_wiring.py` turns red the moment the names land, so its mark is removed then.

**The inspector role paragraph (to paste into `.claude/agents/inspector.md`, after the
"Evidence classes you may assign" line).** The harness refused the worker's write to
`.claude/agents/`; `test_the_adr_paragraph_example_parses` checks the example below, and the
strict xfail `test_the_role_file_example_block_parses` turns red once it is pasted (remove the
mark then). The verdict rules are not touched.

> **The owner's trial (ADR-0258).** For every claim whose evidence is READY_FOR_OWNER, write the
> trial the owner makes, above the verdict, as four lines alone on their own lines in this fixed
> form (`services/api/app/team/trials.py` `parse_inspector_trials` reads it into an
> `owner_trials` object): `deneme:` an id (lower case, digits, `-`), `cumle:` what the owner says
> or does (at most 300 characters), `makine:` the device, `beklenen:` what the owner must see or hear.
> A block missing a line is skipped, not guessed:

```
deneme: ses-saat
cumle: Saat kaç?
makine: ev PC (masaüstü uygulaması)
beklenen: saati Türkçe söyler
```

**Area requests (for the lead).**
- `.claude/agents/inspector.md` - paste the paragraph above; drop the xfail mark.
- `services/api/app/ledger/vocabulary.py` - `EVENT_TYPE_TEAM_TRIAL_PASSED = "team.trial.passed"`,
  `EVENT_TYPE_TEAM_TRIAL_FAILED = "team.trial.failed"` beside the `TEAM_TASK_*` pair and in
  `EVENT_TYPES`; `services/api/tests/unit/test_team_trials.py` - the "vocabulary as it is today"
  case needs a monkeypatched vocabulary without the two names; drop the xfail mark.

**Open risks.** The inspector's report is kept to 40 summary lines in the queue; a trial block
past them is only in the report file - the reader should be given the file, not the summary.

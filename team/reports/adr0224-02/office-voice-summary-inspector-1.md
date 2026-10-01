**Inspector report: office-voice-summary (`c7793882`, branch `team/adr0224-02/worker-office-voice-summary`)**

**Pass 1: run**
- I re-ran the speech and router tests from a clean state: 18 passed in 11.5 s (7 speech, 11 router). This matches the worker's numbers.
- My own mutation: I changed `Tahmini maliyet` to `Maliyet` in `speech.py`. `test_usage_limit_and_estimated_cost_are_named_only_when_they_matter` went RED (1 failed, 6 passed). I restored from a backup copy; sha256 prefix is `1394673093ce` before and after, and `git status` is clean.
- Not re-run by me: the worker's two mutations (approvals sentence dropped, router branch disabled), the 2774-row Owner Utterance Suite and ruff. I did not mutate the router or the tool path. The router tests and the corpus rows are therefore backed only by the worker's own run, which needed a temporary `team.status` registration that was later restored.
- Database and infrastructure: the diff adds no table, migration, store or scheduler. It only reads the stores that already exist. A Postgres-only failure is therefore not at issue, and no integration test is required.
- The fast gate and the full `quality-gate.ps1` were not run. This is a worker branch, and the files outside the area are the lead's at merge.

**Pass 2: adversarial**
- All 7 files are inside the task area.
- Corpus rows for `team.status` fail in the harness until the lead registers the tool. The worker's report names this, and the lead's registry line fixes it.
- `office_paragraph` handles empty and `None` input (`{}` and `None` agents, approvals and cycle) and returns the idle sentence.
- The paragraph carries no task id, and a title is cut at its first clause.
- Probe: 8 working agents, 12 approvals, usage limit "waiting" and 3.2 USD gave 19 words. It was one paragraph, in this order: workers, approvals, usage limit, estimated cost.
- Two cosmetic edges:
  - It said "Altı kişiden sekiz çalışan", so working agents can exceed the stated capacity. This is impossible in real data.
  - It said "12 onayınız" with digits above ten, which a Turkish voice reads fine.
- The `team.status` argument object is empty, so the relay's key filter cannot trigger.
- No secrets, paths or personal data (KVKK) go to logs or audit. The tool only reads and speaks.
- The router matches the exact words "ekip" and "ajanlar", not stems. Lookalike tests ("Ajandada ne var?", "Ekip toplantısını takvime ekle.") cover the collision risk.
- "Ofiste ne yaptın?" resolves to `artifact_list`, not the narrative intent. This is existing behaviour, and the worker declared it honestly. It does not violate the acceptance, which only requires it to stay out of TEAM_STATUS.
- The tool's fallback is a FileStore on the repository `team/` directory. In database mode that is wrong unless the lead puts `team_store` and `team_root` into `ctx.live`. The worker flagged this too, and it is merge item 3.
- Rollback is trivial: one new intent, one new tool, and the added lines in the registry and step-up files.

**For the lead at merge (confirmed from the diff)**
1. `tools.py`: import `register_team_tools` and call it after `register_briefing_tools(reg)`.
2. `step_up.py`: add `"team.status": TIER_OPEN`.
3. Voice service: put `team_store` and `team_root` into `ctx.live`. After merge, check that "ekip ne yapıyor" in database mode reads the real queue.
4. Run the corpus after registration. The worker's pass was 2774/2774, but with a temporary registration.
5. Number the ADR in `docs/DECISIONS.md`.

**Evidence classes**
- Paragraph content, idle sentence, router resolution and the tool through the real handler: PROVEN_AUTOMATED.
- Suite 100 % with the tool registered: PROVEN_PROXY (the worker's run; not re-run here).
- Owner asks by voice during a cycle: NOT_RUN. `PROVEN_REAL` needs the owner.

APPROVE

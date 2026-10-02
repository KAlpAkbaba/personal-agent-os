**Inspector report — webtask-prc-gate-bindings (second pass), branch `team/d20261001/worker-webtask-prc-gate-bindings` @ `ca78b12b`**

**Pass 1 — run it**
- The two files: `302 passed`. Every webtask unit test (`-k webtask`): `349 passed`. Both match the worker's numbers.
- RED before: main's `gate.py` + `risk.py` under the branch's tests gives `54 failed, 248 passed`.
- `ruff check .` (whole API) and `ruff format --check` on the four files: clean.
- Full API unit suite: NOT_RUN to completion. It ran green to about 60% and my 1700 s cap killed it (another agent's pytest shared the machine); the lead's gate must complete it.
- My own mutations: 29, different from the worker's, each restored from a backup copy. `gate.py` is `2b733271…a5a9` and `risk.py` `7e5b5c70…5fe6` before and after; tree clean. 24 were RED, including:
  - writes not classified (13 failed), high-impact name ignored on writes (8), `submits` ignored on writes (1);
  - toggle click treated as a plain click (2), `in_form` ignored for unnamed controls (8);
  - each of `submits` / `in_form` / `href_host` dropped from the grant comparison (5 / 3 / 3);
  - sensitive check for fill only (2), payment check skipping `set_checked` (3) or `fill` (1);
  - 'adsız' not said (6), second read-back not announced (6), every act read as a click (7).
- Postgres / infra / host snapshot: not applicable. No table, migration, store, broker, container or script is touched.
- The worker's two commits stay inside the area. The `docs/HANDOFF.md` change in `main...HEAD` comes from the lead's commit `4aa9e7e4`, not from the worker.

**Pass 2 — break it**
- Card acceptance holds under direct probing of `decide` / `url_is_allowed`: every item behaves as the card says, except that the 'Satın al' select and 'Abone ol' checkbox are handed over as payment rather than read back (the ADR's stricter reading). A grant never opens a payment.
- The grant held on select and set_checked too: rewired `submits` / `in_form`, a changed host, a legacy grant without the wiring facts and an empty-facts grant all produce a new read-back. A grant for one value or action does not open another.
- Five mutations survived (GREEN):
  1. **Suffix boundary on the no-apostrophe alternative removed** (`…|ya|ye)(?![0-9a-z])`, new code). Behaviour is correct today ("karadeniz haberlerini bul" does not allow kara.com; "trendyoldaş" and "tatildeyken" are refused), but no test pins it.
  2. **A glyph-only name ('×') read back as a name.** Classification is covered; the read-back sentence for a glyph is not.
  3. `len(label) >= 4` relaxed to 1, and
  4. the address-word scrub removed. Both lines predate this task and are untested.
  5. `submits` dropped from the unnamed-control rule: equivalent, since `classify_element` / `classify_write` already raise it.
- The select's chosen value is not judged: `select_option` on a select named 'İşlem' with the value "satın al" (when the owner said those words) is allowed as REVERSIBLE_WRITE. Outside the card; decide it on the v1.8 card.
- An unnamed button outside a form with `submits=False` is clicked freely. This is the usual single-page-app icon button; it follows the card, and the worker flagged it.
- Narrower than main for the owner's phrasing:
  - "YouTube aç" (bare name) and "youtubeu aç" (apostrophe dropped) now name no site.
  - "n11'de" is refused by the 4-letter floor, which predates this task.
  - `sitesi[a-z]*` also accepts "sitesiz".
  - No production effect today: `webtask` is reached only from `app/worker.py`, not from voice. Whether production speech-to-text keeps the apostrophe is still unverified and needs settling before `webtask-model-planner` meets real speech.
- No secrets or paths, no logging added, the typed value is not in the read-back, both modules stay pure. Rollback is a revert of two commits; no state or schema.
- `risk.py`'s docstring cites `tests/unit/test_webtask_risk.py`, which does not exist. The line predates this task.

**Evidence classes**
- Gate and acceptance behaviour, the card's two mutations, grant binding: PROVEN_AUTOMATED.
- Full API unit suite to completion, `quality-gate.ps1`: NOT_RUN.
- Real Chrome, a real model planner, real speech-to-text apostrophes: NOT_RUN, and outside this card's expected evidence.

**For the lead at merge**
- Add two near-miss tests on the next card that touches these files: "karadeniz haberlerini bul" must not allow kara.com, and a '×' button's read-back must say 'adsız'.
- Record the next card "contract v1.8: the ceiling on fill/select/set_checked", and put the select-value question on it.
- Number the ADR.
- Let the full unit suite finish in the gate.

APPROVE

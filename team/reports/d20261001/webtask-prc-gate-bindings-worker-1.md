## Şu an üzerinde çalışılan
`webtask-prc-gate-bindings` (PR-C 2/2, roadmap 2a) — area: `services/api/app/webtask/{gate,risk}.py`, the two test files, the ADR draft — machine: owner's dev PC, worktree `worker-webtask-prc-gate-bindings`. **Done, committed, pushed; tree clean.**

**sha:** `999b1b471a9480435e787907773416795b9a969b` on `team/d20261001/worker-webtask-prc-gate-bindings`
**Files changed:** 5, all inside the area (`gate.py`, `risk.py`, `test_webtask_gate.py`, `test_webtask_acceptance.py`, `team/plans/webtask-prc-gate-bindings-adr.md`).

**One deviation from the card — please rule on it.** The card asks for a HIGH_IMPACT read-back on a select named 'Satın al' and a checkbox 'Abone ol', but both names are payment markers. I made them `ask_owner(payment)` with risk HIGH_IMPACT, because a read-back can be confirmed and ADR-0207 decision 4 says a payment is not performed with a confirmation either. The confirmable HIGH_IMPACT read-back is shown on 'Hesabı sil' instead.

**RED → GREEN (PROVEN_AUTOMATED)**
- Baseline was 189 passed. With the new tests and no implementation: 47 failed (a 48th was my own test's Playwright import, fixed in the test).
- After: the two files 269 passed; with `test_webtask_service`, `test_webtask_device_port`, `test_browser_contract_v17` and `test_execution_target`, 383 passed.
- `tests/integration/test_webtask_worker.py` on the dev stack (real Temporal and Postgres): 5 passed when run by itself. In the same pytest invocation as two unit files it gave 5 setup errors; I did not investigate why.
- ruff check and ruff format: clean. There is no type checker in the API venv.

**What was built**
- **Writes:** fill / select_option / set_checked are classified from name and `submits`; the Decision keeps `risk_ceiling`.
- **Payment:** a payment marker hands over for all four element actions, and a grant does not change it.
- **Unnamed:** an unnamed control (including a glyph like "×") that submits or sits in a form is EXTERNAL_COMMUNICATION and is read back as "adsız bir düğme". A plain link and text entry are exempt.
- **Site name:** only site position counts. "youtube'da", "trendyol sitesinde" allow; "dünya haberlerini bul" allows nothing.
- **Hostile SPA:** three variants (submits, in_form, link host rewired) each end with a spent grant, a second read-back and zero click commands.
- **Icon-only:** three acceptance tasks (in-form icon, icon link, two indistinguishable icons).
- **Sensitive field:** needed no code and was green before, so it has no RED-first. The new test runs the worker's own `is_sensitive` on a text field named 'Kart numarası' for all three writes; its mutation is below.

**Mutation RED** — each restored from a backup copy; sha256 equal before and after (`gate.py` 6f70a14a…, `risk.py` 7e5b5c70…):
- payment check restricted to click: 6 failed
- site rule reverted to every word: 7 failed
- writes static again: 12 failed
- unnamed rule off: 8 failed
- grant compares name and role only: 9 failed
- "adsız" not said: 6 failed
- sensitive check for fill only: 2 failed

**Beyond the card (all in the ADR draft)**
- A click on a checkbox, radio or switch is classified as `set_checked`; otherwise a planner reaches the wired checkbox through `click`.
- The site rule also accepts `tan/ten`, `-ki` and `'a/'e` (apostrophe required), so "Facebook'tan", "YouTube'daki" and "Google'a git" keep working.
- The read-back names the action ("alana yazacağım", "listeden seçim yapacağım", "kutunun işaretini değiştireceğim") instead of "düğmeye basacağım".
- A re-ask after a spent grant starts with "Onayınızdan sonra sayfa değişti; yeniden soruyorum."

**Not done / NOT_RUN**
- Full API unit suite and the full local gate: NOT_RUN (only the webtask and neighbouring files above).
- Real Chrome and a real model planner: NOT_RUN; the planner is scripted and the browser is the fake.
- The ceiling on the wire for the three writes is not here. **Lead at merge: record "contract v1.8: the ceiling on fill/select/set_checked" as the next card.**
- I did not touch `docs/HANDOFF.md` or `docs/DECISIONS.md`; the ADR draft is unnumbered.

**Open risks**
- A bare site name no longer names a site: "Trendyol aç" or "YouTube aç" is refused and the task fails after three rounds. This is pinned in a test; if that phrasing matters it needs its own rule.
- An ordinary noun in the locative still counts as site position ("listede" allows liste.com) — navigation only.
- A control rewired into one the rules call free is clicked without the facts comparison.
- A text field whose name contains a marker ("Yanıtla") is now read back before typing — over-asking.
- With two indistinguishable unnamed controls, `loop._rebind` says "sayfa değişmiş"; nothing is clicked, but the sentence is inexact and `loop.py` is outside the area.

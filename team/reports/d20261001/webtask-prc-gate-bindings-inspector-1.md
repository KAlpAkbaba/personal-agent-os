**Inspector report — webtask-prc-gate-bindings @ `999b1b47` (cycle d20261001)**

**Pass 1 — run it** (worktree code confirmed as the import; main venv with `PYTHONPATH`)
- `test_webtask_gate.py` + `test_webtask_acceptance.py`: **269 passed**. Every unit file that references webtask (6 files): **383 passed**.
- The commit touches only the 5 area files and removes no existing test line.
- `ruff check` and `ruff format --check` on `app/webtask` and the two test files: clean.
- `tests/integration/test_webtask_worker.py -m integration` on the dev stack, run alone: **5 passed**. The diff touches no table, migration, store or broker.
- Tree clean after my work; sha256 of `gate.py` `6f70a14a…` and `risk.py` `7e5b5c70…` equal the worker's, before and after.
- My own 12 mutations (different from the worker's), each restored from a backup copy with the hash checked — 11 RED:
  - payment check skips fill only: 1 failed
  - grant ignores `in_form` only: 3; grant ignores `href_host` only: 3
  - one-letter dative without apostrophe: 1
  - unnamed rule on `submits` only: 8
  - click on a toggle treated as a plain click: 2
  - `risk_hint` ignored: 2
  - unnamed plain link not exempt: 2
  - re-ask prefix dropped: 6
  - `sitesi` rule off: 3
  - write ignores `submits`: 1
  - **answers joined with a space instead of `" | "`: 269 passed — survives.** The comment at `gate.py:310` claims a behaviour no test holds.
- Probe through the loop: a confirmed HIGH_IMPACT `select_option` is performed once with `confirmed_by=voice`. The acceptance file only tests the decline path for this.
- NOT_RUN by me: the full API unit suite, the full `quality-gate.ps1`, real Chrome, a real model planner.

**Pass 2 — break it**
1. **Undisclosed regression: the accusative names no site.** `url_is_allowed` returns False for "YouTube'u aç", "Trendyol'u aç ve kulaklık ara", "Google'ı aç", "Instagram'ı aç" and "Hepsiburada'yı aç". All were allowed on main (every-word rule).
   - "YouTube'u aç" is the owner's own recorded production phrasing: 15 occurrences in the repo, e.g. `test_operator_b39.py:1928`.
   - The worker disclosed only the bare name ("YouTube aç"). The accusative is the standard form of "open X", and the navigation is refused until the task fails after three rounds.
   - The worker already added `'a/'e` beyond the card for this same reason; apostrophe-required `'u/'ü/'ı/'i/'yu/'yü/'yı/'yi` is the same class and equally safe.
2. **Same rule, also refused:** "Trendyol'un sitesinde ara" (genitive before `sitesi`), "trendyol web sitesinde ara", "Yemeksepeti'nde / 'nden" (buffer n).
3. **Still allowed, as the worker disclosed (navigation only):** "dünyada", "dünyaya", "masada", "bu sitede" → site.com, "haber sitesi bul" → haber.com, "alışveriş sitesinde" → alisveris.com. "dünya haberlerini bul" allows nothing, as the card requires.
4. **Read-back states a falsehood for a fill.** Typing into a textbox named "Mesaj gönder" gives a HIGH_IMPACT confirm that ends "Bu işlem geri alınamaz". The classification follows the card; the closing sentence is wrong for typing. Lead's call.
5. **Payment handover on fill blocks newsletter-style fields.** A fill into "E-posta ile abone ol" returns `ask_owner(payment)`, so the task cannot type there. Follows the card.
6. **Card deviation, accepted as stricter:** the 'Satın al' select and 'Abone ol' checkbox give `ask_owner(payment)`, not a confirmable HIGH_IMPACT read-back. The read-back is shown on 'Hesabı sil' instead. This is consistent with ADR-0207 decision 4.
7. **An unnamed button outside a form with `submits=False` is clicked freely.** That is every icon-only send button in a div-based single-page app. Within the card's rule; it limits what item (2) of the card protects.
8. No secrets or paths in the code, no contract file touched, nothing new logged. The value is never in the read-back. Rollback is a revert of one commit.
9. `risk.py`'s docstring cites `tests/unit/test_webtask_risk.py`, which does not exist. Not this task's doing; for the lead.

**Evidence classes**
- Write classification, payment for all four actions, unnamed rule, grant bound to wiring, hostile SPA (3 variants), icon-only tasks, sensitive field: PROVEN_AUTOMATED.
- Site-name rule: PROVEN_AUTOMATED for the card's cases, with the regression in finding 1.
- Real browser and real model planner: NOT_RUN. Ceiling on the wire: not in this task (next card, contract v1.8).

**RETURN (**
**1. site position must accept the apostrophe-required accusative — "YouTube'u aç", "Trendyol'u aç", "Google'ı aç", "Hepsiburada'yı aç" — with tests, and "dünyayı"/"dünyı"-style words without an apostrophe must still name nothing;**
**2. decide, test and write in the ADR: genitive + sitesi ("Trendyol'un sitesinde"), "web sitesinde", and buffer-n ("Yemeksepeti'nde") — allowed, or listed as known limits;**
**3. add a test that fails when the answers are joined with a space ("… youtube" then "sitesi …"), or remove the claim at `gate.py:310`;**
**4. add the confirm path for a HIGH_IMPACT select/checkbox to the acceptance file (performed once, `confirmed_by` set)**
**)**

## Şu an üzerinde çalışılan
`webtask-prc-gate-bindings` (PR-C 2/2, inspector return) — area: `services/api/app/webtask/gate.py`, the two test files and the ADR text; machine: owner's dev PC, worktree `.claude/worktrees/team/d20261001/worker-webtask-prc-gate-bindings`. Done; for the lead: set to "Yok" at merge.

**sha:** `ca78b12bd0da27557ed2357e6ee3bf8b7f4fa9c4` on `team/d20261001/worker-webtask-prc-gate-bindings`, pushed, tree clean (on top of `999b1b47`).
**Files changed:** 4, all inside the area — `gate.py`, `test_webtask_gate.py`, `test_webtask_acceptance.py`, `team/plans/webtask-prc-gate-bindings-adr.md`. `risk.py` is untouched.

**The four return items**
1. **Accusative is site position, apostrophe required.** "YouTube'u aç", "Trendyol'u aç ve kulaklık ara", "Google'ı aç", "Instagram’ı aç", "Hepsiburada'yı aç", "Netflix'i aç" and "Kitapyurdu'nu aç" are allowed. "dünyayı gez", "dünyı gez" and "youtubeu aç" name nothing.
2. **Decided, tested and written in the ADR (decision 5): all three are allowed.**
   - Genitive: "Trendyol'un sitesinde", "trendyolun sitesinde", "Yemeksepeti'nin sayfasını", and the genitive alone after an apostrophe ("Trendyol'un indirimlerine bak").
   - "web sitesinde" / "internet sitesini".
   - Buffer n, apostrophe required: "Yemeksepeti'nde / 'nden / 'ndeki / 'ne", "Kitapyurdu'ndan".
   - The rule behind all of it: after an apostrophe every case ending is site position; without one, only the old locative / ablative / dative and `sitesi` / `sayfası`.
3. **The join claim has a test:** `test_two_things_he_said_are_not_read_as_one_phrase`, three cases, across goal→answer and answer→answer. Each case also asserts that the same words said as one phrase do name the site.
4. **Confirm path in the acceptance file:** a confirmed HIGH_IMPACT select ('Hesabı sil') and checkbox ('Aboneliği iptal et', added to `plan_site`) are performed once, with `confirmed_by == "voice"`, verified, risk HIGH_IMPACT. Planned again, each is read back again.

**RED → GREEN (PROVEN_AUTOMATED)**
- Before the change, with the new tests: `20 failed, 282 passed` (18 site-name cases and 2 join cases).
- After: `302 passed` for the two files; `349 passed` for every webtask unit test (`-k webtask`).
- The two confirm-path tests were green on arrival: they add coverage the inspector had only probed by hand. The first join case was also green on arrival.

**Mutation RED** — `gate.py` restored from a backup copy each time; sha256 `2b733271…a5a9` before and after, final run `302 passed`:

| Mutation | Result |
|---|---|
| answers joined with a space | 3 failed |
| accusative off | 6 failed |
| apostrophe made optional | 10 failed |
| genitive after an apostrophe off | 1 failed |
| genitive before `sitesi` off | 2 failed |
| `web` / `internet` off | 3 failed |
| buffer n off | 6 failed |
| grant opens a click only | 3 failed |
| card: payment check for click only | 6 failed |
| card: site rule back to every word | 12 failed |

**Fast checks:** `ruff check` and `ruff format --check` clean on the touched files.

**NOT_RUN:** the full API unit suite, mypy, `quality-gate.ps1`, real Chrome, a real model planner.

**Open risks / for the lead**
- The apostrophe-required forms are refused when speech-to-text drops the apostrophe: "youtubeu aç", "yemeksepetinde pizza ara", "trendyolun indirimleri". Tested as refusals and listed in the ADR. Whether the production STT keeps the apostrophe is unverified.
- The apostrophe rule widens navigation to any proper name in any case: "Ankara'yı" → ankara.com, "Kadir'in" → kadir.com. Navigation only, same class as the "Adana'da" limit already listed, and written in the ADR.
- Only `web` / `internet` may stand before `sitesi`: "trendyol alışveriş sitesinde" names alisveris.com, not trendyol.
- Not changed, left as the lead's call from the inspector's findings:
  - "Bu işlem geri alınamaz" is said for a HIGH_IMPACT fill (4).
  - A fill into "E-posta ile abone ol" is handed over as payment (5).
  - An unnamed button outside a form with `submits=False` is clicked freely (7).
  - `risk.py`'s docstring cites `test_webtask_risk.py`, which does not exist (9).
- At merge, record the next card: "contract v1.8: the ceiling on fill/select/set_checked".

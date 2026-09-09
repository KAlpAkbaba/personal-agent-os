# M27 Creative Tools Operator — milestone report (2026-09-09)

Owner directive: master directive "CLOSE M18.4 AND COMPLETE M19 -> M28" (M27 section: "add creative-tool operation; detect what is actually installed; an application that is not installed is named, never imitated"). Decision records: ADR-0093 (kickoff), ADR-0094 (what the build settled). Spec: `docs/M27_CREATIVE_TOOLS_SPEC.md`. QUALIFICATION Stage 25.

**THE TOOLS WERE MEASURED BEFORE ANYTHING WAS DESIGNED** (`docs/evidence/m27-m28-tool-detection-2026-09-08.json`, 2026-09-08 10:25Z), and measured again on the finished code:

```
paint:       installed=True   C:\Windows\System32\mspaint.exe
photoshop:   installed=False  "registry key present but no Photoshop.exe found"
illustrator: installed=False  "not installed"
figma:       installed=False  "no owner Figma token configured"
```

That second line is worth the space: the provider originally read the bare `HKLM\SOFTWARE\Adobe\Photoshop` key as proof of installation, and Creative Cloud writes that key even when Photoshop is not there. On this machine it reported Photoshop "installed" — the exact "imitated, not named" failure ADR-0093 decision 3 forbids. Caught and fixed before it shipped.

## The decision the milestone turns on

**Paint has no scripting surface, so its document model IS the interface.** The edit is performed on the bitmap with Pillow — deterministic, inspectable, testable — and Paint is opened on the result through M19 so the owner sees it where they asked for it. That is the browser rule (`API > DOM > accessibility > UI Automation > vision > coordinates`) applied to a creative application: **the file is the API**. Pointer drawing is never used, and no script is ever generated from owner or model text.

And one correction made to the spec *before* a line of it was built: the draft promised "SSIM via PIL+numpy". numpy is not a dependency of this repository, and adding one to the Cloud Core image for a single function is not a trade this milestone needed. The comparison measures dimensions, per-tile colour distance and the presence of asked-for shapes and text by masks — and the aggregate it derives is **not called SSIM anywhere**, because that would be the same class of overclaim three M26 reviews existed to catch.

## PROVEN_REAL: the Paint lab (`docs/evidence/m27-paint-lab-2026-09-09.json`)

Spec §6 asked for exactly this, and `scripts/tests/creative-paint-lab.py` runs it against the real service with no fake in the path:

| measured | result |
|---|---|
| output | a real **3,703-byte PNG** |
| reopened by an *independent* reader | 320×240, PNG |
| colours at known points | red inside the rectangle, blue inside the ellipse, white background |
| text | **969 ink pixels** where planned |
| comparison | agrees, **4 checks** |
| a deliberate wrong colour | **caught** — *"no ink within tolerance of (20, 200, 60, 255) near the planned position"*, wire defect `missing_region` |
| the corrected round | agrees again |

## NOT_YET_PROVEN, measured rather than assumed

The spec also asks for the result to be **"opened in Paint through M19, the window observed"**. I read the device protocol instead of guessing: `DEVICE_PROTOCOL.md` §6 puts `desktop.open_application`'s allowlist at **`notepad`/`calc`**, and §6a puts `desktop.open_artifact`'s extension allowlist at **`.pdf .docx .html .htm .txt .md` — no image format at all**. Opening a PNG in `mspaint.exe` needs a device-side change, which needs the elevated agent update (**owner item 28**). Spec §6 anticipated this in its own words — *"item 28 permitting"* — so it is a named gate, not a discovered gap. The same is true of §4's `creative.export_check`.

## TESTED

`services/api` **7547 passed / 2 skipped**, run exactly as CI runs it. `services/browser` 405. `apps/web` **77 files / 1491 tests**, `tsc` clean, `oxlint` 0 errors. Voice corpus **1719 cases across 20 categories** (`creative` 83). New Python suites: spec 23, compare 14, execute 11, providers 11, service 14, routes 7, intents 20, tools 8, review findings 17, activity vocabulary 7.

## SECURITY — one review, three findings, all closed with regressions watched failing first

- **HIGH** — `_apply_background_remove`'s threshold branch was an unbudgeted per-pixel Python loop, and threshold is the *only* method the shipped voice tools request. Two ordinary turns reach it: an 8192×8192 canvas, then *"arka planını kaldır"* — roughly **eighty CPU-seconds** of synchronous work on the request thread. `spec.py`'s own comment claimed a runaway request is refused "well before any pixel is ever touched"; `MAX_DIMENSION` bounded the canvas, never the cost of walking it. Vectorised with Pillow's own arithmetic: **0.094 s at 2048×2048 against ~5 s for the loop**, with the metric proven unchanged pixel for pixel including at the tolerance boundary.
- **MEDIUM** — the same shape in the comparison's region scan, once per drawn shape or text; and `_open_source` bounded the *encoded* bytes but never the *decoded* size, so a few-kilobyte PNG could decode far past the "≤ 8192×8192" spec §7 promises.
- **LOW, and the one that mattered most** — `creative_redraw` named a source and never handed over its bytes, so *"Bu resmi Paint'te yeniden çiz"* — the sentence this capability is named for — always fell through to a blank canvas and reported an empty-output mismatch.

## BUGS FOUND AND FIXED beyond the review

1. **The fourth intent collision, caught by another milestone's negative case.** `CREATIVE_EXPORT` matched on "dışa" + an export verb, which is every "… dışa aktar." sentence in Turkish — including M25's own **"Sahneyi dışa aktar."**, whose expected answer is *none*, because the 3D family has no export operation and spec §7 says an operation outside the vocabulary is never guessed. M27 would have told the owner Paint was exporting a Blender scene. Fixed by **narrowing, not reordering** — a reordering only moves the collision to whichever family loses the race. It was found by M25's corpus case, not by any creative test, which is the whole argument for a shared corpus every milestone keeps running.
2. **A wall-clock time bomb that detonated looking like a flake.** M18.3's greeting-token test called `store.take(token)` with no clock, comparing an entry stamped at the fixture's `04:01Z` against the real time, against a five-minute TTL — so it could only pass **between 04:00 and 04:06 UTC on one particular day**. It passed for weeks, passed locally, and failed in CI at `04:31Z`. The product was right; the test was reading a different clock from the one it set up.
3. **A registry key mistaken for an installation** (above), and a `turkish_casefold` mismatch that meant "Illustrator'da" never routed — the proper noun is spelled with an ordinary Latin `I`, which casefolds to dotless `ı`.
4. **The contract guard's own blind spot.** `FAMILIES` was hand-maintained, so a family present on ONE side was invisible: the web landed `CREATIVE_RUN_STATES` and every test in that file still passed. It now discovers every run vocabulary in the TypeScript and demands a row for each. That surfaced **two more — M21's mail-draft and calendar-proposal vocabularies, never compared with anything since M21 shipped**. They agree today; nothing was holding them there. Seven families watched now, not four.
5. **A channel nobody published.** ADR-0094 deferred `creative.activity` while the halves were separate branches — right then, wrong once merged: agreeing words that nobody ever says make every posture the panel can draw unreachable, and *no vocabulary guard notices*, because both lists are still identical. The loop publishes as it goes now, and a test asserts the channel is published at all.
6. **A `.panel li` layout defect on already-released panels.** Every Cockpit row is a stack of sibling spans in a plain block, so the released M25 panel really rendered `"doğrulandı (3 nesne)Kure, Kamera, Gunes"` and `"yapılamadıNo valid Unity Editor license found."` — a refusal run into a vendor sentence, read as one garbled claim. It affects M23, M24, M25 and M26 alike. Found while building M27's panel beside them.

## THREE MISTAKES OF MY OWN, named because they are the same ones I keep finding

1. **A budget written from expectation.** My first test for the HIGH used a 5-second threshold, chosen before I timed anything. Under mutation the Python loop slipped *under* it and the test passed. I measured both paths and set the bound at 1.5 s — sixteen times above the vectorised path, three times below the loop.
2. **A vacuous cross-file check, inside the file written to prevent vacuous cross-file checks.** My defect-vocabulary test resolved the web contract with `parents[3]`, landed in `services/`, and **skipped**. It walks up now, and a missing file fails.
3. **A weaker configuration than the gate.** I ran the suite with `-p no:randomly` throughout while CI runs with random ordering — a structural difference, not a statistical one: no number of runs in that configuration could surface an order-dependent failure. The number cited in this report comes from a run made exactly as CI makes it.

## The subagent handover, recorded because it will recur

The Cloud Core track was delegated. That agent ended its turn **three times** saying it was "waiting on background jobs", with **zero commits** in its worktree despite ~4,400 lines of working code. This project's own memory already records the pattern, so the work was checked rather than trusted, every ADR-0093 constraint verified by hand, and then committed by the integrator. The work itself was good; the handover was not, and the difference is only visible if you look.

## OWNER ACTIONS

| item | what | time |
|---|---|---|
| **28** | The elevated agent update. Until it lands, opening the result in Paint through M19 stays `NOT_YET_PROVEN`, and so does the device-side `creative.export_check` | 2 min (one UAC) |
| — | Photoshop / Illustrator: install through your own Creative Cloud licence whenever you like. The providers, their capability lists and their labs already exist and run the real application the day it does — no code change | — |
| — | Figma: a token, if you want that path. Until then it says so | — |

## NOT PROVEN, stated rather than hidden

- The M19 "opened in Paint" round trip and the device-side `creative.export_check` (owner item 28).
- Photoshop / Illustrator / Figma against the real applications: `PROVEN_PROXY` by construction, since none is installed or configured here.
- The Cloud Core half on production: see Stage 25 row 25.12 for what the release actually proved.

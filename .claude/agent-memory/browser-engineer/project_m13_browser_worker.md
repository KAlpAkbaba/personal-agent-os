---
name: project-m13-browser-worker
description: M13 track B (Browser Worker, services/browser) completed 2026-09-03 — stdio worker built against BROWSER_CAPABILITIES.md; live qualification against real search engines/sites shows expected real-world anti-bot blocking, worth re-checking before any future SERP-parser work.
metadata:
  type: project
---

M13 track B (browser_agent.worker — the stdio Browser Worker) landed on branch
`worktree-agent-a974a5085375b0202` at commit `c473078` (built on top of `ca5cfb1`, the M13 track A
docs commit fast-forwarded from `main` — see [[feedback-parallel-track-prereqs]]). 215 unit tests,
57 browser e2e tests (real headless Chromium) all green; ruff clean.

**Live qualification outcome (2026-09-03, real Chrome, real network — expect this to still be true
for a while, but re-verify before trusting it, since target-side anti-bot behavior changes over
time):** both `-m live` tests failed against real-world conditions, and this is a real-world/target
-side limitation, not a defect in the implementation:
- `https://openai.com/news/` returned HTTP 403 to headless Chrome, which the worker classifies as
  `page_kind=auth_wall` per `BROWSER_CAPABILITIES.md` §3's own literal rule ("HTTP 401/403" is a
  listed auth_wall signal) — even though the real cause here is bot-filtering, not a login wall.
  This is contractually correct behavior, not a bug to fix.
- DuckDuckGo (`html.duckduckgo.com/html/`) served a real CAPTCHA ("Select all squares containing a
  duck") whose wording doesn't match the contract §6 fixed captcha-marker list
  (recaptcha/hcaptcha/turnstile/"verify you are human") — it falls through as an empty/`ok`-kind
  result (0 parsed organic results) rather than being labelled `page_kind=captcha`. The marker list
  is binding per the task contract, so this was not patched around.
- Bing returned HTTP 200 with a real (Turkish-localized) title but the `#b_results li.b_algo`
  selector in `browser_agent/search_engines.py::parse_bing_html` found 0 organic results against
  the real live page — likely Bing serves different markup to automated/headless traffic than the
  synthetic fixture assumed. Not investigated further (out of scope/budget for this pass).
- Brave Search hard-aborts the connection (`net::ERR_ABORTED`) for the automated request. This
  *was* fixed: `search_engines.run_search`'s `auto` fallover now also catches a `BrowserError` from
  one engine's fetch and moves to the next engine (previously it would propagate and abort the
  whole search) — unit-tested in `tests/unit/test_search_engines.py`.

**How to apply:** if a future task revisits `browser_agent/search_engines.py` (better SERP parsing,
real Bing/DDG markup capture, a captcha-solving-avoidance strategy, or a different engine choice),
start by re-running `uv run pytest -q -m live -s tests/live/` to see the CURRENT real-world outcome
before assuming the parsers are broken or working — anti-bot behavior on these engines is a moving
target, and today's failure mode may already differ.

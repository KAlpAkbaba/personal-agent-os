---
name: project-owner-location-weather-briefing-status
description: Owner Location Context / Live Weather / Morning Briefing (docs/DECISIONS.md ADR-0091) completed 2026-09-09 on branch worktree-agent-a6616e449e5a5cf7e; not merged/pushed; live weather NOT_YET_PROVEN against the real Open-Meteo API (no outbound network in this sandbox)
metadata:
  type: project
---

Completed 2026-09-09 in
`E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\agent-a6616e449e5a5cf7e`,
branch `worktree-agent-a6616e449e5a5cf7e`. Full detail in `docs/DECISIONS.md` ADR-0091 —
this note is the quick-recall version.

**What was built:** `app/location/` (LocationContextRow + the six-tier resolution order in
`LocationService.resolve`), `app/weather/` (a real, genuinely keyless `OpenMeteoProvider` —
no signup, no key, verified against the vendor's own docs), `app/briefing/` (the morning
briefing assembled from real sources only, plus two narrower single-topic tools). Seven new
voice intents in `app/voice/intents.py`, seven new voice tools, a `weather` corpus category
(107 cases) in `tests/voice_corpus/corpus.py`, migration `0034_location_weather_briefing`.

**Real bugs found and fixed, each with a regression test** (see ADR-0091 for the full
writeup, [[feedback_intents_prefix_collision_risk]] for the general lesson):
1. A raw `datetime` reaching a receipt's JSON ledger field also exposed that
   `app.actions.receipt.record_receipt` didn't roll back the session on a failed ledger
   write — fixed both, independently.
2. "konu" (mail subject) vs "konum" (location) prefix collision stole the location-default
   utterance into an existing mail intent.
3. A newly broadened system-status phrase collided with a pre-existing EXPLAIN/world_state
   contract case.

**Verified:** full `tests/unit/test_owner_utterance_corpus.py` (1506 cases, ALL categories)
green after the fix — no cross-category regression. `ruff check`/`ruff format --check`
clean on every touched file (31 files). `create_app()` builds and wires all three services
through the real application object (`tests/unit/test_weather_briefing_wiring.py`).

**Honest gaps, not hidden:** `default_weather_location` is UNSET (owner action item 34,
`SET_DEFAULT_WEATHER_LOCATION`, in `docs/OWNER_ACTIONS.md`) — nothing invents one. No device
today can supply `windows_location`/`mobile_gps` (measured: the deployed Windows agent's 29
capabilities have nothing location-related — the provider seam is real, just unwritten).
Coarse IP geolocation is unconfigured (no vendor hardcoded, on purpose — see ADR-0091
decision 3). No news resolver exists in this repo at all (another track's scope) so the
briefing's news section is an honest one-sentence absence, never a fabrication. **Live
weather itself is real code (`OpenMeteoProvider`, real `httpx` calls, real WMO condition
mapping) but is `NOT_YET_PROVEN` against the actual live API** — this sandbox has no
outbound network, so it is unit-tested against `httpx.MockTransport` only. The first real
run against the live Open-Meteo API happens on a machine/deployment with real internet
access; nothing in the code needs to change for that, it just needs to actually happen once
before calling it `PROVEN_REAL`.

Not merged, not pushed, not released. `state/BUILD_STATE.json` key
`owner_location_weather_briefing` carries the same summary in the durable build record.

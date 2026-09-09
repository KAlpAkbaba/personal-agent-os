---
name: m26-location-weather-briefing-review
description: Owner Location Context / Live Weather / Morning Briefing pre-merge review (2026-09-09). HIGH (live PoC) - WeatherService.current committed its weather_query_evidence row unguarded; a failed commit leaves the session needing a rollback, the realtime tool dispatcher's generic handler swallows it WITHOUT one, and the dispatcher's own unconditional commit then raises PendingRollbackError, failing the entire tool-call turn rather than losing one receipt - the same session-poisoning bug this branch had already fixed once in record_receipt, one frame away. Trigger reachable on EVERY successful answer - Open-Meteo's geocoded name/admin1 are unbounded and land in summary String(500), a width Postgres enforces and SQLite does not, so the sandbox could never show it. MEDIUM - the router's place extractor is a closed 13-city gazetteer, so every other place name fell through to the MODEL's own tool argument unchecked against the transcript, making location.set_default a durable mutation an injected instruction could aim. MEDIUM - location_context insert-only with no retention, expires_at written but never read; becomes the forbidden raw location-history archive the day a device can write. LOW - a 200 that is not JSON raises JSONDecodeError, a ValueError not an httpx.HTTPError, escaping every typed handler. Verified sound - full resolution order incl. IP-coarse never overriding a trusted tier (live PoC, 5 tier combinations), no seeded default, IP-geo URL empty with no runtime setter (no SSRF), every outbound call timeout-bounded, both documented intent collisions still closed, overnight summary never over-claims, migration matches the ORM exactly.
metadata:
  type: project
---

Reviewed `worktree-agent-a6616e449e5a5cf7e` before it merged: `app/location/*`,
`app/weather/*`, `app/briefing/*`, `app/actions/receipt.py`, the voice router's seven new
intents and their tools, and migration `0034_location_weather_briefing`. 113 new unit tests
and 42 weather-corpus cases were executed live; no outbound network call was made.

The reviewer has no Write tool, so the findings were transcribed here by the integrator,
with the fixes recorded beside them. Full text in ADR-0091 addendum 1.

**The HIGH is a class, not a site.** The same branch had already found and fixed
session-poisoning in `record_receipt` — and then reintroduced it one call frame away, in
code written in the same sitting. That is the argument for fixing the class: when a defect
is "an unguarded commit on a best-effort write", grep for the shape, do not patch the
instance. Regression: `tests/unit/test_weather_location_review_findings.py`.

**The MEDIUM about the gazetteer is the one to remember.** "Owner's words win over the
model's argument" was implemented for thirteen cities and left as a fallback to the model's
free text for everything else — so the rule held for the examples in the spec and failed for
the general case. A closed vocabulary is a canonicaliser, never a substitute for
corroboration: the tool now checks the argument against the owner's own transcript for that
turn and asks rather than writing when it cannot.

Related: [[m26-executive-autonomy-review]], [[m26-latest-news-mode-review]] — three
consecutive pre-gate reviews, three real HIGHs, none of which any suite was catching.

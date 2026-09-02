---
name: project-pagentos-m12-m13
description: M12 tracks A+E (realtime voice) and M13 prep (browser-research) verification outcome at 69ae5b3 — one real defect found in audit-scrub key matching
metadata:
  type: project
---

Verified M12 tracks A+E (docs/M12_REALTIME_VOICE_SPEC.md, ADR-0036) and M13 prep
(ADR-0035) at commit 69ae5b3 (merge(m12-a+e)). 10 of 11 claims PROVEN with
independent adversarial/mutation probes (not just "tests pass"); one real defect
found and reported, not silently accepted.

**Result: 1533 unit tests pass, 2 skipped, 0 failed** (`services/api`, `uv run
pytest tests/unit`). Integration test `tests/integration/test_voice_realtime_sessions.py`
passes against real PostgreSQL. `alembic current` was already at `0011_realtime_voice
(head)`; downgrade -1 -> upgrade head round-tripped cleanly and was restored to head
before finishing (per task instruction).

**Defect found (claim e, M12 audit scrub):** `app/voice/realtime_sessions/service.py`
`_FORBIDDEN_KEY_PARTS` and `app/voice/realtime_sessions/routes.py`
`_FORBIDDEN_PAYLOAD_KEY_PARTS` both use substring matching against `"api_key"`
(with underscore). A client-controlled key with a different separator/case —
`"apiKey"`, `"api-key"`, `"APIKEY"` — is NOT caught by either the request-level
validator (`_no_forbidden_keys` in routes.py) or the audit scrubber
(`scrub_metadata` in service.py). Reproduced end-to-end: a `POST
.../sessions/{id}/events` body with `kind: "error", payload: {"apiKey": "..."}`
sails through Pydantic validation unrejected and would land verbatim in an
`audit_events` row. Minimal repro is a 15-line Python script constructing
`ClientEvent(kind='error', t_ms=100, payload={'apiKey': '...'})` and calling
`scrub_metadata` on the resulting event dict — both keep the key. Fix would be
matching on a normalized (de-separated) key, not a raw substring list.

**Mutation-probe methodology that worked well here** (reuse for future gate
verification, see [[project_pagentos_m9]] for the general principle): rather than
trusting "the tests pass," for each claim about a gate/guard *actually catching a
regression*, I built the regression in-memory (never edited repo files, since
test-engineer must not touch product source) and re-ran the real check function:
- route-table sweep (claim c): loaded the real FastAPI app, stripped
  `require_owner_session` from each realtime route's `dependant.dependencies` at
  runtime, re-ran the sweep's own open-endpoint computation, confirmed it flags
  all 9 routes and would fail `EXPECTED_OPEN` comparison.
- benchmark harness (claim b): ran `run_simulator_benchmark` with a
  deliberately-slow/gappy `SimulatorTimings` distinct from the shipped test
  fixtures, confirmed `all_targets_met() is False`.
- security_scope_error guard (claim i): monkeypatched
  `require_research_authorization` to a no-op, confirmed the exact
  `pytest.raises(BrowserError)` pattern used in the real test would then see
  "did not raise".
This in-memory-mutation technique is the right way to satisfy "would the gate
catch a regression" claims without an Edit/Write tool and without touching
product source — worth reusing whenever a milestone claim is phrased as
"X would fail if Y were removed/broken."

**Turkish console gotcha recurs** (see [[project_pagentos_m3]]): plain
`uv run python -c "..."` on this Windows/PowerShell-launched Bash prints via
cp1252 and throws `UnicodeEncodeError` on Turkish text unless
`PYTHONUTF8=1 PYTHONIOENCODING=utf-8` is set first.

**Everything else PROVEN**: provider selection (a) matches spec §2 exactly
(hard requirement, semantic-EOT preference, WebRTC preference, preference-list,
name tie-break — all independently probed with synthetic capability sets);
tool-call idempotency + 409 on stale leg after attach (d) — direct route-level
test `test_attach_moves_the_leg_replays_sideband_and_closes_the_old_leg` asserts
`stale.status_code == 409` verbatim; Turkish intent resolver (f) — every spec
§5 intent resolves correctly and "Durum raporunu oku" does not trigger stop
(token-exact matching, not substring) across every `NarrationState`/
`RealtimeState`; Turkish relative-date parser (h) — "son üç gün"/"son 24
saat"/"son bir hafta"/"bugün"/"dün" all parse to the documented windows,
diacritic-tolerant, unrecognized text returns `None` (forces the documented
3-day default rather than guessing); labelled-statement provenance + executive
structure (j) — `DeterministicSynthesisProvider` never emits `source_fact`
without a citation to a real evidence URL, `uncertainty` is the only
uncitable label, and the four-section Executive Summary -> Why it matters ->
Recommended action -> Details structure round-trips through
`render_executive_markdown`; M3 provider/compose (k) — `git log` shows
`app/research/provider.py`/`compose.py` and their tests were touched only in
the original M3 commit `cadcf8c`, never since (14/14 tests still pass).

How to apply: when re-verifying downstream M12 work (tracks B/C/D, real OpenAI
Realtime adapter) or M13's real browser wiring, check whether the `apiKey`/
`api-key` audit-scrub gap above was fixed before trusting "credentials never
reach audit rows" claims again.

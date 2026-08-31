# M5 Security Review — 2026-08-31

Independent review of the M5 memory subsystem. Companion verification: independent test-engineer reproduced all M5 gates + behavioral matrix PASS, including adversarial checks with direct-SQL forget verification. **No Critical findings; 3 live High + 1 forward-looking High, all addressed same-day.**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High (live) | `forget_memory` had no owner-authority check — a POLICY/SYSTEM actor could hard-delete an explicit/pinned memory (the one irreversible operation), violating the "explicit rows only mutable by owner" invariant exactly where it matters most for the Evolution Engine. | **Fixed**: `_require_owner_for_explicit` (now also covering `pinned`) enforced on forget AND pin; regression test proves POLICY/SYSTEM forget raises `explicit_protected` while owner forget still works. |
| 2 | High (live) | Secrets guard was bypassed by `PATCH /{id}` (edit) and `POST /{id}/supersede`, which never pass through `policy.decide()` — a token could be stored and re-embedded via those paths. | **Fixed**: `_reject_if_secret` helper (refusal + `refused_secret` audit with pattern name only) called from both `edit_memory` and `supersede_memory`; regression test asserts refusal, unchanged memory, and content-free audit. |
| 3 | High (live) | `Observation.source` (provenance payload) was never scanned — a secret nested in `source` persisted unredacted and was retrievable via inspection. | **Fixed**: `policy.decide()` now scans `repr(observation.source)` alongside text/value; regression test. |
| 4 | High (forward-looking) | A trigger phrase ("remember", "always use", "bundan sonra") inside arbitrary text granted full OWNER authority (`explicit=True`, confidence 1.0, durable) with no provenance check — the write-policy analogue of SECURITY_MODEL §6: once ingestion pipelines (browser/research/document, M6+) feed text into `/observe`, a webpage could mint an explicit owner memory in one shot. | **Fixed by design change**: OWNER/durable now requires the caller-asserted `explicit` flag from a trusted owner surface; a phrase match without the flag is only a strong CANDIDATE signal (confidence ≤ 0.4, POLICY actor). Behavioral tests updated + regression tests. The remaining end-to-end piece (authenticating "the owner surface" itself) is the standing API-auth hard gate shared with M4 finding #1. |
| 5 | Medium | The memory REST surface's hardcoded owner-authority mutations rest on the same missing per-request auth layer as the M4 speaker-verification gate, but weren't cross-referenced in the tracked hard-gate list. | **Tracked**: BUILD_STATE hard-gate entry extended to name the memory REST surface (esp. forget) alongside speaker verification. |
| 6 | Low | Semantic/keyed corroboration mutated an explicit memory's evidence/confidence/last_confirmed via a POLICY actor. | **Fixed**: POLICY-actor corroboration on explicit rows is a no-op on the row; regression test. |
| 7 | Low | No depth/size bound on `value`/`source`/`attrs` JSON payloads (pre-existing repo-wide gap: no global body-size middleware). | **Tracked repo-wide**: to be fixed once with a shared body-size middleware (also covers voice/artifacts); memory `find_secret` patterns verified linear (no ReDoS), so cost is bounded by payload size only. |
| 8 | Low | CORS `allow_methods` lacked PATCH/PUT/DELETE (pre-existing since M4), blocking a future owner web UI from correcting/forgetting memory per constitution §9. | **Fixed**: methods extended. |

Also fixed from verification: `RetentionClass.SHORT` was dead code — now assigned to inferred episodic candidates (events decay naturally) with a sweeper regression test for the short rung.

## Explicit verdicts (post-fix)

- **Forget-completeness: PASS** — hard-delete cascades verified in code, FK schema, integration test and the verifier's direct psql check; audit is content-free; and the authority gate now covers the delete path.
- **Explicit-memory protection: PASS at the service seam** — edit/supersede/pin/forget/corroborate all owner-gated; phrase-matching can no longer mint explicit authority. The seam the Evolution Engine (M6/M7) must build on is in place; the end-to-end guarantee still depends on the tracked API-auth hard gate.

## Clean areas (verified)

Hard-delete cascade + content-free audit (tested to the SQL level); no code path logs memory text/value/source; secret patterns linear (no ReDoS); SQL/vector queries fully parameterized (ORM operators, no interpolation); retrieval limits capped; migration 0005 reversible with correct CHECK constraints and hnsw index; strict project isolation (eval contamination 0.0); eval corpus fully fictional; quality-gate scan patterns untouched by the fixture fix (zero diff); no new tracked secrets; no non-loopback binds.

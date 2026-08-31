# M6 Security Review — 2026-09-01

Independent review of the M6 self-healing additions (recovery supervisor, self-healing pipeline, staging demo target). Companion verification: independent test-engineer reproduced every M6 acceptance bullet PASS, including hand-driving the supervisor CLI and live incident ingest.

**One Critical finding, fixed same-day. Recovery-root independence and the ADR-0023 memory boundary both passed cleanly.**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | **Critical** | `DeterministicCodingBackend._rewrite_mapping` spliced the incident-supplied `evidence.expected` value into a generated Python string literal **without escaping** (the sibling regression-test generator three lines away correctly used `!r`). Combined with the unauthenticated `POST /v1/selfhealing/incidents/ingest` (which could also overwrite an in-repair incident's evidence) and `POST /v1/selfhealing/pipeline/run`, this formed a path from network-reachable, unauthenticated input to arbitrary code in a generated candidate that the pipeline would then promote to the active release — exactly the "self-update escapes its sandbox" failure the constitution's evolution boundary exists to prevent. | **Fixed**: `expected`/`actual` are validated against a strict error-class token shape (`^[a-z][a-z0-9_]{0,63}$`) at the single `analyze_issue` choke point AND re-validated at the splice point (defense in depth), so no value that could terminate a literal or inject a statement can reach generated source. Regression tests cover hostile payloads (quote-escape, `__import__`, newline, backslash, spaces, empty) and assert the generated candidate always parses as valid Python with no smuggled statements. |
| 2 | Medium | A duplicate incident report could overwrite the evidence of an incident already being repaired, swapping the counterexample under a running pipeline. | **Fixed**: evidence is only refreshed while the incident is still `open`/`recovered`; incidents in repair or repaired keep the evidence the pipeline derived from. |
| 3 | Low | No size bound on the incident report envelope (table bloat / DoS). | **Fixed**: 64 KiB cap validated at the request schema (422), with a regression test; complements the existing per-field limits in `monitoring.draft_from_supervisor_report`. |
| 4 | Low | The unauthenticated ingest and `pipeline/run` endpoints inherit the standing repo-wide no-auth posture. | **Tracked** under the existing API-auth hard gate in BUILD_STATE (same class as M4/M5 entries): the self-healing endpoints must be behind owner authentication before the system runs outside a trusted loopback/private network, and before a non-deterministic coding backend is enabled. |

## Explicit verdicts (post-fix)

- **Sandbox escape / unauthorized promote: CLOSED for the shipped pipeline.** Generated code can no longer carry attacker-controlled text; the patch path writes only into the isolated candidate workspace; promotion requires the independent reviewer gate (regression fail-on-broken AND pass-on-candidate) plus staging health — a backend cannot self-approve (LyingBackend test).
- **Recovery-root independence: PASS.** The supervisor is a separate stdlib-only project (`dependencies = []`, no app/SQLAlchemy imports), runs as its own process, keeps its own pointer files, and rolls back without the main app; nothing in `app/selfhealing` can modify supervisor code or pointers outside the configured workspace root.
- **ADR-0023 memory boundary: PASS** — enforced by a guard test, not convention: `app/selfhealing` imports no owner-actor memory mutation path.

## Clean areas (verified)

Workspace path confinement (allowed_roots) on pipeline and routes; release immutability (activate refuses to overwrite an existing release; rollback deletes nothing; promote only accepts the health-verified active release); recovery-before-reporting ordering is tested; incident reports carry structured fingerprint material only (no model prose, no secrets); supervisor process handling kills only its own supervised tree; loopback-only health/selftest polling; the staging demo target loads release code in its own process (never the API), with selftest expectations held by the monitor so a broken release cannot redefine success; `ClaudeCodingBackend` inert without configuration and never exercised by tests; no new tracked secrets; no non-loopback binds; the gate's new supervisor step is safe.

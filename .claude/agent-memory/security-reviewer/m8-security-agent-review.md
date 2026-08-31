---
name: m8-security-agent-review
description: Summary + pointer to full findings for the M8 Authorized Security Agent review (registry, scope guard, evolution-provider wiring, collector confinement) (2026-09-01)
metadata:
  type: project
---

M8 (Authorized Asset Registry + defensive security agent, `services/api/app/security/**`) reviewed at HEAD 2026-09-01. Scope guard (`scope.py`) is exemplary: fail-safe classification, real `ipaddress` membership, no DNS resolution, ambiguity refuses. Registry integrity (immutable kind/locator, append-only events, expiry re-derived per-decision) is sound.

Two load-bearing findings:

- **High: `RegistryAuthorizationProvider` (app/security/provider.py) is correctly built and unit-tested but never wired into `EvolutionRuntime`/`IndependentSkillReviewer`.** `main.py` constructs `EvolutionRuntime(settings)` and `SecurityRuntime(settings)` as fully independent objects; `EvolutionRuntime.authorization` (evolution/runtime.py ~161-180) still only returns `NullAuthorizationProvider` or an env-var-driven `StaticAuthorizationProvider`, never the registry-backed one. So the ADR-0026 / M7-review claim that M8 "closes the M7 gap" is **false of the running system** — deny-by-default still holds (safe) but the only functioning grant path bypasses the registry entirely via `PAGENTOS_EVOLUTION_AUTHORIZATIONS`, reintroducing the exact "asserted, unauditable authorization" problem the registry was built to fix. No test exercises the cross-runtime wiring. **Recheck this specifically whenever evolution or security runtime wiring changes** — this is the kind of gap that "both modules have 100% passing unit tests" completely hides.

- **Medium: collector root confinement (`checks.py::collect_files`) can be walked outside the authorized root via a Windows NTFS junction.** Reproduced directly: `Path.is_symlink()` returns `False` for both a junction directory and files reached through it (junctions use `IO_REPARSE_TAG_MOUNT_POINT`, not `IO_REPARSE_TAG_SYMLINK`), while `Path.resolve()` correctly reveals the true out-of-root location. `remediation.py`'s equivalent path-containment check does this correctly (checks `root not in path.resolve().parents`); `checks.py` does not. See `[[junction-escape-recurring-pattern]]` — this is the **second** module in this codebase with the same class of bug (M3 artifact rendering had one too).

Also flagged: no upper-bound guard on network-asset CIDR breadth at enrollment (`0.0.0.0/0` enrolls without complaint — acceptable today since only filesystem-touching testing classes are implemented, but becomes a real third-party-testing risk once `vulnerability_scan`/`controlled_validation` land); BUILD_STATE.json's standing no-auth hard gate (line 15, items a-d) has not been extended to cover the M8 security endpoints even though enrollment/remediation are at least as sensitive as the items already listed.

Clean: secret redaction (shared pattern list with app/memory/policy.py, every write path redacted + fail-closed `assert_redacted`, artifact publish has a final `contains_secret` gate); migration 0008 byte-identical to ORM models; secret-scan (`scripts/quality-gate.ps1`) unweakened — fixture credentials are shaped to miss the scan pattern, not exempted from it; all service binds are 127.0.0.1, CORS origins explicit.

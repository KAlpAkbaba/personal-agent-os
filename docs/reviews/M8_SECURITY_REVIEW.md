# M8 Security Review — 2026-09-01

Independent review of the Authorized Security Agent — the subsystem that decides what the agent may do to real infrastructure, and (since ADR-0026) the authorization source for evolution permission grants. Two authorities in one table, reviewed accordingly. Companion: independent acceptance verification, which drove the scope guard with its own adversarial fixtures.

**No Critical. One High and one Medium, both fixed and committed (`8b7812a`), plus a breadth guard added.**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High | `RegistryAuthorizationProvider` was correctly implemented and unit-tested, but **never wired into `EvolutionRuntime`** — the live grant path still resolved to the deny-everything default, so ADR-0026's claim that this registry closes M7's High finding was **false of the running system**. Safe (deny-by-default), but "built and tested in isolation" is not "in force". | **Fixed**: `EvolutionRuntime.authorization` now resolves through the registry by default (env override retained for environments with no registry; deny-by-default when neither exists), and the reviewer the pipeline constructs carries it. The regression test asserts the **wiring**, not the class's existence — the failure mode here was a component that looked correct in isolation while changing nothing in production. |
| 2 | Medium | `checks.py::collect_files` decided containment with `is_symlink()` on the leaf entry. A Windows NTFS junction does not set that flag, so a junction planted inside an authorized config root walked the collector outside it — reproduced live by the reviewer. **Third occurrence of this bug class** (M3 Windows agent `ArtifactOpener`, M6 ancestor walk, now here). | **Fixed**: each candidate is resolved and required to remain under the resolved root, matching what `remediation.py` already did correctly. Regression test plants a real `mklink /J` junction. Recorded as a recurring cross-milestone pattern: *`is_symlink()` is not a containment check; resolve, then verify containment.* |
| 3 | Medium | No breadth guard at enrollment: `0.0.0.0/0` enrolled silently, making the whole internet in-scope from one typo. | **Fixed**: network locators broader than /16 (IPv4) or /48 (IPv6), and multicast/reserved/unspecified blocks, are refused. A single-owner system may legitimately authorize wide ranges — but as several deliberate, auditable entries, not one silent wildcard. Owner authority is preserved; the foot-gun is not. |
| 4 | Low | The unauthenticated loopback posture now also covers asset **enrollment**, i.e. defining what the agent is allowed to touch. | **Tracked** on the standing API-auth hard gate in BUILD_STATE, extended to the security endpoints. |

## Explicit verdicts (post-fix)

- **(a) Can the scope guard be defeated? No.** Classification is total and fail-safe; membership is real `ipaddress` arithmetic; there is no DNS resolution anywhere (resolving would hand scope authority to whoever controls DNS); ambiguity refuses rather than picking. Independently confirmed against hex/decimal/octal IP encodings, IPv4-mapped IPv6, zone ids, embedded-IP hostnames, and domain-suffix lookalikes.
- **(b) Can scope be widened without audit? No.** `kind`/`locator` are immutable after enrollment (re-pointing requires revoke + re-enroll, two auditable events); every mutation and every refusal writes an append-only authorization event; expiry is re-derived on each decision, not only by a sweeper.
- **(c) Does the evolution provider close M7's High finding? Yes — now that it is wired.** It was not before finding #1 was fixed.
- **(d) Is redaction sufficient? Yes for the acceptance bullet**, verified against independently planted credentials across findings, result payloads, artifact canonical body, both renders, and the audit trail; the finding still names the exposure. `pem_body` (bare long base64/hex) is deliberately excluded because it matched content hashes and the system's own fingerprints — a detector that matches everything protects nothing.

## Clean areas (verified)

Enrollment requires authorization evidence; evidence and constraints are redacted before storage so the registry cannot become a credential store; assessment checks are read-only with file/size caps and roots taken from the asset's own constraints (an authorized asset does not authorize reading arbitrary paths); remediation re-enters the scope guard carrying the fix's own disruption, backs up before applying, re-derives the patch from disk and reports `already_resolved` rather than clobbering; artifacts go through the existing M3 service with a secret-free precondition; the repo secret scan was not weakened when the fixture's secret-shaped values were reworked.

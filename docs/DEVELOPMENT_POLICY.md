# Development Policy — closed-loop autonomous engineering

Status: permanent (owner directive, 2026-09-07). Applies to every feature, module, request,
bug fix, optimisation, integration and self-evolution candidate from this date on. The
operating mode in `CLAUDE.md` is the short form; this file is the contract.

## 1. Every request is a tracked engineering object

Objective · acceptance criteria · affected subsystems · dependencies · risk level ·
required permissions · expected tests · deployment impact · rollback strategy ·
qualification state. Recorded in the milestone spec / ADR and in `state/BUILD_STATE.json`.

## 2. Lifecycle — no step skipped because the code compiles

```
UNDERSTAND -> DESIGN -> IMPLEMENT -> TEST -> REVIEW -> FIX -> RETEST -> INTEGRATE
-> QUALIFY -> DEPLOY if authorised -> VERIFY RUNTIME -> CLOSE
```

## 3. Bugs found during the work are part of the same task

Never stop to wait for "fix the bug". Capture evidence → reproduce → classify → root cause
→ minimal correct fix → regression test → rerun the affected tests → rerun the
cross-system gates. Continue until every defect the work introduced or exposed is fixed,
explicitly quarantined, or genuinely owner-blocked.

## 4. Never patch blindly

Evidence → reproduction → root cause → fix → regression proof. No random changes until
tests pass; no weakened tests to hide a real failure; no suppressed exceptions for a green
CI.

## 5. Every real bug gets a regression test that would have caught it.

## 6. Test hierarchy

unit → subsystem → integration → regression → security → end-to-end → real owner
qualification only where necessary. Smallest useful tests first; the full gates once the
local gates are green.

## 7. Integration is part of done

Router integration · event/state integration · Activity Ledger · World/Self Model where
applicable · UI representation where applicable · permissions/authority · receipts and
read-back for mutations · restart/recovery · relevant cross-module behaviour. An
integration-point test goes through the REAL application object (ADR-0078: three
"built, tested, never wired" defects taught this).

## 8. Major capabilities expose truthful state to the Living Core / Cockpit. No fake activity.

## 9. Runtime defects self-heal

incident → `EvolutionOpportunity` → isolated candidate → fix → tests → shadow/canary →
promotion according to risk (M18.4). Routine fixes need no owner request.

## 10. Deployment is not the end

Verify health, version, runtime/source/install provenance, critical capability behaviour,
migrations, agent heartbeat and the relevant smoke tests before marking `LIVE /
PROVEN_REAL`.

## 11. Automatic rollback

Health regression, crash, capability regression, security regression or failed
qualification → stop promotion → roll back to last-known-good → preserve the incident
evidence → create/fix a candidate → retest. Production is never left degraded while
development continues.

## 12. Risk and approval still bind

`AUTO_SAFE` · `AUTO_CANARY` · `OWNER_APPROVAL_REQUIRED` · `NEVER_AUTO_PROMOTE`. Autonomous
engineering is not autonomous production authority.

## 13. No owner blocking for engineering

An owner-only step is marked `READY_FOR_OWNER` (audio / visual / physical / approval) and
every independent task continues.

## 14. Do not repeat proven work

Durable evidence stands unless a dependency changed, regression risk is real, or the
evidence is stale for a relevant reason.

## 15. Completion report — every work item ends with

```
IMPLEMENTED · TESTED · BUGS FOUND · BUGS FIXED · REGRESSION TESTS ADDED ·
SECURITY REVIEW · CI · DEPLOYMENT STATE · RUNTIME VERIFICATION ·
PROVEN_REAL / PROVEN_PROXY / SHADOW_READY / NOT_YET_PROVEN · OWNER ACTION if any
```

## 16. Key invariant

```
NOT DONE until code works + tests pass + discovered bugs are resolved
+ regressions are covered + integration is verified + runtime state is truthful
```

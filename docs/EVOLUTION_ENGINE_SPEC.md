# Evolution Engine Specification

## 1. Goal

The product must be able to add missing capabilities and improve existing ones with minimal owner involvement while preserving recoverability.

## 2. Capability registry

Each capability must be machine-readable.

Example:

```yaml
id: research.web.deep
version: 1.2.0
status: production
inputs: []
outputs: []
permissions: []
dependencies: []
owner_scope: normal
health_metrics: []
```

## 3. Gap detection decision tree

For every unmet request:

1. Can an existing capability solve it?
2. Can existing capabilities be composed?
3. Can an existing skill be configured?
4. Can an existing skill be safely extended?
5. Is a new skill/module required?
6. Does the requirement imply a product/core change?

Create code only when composition/configuration is insufficient.

## 4. Engineering pipeline

```text
Capability Gap / Incident
        |
        v
Requirement Work Order
        |
        v
Architecture Proposal (machine-readable + docs)
        |
        v
Isolated git worktree/branch
        |
        v
Implementation by coding backend
        |
        v
Unit + integration + regression tests
        |
        v
Independent review
        |
        v
Security/dependency checks
        |
        v
Build artifact/container
        |
        v
Sandbox/shadow/canary
        |
        v
Metric comparison
      /   \
 promote reject/rollback
```

## 5. Coding backend v1

Use Claude Agent SDK/Claude Code programmatic capabilities as the first coding backend behind an interface such as:

```text
CodingBackend
- analyze_issue
- implement_change
- review_change
- summarize_patch
```

Do not couple domain code to Anthropic-specific response formats.

Later providers/local coding models can be added.

## 6. Builder/reviewer separation

The same model process that wrote a patch must not be the only acceptance authority.

Use:

- separate reviewer context/subagent;
- deterministic test suites;
- static analysis;
- runtime health checks;
- benchmark comparison.

## 7. Bug fixing

Incident flow:

1. capture stack/error/telemetry;
2. classify;
3. reproduce in isolated environment;
4. write regression test;
5. patch;
6. run targeted + full gates;
7. deploy candidate;
8. monitor stability;
9. promote or rollback;
10. record incident/learning.

## 8. Proactive improvement

A module may propose improvement when telemetry shows measurable opportunity:

- high failure rate;
- repeated owner correction;
- latency regression;
- unnecessary cost;
- duplicated modules;
- repeated manual workflow.

A proposal needs an objective metric and evaluation plan.

## 9. Release scoring

Example dimensions:

- functional success rate;
- regression count;
- P95 latency;
- cost/task;
- owner correction rate;
- crash rate;
- resource use;
- security findings.

No automatic promotion solely from “LLM reviewer likes it”.

## 10. Self-generated skills

Generated skill layout should be standardized:

```text
skills/generated/<skill>/
  manifest.yaml
  README.md
  src/
  tests/
  evals/
```

The runtime loads only registered/validated versions.

## 11. Product feature changes

Schema/API/UI changes require:

- migration plan;
- compatibility plan;
- integration/E2E tests;
- release notes;
- rollback strategy.

## 12. Core/recovery changes

Recovery/identity/signing components may have new versions developed automatically, but activation must preserve an independently runnable previous recovery path.

## 13. Evolution sandbox

Coding backend runs with:

- isolated checkout/worktree;
- restricted secrets;
- no direct production write access;
- controlled package/network policy;
- disposable environment where practical.

Claude Code `bypassPermissions` is acceptable only inside an isolated disposable VM/container designed for it. Normal owner workstation development should prefer Auto mode.

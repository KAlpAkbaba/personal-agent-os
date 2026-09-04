# M17 — Cognitive Foundations (2026-09-05)

From *what happened?* to *what did I learn, what am I trying to do, what am I, and what
should I become?* — built as separate, inspectable subsystems over the durable evidence the
Activity Ledger already keeps, not as one large model prompt.

Six packages, one rule they all obey: **evidence first, then words.** Nothing in this
milestone may state as fact anything it cannot point at.

```
                      durable evidence
 research/voice/browser/deployment ──► activity_events (M16 ledger) ──► incidents/releases
                                            │
        ┌───────────────────────────────────┼───────────────────────────────┐
        ▼                                   ▼                               ▼
  app/experience                      app/worldmodel                  app/selfmodel
  episodic + semantic memory          what is true NOW, in four       what the system IS:
  (through the M5 memory              truth kinds (source /           modules, symbols,
  subsystem), lessons                 installed / runtime /           tests, ADRs, releases,
  compiled from incidents             evidence)                       provenance per module
        │                                   │                               │
        └───────────────► app/goals (goal engine + cognitive loop) ◄────────┘
                                            │
                                            ▼
                                     app/evolution
                        opportunities, lab authority, shadow candidates
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
              app/uistate (ADR-0052)                     app/explain (M16)
        truthful states for the Holographic Core     the owner hears it, in Turkish
```

## 1. Experience Engine (`app/experience`, Phase 2)

Turns durable activity into memory, through the **existing** M5 memory subsystem
(`app/memory`) — its classes, write-policy ladder, corroboration, supersession, hard
delete and audit are reused unchanged.

- **Episodic**: one memory per meaningful ledger event (a research run completed with
  these counts; a qualification passed; a browser session closed clean), carrying evidence
  refs to the ledger rows, `occurred_at` from the event, retrieval tags (subsystem, event
  type, outcome) and a confidence derived from the evidence.
- **Semantic**: stable facts, written only with corroboration from ≥ 2 distinct events
  (research's default provider is DuckDuckGo; the owner prefers concise spoken briefings).
- **Procedural**: never written directly here — it is the Experience Compiler's output.
- **Idempotent**: keyed on the ledger event, so a second ingest writes nothing new.
- An inference is never stored as a fact: provenance names the kind, and confidence stays
  below certainty.

## 2. Experience Compiler (`app/experience/compiler.py`, Phase 3)

`incident → root cause → resolution → generalizable lesson`, from real failures and what
actually fixed them.

```
Incident:            repository current, runtime stale
Root cause:          deployment/live-worker mismatch
Resolution:          compare checkout → staged → installed → running before declaring success
Generalized lesson:  runtime provenance must be independently verified
```

Not every event becomes a lesson. A candidate is scored on generalizability, confidence,
recurrence and owner relevance, with an explicit **overgeneralization penalty**; only
lessons above the named threshold become procedural memories, and a lesson from a single
event is promoted only when its confidence is high and its risk low. Everything else stays
a `candidate` row the owner can inspect, promote or reject.

## 3. Goal Engine + Cognitive Core (`app/goals`, Phase 4)

Goals persist above individual commands: goal → subgoal → task, with dependencies,
priority, horizon, deadline, success criteria (satisfied **from evidence**, never by
assertion), blockers, an owner-approval requirement and evidence refs. Illegal status
transitions raise, exactly like the artifact state machine.

The cognitive loop is a set of **narrow roles**, not a monolith:

| Role | Responsibility |
|---|---|
| Orchestrator | runs the loop, bounds replanning, escalates instead of spinning |
| Planner | goal + world state → ordered steps (deterministic today; a model may be injected) |
| Actor | executes one step through the Capability Router |
| Critic | evaluates an observation against the step's success criterion |
| Memory Manager | what to recall before planning; what to record after |
| Capability Router | name → capability, behind an allow-list |

`understand goal → inspect world → identify missing information → plan → execute →
observe → evaluate → replan (bounded) → complete or escalate → learn.` No LLM call lives
in the package; every seam is a Protocol.

## 4. World Model (`app/worldmodel`, Phase 5)

The current state of the system, assembled read-only from existing rows: owner, devices,
services, capabilities, running tasks, active goals, modules, production/shadow/lab states,
dependencies, recent events, incidents, assumptions, uncertainties, runtime versions.

Every fact carries its **truth kind**, and they never collapse into one:

| Truth | Means | Source |
|---|---|---|
| `SOURCE_TRUTH` | what the checkout says | repository |
| `INSTALLED_TRUTH` | what is installed | install evidence, release rows |
| `RUNTIME_TRUTH` | what is actually running | hello/audit/observed evidence |
| `EVIDENCE_TRUTH` | what a durable record proves | ledger, reports, qualification files |

A fact known only from source is labelled as such and marked uncertain about runtime.
This is the discipline the 2026-09-04 deployment incident bought: a repository is not a
runtime.

## 5. Self Model / Code Intelligence (`app/selfmodel`, Phase 6)

An index of the system itself — modules, symbols, dependency edges, tests, ADRs and specs,
releases, incidents, and per-module provenance in the same four truth kinds. Built by
static analysis (Python `ast` for the API, name/path scanning elsewhere), incremental and
bounded: **the repository is never pushed into a model context**; the index is retrieval
structure, and only the relevant slice is ever fetched.

It answers, from evidence: *what is this module, is it live, which version really runs,
what is wrong with it, why was it written this way, what did the last test failure say, is
it ready for production?* — each answer with references and an explicit confidence, or an
honest "no runtime evidence".

## 6. Evolution Engine (`app/evolution`, Phase 7) and its boundary

An evidence-driven backlog of improvement opportunities with the lifecycle
`IDEA → RESEARCHING → DESIGN_READY → BUILDING → TESTING → EVALUATING → SHADOW_READY →
OWNER_APPROVED → QUALIFYING → LIVE`, plus `REJECTED`, `SUPERSEDED`, `QUARANTINED`,
`ROLLED_BACK`. Opportunities are scored on owner relevance, expected utility, recurrence,
confidence, engineering cost and operational risk — where **risk can veto**, so utility
cannot outbid danger. Not every idea is built.

**The absolute boundary, enforced structurally.** The lab may research, design, write code
and tests, run them in an isolated workspace, benchmark, security-review, critique,
rewrite, package a candidate, mark it `SHADOW_READY` and explain it. It may **not** deploy,
sign a release, read unrestricted production secrets, write production data, or weaken the
root policies (owner identity, approval boundary, deployment authority, audit guarantees,
secret boundaries, sandbox boundary, rollback guarantees, security policy kernel).
`OWNER_APPROVED` can only be entered by an owner actor; `LIVE` only with an owner-approved
release. Production authority is a capability the lab code cannot construct — knowledge
access is not execution authority.

## 7. What the owner sees

Every subsystem publishes UI state (ADR-0052) and writes the Activity Ledger, so the
Holographic Core can draw truthfully and Self Explanation can answer in Turkish at the
existing levels — executive by default, detailed and technical on request, everything only
on "hepsini oku". The questions this milestone makes answerable:

> "Ne öğrendin?" · "Son hatalardan ne öğrendin?" · "Kendi üzerinde ne geliştiriyorsun?" ·
> "Hazır modüllerin neler?" · "Canlıya alınmayı bekleyen ne var?" · "Diagnostic Observer'da
> sorun ne?" · "Bu özelliği neden geliştirdin?" · "Test sonuçlarını anlat."

## 8. What this milestone is not

It is not a claim of general intelligence. These are foundations with measurable
behaviour: memories with provenance, goals with evidence-checked criteria, a world model
that refuses to confuse a repository with a runtime, an index that can say "I don't know",
and a lab that cannot reach production. Capability is claimed only where a test or a real
run proves it.

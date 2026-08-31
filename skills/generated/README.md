# `skills/generated/` — self-generated skill workspace

This directory is the **publication root** for skills the Evolution Engine
generates (EVOLUTION_ENGINE_SPEC §10, M7 / ADR-0025). Nothing here is
hand-written: every file under a skill directory is emitted by a
`SkillGenerator` and reaches this root only after it passed the full gate chain.

## Layout

```text
skills/generated/
  <skill>/<version>/
    manifest.yaml     machine-readable capability manifest (§2)
    README.md
    src/<skill>.py    run(payload) -> dict entrypoint + JSON stdin/stdout main
    tests/            generated unit tests (stdlib script, exit 0/1)
    evals/            generated eval set + runner (prints EVAL-RESULT <json>)
  .work/              DISPOSABLE candidate workspaces (sandbox root, §13)
```

§10 specifies `skills/generated/<skill>/`; the extra `<version>/` level is a
deliberate deviation so published skill versions stay immutable and a rejected
or superseded candidate can never overwrite the version production is currently
dispatching.

## What may appear here

Only a skill version that has:

1. come from a capability gap whose decision trail shows the whole resolution
   order was walked — existing capability, **composition attempted first**,
   configure, extend, and a **vetted reusable component** from the local
   catalog — each reported insufficient before code was written;
2. been generated inside `.work/` (the sandbox root, which the policy refuses to
   place anywhere near `services/recovery-supervisor` or the API source), with a
   rebuilt environment carrying **no production secrets**, deny-by-default
   network/filesystem/device access, and enforced timeout/disk/output budgets;
3. passed the **supply-chain scan**: every dependency pinned by
   name + version + source + digest, resolvable in the local component catalog,
   with no install scripts;
4. had its generated tests and eval set **actually executed** and scored against
   the §9 release gates;
5. passed an **independent** review that re-ran those tests itself, re-scanned
   the supply chain and permissions, and proved the tests fail on a sabotaged
   entrypoint;
6. walked the lifecycle `candidate -> sandbox -> validated -> shadow -> canary`
   with recorded evidence at every edge;
7. been registered through `CapabilityRegistry.register`, which refuses anything
   that is not `evaluated` with passing gates, lacks the promotion evidence, or
   carries a permission grant the reviewer did not explicitly approve.

The runtime loads only registered/validated versions: dispatch goes through
`CapabilityRegistry.resolve`, which returns a capability only when it is
`production` **and** its current skill version is `registered`. A superseded or
rolled-back version stays on disk and in the registry so it remains a valid
rollback target.

## Version control

Runtime output is git-ignored (see the repository `.gitignore`): candidate
workspaces under `.work/` and published skill versions are machine-produced
artifacts recorded in the `skill_versions` table with their manifest digest, not
source. Only this README is tracked.

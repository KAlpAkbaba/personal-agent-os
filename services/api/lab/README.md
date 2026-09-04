# `services/api/lab/` — the Evolution Engine's committed candidate tree

Code the lab has built and taken to `SHADOW_READY`, kept in git so the owner can
read it before approving anything. **Nothing under `services/api/app` imports
anything here**, and a unit test walks the whole application package's import
graph to prove it.

## Why not `app/evolution/lab/…`

`app/evolution/sandbox.py` lists `services/api/app` in `PROTECTED_TREES`: a
sandbox root may never overlap the API's own source, because the engine must
never write generated code into the running product (CLAUDE.md self-development
rule, constitution §6). Candidate code inside `app/` is also importable as
`app.*` and ships in the wheel — `pyproject.toml` publishes `packages = ["app"]`.

## Why not `skills/generated/`

That tree is the *generator's* runtime publication root
(`PAGENTOS_EVOLUTION_SKILLS_ROOT` in `app/evolution/runtime.py`). It is
git-ignored, and its README states that nothing there is hand-written: it holds
machine-produced skill versions recorded in `skill_versions` with a manifest
digest, not reviewable source. A hand-authored, committed candidate does not
belong there.

## What this tree is

`services/api/lab/` is outside `app/`, outside the wheel, and overlaps no
protected tree — a `SandboxPolicy` constructed on it succeeds, which the tests
assert. It has no `__init__.py` at any level, so it is not importable as a
package; tests load a candidate from its path.

## Layout

```text
services/api/lab/
  candidates/<name>/
    SPEC.md            what it does, why, and its acceptance criteria
    THREAT_MODEL.md    what the candidate itself could get wrong
    NOTES.md           the independent critique and what changed because of it
    src/<name>.py      the implementation (stdlib only unless stated)
    run_lifecycle.py   drives the real EvolutionService IDEA -> SHADOW_READY
    evidence/          the recorded run: ledger events, UI states, scan output
```

Tests live with the rest of the suite, at
`services/api/tests/unit/test_lab_<name>.py`.

## The rule for everything here

A candidate stops at `SHADOW_READY`. It is not imported by the application, not
wired into CI, and not in `scripts/quality-gate.ps1`. `OWNER_APPROVED` requires
an `OwnerCapability` minted from a verified owner session, which lab code has no
expression to produce.

## Candidates

- `acceptance_wording_guard` — reports acceptance assertions that depend on
  generated natural-language wording rather than on structure
  (ADR-0051 addendum 4). `SHADOW_READY`, advisory only, never deployed.

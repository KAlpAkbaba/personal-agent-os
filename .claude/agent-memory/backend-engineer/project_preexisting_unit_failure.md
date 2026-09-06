---
name: preexisting-identity-enforcement-failure
description: tests/unit/test_identity_enforcement.py's unauthenticated-endpoint allowlist fails on main independently of research/voice work — check before chasing it
metadata:
  type: project
---

`tests/unit/test_identity_enforcement.py::test_only_the_four_deliberate_endpoints_are_unauthenticated`
fails on `main` as of 2026-09-07: the M18.3 alarm audio route `GET /v1/alarms/audio/{token}`
is unauthenticated by design but was never added to that test's allowlist. Everything else
in `tests/unit` passes (4102 passed, 2 skipped).

**Why:** M18.3 shipped the alarm audio token route without updating the identity-enforcement
allowlist. It is not caused by the M18.2 research-focus work — verified by stashing that work
and re-running the file, where it still fails.

**How to apply:** when a full `uv run pytest tests/unit -q` comes back "1 failed", check
whether it is this one before investigating; report it as pre-existing and out of scope
unless the task actually covers `app/alarms`. Confirm it is still the same failure (the
allowlist may have been fixed since) rather than assuming — see
[[env-worktree-venv-and-test-speed]] for how long a confirming run takes.

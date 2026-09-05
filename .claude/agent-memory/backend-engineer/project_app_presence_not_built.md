---
name: project-app-presence-not-built
description: app/presence does not exist yet (as of 2026-09-05); anything depending on owner presence must go through app/uistate event-name strings, never through app/presence internals
metadata:
  type: project
---

As of 2026-09-05 (M18 Routine Engine build), `app/presence` does not exist in
`services/api/app`. It is described as "being written in parallel" by task briefs that
reference it, and `app.uistate.contract.UiState` has no presence-specific enum members
either (`owner.returned` / `owner.awake` / `owner.likely_asleep` are not there).

**Why this matters:** a consumer that needs owner-presence signals cannot import anything
from `app.presence`, and should not assume the exact event NAMES are already registered as
`UiState` enum members in `app.uistate.contract` — they may arrive as plain strings on the
`app.uistate.publisher` tail instead (`event.state` may not have a `.value` attribute).

**How to apply:** match presence-derived event names via `getattr(event.state, "value",
event.state)` string comparison against locally-owned constants (see
`app/routines/triggers.py`'s `PRESENCE_TRIGGER_EVENTS`), not via `UiState` enum membership.
Do not add presence-owned vocabulary to `app.uistate.contract` on behalf of that track — see
[[project-m18-spec-gap]] and `docs/DECISIONS.md` ADR-0056 decision 4 for the reasoning. When
`app/presence` eventually lands, re-check whether it publishes through this same seam or a
different one before assuming the routine engine "just works" against it.

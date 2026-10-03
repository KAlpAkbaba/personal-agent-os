# ADR-XXXX (lead numbers it): the ceiling on a write, and the page a task keeps (ADR-0207 PR-C)

Date: 2026-10-03. Card: `webtask-write-ceiling-retention`. Status: accepted. Closes the two
items ADR-0240 left under "Not here (for the lead at merge)".

## Context

The Cloud Core's gate classifies every step (`app/webtask/risk.py::classify_step`) and the
loop passes `risk_ceiling` to `BrowserPort.act` for every action, but
`DeviceTaskBrowser.act` put it on the wire for `browser.click` only, and the worker's
`_op_fill` / `_op_select_option` / `_op_set_checked` classified nothing (static
REVERSIBLE_WRITE). For a `ref` target `_bound_target` already refuses a changed
(tag, role, name); the gap was a control whose WIRING changed (moved into a form, made
to submit) under the same name, and every semantic target. Separately, `TaskState.observation`
(the last observation, up to 6 000 characters of page text) stayed in `web_tasks.state_json`
for ever after the task ended.

## Decision

1. **Contract v1.8** (`BROWSER_CAPABILITIES.md` §4a, changed first). `risk_ceiling` is
   accepted on `browser.fill`, `browser.select_option`, `browser.set_checked`. With it the
   worker describes the resolved element, classifies the WRITE (HIGH_IMPACT marker →
   HIGH_IMPACT; `submits` or an external-communication marker → EXTERNAL_COMMUNICATION;
   unnamed and (`submits` or `in_form`), not for `fill` → EXTERNAL_COMMUNICATION; else
   REVERSIBLE_WRITE), applies the session policy with that class, refuses above the
   ceiling with click's error (`security_scope_error`, `retryable:false`, evidence
   `reason:"above_ceiling"`, `risk_class`, `risk_ceiling`) before anything is written,
   and answers `{"ok":true,"risk_class":…}`. Without the field: v1.7 exactly
   (`{"ok":true}`). No operation name, no `contracts` key.
2. **The name comes from the collector.** The worker reads name / `submits` / `in_form`
   through `_DESCRIBE_OBSERVED_JS` (extended to return the two flags) - the same
   definition an observation uses, so both halves classify the same inputs. Not
   `_DESCRIBE_ELEMENT_JS`: its `name` falls back to a field's VALUE and would classify a
   write by what is typed into it. An element the collector does not list is described
   as nameless and in a form (classified as one that may send).
3. **`policy.classify_write`** mirrors `risk.classify_write` + the unnamed-and-wired rule;
   `tests/unit/test_write_ceiling.py` runs one table through both (the Cloud's module
   loaded from its source file).
4. **The old-agent limit.** A worker from before v1.8 ignores the field and writes. The
   Cloud Core detects it by the missing `risk_class` and raises `capability_missing`
   (naming contract v1.8). That ONE write was performed, gated only by the Cloud Core's
   own gate. Because the loop (not changed here) counts a failed act and may plan
   another write, `device_port` remembers the device (process memory) and sends it no
   further write; clicks still go (their ceiling is enforced since v1.7). A Cloud Core
   restart forgets this and costs at most one more Cloud-gated write; an agent updated
   while the Cloud Core runs stays refused for writes until the next restart (the safe
   direction).
5. **Retention.** `service._write` stores a new document (never an in-place edit):
   terminal status (done / failed / cancelled) → `observation: null`,
   `observation_fresh: false`; waiting for the owner and not fresh → url / title /
   elements kept, `text` dropped; running → kept whole (the next round acts on it without
   observing twice). Triggers: every terminal `_write` (run_round_db, cancel_db, fail_db),
   `active_task` failing an orphan (ORPHAN_AFTER 20 min) or an abandoned parked task
   (PARKED_AFTER 3 h), and `scrub_observations(db, now)` - idempotent, called from
   `start_task_db` before the new row - for rows written before this rule.
   Readers checked (grep `.observation` in service.py / loop.py and app-wide): `loop._observe`
   reads the stored one only when `observation_fresh` is true (never in a wait, never after
   the end); `loop.element_of` reads elements (kept while waiting) and has no caller in
   `app/`; `task_dict` never showed it; the planner, gate and verify read the observation the
   current round obtained. No reader needs the text after a park or anything after the end.

## Not here

- A timed sweep from the app's lifespan (`app/main.py` is held by open cards): rows are
  scrubbed when the next task starts, not on a clock. Named follow-up.
- No migration (`state_json` is one JSON column), no setting, no compose change.
- `gate.py`, `risk.py`, `loop.py` unchanged (the Decision already carries the ceiling).
- Unreachable in production until PR-D gives `start_task_db` a caller and the home PC's
  agent is updated to a worker serving v1.8 (an owner-visible agent update, not this card).

---
name: feedback-temporal-worker-restart-test-hang
description: Testing a "worker restart mid-workflow" scenario against temporalio's time-skipping WorkflowEnvironment can hang indefinitely two different ways — avoid both
metadata:
  type: feedback
---

Testing "a Cloud Core restart mid-run" against `temporalio.testing.WorkflowEnvironment.
start_time_skipping()` (not a real Temporal server) by literally tearing down one `Worker`
and standing up a second on the same task queue is fragile and cost ~1 hour to debug
(M26, 2026-09-08).

**Two distinct hangs found:**
1. A graceful `async with worker1: ...` exit while ANYTHING is still in flight (even after
   the in-flight activity itself completes) can hang the whole test indefinitely — the
   worker's graceful shutdown appears to wait to drain a poller the in-memory time-skipping
   server has nothing left to deliver to.
2. Cancelling the worker's own `run()` task (`task.cancel()`) and NOT awaiting it leaves the
   Rust bridge worker still registered when a second `Worker(...)` is constructed on the
   same task queue, raising "multiple workers with overlapping worker task types". AWAITING
   the cancellation (even bounded, `asyncio.wait_for(task, timeout=5)`) avoids that error but
   then `handle.result()` on the original workflow handle can itself stall past any
   reasonable bound — worker2 does not reliably win the sticky-queue handoff within a real
   (non-skipped) wall-clock window in this environment.

**Why:** these are properties of the SDK's worker lifecycle under this specific in-memory
test server, not of the workflow code under test.

**How to apply:** do NOT try to prove "restart mid-run" by literally killing/replacing a
`Worker` against the time-skipping environment. Prove the SAME durability property in two
smaller, reliable pieces instead:
- idempotent replay at the ACTIVITY layer — call the real activity function twice with the
  step already marked verified/with prior evidence persisted, assert the underlying service
  is not called again (fast, deterministic, no Temporal machinery needed at all);
- reattachment at the WORKFLOW layer — start a workflow, let it run (same single worker the
  whole time), then get a FRESH handle via `env.client.get_workflow_handle(workflow_id)`
  (never the original `start_workflow` handle) and prove it still reaches the same execution.
  This is the actually-observable property a restarted process depends on (it re-derives the
  workflow id from its own DB row, never holds the original in-process handle) and needs no
  worker teardown at all.

See [[project_m26_cloud_core_status]] for where this was applied
(`test_worker_restart_mid_run_resumes_from_history` in
`services/api/tests/unit/test_executive_workflow.py`) and ADR-0089 addendum 1 decision 5 in
`docs/DECISIONS.md` for the recorded reasoning.

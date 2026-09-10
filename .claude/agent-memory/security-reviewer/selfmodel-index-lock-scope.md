---
name: selfmodel-index-lock-scope
description: a lock added for one caller does not cover a background task added later -- found and FIXED in app/selfmodel 2026-09-10 (build_index now owns a process-wide lock); keep checking the shape.
metadata:
  type: project
---

`app/selfmodel/routes.py` defines `_index_lock = asyncio.Lock()` at module scope and only
acquires it inside `rebuild_index()` (`POST /v1/selfmodel/index`). `app/selfmodel/refresh.py`
(`SelfModelRefresher`, added 2026-09-10, ADR-0111) runs the identical `build_index()` /
`Indexer.run()` on its own schedule (5s after startup, then every 900s) via
`asyncio.to_thread`, in a completely separate code path that never imports or touches
`_index_lock`. Confirmed by reading both files: nothing prevents an owner-triggered
`POST /index` from running concurrently with a scheduled refresh pass, each opening its own
DB session/transaction and doing delete-then-reinsert on `CodeSymbol`/diff-based
`CodeEdge` sync against its own stale snapshot of `existing_rows` -- a plausible
IntegrityError or lost-update race. No test exercises this interaction as of the review;
`tests/unit/test_selfmodel_refresh.py` only tests the refresher in isolation.

**Why:** this is a general shape worth watching in this repo -- a lock introduced for one
caller (an HTTP route) does not automatically cover a background task added later that does
the same underlying work, because nothing forces the two call sites to share a coordination
primitive.

**FIXED the same day (ADR-0111).** The lock moved into `app.selfmodel.indexer` as a module-level `threading.Lock` taken by `build_index` itself, so both callers are covered whether or not they remember to ask; `index_running()` lets the route keep answering 409 instead of blocking. The regression runs four concurrent `build_index` calls and asserts peak concurrency 1 -- without the lock it fails with exactly the predicted `UNIQUE constraint failed: code_modules.module_id`. The finding above is kept because the SHAPE recurs, not because the instance is open.

**How to apply:** whenever a new background scheduler/refresher is added next to an existing
HTTP-triggered mutation of the same tables, check whether they share a lock (in-process
`asyncio.Lock` is not enough across multiple worker processes either -- an advisory Postgres
lock or similar would be needed for that case). See also [[redaction-not-automatic]] for the
same review pass this was found in.

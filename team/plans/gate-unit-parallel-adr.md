# ADR draft: the gate's API unit step runs under pytest-xdist

Status: proposed (gate-unit-parallel, cycle d20261006)

## Context
The gate's "API unit tests" step ran ~17,000 tests in one process: 30-50 minutes (3040 s in the
pre-check of the 2026-10-06 integration), the longest step of a ~95-minute gate the owner saw
restarted five times that day ("çok zaman kaybediyoruz").

## Decision
- `pytest-xdist>=3.6` is a dev dependency of services/api.
- `scripts/quality-gate.ps1` runs `pytest tests/unit -q -n <N> --dist load`, with
  N = min(8, cores-2), lowered to floor((free memory - 8 GB floor) / 2 GB per worker).
  Fewer than two workers, or xdist not importable -> the step runs serially exactly as before and
  prints why. `PAGENTOS_GATE_UNIT_WORKERS` sets N by hand (1 = serial).
- Pass/fail meaning is unchanged: every test is collected and run, the exit code is asserted the
  same way; no test is skipped or marked serial.
- Worker-safety: xdist refuses a run whose workers collected different ids. Two sources were
  found by collecting twice: route-guard tables that put a fresh uuid4 into a parametrize value,
  and parameter tables built from sets (string-hash order per process). `tests/conftest.py`
  writes a uuid inside a parameter id as `<uuid>`, and the xdist controller sets one
  `PYTHONHASHSEED` (kept if the caller set one) for all its workers. Serial runs are untouched.
  No test shared a file path, port, env variable or singleton across processes in the runs made
  (tests already use tmp_path / in-process state).

## Consequences
- The step is limited by the slowest worker's share instead of the sum (see the task report for
  measured times).
- On a loaded machine (free memory under ~12 GB) the step falls back to serial; that is slower
  but never wrong. The memory constants (8 GB floor, 2 GB/worker) are the knobs if that is too
  conservative.
- `scripts/tests/gate-unit-parallel.tests.ps1` guards the worker count, the -n call, the serial
  fallback, failure propagation and the identical-collection property.

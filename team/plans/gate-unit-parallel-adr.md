# ADR draft: the gate's API unit step runs under pytest-xdist

Status: proposed (gate-unit-parallel, cycle d20261006)

## Context
The gate's "API unit tests" step ran ~17,000 tests in one process: 30-50 minutes (3040 s in the
pre-check of the 2026-10-06 integration), the longest step of a ~95-minute gate the owner saw
restarted five times that day ("çok zaman kaybediyoruz"). Measuring it for this card showed a
second problem: the serial process grew to 21-24 GB (seen twice on 2026-10-06/07 in the lead's
int-catchup gate), leaving the 48 GB machine 0.5-2 GB free.

## Decision
- `pytest-xdist>=3.6` is a dev dependency of services/api.
- `scripts/quality-gate.ps1` runs `pytest tests/unit -q -n <N> --dist load -m "not serial_tail"`,
  then `pytest tests/unit -q -m serial_tail` serially; either part red fails the step, and the
  tail runs after a red parallel part too. N = min(8, cores-2), lowered to
  floor((free memory - floor) / 2 GB); the floor is `team/cycle-settings.json`
  `test_memory_floor_gb` (8 when missing). Under two workers, or without xdist, the step runs
  serially exactly as before and prints why. `PAGENTOS_GATE_UNIT_WORKERS` sets N (1 = serial).
- Every test still runs; nothing is skipped. Worker-safety, all in `services/api/tests/conftest.py`:
  1. ids: a uuid4 inside a parametrize value is written `<uuid>`, and the xdist controller sets one
     `PYTHONHASHSEED` (set-ordered tables) - xdist refuses workers that collected different ids;
  2. memory: FastAPI's three `fastapi.dependencies.models` lru_caches (4096 entries each) are keyed
     by endpoint callables, i.e. closures over a `create_app()` app - every test's app stayed alive
     (25 of 25 after one file). Cleared after each test: a 38-file sample grew 138 MB instead of
     1143 MB. This helps the serial run as much as the parallel one;
  3. `SERIAL_TAIL` (marker `serial_tail`, each id with its reason): a mutation proof that deletes
     `packages/protocol/realtime-session-contract.json` from the shared tree (two tests read it and
     failed beside it), and a test whose PowerShell guard must finish within `-HangSeconds 2`
     ("red" came back "hung" beside eight workers);
  4. `XDIST_GROUPS` + `--dist loadgroup`: the three corpus files whose aggregate tests read a
     module-level result cache the parametrised cases fill are one xdist group each. Under
     `--dist load` the cases went to other workers and every worker that got an aggregate re-ran
     the whole corpus (768 s and 7-10 GB, twice a run; two such workers at once took the tree to
     17 GB). Tests without a group are scheduled one by one as before;
  5. `LONG_FIRST`: in xdist workers the owner-corpus file is collected first, so its group starts at
     once instead of mid-run. Only that file: the STT corpora moved first too ran three
     memory-heavy corpora together (tree 14.2 GB). A serial run keeps its order.

## Measurements (28 logical cores, 48 GB, the team's other runs on the same machine)
| run | workers | pytest s | step s | tree peak GB | result |
|---|---|---|---|---|---|
| serial (before) | 1 | 3040 | - | 21-24 (one process) | - |
| direct, leak fixed | 8 | 1470 | - | 14.0 | 1 known red (ci.yml) |
| direct | 12 | ~1500 | 1567 | 10.9 | 1 known red |
| gate, before LONG_FIRST | 8 / 6 / 7 | 1289 / 1297 / 1089 | 1354 / 1348 / 1136 | 10.9 / 9.3 / 11.8 | 1 known red each |
| gate, final (loadgroup), 3 in a row | 7 / 7 / 6 | 1268 / 1167 / 1185 | 1321 / 1213 / 1221 | 5.4 / 5.8 / 5.3 | 1 known red each |
| gate, final, serial | 1 | 2520 | 2542 | 2.9 (one process 2.7) | 1 known red |

One xdist worker is ~1.07 GB once it has collected the suite; with the corpus groups no worker
passed 2.1 GB. 6, 7, 8 and 12 workers end at about the same time: the step's length is now the
owner-corpus group on one worker (~20 min), not the worker count. Eight stays the cap.

## Consequences
- The 15-minute target is NOT met: the step takes 20-22 minutes (from 42 serial in the same
  conditions, 50 before). Below that the owner-corpus file must stop needing its whole corpus on
  one worker - e.g. its aggregates checked per case, so the file needs no group and xdist can
  spread its ~2,900 cases (follow-up card; the test file is outside this card's area).
- On a loaded machine (free memory under ~12 GB) the step runs serially; with the leak fixed a
  serial run peaks at 2.7 GB instead of 21-24 GB.
- `scripts/tests/gate-unit-parallel.tests.ps1` (in the gate's parallel suite group) guards the
  worker count, the floor, the -n call and the tail, the serial fallback, failure propagation of
  both parts, identical collection across workers, the freed apps, the SERIAL_TAIL ids and
  LONG_FIRST order.
- `test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite` is red until
  `.github/workflows/ci.yml` lists the new suite (outside this card's area; one line).

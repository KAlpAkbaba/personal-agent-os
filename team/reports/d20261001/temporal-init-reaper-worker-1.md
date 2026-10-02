## Şu an üzerinde çalışılan
`temporal-init-reaper` — alan: `infra/docker/docker-compose.{prod,dev}.yml`, `services/api/tests/unit/test_compose_init.py`, `team/plans/temporal-init-reaper-adr.md` — makine: sahibin geliştirme PC'si (dev stack, gerçek Docker). Durum: bitti, commit ve push edildi, worktree temiz.

## For the lead at merge
- **This is a compose change (ADR-0214 addendum 9): the release step must not release it by itself — ask the owner.**
- **A blue/green release does not apply it.** Every `compose up` in `release-cloud-core-bluegreen.sh` is `--no-deps` on an api colour or the edge, so prod temporal keeps `Init` nil after an ordinary release.
- **Applying it is one explicit host step:** `compose up -d --no-deps --wait temporal` against the released tree. It recreates the temporal container; on dev it was healthy again about 9 s after `up`.
- `deploy-cloud-core.sh:114` (the non-blue/green path) would apply it as a side effect.
- **The dev temporal container is now created from this worktree's compose file.** Until the merge, a `dev-up.ps1` from the main checkout recreates it without init and the zombie returns.

## Result
- **sha:** `bccda1c9252cc1ae4481fb0ce2aab8d5cbf855fb`, pushed to `team/d20261001/worker-temporal-init-reaper`.
- **Files:** 4, all inside the area. Each compose file gains exactly one line (`init: true` on `temporal`).
- **Tests added:** `test_compose_init.py`, 4 cases: `init is True` for prod and dev, plus a pin on the image tag `temporalio/auto-setup:1.27.2` and on no `entrypoint:`/`command:` override. The pin is my addition: the proof only holds for this image's own entrypoint.
- **RED → GREEN:** before the change `2 failed, 2 passed` (`prod` and `dev`: "the temporal service has no `init: true`"); after, `10 passed` together with `test_pilot02_wiring.py`.
- **Fast checks:** ruff check and format clean; `docker compose config` accepts the dev file.

## Mutation proof (restored from a backup copy, sha256 identical before and after)
| Mutation | Result | sha256 before = restored |
|---|---|---|
| prod: `init` removed | `[prod]` RED, 1 failed | `e9677956…8f2f` |
| prod: `init: "true"` (string) | RED | `e9677956…8f2f` |
| prod: `entrypoint:` override added | pin test RED | `e9677956…8f2f` |
| dev: `init` removed | `[dev]` RED | `6331d492…b489` |
| dev: `init: false` | RED | `6331d492…b489` |

## Dev stack, real Docker (`docker exec pagentos-temporal ps -eo pid,ppid,stat,comm`)
- **Before** (`Init=<nil>`): `1 0 S temporal-server` / `92 1 Z auto-setup.sh`
- **After** `up -d --no-deps temporal` (`Init=true`, started 22:30:08 UTC): `1 0 S docker-init` / `7 1 S temporal-server`, with no `Z` row. It was still clean after the integration run.
- **Health:** container `healthy`; `tctl cluster health` says `SERVING`; namespaces `default` and `temporal-system` are present.
- **Integration:** `tests/integration -m integration` gave `124 passed in 204.56s` against the restarted container.
- **Cause, read in the image:** `auto-setup.sh` ends with `setup_server &`, and the entrypoint then execs `temporal-server` as pid 1, which never waits for the orphan. With tini as pid 1 the orphan is reaped. The ADR text carries this.

## Evidence class
- Unit test and mutations: PROVEN_AUTOMATED.
- Zombie gone, Temporal healthy, integration suite: PROVEN on the dev stack.
- Cloud Core: NOT_RUN. It becomes PROVEN_REAL only after the owner-approved step, when `ps -eo stat,comm` there shows no defunct process and the maintenance script's `zombies` line reads 0.

## Not done / open risks
- `docker compose config` on the prod file: NOT_RUN to completion (my dummy env file did not satisfy it). The key is the same one the `cloud-browser` service already uses in that file, and the yaml test reads it as `True`.
- `docker stop` timing under tini: NOT_RUN. The ADR says SIGTERM forwarding is tini's documented behaviour, not measured.
- tini reaps orphans only. A child that a living `temporal-server` started and never waited for would stay defunct; there is none today.

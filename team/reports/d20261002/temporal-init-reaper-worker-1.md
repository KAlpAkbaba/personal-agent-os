## Şu an üzerinde çalışılan
`temporal-init-reaper` — area: `infra/docker/docker-compose.{prod,dev}.yml`, `services/api/tests/unit/test_compose_init.py`, `team/plans/temporal-init-reaper-adr.md` — machine: owner's dev PC (Windows 10, Docker Desktop), worktree `worker-temporal-init-reaper`.

## For the lead at merge
- **This is a compose change (ADR-0214 addendum 9): the release step must not release it by itself; ask the owner.**
- The blue/green release never applies it: every `compose up` in `release-cloud-core-bluegreen.sh` is `--no-deps` on an api colour or the edge, so temporal keeps `HostConfig.Init` nil.
- Applying it is one explicit host step, `compose up -d --no-deps --wait temporal`, which recreates the container. Temporal is away for seconds (the dev recreate with `--wait` plus my 20 s sleep took 28 s in total).
- The ADR text is in `team/plans/temporal-init-reaper-adr.md`, unnumbered, with this block first.

## Result
- **sha:** `bccda1c9252cc1ae4481fb0ce2aab8d5cbf855fb`, pushed (`origin/team/d20261001/worker-temporal-init-reaper` is the same sha), worktree clean.
- **No new commit this run:** the work was already committed; I re-ran every proof below first-hand and nothing needed changing.
- **Files changed:** 4, all inside the area. The compose diff is exactly one `+    init: true` line per file.

## Evidence (all run in this session and waited for)
- **Unit test — PROVEN_AUTOMATED:** `tests/unit/test_compose_init.py` is green on HEAD, `4 passed`.
- **Mutation, init removed from prod — RED:** `FAILED …runs_under_an_init_that_reaps[prod]`, `1 failed, 3 passed`.
  - sha256 before `e9677956…8f2f`, mutated `ecd6da39…f640`, restored from the backup copy `e9677956…8f2f`.
- **Mutation, init removed from dev — RED:** `FAILED …[dev]`, `1 failed, 3 passed`.
  - sha256 before `6331d492…b489`, mutated `ba38aed2…c4a6`, restored `6331d492…b489`.
  - These two mutations are also the RED-before state for both files; the original pre-change RED run was not repeated.
- **Dev stack BEFORE (dev file without init, `up -d --no-deps --wait temporal`) — PROVEN on real Docker:** `Init=<nil>`, healthy.
  ```
  PID   PPID  STAT COMMAND
      1     0 S    temporal-server
     97     1 Z    auto-setup.sh
  ```
- **Dev stack AFTER (file restored, same command) — PROVEN on real Docker:** `Init=true`, `Health=healthy`, no defunct process.
  ```
  PID   PPID  STAT COMMAND
      1     0 S    docker-init
      7     1 S    temporal-server
  ```
  - `tctl cluster health` → `temporal.api.workflowservice.v1.WorkflowService: SERVING`; namespaces `default` and `temporal-system` present.
- **Integration suite — PROVEN_AUTOMATED on the dev stack:** `tests/integration -m integration` after the restart gave `124 passed, 1 warning in 192.28s`, 0 skipped, exit 0.
- **Fast checks:** ruff check and format pass on the test; `docker compose -f docker-compose.dev.yml config -q` is OK.

## Not run / not done
- **`compose config` on the prod file: NOT_RUN.** It stops on the required `/opt/pagentos/.env` secrets (`PAGENTOS_VOICE_PROFILE_SECRET` missing); the unit test's YAML parse is the only syntax check for that file here.
- **PROVEN_REAL on the Cloud Core: NOT_RUN.** It needs the owner-approved host step; afterwards `ps -eo stat,comm` there must show no defunct process.
- **Blue/green release PS suite: NOT_RUN by me.** The previous report was waiting on it; it is outside this card's acceptance and area, and it does not touch temporal.
- **`docker stop` time under tini: not measured.**

## Open risks
- tini reaps orphans only; a child that a living `temporal-server` itself started and never waited for would stay defunct (none today).
- The proof holds for `temporalio/auto-setup:1.27.2` with its own entrypoint. The test pins the image tag and the absence of `entrypoint:`/`command:`, so an image bump turns it RED on purpose and needs a new dev-stack proof.
- The dev temporal container was recreated twice in this run (03:01 UTC), a few seconds away each time; a sibling worker's Temporal test in that window may have flaked.

## Hüküm
**DONE — merge-ready.** Every acceptance criterion on the card is met and proven first-hand. Release is **READY_FOR_OWNER**: the lead asks the owner before the temporal recreate on the Cloud Core.

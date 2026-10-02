# Inspector report — `temporal-init-reaper` (branch `team/d20261001/worker-temporal-init-reaper` @ `bccda1c9`)

**Diff:** 4 files, all inside the area; the compose change is exactly one `+    init: true` per file. Tree clean after my run; sha256 prod `e9677956…8f2f`, dev `6331d492…b489` (same as the worker's).

## Pass 1 — run it
- **Unit test — PROVEN_AUTOMATED:** `test_compose_init.py` plus the four sibling compose-reading unit files: `42 passed`. Ruff check and format clean.
- **RED-before:** main's dev file has no `init: true`, and main's prod file has it only on `cloud-browser`, so the test is RED on main for both.
- **Mutations (mine, different from the worker's), all RED with `1 failed, 3 passed`, each restored from a backup copy:**
  - prod `init: false` → `[prod]` fails.
  - dev `init: "true"` (string) → `[dev]` fails.
  - prod `init` moved from `temporal` to `api` → `[prod]` fails.
  - dev `command:` override added → the entrypoint-pin test fails for `[dev]`.
- **Real Docker, dev stack — PROVEN on real Docker:**
  - BEFORE (04:17:13 UTC): `Init=<nil>`, healthy, `1 0 S temporal-server` / `88 1 Z auto-setup.sh`. I did not have to stage this: the lead's full gate had recreated the container from the main checkout's file at 04:02:37.
  - AFTER `up -d --no-deps --wait temporal` from the branch file (11 s): `Init=true`, healthy, `1 0 S docker-init` / `7 1 S temporal-server`, 0 defunct.
  - `tctl cluster health` → `SERVING`; namespaces `default` and `temporal-system` present.
  - **`docker stop` under tini, which the worker left unmeasured:** 2142 ms, ExitCode 0. After `docker start`: healthy again, still 0 defunct.
- **Integration suite on the dev stack with Temporal under init — PROVEN_AUTOMATED:** `124 passed, 1 warning in 221.91s`, exit 0.
- **PowerShell suites from the worktree:**
  - cloud-release 38/0, cloud-secret 42/0, maintenance-reboot 35/0, host-snapshot 95/0.
  - cloud-release-bluegreen `85 passed, 0 failed` (the worker's NOT_RUN). My first attempt was cut by my own 580 s guard at 48 PASS / 0 FAIL; the rerun to completion is the number.
- **NOT_RUN:**
  - `compose config` on the prod file: it needs `/opt/pagentos/.env`.
  - Full `quality-gate.ps1`: this is not the integration branch, and the lead's gate was running.
  - Cloud Core `ps`: READY_FOR_OWNER.

## Pass 2 — break it
- **The plan's claim (a) holds:** every `compose up` in `release-cloud-core-bluegreen.sh`, `release-cloud-core.sh` and `install-env-secret.sh` is `--no-deps`; `maintenance-reboot.sh` and the recovery supervisor run no `compose up` at all. Only `deploy-cloud-core.sh:114` would recreate temporal as a side effect. An ordinary release therefore does not apply this change, and a reboot does not either.
- **Host snapshot is stale:** `collected_at` 2026-10-01T19:18:51Z is before the last release (ca5cc795, 20:47 UTC); it still says blue serving and release 858c3e0b. It records neither `HostConfig.Init` nor zombies, so it cannot judge this change. The lead should collect a new one.
- **The test hard-codes nothing from the fixture:** the pinned image tag comes from the compose files.
- **The dev stack reverts until merge:** any `dev-up` from a checkout without this commit recreates temporal without init. That is what happened to the worker's "after" state at 04:02.
- **Rollback:** an LKG tree without `init` leaves the running container as it is (`--no-deps` paths); removing it again is the same one-step recreate.
- **No findings on:** contract drift, secrets or paths, KVKK, memory on CPX32 (tini is negligible).

## For the lead at merge
1. This is a compose change (ADR-0214 addendum 9): do not auto-release; ask the owner.
2. The cure only lands with the explicit host step `compose up -d --no-deps --wait temporal`, about 10 s of Temporal outage.
3. Before that step, read `docker info --format '{{.InitBinary}}'` on the host (read-only). The recreate removes the old container first, so a missing `docker-init` would leave Temporal down.
4. PROVEN_REAL is the owner's step: `ps -eo stat,comm` on the Cloud Core shows no defunct process and the maintenance `zombies` line reads 0.
5. Collect a new host snapshot.

**Evidence classes:** unit and mutations PROVEN_AUTOMATED; dev stack PROVEN_PROXY for production (same image and service definition, real Docker); Cloud Core READY_FOR_OWNER.

APPROVE

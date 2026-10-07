# ADR (draft, lead numbers it): compute.run - a one-shot, network-less docker sandbox

Status: accepted for the package; UNBOUND (no router, no ToolSpec, no ledger kind). Cycle d20261006,
card compute-run-sandbox (Bulutta yürütme PR 3, team/plans/pilot-01-split.md row 6; ADR-0213 PR 1/2).
Integration plan with measurements: team/plans/compute-run-sandbox-integration.md.

## Principle

**Model-written code is untrusted.** Whatever the model writes for "bulutta şunu hesapla" runs as if
an attacker wrote it: no network, no host file, no secret, no environment, no capability, hard
ceilings, and a receipt for every run, including refusals. A hardened container is NOT a full
boundary (it shares the host kernel); it is the v1 boundary, and gVisor is recorded debt below.

## Decision

`app/compute` (pure `policy.build_argv` + `runner.run` + `receipt.ComputeReceipt`) runs each request as
`docker run --rm --pull never --name pagentos-compute-<32 hex> --network none --cap-drop ALL
--security-opt no-new-privileges --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m
--memory <m>m --memory-swap <m>m --cpus <c> --pids-limit 128 --ulimit nofile=64:64 --init
--user 65534:65534 --log-driver none -i python@sha256:ddb0207a…fe74c python3 -`, the code on stdin.

Beyond the card's argv, from the integrator's measurements: `--name` + `docker kill <name>` at the
deadline (killing the client leaves the container running - measured again here: mutation R2 left
`pagentos-compute-…` `Up 27 seconds`), `--log-driver none` (no copy of the output on the host disk),
the hardened tmpfs options, `--ulimit nofile=64:64`, and `--pull never` (a run never touches a
registry; the image is pulled once at release; a missing image is exit 125 = `docker_unavailable`).

## Ceilings and why

| ceiling | value | why |
|---|---|---|
| memory (= swap) | <= 512 MiB, default 256 | CPX32 has 8 GB beside api/Postgres/Temporal; no swap thrash |
| cpus | <= 1, default 1 | CPX32 has 4 vCPU; one is the most one calculation may starve |
| wall time | <= 60 s, default 30 | a voice answer that waits a minute is already a failure; covers container start (0.9 s cold) |
| stdout / stderr | <= 64 KiB each, kept; the rest drained and dropped | a receipt and a spoken answer, not a log store; the pipe never blocks |
| code | <= 64 KiB (UTF-8 bytes) | far above a hand-sized calculation; refuses pasted blobs |
| pids | 128 | fork/thread bombs |
| stdin | must be "" | `python3 -` reads the code from stdin to EOF; a program stdin needs a later bootstrap card |

Outside a ceiling is **refused** (ComputePolicyError -> receipt `refused`), never clamped. The image must be
`name@sha256:<64 hex>` with no tag; `latest` anywhere is refused.

Receipt outcome classes: `ok` (ran to its own end; `exit_code` may be non-zero, e.g. a clean
`MemoryError` = 1), `timeout` (the runner killed it), `oom` (137 the runner did not send),
`refused`, `docker_unavailable` (no CLI = FileNotFoundError, or docker's own exit 125). The runner
never raises for a run that went wrong.

## Debt (owner's decision, not in this card)

- gVisor `runsc` (Apache-2.0) as `--runtime runsc`: the real boundary for model-written code; a new
  host package + `daemon.json` entry on CPX32. Second option: `userns-remap`.
- A program stdin (fixed `-c` bootstrap reading a length-prefixed header).
- CPX32's Docker/runc version must be >= Docker 25.0.2 / runc 1.1.12 (CVE-2024-21626) - checked at release.

## Binding card (full text; hub files, its own area)

> **compute-run-bind** - Bulutta yürütme PR 3b: compute.run'ı uygulamaya ve araçlara bağla.
> Area: `services/api/app/main.py`, `services/api/app/realtime_sessions/tools.py`,
> `services/api/app/voice/intents.py` (only if a spoken intent is added), `services/api/app/ledger/vocabulary.py`,
> `services/api/app/compute/routes.py` (new), `services/api/tests/unit/test_compute_bind.py`,
> `infra/docker/docker-compose.prod.yml` (only the image pre-pull note, see below).
> 1. Ledger kind `compute.run` (`app.compute.LEDGER_KIND`) in `ledger/vocabulary.py`; one record per run
>    with `detail_json = receipt.to_detail_json()` (refusals too).
> 2. `routes.py`: `POST /v1/compute/run` (owner auth), body = ComputeRequest fields; before running, read
>    `app.execution.rule.decide(ExecutionRequest(JobKind.COMPUTE, availability))`: a non-`selected`
>    decision or a target other than `Target.CLOUD` returns the refusal and runs nothing; write
>    `rule.events(decision)` to the ledger as the other call sites do. `run_compute` runs in a thread
>    (`asyncio.to_thread`); never on a device.
> 3. `main.py`: one `app.include_router(compute_router)` line next to the other routers.
> 4. `realtime_sessions/tools.py`: ToolSpec `compute.run` (name = `app.compute.TOOL_NAME`), argument
>    `content` (the code; voice relay filters keys with text/audio/token), optional `timeout_s`; the
>    answer spoken is the short stdout or the outcome class, never the whole receipt.
> 5. Onay Merkezi: a "sahibin deneyecekleri" row: "Bulutta 2 üzeri 100'ü hesapla" -> the answer is
>    spoken and a `compute.run` ledger record shows outcome `ok`; this is the first PROVEN_REAL run.
> 6. Release (lead/owner, not the card): `docker pull python@sha256:ddb0207a…fe74c` on CPX32; check
>    `docker version` >= 25.0.2; the api container needs the docker CLI + socket to start sibling
>    containers - THAT is a security decision of its own (the api would hold docker.sock = host root);
>    the alternative is a tiny host-side runner service the api calls over a unix socket. The binding
>    card must decide this in its own ADR before any prod compose change.

Wiring into the production compose is a **release decision**, not this card's.

## Evidence

PROVEN_AUTOMATED: 47 unit cases (argv exact list, refusals, negative argv scan, Dockerfile digest ==
DEFAULT_IMAGE, runner with fake subprocess); mutations M1-M4 (+M4b) RED, restored by sha256.
PROVEN_PROXY: 6 real-docker cases on the dev PC (Engine 28.3.2): network unreachable, read-only,
timeout + no container left, `print(2+2)` = 4, uid 65534 / CapEff 0 / NoNewPrivs 1, 400 MB under
128m = oom; real-docker mutations R1 (network) and R2 (container kill) RED.
NOT_RUN: CPX32 (release decision). PROVEN_REAL: only after the binding card, by the owner.

## Rollback

Revert the commit; nothing imports `app.compute` yet. `docker image rm python@sha256:ddb0…` on a host.

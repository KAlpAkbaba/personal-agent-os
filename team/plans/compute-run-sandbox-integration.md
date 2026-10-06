# compute-run-sandbox — integration plan (cycle d20261006, integrator)

Card: Bulutta yürütme PR 3 (`team/plans/pilot-01-split.md` row 6). Written 2026-10-06.
Nothing was added to any tree by this step. One image was pulled on the dev PC to measure.

## Decision: ADAPT (our own ~250-line package around the stock Docker CLI and an official image)

What exists in the world: hosted sandboxes (E2B, Modal, Daytona — paid SaaS, phone home, rejected:
provider + cost + data leaves the house), `epicbox` (MIT, 2019-era, unmaintained, docker-py dependency),
`llm-sandbox` (MIT, active, but pulls docker-py/kubernetes clients and a session model far wider than
one shot), `codejail` (AppArmor-only, Ubuntu-host-bound). None fits the seam better than ~250 lines over
`subprocess` + the `docker` CLI that the Cloud Core host already has. No Python dependency is added;
the `docker` CLI is called by absolute-free name and a missing CLI is the `docker_unavailable` receipt.
Adopt only the **recipe** (the flag set below) and the **official image**.

## Image (measured 2026-10-06 on the dev PC)

- `docker.io/library/python:3.12-slim` = `python:3.12.15-slim-trixie` (Debian trixie-slim base),
  multi-arch **index digest** `sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c`
  (amd64 manifest `sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe`, built
  2026-10-06T01:55Z, source docker-library/python@2a3b794c). CPX32 is amd64; pin the INDEX digest.
- Reference to use everywhere: `python@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c`.
- Size 43.3 MB (`docker image inspect .Size`), pull 8.5 s on the dev PC; `print(2+2)` cold run 0.89 s
  wall incl. container create/destroy. Image user is root (`Config.User` empty) -> `--user 65534:65534`
  is what makes it non-root; measured inside: uid 65534, `CapEff 0000000000000000`, `NoNewPrivs 1`.
- Licence: PSF-2.0 (CPython) + Debian packages (mixed DFSG; GPL parts are OS userland, run, not
  linked into our code, same as the api image). Docker CLI/Engine Apache-2.0. Phones home: the
  image does not; the only network event is the one-time `docker pull` at release time.
- The Dockerfile (C): `FROM python@sha256:<same digest>`, `ENV PYTHONDONTWRITEBYTECODE=1
  PYTHONUNBUFFERED=1`, `USER 65534:65534`, nothing installed. Runtime does NOT need to build it
  (the official digest + `--user` is the same thing); it exists so a release can mirror the image
  into GHCR. **Contract test:** the Dockerfile's FROM digest == `policy.DEFAULT_IMAGE` digest (one test
  reads the other's source — the two halves must not drift).

## What each flag stops (measured where marked M)

| flag | stops |
|---|---|
| `--network none` | every socket off the box: internet, tailnet (Core is on Tailscale), Hetzner metadata 169.254.169.254, the Postgres/Redis/Temporal containers. Only `lo` remains. M: `OSError: [Errno 101] Network is unreachable` |
| `--cap-drop ALL` | raw sockets, mount, chown, ptrace of others, setuid binaries' power. M: CapEff 0 |
| `--security-opt no-new-privileges` | setuid/setgid escalation inside (no regained caps). M: NoNewPrivs 1 |
| `--read-only` + `--tmpfs /tmp:size=64m` | writing the image layer, planting files for the next run, filling host disk. M: `Errno 30 Read-only file system: '/x'` |
| `--memory m --memory-swap m` | RAM exhaustion of the 8 GB host, swap thrash. M: 700 MB bytearray under 512m -> exit 137 |
| `--cpus 1` | starving api/Temporal of CPU (CPX32 = 4 vCPU) |
| `--pids-limit 128` | fork bombs, thread bombs |
| `--init` | zombie reaping; SIGTERM/SIGKILL reach the child under PID 1 |
| `--user 65534:65534` | root-in-container (no userns remap by default, so container root == host root uid) |
| `--rm` | leftover containers/fs between runs |
| no `-v/--mount/-e/--env-file/--privileged/--device/--pid host/--ipc host` | host files, secrets, docker.sock, host namespaces never enter |

Default seccomp (`docker info`: `name=seccomp,profile=builtin`) and, on the Ubuntu host, AppArmor
`docker-default` stay ON: the policy must never emit `--privileged` or `seccomp=unconfined`.

## Findings the card's text does not yet cover (worker: please add, all inside the area)

1. **Killing the client does not kill the container (M).** `timeout -s KILL 4 docker run ...` -> client
   exit 137, `docker ps` still showed the container `Up 5 seconds`. The runner MUST pass
   `--name pagentos-compute-<uuid4 hex>` and on the deadline call `docker kill <name>` (then reap the
   client). The integration test (c) must assert `docker ps -a --filter name=<name>` is empty after.
   `--name` is neither env nor mount, so the negative scan still holds.
2. **`--log-driver none`.** Without it the json-file driver also writes stdout to host disk while the
   run lasts (a 60 s `while True: print` = GBs before `--rm`). Verified output still streams with `-i`.
3. **Bounded reading.** `communicate(timeout=)` buffers everything in memory. Read stdout/stderr in
   chunks on two threads, keep the first 64 KiB, discard the rest (keep draining so the pipe never
   blocks), set `truncated`. Bytes, decoded `utf-8, errors="replace"` (Windows dev PC code page).
4. **stdin vs `python3 -`.** `python3 -` reads the CODE from stdin to EOF, so the program has no stdin
   of its own. Recommendation for v1: keep the card's argv exactly, and `ComputeRequest.stdin` must be
   `""` — anything else `refused` (tested). A later card may switch to a fixed `-c` bootstrap that reads a
   length-prefixed header; `-c <64 KiB code>` is NOT an option (Windows 32 767-char command line).
5. **137 is not only OOM.** It is any SIGKILL. Order: if the runner itself killed on the deadline ->
   `timeout`; else 137 -> `oom`. Note: a Python allocation that fails cleanly raises `MemoryError`
   (exit 1) -> `ok`-class with non-zero exit, not `oom`; the receipt carries `exit_code` for that.
6. Suggested extras (optional, cheap): `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m`,
   `--ulimit nofile=64:64`, `--stop-timeout 1`. If adopted, the ADR lists them and the full-argv test pins them.

## Known escape classes (why "hardened container is not enough for untrusted code")

Containers share the host kernel. Classes: kernel LPE reachable through allowed syscalls (Dirty Pipe
CVE-2022-0847, Dirty COW); runtime bugs (runc CVE-2019-5736 /proc/self/exe overwrite — needs root in
container; CVE-2024-21626 "Leaky Vessels" fd leak — fixed in runc 1.1.12, Docker 25.0.2+; dev PC runs
Engine 28.3.2, the CPX32 version must be checked at release); side channels (Spectre-class, accepted).
Mitigated, not removed: non-root + no caps + nnp + seccomp + no network shrink the reachable surface.
**Debt (owner's decision, NOT in this card):** gVisor `runsc` (Apache-2.0, google/gvisor) as
`--runtime runsc` — a userspace kernel, the real boundary for model-written code. Needs a host
package + `daemon.json` runtime entry on CPX32 and an owner yes (new dependency). Second option:
Docker `userns-remap`. Both are a release/host change, recorded in the ADR's debt list.

## Seam and files (all within the card's area)

`app/compute/policy.py` (ComputeRequest, limits, `build_argv`, ComputePolicyError) ·
`runner.py` (`run(request, *, popen=subprocess.Popen, kill=...) -> ComputeReceipt`, injectable for the
fake-subprocess tests) · `receipt.py` (dataclass + `to_detail_json()`) · `__init__.py` (single entry
`run_compute(...)`; ToolSpec/router wiring = binding card) · `infra/docker/sandbox/{Dockerfile,
compose.fragment.yml}` · two tests + ADR. Reads `app/execution/rule.py` (JobKind.COMPUTE -> CLOUD), changes nothing there.

## Tests to add (beyond the card's list)

- Dockerfile FROM digest == DEFAULT_IMAGE (contract halves).
- argv contains `--name pagentos-compute-…` and `--log-driver none`; never `--privileged`, `seccomp=`,
  `-v`, `--mount`, `-e`, `--env*`, `--device`, `host`.
- Integration (c): no container left after timeout. Integration (e, optional): 700 MB alloc -> `oom`.
- Non-empty stdin refused (if finding 4 is taken).

## Rollback

Package is unbound (no router, no ToolSpec, no ledger kind): rollback = revert the commit. The pulled
image on a host is removed with `docker image rm python@sha256:ddb0…`.

## THIRD_PARTY_COMPONENTS.md entry (draft; the worker/lead adds it)

```
## Compute sandbox image: python:3.12-slim by digest (2026-10-06, compute-run-sandbox)

- **Role:** the one-shot, network-less container `compute.run` executes model-written Python in
  (`app/compute`, `infra/docker/sandbox`). Run as uid 65534, no capabilities, read-only, no network.
- **Image:** `docker.io/library/python@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c`
  (3.12.15-slim-trixie, official Docker library image). 43 MB. Pinned by index digest; never `latest`.
- **Licences:** CPython PSF-2.0; Debian userland mixed DFSG (run, not linked). Docker CLI Apache-2.0.
- **Python dependencies added:** none (stdlib `subprocess` over the host's `docker` CLI).
- **Phones home:** no; one `docker pull` at release time. The container itself has no network.
- **Not a full boundary:** shares the host kernel; gVisor (`runsc`) is recorded debt, owner's decision.
```

## Worker: what was taken (2026-10-07, compute-run-sandbox)

Findings 1-6 all adopted: `--name` + `docker kill <name>` at the deadline (mutation R2 on real docker
left the container `Up 27 seconds`, so the integration test now proves the kill), `--log-driver none`,
chunked reading on two threads (first `output_bytes` kept, rest drained), `stdin` must be `""`,
137 -> `timeout` only when the runner killed it, else `oom`; tmpfs `rw,noexec,nosuid,nodev`,
`--ulimit nofile=64:64`. Not taken: `--stop-timeout 1` (the runner uses `docker kill`, never stop).
Added: `--pull never` (a run never contacts a registry; missing image = exit 125 = `docker_unavailable`).

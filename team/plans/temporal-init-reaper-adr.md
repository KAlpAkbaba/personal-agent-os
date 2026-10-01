## ADR (unnumbered) - The temporal container runs under Docker's init, which reaps what the image's entrypoint leaves behind (follow-up of ADR-0223 addendum 2)

**For the lead at merge - read first.** This is a COMPOSE change (ADR-0214 addendum 9): the
release step must not release it by itself; ask the owner. Two facts for that question:
(a) the blue/green release never applies it - every `compose up` in
`scripts/cloud/release-cloud-core-bluegreen.sh` is `--no-deps` on an api colour or the edge,
so after an ordinary release the running temporal container still has `HostConfig.Init` nil;
(b) applying it is one explicit step on the host, `compose up -d --no-deps --wait temporal`
against the released tree, which RECREATES the temporal container: Temporal is away for
about ten seconds (measured on the dev stack: healthy 9 s after `up`), workflows are durable
in Postgres and resume, workers reconnect. `scripts/cloud/deploy-cloud-core.sh` (the
non-blue/green path, line 114) would apply it as a side effect. A reboot does not apply it:
a restarted container keeps the configuration it was created with.

**The defect, as read in the image (`temporalio/auto-setup:1.27.2`).** `entrypoint.sh` runs
`auto-setup.sh` in the foreground; its last line is `setup_server &` (wait for the frontend,
register the default namespace, add the search attributes), after which `auto-setup.sh`
exits and the entrypoint `exec`s `temporal-server` as pid 1. The background subshell is an
orphan, re-parented to pid 1; it ends about two seconds after the server answers, and
`temporal-server` never calls wait: one defunct `auto-setup.sh` per container start, for the
life of the container.

**Decision.** `init: true` on the `temporal` service in `docker-compose.prod.yml` and
`docker-compose.dev.yml`, and nothing else in those files. Docker's `docker-init` (tini) is
pid 1, the entrypoint and then `temporal-server` are pid 7, the orphan is re-parented to
tini, and tini reaps it. tini forwards SIGTERM to its child, so `docker stop` should behave
as before (tini's documented behaviour; the stop time was not measured here).

**Not chosen.** An `entrypoint:` override that waits for the subshell (we would own a copy of
the image's script); `SKIP_DEFAULT_NAMESPACE_CREATION` + `SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES`
(the subshell still starts, and the default namespace is needed on a fresh volume).

**Limit of the cure.** tini reaps orphans only. A child that a living `temporal-server`
itself started and did not wait for would stay defunct; there is none today. The proof is
for this image and its own entrypoint, so `tests/unit/test_compose_init.py` also pins the
image tag and the absence of `entrypoint:`/`command:` on the service in both files: a new
image or an override is a new proof on the dev stack, not a silent change.

**Evidence.** PROVEN_AUTOMATED: `services/api/tests/unit/test_compose_init.py` (RED before
the change for both files, green after; mutations RED). PROVEN on the dev stack, real
Docker, 2026-10-01 22:30 UTC: before, `1 temporal-server` / `92 ppid 1 Z auto-setup.sh`,
`Init=<nil>`; after `up -d --no-deps temporal`, `1 docker-init` / `7 temporal-server`, no
defunct process, `Init=true`, `cluster health` SERVING, namespaces `default` and
`temporal-system` present. PROVEN_REAL only after the owner-approved step on the Cloud Core:
`ps -eo stat,comm` there shows no defunct process, and the maintenance script's `zombies`
check line reads 0.

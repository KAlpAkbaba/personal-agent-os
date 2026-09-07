# M18.4 — Continuous self-evolution, self-healing and the zero-downtime update foundation

Status: FOUNDATION CLOSED 2026-09-07 night (ADR-0081 and its two addenda; QUALIFICATION Stage 16). Decision record: ADR-0081.
Predecessors this builds on and does not replace: `EVOLUTION_ENGINE_SPEC` (M7 pipeline,
sandbox, review, registry), `RECOVERY_AND_SELF_HEALING.md` (M6 recovery supervisor,
incidents, last-known-good), ADR-0055 (the authority kernel: LAB vs PRODUCTION),
ADR-0059 (release executor, risk tiers, preflight), `docs/DEVELOPMENT_POLICY.md` (the
closed loop every request runs through), ADR-0080 (the Owner Utterance Corpus and the
first signal → opportunity path).

The owner's rule for this milestone, in one line: **the system finds its own defects and
opportunities from durable evidence, fixes them in isolation, proves the fix, and promotes
only as far as its authority allows — and every state it shows is a fact about rows.**

## 1. What exists already (the inventory the design starts from)

| Concern | Exists today | Where |
|---|---|---|
| Authority kernel | LAB scope for the engine, PRODUCTION only from an authenticated owner capability; `guard_production_action` inside `EvolutionService` | `app/evolution/authority.py`, `service.py` |
| Opportunity backlog | `EvolutionOpportunity` rows, unique `(source, source_ref)`, 24-status lifecycle with a legal-transition table, ledger rows per transition, UI states `evolution.*` | `app/evolution/backlog.py`, `models.py`, `lifecycle.py` |
| Signals → opportunities | `create_from_evidence` (verified evidence refs only); ADR-0080 opens one per voice-routing regression | `service.py`, `app/voice/qualification/service.py` |
| Self-coding in isolation | sandboxed workspaces, generator seam, generated tests + eval set, independent review, registry publish only after every gate | `app/evolution/pipeline.py`, `sandbox.py`, `review.py`, `registry.py` |
| Shadow / canary | `ShadowRunner`, `CanaryRunner` over eval cases | `app/evolution/rollout.py` |
| Risk tiers | derived from touched paths, never typed | `app/evolution/risk.py` |
| Release execution | QUALIFYING → DEPLOYING → VERIFYING → LIVE / FAILED → ROLLING_BACK → ROLLED_BACK; preflight incl. `migration_impact_known`; `FakeDeploymentBackend` only | `app/release/execution.py`, `preflight.py`, `backend.py` |
| Self-healing | incidents (fingerprint dedup), pipeline incident → reproduce → patch → review → build → staging → promote; recovery supervisor outside the app with `last_known_good`, rollback first, incident outbox | `app/selfhealing/*`, `services/recovery-supervisor` |
| Real self-healing proof | real processes: broken release injected → auto rollback → pipeline fix → staging → promote; bad candidate rejected | `tests/integration/test_selfhealing_e2e.py` |
| Cloud Core release | `release-cloud-core.sh`: tree swap `app → app.prev`, migrate, `--force-recreate` the ONE api container (a gap of seconds), health/provider/self-test gates, rollback to the previous tree and image | `scripts/cloud/release-cloud-core.sh` |
| Windows agent update | installer with a journaled staging step (`RetryFromStaging`), pinned binaries, `verify-device-service.ps1` | `scripts/install-device-service.ps1` |

What did NOT exist before M18.4: a supervisor that turns the OTHER durable signals into
opportunities on a clock; priority and promotion classes; a per-component version model
the owner can ask about; zero-downtime handoff for the Cloud Core; owner voice controls
over evolution; expand/contract enforcement for migrations; a measured availability
number; and the M18.4 acceptance list.

## 2. Components and the version model

Each deployable component has ONE current version the system can name, and says where the
name came from:

| Component | Version source | Reported by |
|---|---|---|
| `cloud-core` | `PAGENTOS_RELEASE` (the git sha the release script writes into the host env file; `unknown` when unset) plus the app's `__version__` and every contract version it serves | `/v1/system/health.release`, `GET /v1/release/current` |
| `windows-agent` | the device row's reported version and capabilities (`/v1/devices`) | `GET /v1/release/components` |
| `browser-worker` | the worker's `session_open` echo (profile, session_kind) when a session exists; otherwise "not observed" | `GET /v1/release/components` |
| `web` | the served `<meta name="pagentos-core-build">` marker — known to the browser, not to the API; reported as `unknown_from_server` | `GET /v1/release/components` |

`last_known_good` for the Cloud Core is the recovery supervisor's pointer on the host and
the `app.prev` tree; the API reports the value only when the release script exports it
(`PAGENTOS_LAST_KNOWN_GOOD`) and says `unknown` otherwise. Contract versions are part of
the version model because a deployed sha is not enough to know what the server speaks
(the 2026-09-03 422 and every "merged but not released" incident since).

## 3. The Evolution Supervisor

`app/evolution/supervisor.py` — a pure, clock-driven observer. It reads durable signals,
never a chat, and writes only opportunities (through `EvolutionService.create_from_evidence`,
so every one is evidence-verified and audited) and its own ledger rows.

### 3.1 Signals (durable reads only)

| Signal | Source rows | Opportunity `source` / `source_ref` | Priority |
|---|---|---|---|
| A self-healing incident recovered by rollback or still open | `incidents` (status `open` / `recovered`) | `incident` / incident id | **P0** |
| A release execution that failed or rolled back | `evolution_opportunities` in `failed` / `rolled_back` | annotated on the row (no new opportunity) | **P0** |
| A voice-routing regression | latest `voice.qualification` run failing (ADR-0080 already opened one per case) | `voice_corpus` / case id | **P1** |
| A recurring owner-facing action failure | `action.receipt` rows with `execution_status = failed`, same capability + error class ≥ 2 in 7 days | `ledger_event` / latest row id | **P1** |
| A recurring research failure | `research.failed` rows with the same error class ≥ 2 in 7 days | `ledger_event` / latest row id | **P2** |
| An open capability gap resolved as `generation` | `capability_gaps` | listed as a signal; the M7 pipeline owns it | **P3** |

Dedup is the backlog's own `(source, source_ref)` uniqueness: the same incident, case or
error class seen again is the same opportunity, its `recurrence` score raised, never a
second row.

### 3.2 Priority classes

- **P0** — availability or safety: an incident, a failed/rolled-back release, a security
  guard trip. Surfaced first; never auto-promoted (see §3.3).
- **P1** — the owner's own commands failing: routing regressions, failed action receipts.
- **P2** — a subsystem failing its job quietly: research, browser, media.
- **P3** — code health and capability gaps.

### 3.3 Promotion classes (derived, never chosen)

Derived from the risk tier of the paths the signal's component maps to
(`app/evolution/risk.py`, the same table the release path uses) — a signal cannot declare
its own class:

| Class | Tier | Meaning |
|---|---|---|
| `AUTO_SAFE` | 1 (UI/additive: `apps/web`, `docs`) | may be built, tested and shadowed without asking; promotion still goes through the release path |
| `AUTO_CANARY` | 2 (internal logic: the risk table's default - tests, corpus rows, generated skills, anything outside the Cloud Core app tree) | may reach canary on its own; LIVE needs the release path, which needs the owner |
| `OWNER_APPROVAL_REQUIRED` | 3 (production behaviour: EVERY Cloud Core app module - the router's vocabulary included, the alarms, the devices, the research pipeline, self-healing) | stops at `owner_approval_required` |
| `NEVER_AUTO_PROMOTE` | 4–5 (schema/deployment mechanics, identity/root/secret/authority, the recovery supervisor, this supervisor) | may be proposed; a human does every step past design |

The authority kernel is untouched: these classes narrow what the LAB may do, they never
widen it. The engine never rewrites its own authority, its risk table, or the supervisor
(the risk table already classifies those paths as tier 5).

### 3.4 The pause switch and the tick

- `Kendi kendini geliştirmeyi duraklat` / `... aç` write `evolution.paused` /
  `evolution.resumed` ledger rows; the latest row is the state; the supervisor's tick
  records nothing while paused and says so in its status.
- The tick runs on the `RoutineClock` (`evolution_tick`, every
  `evolution_supervisor_interval_s`, default 300 s), in-process, read-only except for the
  rows above. It is a scan, not a loop of its own: nothing here retries, sleeps or blocks.
- Health: `checks.evolution.supervisor = {enabled, paused, last_tick_at, opportunities_opened}`.

### 3.5 Status for the owner

`GET /v1/evolution/supervisor` and the voice questions below answer from the rows:
what is being built (opportunities in `building` / `testing` / `evaluating`), the last
incident fixed and by which release, pending candidates (`shadow_ready` +
`owner_approval_required`), the running version (§2), open opportunities by priority,
paused or not. No progress bar and no "improving" — a count and a state, each from rows.

## 4. Owner voice controls (through the ONE router, in the corpus)

| Utterance | Intent | Tool | Effect |
|---|---|---|---|
| `Kendi kendini geliştirmeyi duraklat.` | `EVOLUTION_PAUSE` | `evolution.control` | `evolution.paused` row; receipt |
| `Kendi kendini geliştirmeyi aç.` | `EVOLUTION_RESUME` | `evolution.control` | `evolution.resumed` row; receipt |
| `Bu geliştirmeyi iptal et.` | `EVOLUTION_CANCEL` | `evolution.control` | the ONE opportunity in a lab phase → `rejected` (reason `owner_cancelled`); more than one → a clarifying question that names them; none → a truthful refusal |
| `Bunu canlıya alma.` | `EVOLUTION_HOLD` | `evolution.control` | the candidate awaiting approval → `rejected` (reason `owner_held`); never a silent flag |
| `Bunu canlıya al.` | `DEPLOY` | `release.promote` | refused with the authority sentence (ADR-0055); the Approval Center is the surface |
| `Önceki sürüme dön.` | `RELEASE_ROLLBACK` | `release.rollback` | refused with the authority sentence, naming the last-known-good when known; a rollback is a production action |
| `Şu an ne geliştiriyorsun?` | `EXPLAIN` / `evolution_now` | `activity.explain` → `evolution.status` | the building/testing rows |
| `Son hangi hatayı düzelttin?` | `EXPLAIN` / `last_fix` | same | the last incident with a `fixed` status and its release |
| `Hangi sürüm çalışıyor?` | `EXPLAIN` / `running_version` | same | §2 |
| `Bekleyen aday sürüm var mı?` | `EXPLAIN` / `pending_candidates` | same | shadow-ready + approval-required counts and titles |

The action the tool applies is the one the router derived from the owner's words
(recorded on the turn, ADR-0079 §7 / ADR-0080), never the model's argument. Every phrase,
its paraphrases and its ASR-noise variants are corpus cases (category `evolution`).

## 5. Self-coding isolation and provenance

Unchanged from M7 and the closed-loop policy: an opportunity is worked in an isolated
worktree/sandbox, the candidate carries `workspace_ref`, `candidate_ref`, the generator's
identity, the tests it ran and the reviewer's verdict; the registry publishes only after
every gate; a rejected candidate never touches production dispatch. The supervisor adds
the `signal` (kind, source row ids, first/last seen) to the opportunity's detail so the
provenance chain starts at the evidence.

## 6. Zero-downtime Cloud Core: blue/green with a drain

Today's release recreates the one `api` container: every request during the recreate
fails, every device WebSocket drops, and a voice session's tool call in that window is
lost. The foundation replaces it with two colours behind an edge:

```
tailnet:8001 ──> edge (nginx) ──> api-blue   (active)
                            └──> api-green  (idle / next)
```

- `infra/docker/docker-compose.prod.yml` gains `api-blue`, `api-green` (same definition,
  no published port) and `edge` (nginx, the ONE published socket, WebSocket upgrade,
  long read timeouts for the device channel). The active colour is a one-line upstream
  file the script rewrites and `nginx -s reload`s — a reload finishes in-flight requests
  on the old upstream and opens new ones on the new one.
- `scripts/cloud/release-cloud-core.sh --bluegreen`: build the new tree's image; run
  migrations (expand-only, §10); start the idle colour from the new image; wait for
  `/v1/system/health` AND one real self-test on that colour; switch the upstream; **drain**
  the old colour (keep it running for `PAGENTOS_DRAIN_S`, default 60 s, so in-flight
  requests, device reconnects and a voice session's next tool call land on the new
  colour without a gap); stop the old colour; record `RELEASE`, `LAST_KNOWN_GOOD`,
  `ACTIVE_COLOUR`. Rollback is the switch in reverse while the old colour still runs; after
  the drain it is a start of the old image plus the switch.
- Voice session continuity: session state is in Postgres (session rows, tool calls,
  `last_utterance`, focus), the realtime media path is client ↔ provider, so a session
  survives a colour switch by construction; the drain guarantees the tool call in flight
  is answered by the colour that received it. "Keep the old version for an active
  session" is therefore the drain window, sized to the longest tool call (a research start
  is asynchronous and durable; nothing waits longer than the drain).
- Device WebSockets reconnect on their own (Stage 5 proved reconnect after a Cloud Core
  restart); through the edge they reconnect to the new colour inside the drain window.
- Proof: the switch/drain/rollback logic is exercised with a fake `docker`/`curl`/`nginx`
  under the existing PowerShell+Git Bash release test discipline (`PROVEN_PROXY`); the
  first real blue/green handoff happens inside the next owner-authorised release and is
  `NOT_YET_PROVEN` until then.

## 7. Windows agent: staged update with rollback (built 2026-09-07 night; PROVEN_PROXY)

The installer stages the new trees (publish into `.staging`), swaps through the journaled
engine (stop by PID, same-volume renames, ACL, restart, health, commit / rollback in
`finally`) and proves the live browser worker. The gap closure (ADR-0081 addendum 3) adds
what the owner's list named, in `scripts/lib/AgentUpdate.ps1`:

- **the candidate manifest** — written at staging: the staged service binary's own
  `capabilities` verb (which names `software_version` from 0.2.0 on), every staged file
  hashed, the browser worker's release identity (`worker_version`, `package_sha256`); it is
  re-verified file by file immediately before the engine moves anything
  (`Test-AgentCandidateManifest`: a changed, missing or extra file, a missing version, a
  missing required capability, a changed browser package are each refused by name, the
  previous install untouched);
- **heartbeat and capability verification on Cloud Core** — `Test-AgentHeartbeatOnCore`
  runs INSIDE the engine's `TestHealth` handler: Cloud Core must list the device online
  with the candidate's software version and every capability the manifest promised
  (`GET /v1/devices` with the owner session from the DPAPI credential); otherwise the
  engine rolls back to the previous trees. No stored credential → the read is reported
  skipped, never faked (`-SkipCoreVerify` opts out explicitly);
- **retire old** — the engine's commit deletes `.previous\<version>` only after health.

Every agent update is still an elevated owner action (one UAC prompt) and stays one; the
owner's session is preserved (the companion is restarted through its logon task in the
owner's session, never in the elevated installer's). Proven under fakes:
`scripts/tests/agent-update.tests.ps1` (16); the real run is the owner's next elevated
update (item 26 / 27) — `PROVEN_PROXY` until then. The "service supervises the companion
as versioned releases" half remains future work, not claimed.

## 8. Browser worker staged update (built 2026-09-07 night; PROVEN_PROXY)

`BrowserWorkerHost.SwapWorkerAsync(candidate, drainTimeout, expectedVersion,
expectedPackageSha256)`: a candidate worker starts BESIDE the current one and must say
hello, keep every capability the current worker serves (and browser availability), and
match the expected release when one is named; then the current worker drains — its
in-flight requests finish on it, a request arriving during the drain waits on the start
lock (bounded by its own budget) and lands on the candidate, and it must hold no open
browser session (`browser.worker_status`; owned media stays untouched: the swap answers
`busy` and is retried later) — is told to shut down (its sessions close the way a companion
stop closes them) and retires; then new work routes to the candidate. The candidate is
never handed a request while the old worker lives (one research profile, one Chrome); the
owner's own browser is never touched. A failing or rejected candidate is killed and the
current worker keeps serving, untouched. Trigger: `browser-candidate.json` in the
companion's data directory (`BrowserCandidateWatcher`, polled every 5 s) — the candidate
command must live under the admin-only install root; the outcome is written beside it as
`browser-candidate.result.json` and audited (`browser_worker_swapped` /
`browser_worker_swap_failed` / `browser_worker_candidate`). Proven over the real fake
worker: `BrowserWorkerSwapTests` (12). A real second tree under Program Files needs the
owner's elevated hand — `PROVEN_PROXY` until then. A research in flight remains a durable
Temporal workflow and resumes on the new worker (unchanged).

## 9. Web live update

The web shell is static; a new build changes the served marker; an open Core notices the
marker change on its next poll and shows "yeni sürüm hazır — yenile" rather than
reloading itself under a live voice session. Design only in M18.4; `NOT_YET_PROVEN`.

## 10. Migrations: expand/contract and compatibility gates

- Every migration's `upgrade()` is **expand-only**: add tables, add nullable columns, add
  indexes, backfill. `drop_*`, `rename_*`, narrowing `alter_column` are refused by a
  structural test unless the module declares `contract-phase: ADR-XXXX` — a contract
  migration ships only after the release that stopped reading the old shape is LIVE.
- The old colour must run against the expanded schema during the drain: that is what
  expand-only buys. Preflight's `migration_impact_known` remains the gate for the release
  path; the structural test is the gate for the tree.
- One alembic head, always (existing test).

## 11. Shadow and canary

Unchanged (`ShadowRunner`, `CanaryRunner`): a candidate's eval set runs beside the current
version; `AUTO_CANARY` candidates may reach canary on the supervisor's authority; nothing
reaches LIVE without the release path.

## 12. Automatic rollback and last-known-good

- Host level: the recovery supervisor (outside the app) polls health + self-test and rolls
  back to `last_known_good` before it reports (`test_selfhealing_e2e.py`, real processes).
- Release level: the blue/green script rolls back by switching the upstream; the old image
  is never deleted by a release.
- Data level: expand-only migrations mean a rollback never needs a downgrade.
- `last_known_good` is immutable to the app: only the host-side promote writes it.

## 13. Availability, measured

`app/release/slo.py` keeps a rolling window of the supervisor tick's own health probes and
of `deployment.*` ledger rows; `GET /v1/release/slo` reports the measured fraction of
successful probes and the count and total duration of release windows in the last 24 h /
7 d. Until probes have run, it says "no measurement" — never a target dressed as a result.
Foundation only in M18.4: the ledger half is implemented; the probe half runs with the
supervisor tick.

## 14. Crash and boot recovery

Unchanged and already proven (Stage 5): VPS reboot, Cloud Core restart, device reconnect.
Blue/green (ADR-0081 addendum 3): the edge's whole configuration (`nginx.conf` copy,
`upstream.conf`, `active.txt`) lives on the persistent edge directory, and containers carry
`restart: unless-stopped`, so a reboot brings the same colour back. A promotion interrupted
at any point (the script SIGKILLed, the host lost) is rebuilt by
`release-cloud-core-bluegreen.sh --reconcile`: the last COMPLETED promotion (`RELEASE`)
is canonical even when the edge already names the candidate; a colour running a sha
`RELEASE` does not name is a half-promoted candidate — drained, stopped, its tree kept
aside as `app.interrupted` — and never made live by this path; a canonical colour that
cannot start falls back to the other recorded colour LOUDLY (exit 81). The unit
`infra/systemd/pagentos-bluegreen-reconcile.service` runs it once per boot after docker.
`PAGENTOS_INTERRUPT_AT=after_idle_up|after_device_handoff|after_switch|after_drain` is
the controlled crash for the proof.

## 15. Owner-facing states (real states only)

- UiState `evolution.*` (researching / designing / building / testing / shadow_ready) are
  published by the lifecycle, as today; the supervisor publishes nothing on its own.
- The Cockpit's "Evrim" panel shows priority and promotion class per opportunity and a
  "duraklatıldı" badge; a new "Evrim gözetmeni" row set shows the last tick, the paused
  state, open counts by priority and the running version.
- No progress bar, no estimate, no "improving".

## 16. The evolution ledger

Existing event types (`evolution.idea_created` … `evolution.rolled_back`) plus
`evolution.paused` / `evolution.resumed` and `evolution.supervisor_scanned` (one per tick
that opened at least one opportunity; a quiet tick writes nothing).

## 17. Automated acceptance (the directive's §31)

Every row below is a deterministic test or a structural guard; see
`docs/ACCEPTANCE_TESTS.md` "M18.4" and `docs/QUALIFICATION.md` Stage 16.

1. Signals become opportunities, deduplicated, with priority and promotion class derived —
   `test_evolution_supervisor.py`.
2. The supervisor never creates an opportunity while paused; pause/resume are ledger rows.
3. The supervisor holds no production grant and cannot advance a candidate past the lab
   (structural: authority scope asserted).
4. Owner voice controls route through the ONE router and act through receipts — corpus
   category `evolution` (`test_owner_utterance_corpus.py`).
5. "Which version is running" answers from the version model, contracts included.
6. Migrations are expand-only unless marked — `test_migration_compatibility.py`.
7. Blue/green switch → drain → rollback under fakes — `cloud-release.tests.ps1`.
8. The real self-healing story (inject broken release → auto rollback → fix → staging →
   promote; bad candidate rejected) — `test_selfhealing_e2e.py` (CI integration job).
9. Availability is a measurement or "no measurement".

## 18. The synthetic self-healing proof (the directive's §32)

`test_selfhealing_e2e.py` is exactly that proof and already runs in CI: a harmless,
deterministic fault (`/selftest` returning failure on release 1.1.0), automatic rollback to
1.0.0, an incident with a fingerprint, the pipeline producing 1.1.1 with a regression test
that fails on 1.1.0 and passes on 1.1.1, staging, promotion, and a second, deliberately
broken candidate that is rolled back on staging and rejected with production untouched.
M18.4 adds the supervisor's opportunity for the incident it produced.

## 19. Exit criteria (the directive's §34) and their marks

| Criterion | Mark | Evidence |
|---|---|---|
| Signals → deduplicated opportunities with priority/promotion class, on a clock, pausable | `PROVEN_REAL` (two opportunities from production ledger evidence; pause/resume on production) | run 3 phase E; `test_evolution_supervisor.py` |
| Owner voice controls and questions | `PROVEN_AUTOMATED` | corpus category `evolution` |
| Version model per component | `PROVEN_REAL` (production health names the sha, contracts and last-known-good; the agent as its row reports; the web as unknown) | run 3 phase F; `test_release_version.py` |
| Expand/contract migration gate | `PROVEN_PROXY` (structural, whole tree) | `test_migration_compatibility.py` |
| Zero-downtime blue/green handoff with drain and rollback | `PROVEN_REAL` (three production runs 2026-09-07: first cutover 4.1 s once; then 247/0, 234/1 build-time; rollbacks 92/0, 37/0, 25/0, 113/0, 99/0) | `docs/evidence/m18-4-qualification-2026-09-07-*.json`; `cloud-release-bluegreen.tests.ps1` |
| Automatic rollback from a broken release, real processes | `PROVEN_PROXY` (real processes, loopback, CI) | `test_selfhealing_e2e.py` |
| Windows agent staged update with rollback (candidate manifest verified before the swap; Cloud Core sees the candidate or the engine rolls back) | `PROVEN_PROXY` (§7; the real run is the owner's elevated update) | `agent-update.tests.ps1` 16/16; `installer-*.tests.ps1` |
| Browser worker staged update (drain, verified candidate, routing, retire; busy on an open session) | `PROVEN_PROXY` (§8; a real second tree needs the owner's elevated hand) | `BrowserWorkerSwapTests` 12/12 |
| Web live update | `NOT_YET_PROVEN` (design §9) | — |
| Availability measured | `PROVEN_PROXY` for the ledger half; probes `NOT_YET_PROVEN` | `test_release_slo.py` |
| Engine never self-promotes; supervisor outside the component it updates | `PROVEN_PROXY` (structural) | authority tests + supervisor scope test |

## 20. Non-goals and named gaps

No LLM-driven code change is produced by M18.4 itself (the generator seam and the coding
backends are unchanged); no real deployment is performed by any test; no autonomous
production promotion exists or is planned (ADR-0055 holds); the M19 Digital Operator waits
for this foundation.

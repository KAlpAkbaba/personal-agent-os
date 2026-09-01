# Roadmap

## M-1 — Environment & Bootstrap

Goal: make the development machine reproducible and report capabilities.

Deliverables:

- environment report;
- Git hygiene;
- toolchain;
- dev scripts;
- initial lock/version strategy.

## M0 — Foundation

Goal: compile/run a minimal cloud-core stack locally.

Deliverables:

- repo structure;
- web shell;
- FastAPI API;
- PostgreSQL/pgvector;
- Redis;
- MinIO dev S3;
- Temporal dev;
- logging/telemetry skeleton;
- CI;
- quality gate.

## M1 — Cloud / Windows Device Link

Goal: owner UI can execute a safe Windows action through cloud broker.

Deliverables:

- device service + session companion;
- enrollment;
- outbound WSS;
- command protocol;
- reconnect/idempotency;
- Notepad E2E.

M1 is delivered local-first: broker and agent run and are acceptance-tested entirely on the development machine (loopback transport). Tailscale/Hetzner provisioning is a deployment step that reuses the same protocol and is unblocked separately by the owner actions list.

## M2 — Browser Agent

Goal: reliable semantic browser automation.

Deliverables:

- Playwright adapter;
- MCP integration in development;
- browser extension/CDP path;
- browser error taxonomy;
- test browser E2E.

## M3 — Research & Artifact

Goal: research command produces durable report and can open/send it.

Deliverables:

- Research workflow;
- source/evidence model;
- artifact service;
- PDF/DOCX render;
- Artifact Inbox UI;
- Windows open artifact.

## M4 — Voice Core & Narration

Goal: natural Turkish voice interface.

Substages:

- M4A STT/provider benchmark;
- M4B owner speaker verification;
- M4C realtime dialogue;
- M4D Turkish narration engine;
- M4E cross-device narration cursor.

Delivered local-first (ADR-0022): the deterministic core — tr-TR normalizer, narration engine + cursor + command state machine, provider-neutral STT/TTS/realtime interfaces with fakes and HTTP-mocked real adapters, benchmark harness (≥2 providers), OWNER/NOT_OWNER/UNCERTAIN classifier — is built and gated with no owner action. The real-audio quality A/B, real speaker enrollment, and low-latency realtime audio require owner-provisioned provider keys and owner speech samples, which slot into the same interfaces.

## M5 — Memory

Goal: durable owner/project/procedural memory.

Delivered as a first-class subsystem (ADR-0023): six memory classes, write
policy (ignore/session/candidate/durable with explicit-owner authority),
provenance + confidence + evidence, version history, supersede/edit/forget
(hard delete incl. vector rows), pgvector + structured + hybrid retrieval,
entity/project graph, deterministic seeded retrieval evaluation with a
zero-cross-project-contamination gate, and a MemoryBackend abstraction so an
external engine (e.g. Mem0) can plug in without owning the canonical data.

## M6 — Self-Healing Engineering

Goal: detect an injected bug, restore service, generate and validate a fix automatically.

Delivered (ADR-0024): stdlib-only Recovery Supervisor with versioned release
pointers and auto-rollback; fingerprinted incident ingest; deterministic
self-healing pipeline (reproduce → regression test → patch → review gate →
staging/canary → promote/reject) behind a CodingBackend seam whose real
Claude Agent SDK backend plugs in without pipeline changes.

## M7 — Self-Extension/Evolution

Goal: add a genuinely missing capability and resume the original task automatically.

Delivered (ADR-0025): capability registry with code-enforced registration
gates, auditable gap-decision trail (composition attempted before any code
generation), generated skills in the standard layout behind a SkillGenerator
seam, evaluation that actually runs generated tests/evals, independent review
(no self-approval), and original-task resumption — with guard-tested
boundaries: no owner-explicit-memory mutation, no core/recovery targets, and
strict token validation on everything reaching generated source.

## M8 — Authorized Security Agent

Goal: owner-authorized asset scope and autonomous defensive testing/remediation workflow.

Delivered (ADR-0026): the Authorized Asset Registry as the single scope
authority with fail-safe, spoofing-resistant target matching and append-only
authorization events; scope-based (not per-command) approval for in-scope
defensive assessments; constraint-gated remediation; findings published as
ordinary artifacts with redacted evidence; and the registry-backed
AuthorizationProvider that closes the M7 evolution permission-verification gap.

## M9 — Native Mobile

Goal: stronger always-available mobile voice experience beyond PWA limitations.

Delivered (ADR-0027): the owner identity layer this milestone needed and every
earlier one deferred — an opaque bearer session minted from a file-backed owner
credential root that lives outside the database (so a restored database neither
resurrects nor destroys the owner's ability to authenticate), with host-side
recovery, TTL and idle timeout, refresh rotation, a panic control that revokes
everything, and a token-free append-only audit. Every M4–M8 endpoint is now
authenticated, which closes the standing hard gate that had been carried since
M0. On top of it: device-bound sessions that die with the device, push
registration and an artifact-ready announcer that delivers before it records
delivery, narration cursor resume, file share/export, the web shell's owner
sign-in, and a headless reference client that exercises the whole mobile
contract in CI.

Not delivered locally, and deliberately: a real mobile application. The device
half of "microphone/realtime voice under normal mobile lifecycle" and
"push notification arrives on the phone" is verified through the reference
client against the real API, which proves the server contract but not the
platform behaviour. Both are batched as owner actions requiring a physical
device.

## RQ-1 — Real-environment qualification: local Windows (CLOSED 2026-09-01)

The turn from fixtures to the owner's machine. Everything M-1…M9 proved against fakes was
re-earned against the real OS: the Windows Service installed and Running as LocalSystem in
Session 0, the companion in the owner's Session 1, the live pipe's DACL read from the
actual runtime handle, kernel-sourced companion admission, hardened install tree, zero
inbound listeners, device enrollment against a dedicated production database, and the full
command path — broker → Session-0 service → companion → real Notepad → ACK — surviving both
a DeviceService restart and a Cloud Core restart unattended. The final owner credential was
rotated host-side and shown exactly once. Nine real-machine defects were found (all in code
paths tests did not execute) and each is logged in `docs/QUALIFICATION.md` with the
regression that now covers it. Evidence rules and remaining deliberately-open items live in
that document; the qualified runtime is frozen.

## RQ-2 — Real-environment qualification: cloud bring-up (CURRENT)

Goal: the same proof with the Cloud Core on real infrastructure. Hetzner NBG1 host
provisioned from `infra/opentofu` with **no public application port**; Tailscale connecting
the Windows device and the cloud host; Cloud Core deployed on the production database
model; the Windows agent switched from loopback to the tailnet endpoint **without
reinstalling or re-enrolling**; then

`Hetzner Cloud Core -> Tailscale -> Windows DeviceService -> Session Companion -> real Notepad -> ACK`

followed by the resilience matrix: cloud process restart, VPS reboot, Tailscale reconnect,
temporary network loss, Windows service restart — all recovering without owner
intervention, with Windows inbound public ports staying closed. Cloud criteria are marked
`PROVEN_REAL` only from the actual Hetzner/Tailscale environment. Owner dependencies
(batched, one at a time): `gh auth login`, Tailscale sign-in/auth key, Hetzner API token.

## M10 — Optimization

Gated on RQ-2: no speculative optimization or new feature development until real cloud
qualification completes. Only after real use:

- local voice fallback;
- local models;
- additional devices;
- HA cloud;
- advanced screen-history learning;
- proactive workflow automation.

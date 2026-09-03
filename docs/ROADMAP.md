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

## RQ-2 — Real-environment qualification: cloud bring-up (CLOSED 2026-09-02)

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

## The product phase (from 2026-09-02)

Infrastructure engineering is done and frozen as a proven baseline. What follows is the
Personal Agent OS *experience*, whose primary interface is voice — not a dashboard.
Acceptance for every milestone below is REAL only: the owner's real Windows machine,
microphone, speakers/headset, Turkish speech, network and the real Hetzner Cloud Core;
real Chrome and live Internet sources for research; a physical phone for mobile.
Mocks and fixtures remain gates, never `PROVEN_REAL`.

## M12 — Realtime Voice Foundation (owner-blocked: evening K66 re-qualification pending)

Goal: ChatGPT-Voice-class conversational interaction, as close as publicly available APIs
and this architecture allow — behavioural and perceptual parity, never a claim of an
identical backend. A traditional STT → text LLM → TTS pipeline is NOT sufficient for the
primary conversation path: it uses the best available native realtime speech-to-speech,
full-duplex-capable provider behind a capability-driven abstraction (ADR-0034), so a
superior model can replace it without redesigning the OS. OpenAI Realtime is the primary
candidate where it gives the best experience; nothing is hardcoded to one model name.

Shape: owner microphone ↔ realtime WebRTC/audio transport ↔ Realtime Voice Provider ↔
sideband tool channel ↔ Hetzner Cloud Core ↔ Memory / Browser / Research / Windows Agent /
Artifacts / Evolution. Raw audio does not detour through Hetzner when a secure direct
media path is lower-latency; Hetzner stays the authoritative orchestration, tool and
memory brain over the sideband channel.

Explicit modes, not one pipeline: `ConversationRealtime`, `Narration`, `Transcription`,
`VoiceIdentity` (never a sole root of authentication; augments device trust + owner
session). Quantitative latency benchmarks: mic → uplink, end-of-turn → first audible
response, barge-in → playback stopped, tool-call preamble, tool completion → resumed
speech; targets under ~150 ms barge-in-to-stop and ~500–700 ms short-turn first response
where technically achievable — PersonalAgentOS targets, not claims about anyone else.
Long-running tools speak a short natural preamble and keep the session alive; the owner
can redirect mid-task without a disconnected conversation. Gap analysis in
`docs/VOICE_GAP_ANALYSIS.md`, specification in `docs/M12_REALTIME_VOICE_SPEC.md`,
acceptance in `docs/ACCEPTANCE_TESTS.md` (M12).

Status 2026-09-02 — **foundation built and gated offline; nothing PROVEN_REAL yet.** All
five tracks are on `main` (ADR-0036 session service + simulator + benchmark harness +
Turkish intents; ADR-0038 OpenAI Realtime adapter, key-gated, simulator barred outside
dev; ADR-0037/0039 Windows companion audio client speaking the server contract, with the
`voice_sideband` frame riding the device protocol additively; ADR-0040 web WebRTC client;
ADR-0041 the owner credential path). Independent security + verification reviews closed
(one High, one Medium and a scrubber bypass fixed with regressions). `docs/QUALIFICATION.md`
Stage 6 pre-registers 14 criteria, all `NOT_YET_PROVEN`: the next two steps are the owner's
provider credential (`docs/OWNER_ACTIONS.md` item 6) and then the real-microphone Turkish
session on the owner's PC, which is the only thing that can move those rows.

## M13 — Real Browser + Research

The owner's actual use case, end to end and real: voice/text request → Hetzner planner →
research plan → Tailscale → real Windows Browser Agent → real Chrome → multiple live
sources → evidence extraction → deduplication/ranking → synthesis → executive summary →
expandable detail → durable artifact → memory → voice presentation. Semantic
DOM/accessibility/browser APIs first; coordinates only as a last resort; the owner's
existing Chrome session only where explicitly authorised. Every fact keeps provenance and
is labelled source fact / model inference / recommendation / uncertainty. Proceeds in
parallel with M12 where foundations are independent.

**CURRENT from 2026-09-03** (the voice rerun is owner-blocked until the evening; the voice
implementation and evidence are frozen as they stand). Design fixed in ADR-0050 with two
binding contracts: `packages/protocol/BROWSER_CAPABILITIES.md` (fine-grained `browser.*`
device commands, risk classes, the website-error vs browser-error split, the untrusted
content boundary, the companion ↔ worker stdio protocol) and `docs/M13_RESEARCH_SPEC.md`
(plan/discovery/evidence/report shapes, REST, durable workflow and recovery matrix,
synthesis providers, memory policy, presentation). Built as four parallel tracks: the Session
Companion's browser worker host + installer provisioning, the `services/browser` worker and
operations, Cloud Core's device layer + real research pipeline (embedded Temporal worker),
and the web `/research` surface. Real acceptance (`docs/ACCEPTANCE_TESTS.md` M13,
`docs/QUALIFICATION.md` Stage 9) needs one owner action — the Windows agent installer update
(UAC) — and then the first real research run on "Son üç gündeki yapay zekâ ajanlarıyla ilgili
önemli gelişmeleri araştır." Fixtures remain gates, never `PROVEN_REAL`.

Status 2026-09-03 evening — **built, independently reviewed, `PROVEN_PROXY` on the local real
chain; waiting on two owner actions.** All four tracks merged; test-engineering and security
reviews closed (one Critical, two Highs and the Mediums fixed with regressions; ADR-0050
addendum). `scripts/e2e-m13-research.ps1` passes end to end with real Chrome and the live
Internet: discovery through feeds/Hacker News/arXiv and three search engines, 12 sources
fetched through Chrome, a labelled report, PDF/DOCX artifact, memory entry, and a Cloud Core
restart mid-job that resumed without duplicate evidence. `docs/OWNER_ACTIONS.md` item 10:
release the Cloud Core, update the agent once, run the first real research.

## M14 — Voice + Browser/Research integration

"Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır" spoken, answered
with a natural progress preamble, researched through Cloud Core while the voice session
stays alive, redirectable mid-flight ("Sadece OpenAI kısmına bak"), and presented in the
executive-assistant structure.

## M15 — Mobile/Web continuous session

One owner session across Windows desktop, browser and phone: research asked from the
phone, planned on Hetzner, executed by the authorised Windows Browser Agent, artifact
generated; "Oku" from the phone narrates on the active phone session; "Gönder" returns the
PDF/DOCX through the active client; "Aç" on the desktop opens it through the qualified
Windows Agent.

## M16 — Executive Assistant + Personal Memory

Default structure: Executive Summary → Why it matters → Recommended action → Details on
demand. Memory distinguishes owner facts, preferences, project facts, procedures, episodic
history, temporary conversation state and inferred preferences; explicit owner memory
outranks inference; correction, deletion, provenance and confidence are first-class;
explicit preferences are never silently rewritten from behaviour.

## M17 — Long-document narration

"Oku" / "Dur" (persist the exact cursor) / "Devam" (resume from it) / "İkinci maddeyi
tekrar oku" (semantic navigation, not audio replay); the cursor holds artifact, section,
paragraph/chunk, position and presentation context; never pre-synthesises an 80-page
document.

## M18 — Production Self-Evolution

The original goal, in production: a genuinely missing capability is detected, said
naturally ("Bunun için araştırma yeteneğine ihtiyacım var. Modülü hazırlıyorum."), then
gap detection → specification → isolated worktree/sandbox → tests → security review →
independent reviewer → shadow → canary → promotion → registry → retry of the original
request. Never a hot edit of the running core; the proven recovery/security roots stay
protected.

## M19 — Multi-device / roaming owner qualification

Owner requirement recorded 2026-09-03 (PROJECT_CONSTITUTION §11a, ADR-0049): the K66 work is
a per-device audio-quality qualification; the product must be usable and centrally managed
from any owner-authorised device, with Cloud Core as the authoritative control plane.

Scope: device inventory on Cloud Core (online/offline, agent version, capabilities, last
seen, health); centrally managed DeviceService/Companion configuration, policies and
capability advertisement (browser, desktop control, microphone, speakers, GPU, filesystem,
integrations); device selection by explicit target ("ev bilgisayarımda aç", "iş
bilgisayarımda çalıştır", "laptopta devam et") or by presence + capability + policy; roaming
of conversation, memory, tasks, research state and preferences; voice sessions moving between
desktop, laptop, web and phone under one identity; per-device microphone profiles with
automatic calibration of a new microphone, never cross-contaminating; centrally coordinated
agent rollout, health check, rollback and version inventory; per-machine key material with
central removal of a lost device without rotating the owner identity; reconnect after
reboot/network loss without owner intervention; no authority from tailnet reachability alone.

Real acceptance (all on real machines, none of it from fakes): PC-A → owner conversation →
continue from PC-B → command PC-A from PC-B → PC-A offline → Cloud Core detects it → PC-B
selected → PC-A returns → reconnects automatically → shared memory/state unchanged; plus
per-device microphone profiles proven with at least two different physical audio devices.
Not started; recorded so current work cannot make the system single-machine.

## M10 — Optimization (deferred behind the product phase)

Gated on real use of M12+: no speculative optimization or new feature development until real cloud
qualification completes. Only after real use:

- local voice fallback;
- local models;
- additional devices;
- HA cloud;
- advanced screen-history learning;
- proactive workflow automation.

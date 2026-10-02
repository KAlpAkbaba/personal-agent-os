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

## M16 (re-sequenced 2026-09-04) — Activity Ledger + Self Explanation + Voice Narration

After the Research Engine closed PROVEN_REAL, the owner re-sequenced the product phase:
Research → **Activity Ledger + Self Explanation + Voice Narration** → Memory → Cognitive
Core → Self Model → Evolution Engine. This milestone (spec `docs/M16_ACTIVITY_LEDGER_SPEC.md`,
ADR-0051) delivers one durable structured activity stream for every subsystem, an
evidence-first answer to "Son yaptıklarını anlat" / "Ne başarısız oldu?" / "Kanıtı ne?",
three narration levels over the existing realtime transport with "dur" / "devam et" at the
same semantic point, and a proactive briefing policy. The "Executive Assistant + Personal
Memory" milestone below keeps its content and follows as Memory. `state/BUILD_STATE.json`
names milestones by content, not by this list's numbers.

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

## Owner's queue, spoken 2026-09-10/11 (in this order)

Recorded verbatim in intent because a list only I hold is a list that stops existing when a
session ends. Each line says what "done" means, so none of them can be reported finished on
a demonstration.

1. **The system's own findings reach the owner out loud.** `pending_briefings` fills and
   nothing delivers: fourteen rows on 2026-09-10, every one undelivered and every one
   expired. Done = a briefing spoken into a live session or pushed to the phone, and the row
   marked delivered by the thing that actually delivered it. (Named as the missing half in
   ADR-0110; still missing.)

   **Half done, 2026-09-11 (ADR-0114 + its amendments, `667b9fb` in production).** The
   deliverer exists, runs on the app's own lifespan, and the sentence now actually reaches a
   client's buffer: production holds one `say` frame carrying its three briefing receipts,
   on the `web` session rather than the `cli` one, with five `already_queued` refusals
   behind it proving no second copy is ever added. The row is stamped by the drain — by the
   thing that delivered it — and not by the queue.

   **What is still missing, and it is architectural.** A `web` session is pull-only. The
   shell drains `pending_sideband` on a re-attach or when the owner speaks, never on a
   timer, and the only server-initiated channel in this system is the device broker's
   WebSocket, which a browser session does not have. So a briefing waits until the owner
   next talks — which is not "reaches the owner out loud" when they are asleep and the row
   expires first. Closing this needs a real push to the browser (SSE or a WebSocket for
   realtime sessions), or the phone push path, and neither exists yet. Until one does, this
   item is honest but not finished.
2. **"Güldür Güldür aç." without saying "YouTube'dan".** The ADR-0112 matcher requires an
   explicit media marker, deliberately, and it is too narrow: "video", "şov", "dizi", "film"
   are missing, and a bare title with a play verb is not reachable at all. Done = the owner's
   natural phrasing routes, with corpus cases, and "haberleri aç" / "Chrome'u aç" /
   "ekranı aç" still route where they always did.

   **Done 2026-09-11 (ADR-0117).** Both halves: the marker list gained `video`/`şov`/`dizi`/
   `film`/`bölüm`, and a separate bare-title matcher sits at the bottom of the ladder, below
   every branch that knows a noun. "haberleri aç", "Chrome'u aç", "ekranı aç", "bunu aç",
   "uygulamayı aç" and "Blender'da yeni sahne aç" all still route where they did — 1891
   corpus utterances green, 48 of them new. Two boundaries are cases, not comments: one word
   is not a title (`Winamp'ı aç.` stays an application refusal) and a physical object is not
   a title (`Arka kapıyı aç.` stays silent).

   It also uncovered a defect already in production: the scheduling guard matched by prefix,
   so "kur" (alarm kur) swallowed "Kurtlar" and **"Kurtlar Vadisi şarkısını çal." resolved to
   nothing** — with an explicit marker and no time reference in it. Fixed on both matchers.

   Known limitation, stated: a one-word title still needs a marker ("Gülümse şarkısını aç."),
   and a title that really contains a time word ("Gece Yarısı Ekspresi") is refused when a
   play verb is present, because that sentence is genuinely ambiguous with the sixteen
   wake-song cases.
3. **Something advances an idea.** `EvolutionService.advance()` has two callers and neither
   runs on a timer, so a recorded opportunity sits at `idea` for ever — which is what the
   owner watched happen to "YouTube'dan 'Doğum günün kutlu olsun Kadir' aç." Done = a
   recorded idea moves through the lab's own lifecycle without anyone asking it to, and
   stops where the constitution says it stops (`shadow_ready`).
4. **Real sleep detection.** Asked for on 2026-09-11 after seeing why the screen never
   turned off for sleep: `likely_asleep` needs posture + wakefulness, both of which come
   from the camera, and the camera has never reported once — production has only ever held
   `unknown`, `present` and `away`. The waits are three minutes now (owner's number) but the
   asleep path has no trigger behind it. Done = `likely_asleep` is reached from real
   observations, with the privacy posture stated and the owner's own switch over it.
5. **It answers only the owner's voice**, and a listen-only mode until a second command.
   Corrected by the owner on 2026-09-11: "ses algılamadan kastımda sadece benim sesimi
   tanıyacak" — this is SPEAKER VERIFICATION, not sound detection, and CLAUDE.md's voice
   rule already keeps the two apart.

   What exists: `app/voice/crypto.py`, written to encrypt a derived speaker embedding so
   that "neither raw audio nor a plaintext voiceprint is persisted". What does not exist:
   the embedding itself. `VoiceProfile` has no `embedding_ref` column and nothing in the
   tree compares one voice to another. The same shape as the capability layer, the media
   route and the attach backend before them — machinery present, nothing wired to it.

   The binding constraint is in M12's own table: `VoiceIdentity` is "augment-only; never a
   sole root of authentication". So done = the system can tell the owner's voice from
   another and SAYS which it heard, while a voice alone still authorises nothing; enrolment
   is the owner's deliberate act; the stored artefact is an encrypted embedding and never
   audio; and a stranger speaking is a recorded, answerable event rather than silence.

   Listen-only rides on top: "sadece dinle" stops it acting, a second command releases it,
   and the boundary between hearing and acting is visible rather than implied.

## The JARVIS target — spoken 2026-09-27 ("aslında birebir aynı hale getirmek istiyorum")

The owner's north star, in his words: PersonalAgentOS should become JARVIS — the assistant
of the Iron Man films — "birebir". Recorded here so that every later choice can be judged
against it, and so that the parts fiction can have and reality cannot are stated once,
not rediscovered.

### What JARVIS does, and where this system stands (2026-09-27)

| JARVIS | PersonalAgentOS today | State |
|---|---|---|
| Always-listening natural conversation, interruptible, in the owner's language | Realtime voice (OpenAI) + the free local mode (Chrome Web Speech + the ONE router + Haiku), barge-in, the Arbor voice target | **HAVE** — quality work remains (Turkish TTS gap, K66 noise, ADR-0043/0080) |
| Knows the owner completely, remembers everything that matters | Memory (M5, B16–B19) — SEMANTIC with ADR-0200 (local embedder, potion in production, 22/22 rows embedded); extraction in every mode (ADR-0201); rerank built and kept off by decision (ADR-0206); ledger + experience engine | **HAVE** (2026-09-29) — ADR-0201 is PROVEN_AUTOMATED; the owner's live local-mode sentence is pending |
| Researches anything, reads the world's data | Research in the owner's own Chrome (ADR-0183), Latest News Mode, God's Eye | **HAVE** |
| Runs the workshop by voice: machines, files, fabrication | Digital Operator (M19), documents (M20), artifact/app/native/3D factories (M22–M28) | **HAVE** |
| The same JARVIS in the house, the car, the suit, the phone | Multi-device (M29): a second device, GMKADIRAKBABA, is enrolled (ADR-0203, aliases `ofis` / `iş`); browser worker and owner-Chrome research proven in the office 2026-09-29; session→device affinity, launch without the Operator and the spoken device name are on main (ADR-0208/0209/0212), not yet released | **PARTIAL** (2026-09-29) — two PCs; no phone, no handoff of a running task |
| Runs the house: lights, doors, climate | Home Assistant behind a `smart_home` provider (research 2026-09-26) | **MISSING** — adopt |
| Secretary: mail, calendar, answers calls on his behalf | Mail/calendar built (M21) but no account; calendar → Radicale (own CalDAV); calls → a telephony bridge (Twilio/Telnyx) into the realtime voice path, with the KVKK announcement | **MISSING** — accounts and the bridge |
| **Records everything and tells him, whenever he asks** — "her şeyi kaydeden ve istediğim zaman bana anlatan" | Activity ledger (M16), memory (M5/ADR-0200–0206), activity briefing, research reports, audit trails. Missing: ONE narrative over all of it — "bu hafta ne oldu", "ofiste ne yaptın", "ne başarısız oldu" — spoken on demand, with failures included | **PARTIAL** — its own line under order item 2c |
| Proactive: warns, briefs, watches over him | Alarms, routines, morning briefing, presence, notifications; briefings still pull-only for a web session (queue item 1) | **PARTIAL** |
| Holograms and hands in the air | Holographic/Living Core (M18), hand gestures stage 1+2 (ADR-0198/0199, branch), God's Eye | **PARTIAL** — on a screen; volumetric holograms do not exist, AR glasses are the nearest real thing |
| Repairs and improves itself | Self-healing (M6), evolution (M7/M18.4), self-dev (B35), recovery supervisor (B08) | **HAVE** — controlled, and staying controlled |
| Personality, dry wit | The persona instructions | **PARTIAL** — tune, never at the cost of truthful speech (ADR-0063) |
| Breaks into any system; flies the suit; drives the car | — | **NEVER / HARDWARE** — see the limits |

### The limits, stated once

- **No unauthorised access, ever.** JARVIS "gets into" things; this system acts only on
  assets in the Authorized Asset Registry (constitution §8). That face of JARVIS is not a
  goal and does not become one.
- **No holograms in the air.** A screen, the Living Core, and later an AR headset are the
  real versions; the interaction (voice + gaze + hands) is what is being built, not the
  optics.
- **No physical agency without hardware.** Doors, lights and cars are Home Assistant and
  whatever the owner wires to it; a robot arm is a hardware project of its own. The
  software side (device selection, receipts, step-up) is already the shape it needs.
- **An always-on frontier model is not affordable.** The JARVIS feeling is a cheap
  always-on layer (local mode, local embedder, a fast classifier for routing/injection —
  the Jev-shaped seam) with the expensive model called only when the task needs it.
- **The name.** "JARVIS" is the owner's word for it in private; the product carries its
  own name if it is ever shown outside (Marvel's mark).

### The order (binding until the owner changes it)

1. **Memory** — DONE 2026-09-29: PR-1 in production (ADR-0200), PR-2 automated
   (ADR-0201), PR-3 built and off (ADR-0206).
2. **browser-use, anywhere** — the JARVIS that does anything on the web:
   - 2a. **In the owner's own Chrome** (ADR-0113/0183/0207): the task loop's PR-A and PR-B
     landed; PR-C (real Chrome, the six binding risks closed) and PR-D (voice + shell)
     remain. Reversible actions free, irreversible ones behind read-back + the owner's word.
   - 2b. **Execution in the cloud (ADR-0213)** — owner decision 2026-09-29: "işlemleri
     LLM'in koştuğu makine üzerinde yapsak daha stabil olmaz mı?". A headless-Chromium
     browser worker on the Cloud Core registered as a virtual device (`device_kind=cloud`,
     alias "bulut"); an `execution_target` rule (cloud | owner_chrome | device) with
     fallbacks and events; a network-less `compute.run` sandbox for calculation and data
     work; scheduled jobs always run in the cloud. Capacity: measure on CPX32, plan CPX41.
   - 2c. **The narrative** — the table's new row: one spoken account of what happened, on
     demand, failures included.
3. **Secretary** — Radicale (own calendar/contacts), a mail account, then the telephony
   bridge into the realtime voice path (announce the assistant, KVKK).
4. **The house** — Home Assistant as the `smart_home` provider; "salonun ışığını kapat".
5. **Everywhere** — reopened: the office PC is the second device; next: session→device
   affinity in production (ADR-0208), device-to-device handoff of a running task, the phone.
6. **Voice and character** — close the Turkish TTS gap, then give the persona its wit.
7. **Sight** — gesture stage 2 merged after the owner's trial; AR as a later surface.

### Approved ideas (the researcher's, written here by the lead when the owner approves)

Owner rule 2026-10-01: "araştırmacının yeni fikirleri onaylanırsa bu fikirler roadmap'e
eklensin." One line per approved idea; a deferred or rejected idea is not listed (it stays
in the queue with the owner's reason).

| Approved | Idea | Serves | Tasks | State |
|---|---|---|---|---|
| 2026-10-01 | **Rehearsal against the real host's shape** — a read-only snapshot of the Cloud Core feeds the fake-host tests and the schema check, so "green on the fake, red on the real host" is caught at the gate (`team/proposals/2026-10-01-gercek-ev-sahibi-provasi.md`). Priority. | "Repairs and improves itself" (the PROVEN_REAL condition) | `real-host-rehearsal` | queued |
| 2026-10-01 | **Measure Turkish STT engines side by side** — the same recordings through three engines, word error rate AND how many sentences would change intent. Measurement only: adopting an engine or opening a Soniox account is a separate approval (`team/proposals/2026-10-01-stt-soniox-olcum.md`). | Order 6, "Voice and character"; the conversation row's Turkish quality | `stt-engines-measure` | released 2026-10-02 (the instrument; 68.9 % on written renderings - no recording exists yet, see the measurement recording below) |
| 2026-10-01 | **The trial list** — the owner's third gate in the Onay Merkezi: for every released task the sentence to say, the machine, what he must see; "Oldu" readies the PROVEN_REAL line, "Olmadı" queues a fix in his own words (`team/proposals/2026-10-01-deneme-listesi.md`). | "Definition of done" itself (every row is HAVE only with PROVEN_REAL) | `owner-trials-api`, `owner-trials-page` | queued |
| 2026-10-01 | **Chrome's on-device Turkish recognition in the free local mode**, with our own words as hints and the engine recorded per utterance - behind a setting, OFF by default; turning it on is a separate decision after measurement (`team/proposals/2026-10-01-chrome-cihaz-ici-tanima.md`). | The conversation row's Turkish quality; the "cheap always-on layer" limit; order 6 | `chrome-on-device-stt` | released 2026-10-02 (the setting is OFF; the owner's measurement waits for the recordings) |
| 2026-10-02 | **A return that points outside the card's area widens the area** - the inspector names the outside file in a structured line, that return does not count against the worker's two, the cycle widens the area by itself when no open card holds the file and queues the task behind the holder when one does (`team/proposals/2026-10-02-alan-disi-geri-verme.md`). | Repairs and improves itself; the team cycle | `area-widen-rules`, `area-widen-role-lines` | in work |
| 2026-10-02 | **The notebook of misunderstood sentences** - the sentence the recogniser WROTE (text only, never audio) is kept 30 days when no intent was found, a question had to be asked, the owner objected or a tool failed; the Onay Merkezi asks "Ne demek istemiştin?" and the answer feeds the real STT corpus; "defteri unut" deletes it at once (`team/proposals/2026-10-02-yanlis-anlasilan-cumle-defteri.md`). | The conversation row's quality; ADR-0224's 95 % target (measured 68.9 %); order 6 | `misheard-ledger-store`, `misheard-relay-wiring`, `misheard-collector`, `misheard-page` | in work |
| 2026-10-02 | **Guard tests on the task branch** - the small family of tests that read the whole application (every error class has its Turkish sentence, every table is exercised on real PostgreSQL, …) is listed in one file and run by the cycle on a worker's branch before the inspector, so the full gate stops being the first place they run (`team/proposals/2026-10-02-koruyucu-testler-is-dalinda.md`). | Repairs and improves itself; the team cycle (five red gates on 2026-10-02 cost about six hours) | `branch-guards-runner`, `branch-guards-rules` | queued |
| 2026-10-02 | **The measurement recording** - a page of the web shell where the owner reads the twenty scripted sentences once; the recordings are kept 30 days on his own Cloud Core and the STT comparison measures from them, Chrome's recogniser included (`team/proposals/2026-10-02-olcum-kaydi.md`). No audio leaves the owner's own machines by this idea. | The missing half of the STT measurement (ADR-0242); order 6 | `measure-recordings-api`, `measure-recording-page`, `measure-compare-from-core` | queued |

Deferred by the owner, not listed above: Home Assistant as the `smart_home` provider
(2026-10-01: "evde bağlanabilir cihaz envanteri çıkarmadan erken") - it is order 4 already.

### Definition of done (owner, 2026-09-29)

In the owner's words: the project is at its finishing level when it is "iron-man filmindeki
gibi aynı gerçek dünyaya etki edebilen, araştırabilen, her şeyi kaydeden ve istediğim zaman
bana anlatan bir JARVIS". Operationally:

- every row of the table above says **HAVE** with **PROVEN_REAL** evidence — real device,
  real voice, production; PROVEN_AUTOMATED / PROVEN_PROXY from the inspector never close a row;
- the limits above still hold;
- the owner says it feels like JARVIS. That verdict is his alone and is the last gate, as
  with voice (ADR-0034 §6).

Finishing the roadmap is therefore necessary, not sufficient: when the owner says "olmadı",
a new row is opened. The roadmap is the path; this section is the destination.

### How it is built from here (owner decision 2026-09-29)

The project is developed by a **team of Claude agents** working in cycles, not by one
session. Roles, gates and the cycle are binding in `docs/TEAM_PROTOCOL.md`; role
definitions live in `.claude/agents/`. In short:

- **Lead (Proje Hakimi)** owns this roadmap and the definition of done, splits work,
  assigns it, sends incomplete or wrong work back, merges, reports to the owner.
- **Researcher** knows the whole project, scans the world for what to add, proposes to the
  owner; only owner-approved ideas reach the lead. Roadmap changes are proposed by the
  researcher, approved by the owner, written by the lead.
- **Integrator** finds existing code/libraries for an assigned task (licence and safety
  checked, registered in THIRD_PARTY_COMPONENTS) and writes the integration plan.
- **Workers ×2–3** implement under DEVELOPMENT_POLICY in their own worktrees.
- **Inspector** runs the result, runs the gate, tries to break it; reports the evidence
  class to the lead. No merge without the inspector.
- **Three owner gates, and only three:** idea approval, release approval, real-world
  evidence (PROVEN_REAL) plus the final verdict. Everything else runs without him.
- The cycle is driven by a script (`scripts/team/cycle.ps1`), each agent run is short and
  fresh-context, state lives on disk (`team/queue.json`, HANDOFF, BUILD_STATE, reports), so
  the context window is never the limit. Cycles are scheduled nightly on the home PC once the
  pilot has been measured.
- **Since 2026-10-01 (owner):** the cycle runs all day, every 30 minutes; no agent idles
  while the roadmap names work - the lead feeds the queue from "The order" above; an idea
  the owner approves is written under "Approved ideas" (ADR-0214 addenda 6 and 8).

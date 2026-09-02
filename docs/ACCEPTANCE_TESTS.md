# Acceptance Tests

## M-1 Environment

- preflight report exists;
- git repository valid;
- no secrets tracked;
- required toolchain present or one consolidated owner action is documented;
- Docker can run a hello/health container;
- Windows native build shell works;
- test command entry points are documented.

## M0 Foundation

- monorepo skeleton builds;
- API health endpoint passes;
- web shell loads;
- PostgreSQL migration round-trip passes;
- Redis connectivity passes;
- S3 dev abstraction writes/reads/deletes an object using MinIO;
- Temporal dev workflow survives worker restart test if feasible in local environment;
- structured logging has trace/task IDs;
- unit/integration commands succeed from one quality-gate command;
- CI workflow parses and runs core checks.

## M1 Cloud + Device

- Windows agent enrolls with cloud/staging broker;
- outbound connection reconnects automatically (verified against both broker restart and agent restart, no owner intervention);
- device online/offline state reflects reality;
- command idempotency test passes (duplicate creation and duplicate delivery both execute exactly once);
- expired command is never executed and terminates as `command_expired`;
- malformed protocol frame yields a `validation_error` frame and does not kill the connection;
- command to an unavailable/offline agent stays pending and is delivered on reconnect (or expires);
- broker-unavailable behavior: agent retries with backoff, no crash, no duplicate execution after recovery;
- from owner UI/API: `desktop.open_application` opens Notepad in the enrolled Windows interactive session, acknowledgement returns, and the execution appears in the broker audit log and the agent's local audit log;
- wrong/revoked device key is rejected;
- no inbound public Windows port required (agent connects outbound only).

## M2 Browser

Architecture gates:

- a common backend adapter interface exists; higher-level browser commands are transport-agnostic (managed Playwright, existing-session connection, future visual fallback all implement the same interface);
- the deterministic/test path uses a Playwright-managed dedicated profile with no dependency on the owner's real browser state;
- the owner-existing-browser path is modeled as explicit **browser enrollment** (authorization record + granted capabilities), never as unrestricted debug-port exposure; no remote debugging port is ever exposed non-loopback;
- every backend declares capabilities: `authenticated_session`, `downloads`, `uploads`, `extensions`, `existing_tabs`, `multiple_windows`, `visual_fallback`; the orchestrator can query them;
- coordinate-based clicking is prohibited in the deterministic semantic layer (visual/coordinate automation arrives later as an explicit separate fallback adapter).

Deterministic scenario matrix (all must pass against the fixture site / throwaway browsers):

- navigate; back/forward; new tab/close tab; select existing tab;
- text input; click by semantic locator; select/dropdown; checkbox/radio; form submission;
- SPA navigation; iframe interaction; popup/new-window; download; upload using a fixture file;
- browser-side error typed; timeout typed; stale/detached element recovery (retryable + recovery verified);
- ambiguous locator handling; page/browser crash typed; reconnect after crash/disconnect;
- cancellation of an in-flight command; duplicate command/idempotency at the browser-command layer.

Plus:

- existing-session attach path tested against a throwaway browser (transport behind the adapter seam), owner real-browser attach documented as enrollment;
- full M0 + M1 regression gate passes before M2 closes.

## M3 Research + Artifact

Given a fixed test topic:

- research task becomes durable;
- multiple sources can be recorded;
- canonical report created;
- executive summary created;
- PDF and DOCX render created;
- artifact persists after service restart;
- current-device presentation works;
- Windows open-artifact command works;
- task ends `READY` without auto-reading the whole report.

## M4 Voice

- Turkish STT benchmark report exists;
- owner speaker enrollment works;
- `OWNER/NOT_OWNER/UNCERTAIN` path tested;
- realtime Turkish conversation supports barge-in;
- narration TTS benchmark compares >= 2 viable providers;
- Turkish normalizer tests cover date, money, percentage, IP, abbreviation, table;
- “oku/dur/devam/tekrar” works;
- cross-device narration cursor persists;
- provider failure falls back or reports gracefully.

## M5 Memory

Architecture gates:

- six first-class memory classes exist (preference, episodic, project, semantic, procedural, voice_preference) — not a bare vector-store wrapper;
- a Memory Write Policy decides `ignore -> session -> candidate -> durable`; explicit owner instructions outrank inference and enter durable directly;
- inferred memories carry confidence + evidence; a single observation never becomes a high-confidence permanent preference (promotion needs both an evidence-count and a confidence threshold);
- provenance, version history, retention classification and an append-only memory audit trail exist; audit never stores content after a forget;
- the memory API sits behind an abstraction layer (`MemoryBackend`) so Mem0 or another framework can be added/replaced without changing the core data model; PostgreSQL stays canonical;
- embedding rows carry model_id/model_version/dim and a reindex operation supports re-embedding migrations;
- secrets/credentials/tokens are refused as memory content;
- retrieval combines pgvector semantic, structured relational, and hybrid reranking with temporal filtering; superseded/deleted rows are always excluded.

Behavioral matrix (each a named deterministic test):

- owner explicitly teaches a preference; it survives a new conversation/session;
- weak inferred preference stays low-confidence; repeated evidence increases confidence and promotes at thresholds;
- contradictory inferred evidence does not overwrite an explicit owner preference (conflict recorded, surfaced on inspection);
- an old preference can be superseded (history kept, retrieval returns only the active one);
- owner can inspect why a memory exists (provenance + evidence + versions + audit);
- owner can correct a memory (new version) and forget a memory;
- a deleted memory is absent from BOTH semantic (vector) and structured retrieval — the vector/index rows are actually gone;
- project A memories do not contaminate project B retrieval (contamination metric = 0 on the seeded eval);
- relevant memories cross device/session boundaries;
- task -> artifact -> conversation -> project relationships are retrievable (entity graph);
- episodic time-window search works;
- procedural patterns are identified from repeated workflows as candidate proposals only — never by modifying application code;
- deterministic retrieval evaluation on a seeded corpus reports precision/relevance above a stated floor and zero cross-project contamination.

## M6 Self-Healing

Inject a controlled browser-agent bug in staging/test:

- monitoring detects failure;
- incident fingerprint recorded;
- last-known-good service remains recoverable;
- automated engineering loop reproduces bug;
- regression test fails before patch and passes after;
- candidate is deployed to staging/canary;
- health returns green;
- bad candidate simulation triggers rollback automatically.

## M7 Evolution

Ask for a capability intentionally absent from registry:

- gap detector identifies missing capability;
- existing-tool composition is attempted first;
- new generated skill is created only when necessary;
- tests/evals generated;
- independent review occurs;
- capability registered only after gates;
- original user task resumes and completes;
- rejected candidate does not affect production.

### Resolution order (must be enforced and auditable, in this order)

1. use an existing capability; 2. compose existing capabilities; 3. configure/extend an existing skill; 4. install/adapt a compatible reusable component when appropriate; 5. generate a new skill; 6. classify as product/core change only when none of the above can solve it.

Code generation must not happen when composition is sufficient (asserted: no skill_version row created for a composable request).

### Capability manifest (versioned, required fields)

capability ID; human-readable purpose; version; input/output schema; dependencies; network permissions; filesystem permissions; device permissions; secret requirements; external services/providers; expected side effects; risk classification; tests; evaluation metrics; provenance; builder identity; creation reason/task; rollback version. **Permissions are deny-by-default for generated skills.**

### Generated-skill lifecycle

`candidate -> sandbox -> validated -> shadow -> canary -> active -> deprecated/rolled_back`. A generated skill may never reach `active` because the agent that generated it claims success — promotion requires independent validation evidence.

### Isolation, supply chain, resources

- generated code builds/executes in an isolated workspace/worktree/container; no production secrets by default; network/filesystem/device access is capability-scoped;
- no blind package installation: dependency name, version and source recorded and pinned; dependency/security scanning; install scripts cannot silently expand privileges;
- configurable execution timeout, CPU/memory/disk/network budgets, retry limit, and a recursion/self-extension limit that prevents infinite agent→agent capability-creation loops.

### Failure matrix (each case: production stays on last-known-good and the system stays usable)

generated code does not compile; generated tests fail; security reviewer rejects; candidate crashes; canary performs worse; dependency unavailable; generated capability times out.

### Improvement of an existing skill

Telemetry indicating recurring weakness -> candidate improved version -> benchmark old vs new -> promotion only when objectively superior; the old version remains rollback-capable.

### Boundaries (guard-tested, not conventional)

- evolution may read memories as context; it may not mutate explicit-owner memory through any evolution code path; weak inferred preferences never become product requirements automatically;
- the recovery/security root restriction applies to **self-modification authority**, not to owner-authorized operational capabilities: the owner policy subsystem must remain able to grant powerful tools to explicitly authorized devices/assets without Evolution weakening or rewriting the security root.

### Auditability

Every evolution answers: why the capability was needed; what request/incident triggered it; what changed; what code/dependencies were introduced; what permissions were granted; what tests ran; who/what reviewed it; why it was promoted; what the rollback target is.

### Mandatory end-to-end demonstration

With a deterministic fixture capability absent beforehand, asking the running system to perform a task requiring it must automatically produce: `CAPABILITY_MISSING` -> composition check -> skill design -> implementation -> tests -> independent security/reviewer gate -> canary -> promotion -> registry update -> original task resumes -> task succeeds. The same scenario repeated with a deliberately defective implementation must be rejected or rolled back with no owner intervention.

## M8 Authorized Security

- authorized test asset enrolls;
- in-scope defensive assessment runs without repeated approvals;
- out-of-scope target is not silently added;
- results become security artifact;
- remediation respects stored asset constraints;
- full audit trail has no secret leakage.

## M9 Native Mobile

- authenticated native client connects;
- push notification for artifact ready;
- audio narration resumes from cloud cursor;
- microphone/realtime voice works under normal mobile lifecycle;
- file share/export works;
- device revocation invalidates session.

Identity gates (ADR-0027 — this is also the milestone that closes the standing
API-authentication hard gate carried since M0):

- every endpoint outside the documented unauthenticated surface refuses without
  a valid owner session, proven by a test that walks the **live FastAPI route
  table and dependency graph** rather than a hand-maintained list, so a new
  unprotected route fails the suite instead of shipping quietly;
- the unauthenticated surface is exactly: health, `POST /v1/identity/bootstrap`
  (loopback-only, one-time), `POST /v1/identity/sessions` (the credential *is*
  the authentication), `POST /v1/devices/enroll` (single-use owner-minted
  enrollment token) and the device WebSocket handshake (device key);
- the test suite contains **no authentication bypass** — no dependency
  override, no "auth off in tests" setting; every test holds a real token from
  the real service, which is what makes the suite evidence of protection;
- sessions and their audit survive an application restart (real PostgreSQL),
  and a revocation cannot be forgotten;
- a scoped session is *narrower* than an unscoped one at every route, including
  routes that forgot to declare a scope (structural, see M9 review #2);
- a corrupt identity root refuses cleanly and is never overwritten by
  bootstrap;
- device revocation kills every session bound to that device across the whole
  REST API, not just its WebSocket, and survives a crash between its two
  transactions in the safe direction.

Proof status, stated rather than blurred: "authenticated native client
connects", "push notification for artifact ready", "narration resumes from the
cloud cursor", "file share/export" and "device revocation invalidates session"
are proven against the real API by an in-repo headless reference client
(`clients/reference`) plus integration tests. That proves the **server
contract** the mobile app depends on. It does not prove **platform behaviour**:
a real push arriving on a locked phone, and microphone/realtime capture under
iOS/Android backgrounding and interruption, require a physical device and are
batched as owner actions.

## RQ-2 Cloud bring-up — evidence of record (CLOSED 2026-09-02)

Real, on the owner's machines, via `scripts/cloud/qualify-cloud.ps1` against `pagentos-core`:
headline Hetzner → Tailscale → DeviceService → Companion → real Notepad → ACK; audit rows
`command_received`/`command_ack` correlated to the real `command_id`/`trace_id`; recovery
without owner intervention from Cloud Core process restart, VPS reboot (tailnet address,
`/mnt/pagentos-data`, device row preserved), Tailscale reconnect, Windows network loss,
DeviceService restart; no public port (proven from outside). See `docs/QUALIFICATION.md`
Stage 5.

## M12 Realtime Voice Foundation

Real-only acceptance — the owner's real Windows machine, microphone, speakers/headset,
Turkish speech, network and the real Hetzner Cloud Core. Fakes are gates, never acceptance.

- primary conversation over a native realtime speech-to-speech provider selected by
  capability (ADR-0034); STT → text LLM → TTS is not accepted as the primary path;
- natural Turkish conversation; simultaneous listen/speak as far as the provider permits;
- immediate barge-in: the owner interrupts the assistant mid-speech and playback stops
  immediately; measured barge-in → playback-stopped latency, target under ~150 ms where
  technically achievable;
- "dur" stops speech immediately; "devam" resumes correctly; "tekrar oku"; "ikinci maddeyi
  tekrar oku" navigates semantically; "biraz daha yavaş" / "biraz daha hızlı"; "özet geç";
  "detaya gir"; "burayı atla";
- semantic end-of-turn detection: the assistant does not cut the owner off during normal
  Turkish hesitation (measured false-barge rate on a real hesitation set);
- natural Turkish pronunciation, prosody, punctuation and emotional delivery; mixed
  Turkish/English terminology benchmarked on at least: PagentOS, Tailscale, Hetzner,
  PostgreSQL, PowerShell, FortiGate, OpenAI, Claude, Windows, Kubernetes, Redis, Temporal;
  Turkish characters and phonetics ı, İ, ğ, ş, ç, ö, ü;
- microphone switching; headset/laptop/phone microphones; echo cancellation; noise
  suppression; a noisy-room run;
- network interruption and voice-session recovery; conversation continuity across
  desktop/web/mobile;
- tool calls without destroying conversational flow: a long-running tool gets a short
  natural preamble, the session stays alive, and a mid-task redirection ("Sadece OpenAI
  kısmına bak") changes the active plan without a disconnected conversation;
- quantitative benchmarks recorded against the real environment: mic → provider uplink,
  end-of-turn → first audible response (target ~500–700 ms short turns where achievable),
  barge-in → playback stopped, tool-call preamble latency, tool completion → resumed
  speech; no perceptible unnecessary audio gaps;
- modes are explicit capabilities: ConversationRealtime, Narration, Transcription,
  VoiceIdentity — and VoiceIdentity is never the sole root of authentication;
- subjective owner evaluation of voice quality is an explicit, final gate.

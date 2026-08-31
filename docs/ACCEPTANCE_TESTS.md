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

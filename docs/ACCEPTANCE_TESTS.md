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

- dedicated test browser navigation passes;
- DOM/accessibility interaction passes;
- download test passes;
- existing-session integration path is documented/tested where environment permits;
- browser state-change failure is typed and retryable;
- raw coordinate fallback is not primary path.

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

- explicit preference is remembered across sessions;
- inferred preference stores evidence/confidence;
- project memory retrieval works;
- false/obsolete memory can be corrected;
- deletion removes retrieval/index result;
- procedural memory prototype detects repeated test workflow;

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

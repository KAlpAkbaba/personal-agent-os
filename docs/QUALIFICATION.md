# Real-Environment Qualification

The milestone chain M-1…M9 built the system and proved it against fakes, fixtures and
loopback. This document tracks the different question: **does it work for its owner, on the
owner's real machine, real cloud, real browser, real voice and real phone?**

Every criterion carries exactly one status, and the rules for using them are strict:

| Status | Means |
|---|---|
| `PROVEN_REAL` | Demonstrated against the real thing — real OS mechanism, real remote host, real hardware, real network. |
| `PROVEN_PROXY` | Demonstrated against a stand-in (fake, fixture, injected identity, loopback, headless client). The logic is proven; the environment is not. |
| `NOT_YET_PROVEN` | Not demonstrated at all yet. |

A proxy result is never written up as real. Where a criterion is split — logic provable
here, environment provable only on the owner's machine — it appears twice, with the proxy
part marked `PROVEN_PROXY` and the environment part `NOT_YET_PROVEN` until it runs.

Last updated: 2026-09-02 (RQ-2 closed).

> **Local Windows qualification CLOSED 2026-09-01.** The final `finalize-qualification.ps1`
> run and `verify-device-service.ps1` report proved, on the owner's real machine: service
> Running as LocalSystem in Session 0; companion in the owner's Session 1; the live pipe's
> DACL read from the actual runtime pipe handle; kernel-sourced companion admission;
> install-tree posture; no inbound agent listener; DeviceService restart → reconnect → real
> Notepad → ACK; Cloud Core restart → reconnect → real Notepad → ACK; and a final owner
> credential rotated once, host-side, console-only. The qualified runtime is FROZEN — no
> speculative changes. Next: **real cloud bring-up** (Stage 5), where "Cloud Core" moves
> from loopback to Hetzner + Tailscale. Refusal legs that require a second real user
> account or concurrent session (1.4, 1.6, 1.7) deliberately stay `PROVEN_PROXY`, and
> 2.2b (fresh-logon autostart) stays open until a natural sign-out/reboot exercises it.


> **Cloud bring-up (RQ-2) CLOSED 2026-09-02.** On the owner's actual machines: `pagentos-core` (Hetzner, cpx32, nbg1) runs the production Cloud Core on `pagentos_prod`, reachable only over the tailnet (every public port refused, proven from outside); the Windows agent was moved to it without reinstall or re-enrolment; the headline path Hetzner → Tailscale → DeviceService (LocalSystem, Session 0) → Companion (Session 1) → real Notepad → ACK is `PROVEN_REAL`, with `command_received`/`command_ack` persisted and correlated to the real `command_id`/`trace_id`; recovery is `PROVEN_REAL` for Cloud Core process restart, VPS reboot (tailnet address, `/mnt/pagentos-data` and the device row all survived), Tailscale reconnect, Windows-side network loss and DeviceService restart. The cloud/device infrastructure is a FROZEN proven baseline: no reinstall, re-enrolment, device or owner recreation, or broker redesign unless a real observed defect requires it.

---

## Stage 1 — Session-0 service/companion IPC identity (M1 security finding #1)

The finding: under a real Windows Service the pipe's ACL and name both derive from the
*creating* process, so "ACL it to the current user" stops meaning "the owner" the moment the
service becomes LocalSystem — and there was no mutual authentication beyond that ACL.
Closed by ADR-0028 with four layers: DACL, first-instance creation, kernel-sourced peer
admission, and per-connection freshness.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1.1 | Pipe DACL names the owner account explicitly, not "whoever created me" | `PROVEN_REAL` | 2026-09-01 final run: the effective SDDL captured from the REAL pipe handle at creation (`ipc_pipe_created` audit) under the installed LocalSystem service names SYSTEM and the owner's SID — the two halves genuinely different accounts. Runtime proof, not source inspection. |
| 1.2 | Service refuses to share a pipe name another process already holds | `PROVEN_REAL` | `The_service_refuses_to_share_a_pipe_name_someone_else_already_holds` — a real pipe is created first by another process; the service records a listen failure and never serves on that name. **Corrected 2026-09-01:** this row previously credited `FILE_FLAG_FIRST_PIPE_INSTANCE` specifically. Independent verification showed the test passed with that flag deleted, and measurement then showed why: three mechanisms overlap (single instance, non-default security descriptor, the flag) and removing any one still refuses. The outcome is proven; attributing it to one mechanism was not, so the claim no longer does. |
| 1.3 | Companion refuses a pipe not owned by a service account | `PROVEN_REAL` | `The_companion_refuses_a_pipe_that_is_not_owned_by_a_service_account` — a real fake pipe, owned by a standard user, read through the real `GetSecurityInfo` path, refused, and not one frame written to it. **Corrected 2026-09-01:** this row previously said "refused by the production policy" while the shipped companion was in fact running developer trust (see 1.12); the mechanism was real, the claim about the binary was not. |
| 1.12 | The **shipped** companion runs at service-trust strength, not developer trust | `PROVEN_REAL` | `The_shipped_companion_defaults_to_service_trust_not_developer_trust` and `A_runtime_built_without_a_policy_refuses_an_owner_owned_pipe` — the entry point's own policy builder and the runtime's own default are both asserted, because the defect was that neither the class nor its test was wrong; the wiring was. Developer trust now requires `--dev-trust` and is logged as a warning. |
| 1.13 | Service and companion derive the same pipe name from the owner | `PROVEN_REAL` | `The_service_and_the_companion_derive_the_same_pipe_name_from_the_owner`. Under Session 0 the two halves are different accounts; a name derived from "the current user" would leave them on different pipes with nothing in either log saying why. |
| 1.4 | Peer running as another account is refused (wrong SID) | `PROVEN_PROXY` | Policy refuses `SidMismatch`; end-to-end over a real pipe with an injected identity. A second real user account is an owner action. |
| 1.5 | Peer in Session 0 is refused | `PROVEN_PROXY` | `A_peer_in_session_zero_is_refused_even_with_the_right_sid`. Reproducing a real Session-0 client requires the installed service. |
| 1.6 | Peer in a different interactive session is refused | `PROVEN_PROXY` | `A_peer_in_a_different_interactive_session_is_refused_when_the_session_is_pinned`. A second concurrent logon is an owner action. |
| 1.7 | Forged companion (right user, wrong binary) is refused | `PROVEN_PROXY` | `The_right_user_running_the_wrong_binary_is_refused`, plus refusal when the image cannot be read at all. Real proof needs the pinned installed binary. |
| 1.8 | Replayed frame within a connection is refused | `PROVEN_REAL` | `A_replayed_exec_response_does_not_satisfy_a_later_request` — a real captured frame re-sent over a real pipe; the service refuses it and the pending request still times out rather than being answered. |
| 1.9 | Stale credentials from a retired connection are refused | `PROVEN_REAL` | `A_frame_from_an_earlier_connection_is_refused_after_reconnect`, and `A_companion_hello_that_does_not_answer_this_challenge_is_refused` staged over a live pipe. |
| 1.10 | Refusals are audited with their reason and tell the peer nothing | `PROVEN_REAL` | `A_refused_peer_is_written_to_the_audit_trail_and_told_nothing` and `An_admitted_companion_is_written_to_the_audit_trail` — a real `AuditLog` on disk, asserted for the row, the reason and the refused SID, and asserted that the peer reads end-of-stream rather than any frame. **Corrected 2026-09-01:** this row previously cited the source line. Independent verification found no test ever passed an `AuditLog` to the server, so `_audit?.Write` never executed in the suite — confident evidence text, zero executing assertion. Now asserted. |
| 1.14 | The service's composition root builds the policy the install configures | `PROVEN_REAL` | `IpcWiringTests` — what `Program.BuildAdmissionPolicy` returns for a configured SID/binary/session, that both missing-configuration warnings actually reach stderr, and that the pipe name derives from the owner. Added because the Critical in 1.12 was a composition-root defect that no criterion covered. |
| 1.15 | A second connection cannot displace the connected companion | `PROVEN_REAL` | `A_second_connection_cannot_displace_the_connected_companion` — the intruder's connect fails while the companion keeps working. |
| 1.16 | The pipe-owner inspector reads a real owner and accepts a trusted one | `PROVEN_REAL` | 2026-09-01 final run: the companion read the installed service's ACTUAL SYSTEM-owned pipe through the real `GetSecurityInfo` path and connected — the acceptance half that no test process could stage. |
| 1.11 | Service runs as LocalSystem in Session 0 with the companion in the owner's session | `PROVEN_REAL` | 2026-09-01 final run: service Running as LocalSystem, SessionId 0; companion in the owner's Session 1, from the pinned binary — the exact separation the whole of Stage 1 exists for. |
| 1.17 | Only SYSTEM and Administrators can write to the installed tree | `PROVEN_REAL` | 2026-09-01, owner's machine: `verify-device-service.ps1` checked 201 objects under `C:\Program Files\PagentOS\agent` — no other principal holds write authority and **no empty DACLs remain**. The 425 files the earlier hardening had stripped were repaired by the installer itself, with no manual ACL reset. |
| 1.7b | The pinned companion binary is admin-protected and readable by SYSTEM | `PROVEN_REAL` | Same run: owner `S-1-5-32-544`, 3 inherited ACEs. This is what makes `CompanionImagePath` pinning mean something. |
| 5.2 | The agent owns no inbound listening socket | `PROVEN_REAL` | Same run, against the live companion process (pid checked explicitly, not a vacuous pass). |

## Stage 2 — Windows Service installation

**CLOSED 2026-09-01.** This stage cost nine real-machine failures (logged below) before the
final run went clean end-to-end; the log stays because the pattern — every defect in a code
path the tests did not execute — is the lasting lesson.

| # | Criterion | Status | Notes |
|---|---|---|---|
| 2.1 | Service installs, starts, restarts, reconnects unattended | `PROVEN_REAL` | 2026-09-01 final run: installed, Running as LocalSystem/Auto, and a forced restart reconnected and executed a real command with no owner intervention. (A full machine reboot has not been exercised yet; the Auto registration is real, the reboot proof will land incidentally.) |
| 2.2a | Companion runs in the owner's interactive session, from the pinned binary | `PROVEN_REAL` | 2026-09-01: session 1, `MAIL\alpak`, `C:\Program Files\PagentOS\agent\companion\PagentOS.SessionCompanion.exe`. |
| 2.2b | Companion auto-starts at a *fresh logon* | `NOT_YET_PROVEN` | The logon task is registered and has started the companion on demand, but a genuine sign-out/sign-in has not happened yet. Running now ≠ starts at logon; stays open until a natural logon proves it. |
| 2.2c | The service admits the companion on SID + session + binary | `PROVEN_REAL` | 2026-09-01 final run: `ipc_companion_admitted` audit from the installed runtime — kernel-sourced pid/session/image against the configured SID and pinned binary, with the two halves genuinely different accounts. |
| 2.3 | `desktop.open_application` executes in the interactive session from the Session-0 service | `PROVEN_REAL` | 2026-09-01 final run: broker command → Session-0 service → companion → REAL Notepad in Session 1, pid verified alive then closed, ACK and audit recorded. Proven twice (after a service restart and after a Cloud Core restart). |
| 2.4 | The installer is idempotent — a rerun after a partial or failed install succeeds | `PROVEN_REAL` | Claimed twice, disproved twice, then proven the hard way: nine distinct real failure states (see the log), each resumed by rerun — journaled `RetryFromStaging` recovery included — with enrollment, identity and key material preserved throughout. |
| 2.5 | Only SYSTEM and Administrators hold write authority over the installed tree | `PROVEN_REAL` | Same evidence as 1.17: the final `verify-device-service.ps1` allowlist sweep over the real tree — no other principal holds write authority, no empty DACLs. |
| 2.6 | Persisted machine material is usable by LocalSystem and closed to ordinary users | `PROVEN_REAL` | The half no test could reach is now real: the installed LocalSystem service read the device key and rewrote state across restarts, while non-elevated denial was already proven directly. Two domains held: machine material SYSTEM+Administrators only (key SYSTEM-read); owner material owner-scoped; the agent uses no DPAPI (checked, not assumed). |
| 2.7 | DeviceService restart → reconnect → real command → ACK | `PROVEN_REAL` | 2026-09-01 final run, unattended. |
| 2.8 | Cloud Core restart → agent reconnect → real command → ACK | `PROVEN_REAL` | 2026-09-01 final run, unattended. (Local broker; the same proof must be re-earned from the real Hetzner/Tailscale endpoint in Stage 5 and is NOT carried over.) |

### The install attempts so far, and what each disproved

Recorded in full because the pattern matters more than any single bug: nine real-machine
runs, nine defects, and **every one of them was in a code path my tests did not execute**.
None was found by the gate; all were found by the owner running the thing.

| Attempt | Failure | Cause | What it disproved |
|---|---|---|---|
| 1 | `sc create` exit 1639 | PowerShell 5.1 does not escape a native argument containing quotes, so `binPath` split at "Program Files". Only visible elevated — a non-elevated `sc.exe` fails at `OpenSCManager` first. | That the installer worked at all. |
| 4 | Service starts, then SCM 1067 | `UnauthorizedAccessException` on `device.key` in `DeviceIdentity.LoadOrCreate`. The key was created by an owner-context enrollment run and protected with a DACL naming **only that user**, so LocalSystem could not read its own identity. Service-owned material protected as if it were owner material. | That enrollment produced material the service could use. Nothing about the broker, the IPC layer or enrollment itself was wrong — the process died before reaching any of them. |
| 5 | Credential rotation committed, replacement lost | The wrapper ran `--rotate --json`, the child exited 0, and stdout carried **two** JSON documents: this application logs to stdout, and `bootstrap()` emits `identity_owner_credential_minted` — so the mint itself guaranteed the contamination. `ConvertFrom-Json` refused it and the wrapper threw *after* the rotation had committed. The replacement died with the child process. | That a wrapper checking the exit code is enough. The exit code was 0 and the operation had succeeded; the loss was entirely in the protocol between the two processes. |
| 2 | Access denied rewriting `appsettings.json` | Hardening with `/T` and `(OI)(CI)` grants left every pre-existing file with a protected empty DACL — 425 objects on the owner's machine — denying everyone including SYSTEM. | The idempotency claim. The tests behind it covered the create/reconfigure *decision* and never touched a hardened tree. |
| 3 | `Property 'Count' cannot be found` (`PropertyNotFoundStrict`) | A function returning an empty collection unrolls to `$null`; `.Count` on it throws under StrictMode, which the libraries set and dot-sourcing propagates. Reproduced: 0 → `$null`, 1 → `String`, 2+ → array. | The claim that the partial install was *fully recoverable*. It was not: recovery aborted before it began, and a rerun with exactly one component to restore would have failed too. |
| 6 | `Move-Item` access denied on the live service tree; companion left down; pipe server later found dead inside a "Running" service | NTFS refuses to rename a directory holding a running process's mapped images — the deploy never stopped the runtime first. Separately, the pre-fix `/T` icacls (reintroduced for ProgramData) emptied the log file's DACL and one failed log write from a `finally` killed the pipe server's `ExecuteAsync` while the host stayed Running. | That copy-over-live deployment was a deployment at all, and that service `Running` means health. Replaced by the journaled transactional engine (`Deployment.ps1`): verified stop before any move, same-volume renames, symmetric restart of both halves in `finally`, health = service AND companion AND pipe. Loggers may never throw. |
| 9 | Cloud Core started and answered healthy, then `dev-broker.ps1` died on `$health.dependencies` (`PropertyNotFoundStrict`) | The health schema is `{status, version, checks}` — `checks` is a map of subsystem→check; a top-level `dependencies` array **never existed**. The script inherited StrictMode from its dot-sourced libraries without declaring it, and read an imagined property. Orchestration display defect; the broker itself was healthy. | That direct access is acceptable for optional properties. `Test-ObjectProperty`/`Get-OptionalProperty` are now the explicit accessors (required properties stay direct so their absence fails loudly); the checks display warns on absent/empty rather than defaulting silently; StrictMode is declared, not inherited. Also fixed while proving the fix on the real machine: an elevated broker hides its command line from non-elevated queriers, so "already running" is now decided by process match OR a live port listener, and a marker file records which DATABASE each started broker serves — finalize reuses a running Cloud Core only when that is proven, never from health alone. |
| 8 | Transactional deployment COMMITTED (service + companion + pipe healthy), then broker-registration restore died on `ECDsa.ImportFromPem` | Windows PowerShell 5.1 runs on .NET Framework, which has no `ImportFromPem` (.NET Core 3+). Fourth member of the parses-fine-fails-at-the-call family (native quoting, StrictMode cardinality, `??`). | That a script may handle key material at all. ADR-0030: key handling only through the agent's own .NET implementation — new load-only `identity` verb (one JSON doc, distinct not-enrolled/key-missing exits, never mints); a gate lint fails any script calling .NET-Core-only crypto APIs; a PS5.1 gate suite enrolls a REAL key via the real exe against a loopback listener and proves the verb returns it byte-for-byte. `finalize-qualification.ps1` became phase-resumable so the committed deployment is never redone to reach step 2. |
| 7 | Finalize denied reading `state.json` — elevated administrator, access denied | `state.json` was rewritten at enrollment by the old binary's atomic tmp→move, so the fresh file carried only INHERITED ACEs; the earlier pre-fix `/T` run stripped inherited ACEs tree-wide, leaving a protected **empty** DACL. `device.key` survived because its ACEs were explicit — the exact signature of the bug class, timeline-proven from the owner's read-only inspection. | That ACL correctness can live in an installer repair instead of the **write primitive**. Now: every `AgentState.Save`/`IdempotencyStore` write re-applies explicit SYSTEM+Administrators protection after the move (asserted across replace), and `Restore-MachineStateAcl` repairs exactly the named state files — no recursion, no takeown, no new principals (ADR-0029). |

The corrective work is not only the three fixes. It is that the installer's top-level flow now
lives in tested functions (`Invoke-InstallRecovery`), that the cardinality pattern is linted
out of every installer script by the PowerShell parser, and that the tests assert which engine
they ran on — `installer-strictmode.tests.ps1` fails if it is not Windows PowerShell 5.1 with
StrictMode in force, so it cannot pass vacuously on a more forgiving host.

Read-only inspection of the owner's real tree with the fixed code (2026-09-01): the call that
threw now returns an empty array, the posture check reports 51 violations in a 61-object
sample, and 425 children are queued for the inheritance reset an elevated rerun performs.
That is evidence the code recognises the state — **not** evidence the repair succeeds, which
only the owner's elevated rerun can show.

## Stage 3 — Owner identity bootstrap and recovery

| # | Criterion | Status |
|---|---|---|
| 3.1 | Owner credential minted once, never in logs, source control or plaintext config | `PROVEN_REAL` — the final credential was displayed exactly once, in the local elevated console at rotation, and is used only via the DPAPI-stored session; the 2026-09-01 history investigation proved no credential-shaped value ever entered Git (root file stores hash only, never tracked), and the gate now enforces the shape permanently. |
| 3.2 | Host-side recovery rotates the credential without the API | `PROVEN_REAL` — four real rotations on the owner's actual root (including recovery from a lost-intermediate incident), `rotations` advancing, `created_at` stable, sessions revoked each time. |

## Stage 4 — GitHub private repository and CI

| # | Criterion | Status |
|---|---|---|
| 4.1 | Private repo exists and receives the branch | `NOT_YET_PROVEN` (blocked on `gh auth login`) |
| 4.2 | CI runs the full gate on a clean runner | `NOT_YET_PROVEN` |

## Stage 5 — Tailscale + Hetzner production deployment (RQ-2, CLOSED 2026-09-02)

Nothing here may be marked `PROVEN_REAL` from anything but the actual Hetzner host over
the actual tailnet. The local Cloud Core restart proof (2.8) is **not** carried over: it
was loopback, and the thing under test here is the network.

| # | Criterion | Status | Notes |
|---|---|---|---|
| 5.1 | Cloud Core runs on the real VPS | `PROVEN_REAL` | 2026-09-02: deployed to `pagentos-core` (Hetzner cpx32, nbg1). All 13 subsystem checks `ok` — db, redis, object_store, temporal, broker, artifacts, voice, memory, selfhealing, evolution, security, identity, mobile. Image built on the host from the checked-out source per ADR-0032. |
| 5.2a | This PC is on the tailnet, and still opens no inbound public port | `PROVEN_REAL` | 2026-09-01, owner's machine: Tailscale `Running`, `100.92.148.30` / `mail.tail0e6789.ts.net`. Same `verify-tailnet.ps1` run re-checked the posture rather than assuming it — the agent's own pids own **zero** listening sockets, and no enabled inbound firewall rule names PagentOS. |
| 5.2b | Windows PC ↔ Cloud over the private network | `PROVEN_REAL` | 2026-09-02: `http://100.90.158.26:8001/v1/system/health` answered from the Windows machine over the tailnet; `tailscale ping` reports a DIRECT connection via `2.28.67.130:41641`, ~47 ms, not a DERP relay. |
| 5.3 | No public application port on the VPS | `PROVEN_REAL` | 2026-09-02, probed from the Windows machine against the PUBLIC IP: 8001, 22, 5432, 6379, 9000 and 7233 all refused/filtered. On the tailnet address only 8001 answers — PostgreSQL, Redis, MinIO and Temporal are closed there too, because they publish no host port at all (`ss -tlnp` shows the API bound to 127.0.0.1 and 100.90.158.26 only, never 0.0.0.0). Public SSH stays closed; administration is Tailscale SSH. |
| 5.4 | The agent moves to the cloud endpoint without reinstall or re-enrollment | `PROVEN_REAL` | 2026-09-02: existing device row restored into `pagentos_prod` from the public identity document (same device id, capabilities preserved, no private key transported); installed service repointed to `http://100.90.158.26:8001` by the transactional switch. Device shows `online` at the cloud broker. | `switch-agent-broker.ps1` changes only the broker URL (health-probed first, atomic replace, rollback). The device keeps its id and P-256 key; the cloud DB row is rebuilt from the public half by `restore-device-row.sh`. |
| 5.5 | **Hetzner Cloud Core → Tailscale → DeviceService → Companion → real Notepad → ACK** | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet: command POSTed to the Hetzner broker, executed by the LocalSystem/Session-0 service via the Session-1 companion, real Notepad pid verified alive, ACK read back from the same broker. **Audit sub-row closed 2026-09-02:** `command_received` and `command_ack` persisted in the Windows audit JSONL and correlated to the exact real `command_id` and `trace_id` of that ACK (the earlier failure was the verifier reading `at`; the writer emits `ts`). |
| 5.6 | Cloud Core process restart → agent reconnects → command succeeds | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet; recovered with no owner action. |
| 5.7 | VPS reboot → agent reconnects → command succeeds | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet. Additionally proven across the reboot: tailnet address unchanged (non-ephemeral node), `/mnt/pagentos-data` re-mounted from fstab, device row survived in `pagentos_prod`. |
| 5.8 | Tailscale reconnect (link down/up) → command succeeds | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet (`tailscale down` / flagless `up` on the host). |
| 5.9 | Temporary network loss → recovery with no owner intervention | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet (tailnet down/up on the Windows side). |
| 5.10 | Windows Service restart against the cloud broker → command succeeds | `PROVEN_REAL` | Owner-reported real matrix from `qualify-cloud.ps1`, 2026-09-02, on the actual Hetzner host over the tailnet; re-earned across the real network, not carried over from 2.7. |
| 5.11 | Persistent state really lives on the Hetzner volume, not the boot disk | `PROVEN_REAL` | 2026-09-02: the guard EARNED ITS KEEP — the 100 GB volume was attached and formatted but **not mounted**, and `/mnt/pagentos-data` existed as an empty directory on the boot disk, so the deploy would have put PostgreSQL and the identity root on a disk a rebuild discards. Now mounted from `/dev/disk/by-id/scsi-0HC_Volume_106767183` with an fstab entry (`nofail`, so a missing volume can never hang boot); `findmnt` confirms data on `/dev/sdb` while root is `/dev/sda1`. |

## Stage 6 — M12 Realtime Voice Foundation (CURRENT; pre-registered, nothing proven yet)

Rules as everywhere in this file: `PROVEN_REAL` only on the owner's real Windows machine
with a real microphone/headset, real Turkish speech, the real network and the real Hetzner
Cloud Core (ADR-0034, `ACCEPTANCE_TESTS.md` §M12). The simulator and fakes gate the code;
they prove nothing here. Latency rows require the benchmark harness's numbers from the
real run, not impressions.

| # | Criterion | Status | What will count as proof |
|---|---|---|---|
| 6.0 | The realtime provider is live on the real Cloud Core: key present inside the running api container, provider selected in `/v1/system/health`, one real client-secret mint from the Hetzner host | `PROVEN_REAL` (2026-09-02) | Owner's release run on pagentos-core (ADR-0042): env key PRESENT, compose wired, api recreated, migration 0011 applied, container Healthy, key PRESENT in container, health providers `['openai-realtime']`, real mint from Hetzner OK. Re-read independently the same evening: `RELEASE` = fb9d52e (HEAD), health `ok`, providers `['openai-realtime']`, simulator disabled (`environment=prod`). Model `gpt-realtime-2.1`; local probe proved every M12 session layer live (ADR-0038 addendum). 2026-09-03: released again to c6f27c5 after the owner's 422 (contract drift, ADR-0045): the release qualification now proves the served `contract_version` equals the tree's (2) and that `marin` and `cedar` are each minted from the host and echoed unchanged by the provider; `/v1/voice/realtime/contract` answers 401 (owner-gated) from Windows where it answered 404 before. Nothing about audio is claimed here. |
| 6.1 | Primary conversation runs on a native speech-to-speech provider chosen by capability | `NOT_YET_PROVEN` | Session record shows the selected provider and its declared capabilities; no STT→LLM→TTS chain in the conversation path. |
| 6.2 | Natural Turkish conversation, simultaneous listen/speak as far as the provider permits | `NOT_YET_PROVEN` | Owner session transcript + owner evaluation (final gate). Vendor Turkish support is UNVERIFIED in docs — measured, never assumed. |
| 6.3 | Barge-in: owner interrupts, playback stops immediately | `NOT_YET_PROVEN` | Harness `barge_in_to_stop_ms` from the real run; target < ~150 ms where achievable; stop-first ordering in the client event log. |
| 6.4 | "dur" stops immediately; "devam" resumes correctly; "tekrar oku"; "ikinci maddeyi tekrar oku"; "biraz daha yavaş/hızlı"; "özet geç"; "detaya gir"; "burayı atla" | `NOT_YET_PROVEN` | Each intent spoken by the owner, resolved by the intent resolver, and the resulting narration/session state change observed. |
| 6.5 | Semantic end-of-turn: normal Turkish hesitation is not cut off | `NOT_YET_PROVEN` | False-barge rate on the real hesitation set (`VOICE_TURKISH_EVAL_SET.md`), recorded by the harness. |
| 6.6 | Turkish pronunciation/prosody; mixed TR/EN terminology; ı İ ğ ş ç ö ü | `NOT_YET_PROVEN` | The M12 terminology and phonetics sets spoken back; owner evaluation. |
| 6.7 | Microphone switching; headset/laptop/phone mics; AEC; noise suppression; noisy room | `NOT_YET_PROVEN` | Real device switch mid-session; a noisy-room run with the harness. |
| 6.8 | Network interruption → voice-session recovery | `NOT_YET_PROVEN` | Tailnet down/up during a session; reattach observed; no owner intervention. |
| 6.9 | Conversation continuity across desktop/web/mobile | `NOT_YET_PROVEN` | `attach` from a second client with state carried; mobile leg is M15. |
| 6.10 | Long-running tool: natural preamble, session alive, mid-task redirection changes the plan | `NOT_YET_PROVEN` | Real research request; preamble spoken; "Sadece OpenAI kısmına bak" produces `plan_changed` on the same plan. |
| 6.11 | Five latency metrics recorded against the real environment | `NOT_YET_PROVEN` | Harness JSON report from the owner's machine: mic→uplink, EOT→first audio (< ~500–700 ms target), barge-in→stop, tool preamble, tool done→speech; no unexplained gaps. |
| 6.12 | Modes explicit; VoiceIdentity never the sole root of authentication | `NOT_YET_PROVEN` | Code + a real session where identity augments, never replaces, device trust + owner session. |
| 6.13 | Provider credential never leaves Cloud Core; only the ephemeral per-session credential reaches a client | `NOT_YET_PROVEN` | Real session: client receives only the ephemeral value; audit rows carry ids/timings only. |
| 6.14 | Subjective owner evaluation of voice quality | `NOT_YET_PROVEN` | The owner says so, after the real session. |

Revised acceptance target after the first real session (owner feedback 2026-09-02,
`VOICE_OWNER_FEEDBACK.md`, ADR-0043/0044). Voice character and noise processing are
qualified TOGETHER in one session; a row passes only on the owner's word for the
subjective parts and on the session's benchmark for the counted parts.

| # | Criterion | Status | What will count as proof |
|---|---|---|---|
| 6.15 | Voice character perceptually close enough to the owner's Arbor target (closest supported voice + style profile + pacing) | `NOT_YET_PROVEN` | Owner's A/B verdict between the candidates (`marin`, `cedar`) with the Arbor profile applied; the winner recorded as the configured voice. |
| 6.16 | Normal room noise (fan, keyboard, mouse, desk knock, air conditioner, chair, hum) no longer causes distracting activations | `NOT_YET_PROVEN` | Noise matrix scenarios 1–6, 9–10: false speech-start and false-turn counts from the session benchmark near zero; owner confirms no distracting activations. |
| 6.17 | Background human speech (TV, another person) does not frequently command the system | `NOT_YET_PROVEN` | Scenarios 7–8 counted honestly; an owner-directed strategy evaluated, not assumed; residual rate reported, not hidden. |
| 6.18 | Speech preservation: the owner's Turkish stays natural and complete under the final processing (no clipped onsets, no swallowed consonants, no pumping, quiet and far speech still heard) | `NOT_YET_PROVEN` | Scenarios 12–14: missed-owner-speech and interrupted-utterance counts; owner confirms naturalness. |
| 6.19 | Echo: the assistant's own speaker output never becomes owner speech, in headset and open-speaker modes; barge-in still works while it speaks | `NOT_YET_PROVEN` | Scenario 11 in both modes: no loop, barge-in latency within target. |
| 6.20 | Semantic end-of-turn unchanged by the noise pipeline: normal hesitation still does not cause premature responses; barge-in stays fast | `NOT_YET_PROVEN` | Hesitation set and barge-in metric re-measured under the final configuration. |
| 6.21 | Browser processing verified by read-back on the owner's microphone; AGC chosen from measurement, not assumption | `NOT_YET_PROVEN` | Applied MediaTrackSettings/capabilities from the owner's browser recorded in the microphone profile; AGC on/off benchmark result recorded. |

## Stage 9 — M13 Real Browser + Research (pre-registered 2026-09-03; nothing proven yet)

Real only: the owner's actual Windows machine, actual Chrome, live Internet, the real
Hetzner Cloud Core over Tailscale. The local dev-topology proof (`scripts/e2e-m13-research.ps1`)
is recorded as `PROVEN_PROXY` where it runs the real browser and live sources but not the
real cloud.

| # | Criterion | Status | Evidence required |
|---|---|---|---|
| 9.1 | Installed agent advertises `browser.chrome` and the `browser.*` operations after the installer update; worker self-check passes on the owner's machine | `PROVEN_REAL` (2026-09-03 owner run of verify-device-service.ps1 after the engine-based installer: 6b.1 worker starts as the owner from the installed tree, Chrome available; 6b.2 service and worker agree on browser.chrome + 24 operations) | `verify-device-service.ps1` report; `GET /v1/devices` capabilities from Hetzner. |
| 6b.3 | The worker executes the installed release from the installed venv: self-check from a neutral cwd reports `module.file` inside `<root>\browser\.venv`, version/contracts/worker sha256/package digest equal to the installed source, and the site-packages copy is byte-identical to the source | `PROVEN_REAL` | **PROVEN_REAL 2026-09-04 (owner run):** the installer's staged and installed self-checks and the smoke's pre-Chrome check all proved release 0.3.0 with the module inside the installed venv and the package digest equal to the checkout. verify-device-service.ps1 6b.3 (owner, unelevated). Background: 2026-09-04 the installer reported success while the live worker stayed 0.1.0 (ADR-0050 item 16); 6b.1/6b.2 (verifier) measured that a worker starts and that the manifest agrees, not which copy runs. Local proof 2026-09-04: the stale venv was reproduced from the same staging path and refused by the new assertion; the fixed build was accepted; the dev-chain harness ran a packaged worker end to end. |
| 6b.4 | The companion's live worker is that release: newest `browser_worker_started` audit row names a running pid whose executable is `<root>\browser\.venv\Scripts\python.exe`, whose command line names the installed data dir, and whose hello carried the installed version and a module inside the installed venv | `PROVEN_REAL` | **PROVEN_REAL 2026-09-04 (owner run):** the installer committed only after the post-swap live-worker health check (INSTALL VERIFIED names the live pid and release 0.3.0), and the smoke's search/lifecycle session ran on that worker (`worker_pid` in the lifecycle identity, worker image under the installed tree, parent = the installed companion). verify-device-service.ps1 6b.4; the installer's health check enforces the same after every deployment (rollback + INSTALL FAILED otherwise). |
| 9.2 | Hetzner → Tailscale → DeviceService → Companion → worker → real Chrome: `browser.session_open` + `browser.fetch_evidence` of a public page succeed with metadata | `PROVEN_REAL` (2026-09-03 owner run of `real-browser-smoke.ps1`: session, example.com, inspect/extract, search, policy refusal, close — all correlated; installed owner-session worker) | Command rows with `command_id`/`trace_id`; companion audit; Chrome window observed. |
| 9.3 | First use case end to end: plan → device selection → discovery (APIs + `browser.search`) → ≥ 8 live sources fetched through Chrome → evidence → dedup/ranking → synthesis → report | `PROVEN_PROXY` (108 discovered, 12 fetched through real Chrome, 7 findings; cloud leg pending) | `GET /v1/research/{task_id}` stats; the report JSON. |
| 9.4 | Every `source_fact` traces to a fetched source with URL, title, publisher, date, retrieval time, excerpt, device command | `PROVEN_PROXY` (every source carries url/title/publisher/date/retrieved_at/excerpt/device_id/command_id) | Report `sources` cross-checked against `research_evidence`. |
| 9.5 | Durable artifact (JSON + Markdown + PDF/DOCX) with citations preserved; visible in the inbox | `PROVEN_PROXY` (artifact READY, pdf+docx renders, `[eN]` markers in the canonical body) | Artifact id, renders, citation markers in the PDF. |
| 9.6 | Episodic research memory written with provenance; no raw page text in memory | `PROVEN_PROXY` (episodic memory keyed `research:{task_id}`; no excerpt text in the value) | Memory id; inspect endpoint. |
| 9.7 | Website error vs browser error observed for real (one unavailable/blocked source recorded as a fetch failure without aborting the job) | `PROVEN_PROXY` (a 60 s page timeout recorded as a fetch failure without aborting the run, 2026-09-03 09:49) | `fetch_failed` ≥ 1 with `page_kind`/error class recorded. |
| 9.8 | Recovery on the real chain: Cloud Core restart or Tailscale interruption mid-job → job resumes, no duplicate evidence | `PROVEN_PROXY` (Cloud Core process killed mid-fetch and restarted; job resumed, evidence rows unique) | Workflow history; evidence row count unchanged. |
| 9.9 | Device selection: explicit Turkish target ("ev bilgisayarımda araştır") resolves to the enrolled machine; `no_capable_device` when the device is offline | `PROVEN_PROXY` (`ev bilgisayarımda araştır` resolved to the enrolled device; revoked devices excluded) | `POST /v1/devices/select` results. |
| 9.10 | Owner verdict: concise executive summary, 3–7 findings that matter, sources traceable | `NOT_YET_PROVEN` | Owner feedback recorded in the memory entry. |
| 9.11 | Owner's enrolled Chrome/Edge session drives a real authenticated site (contract v2, explicit research grant) | `NOT_YET_PROVEN` | Deferred: requires the owner's browser-session authorisation (OWNER_ACTIONS item 5). |
| 9.12 | `browser.search` on the installed worker uses Google as the primary provider and returns organic results with provider evidence (`requested_provider=google`, `provider=google`, `fallback=false`), DuckDuckGo only as a recorded fallback | `DEFERRED` (optional path) | **Not a Research blocker (owner decision, 2026-09-04):** DuckDuckGo is the production provider; Google stays available explicitly. **Google success not proven.** Owner run 2026-09-04 (`-Mode search`, installed worker 0.3.0, schema 2): `requested_provider=google`, Google genuinely attempted in the installed owner-session worker, `captcha interstitial detected; not answered`, `provider=duckduckgo fallback=True fallback_reason=google:captcha`, DuckDuckGo returned usable organic results. Google organic results remain to be observed; see 9.14 for what that run DID prove and 9.15 for the owner-handoff path. OWNER_ACTIONS item 12: `real-browser-smoke.ps1 -Mode search`; command_id/trace_id correlated; installed worker executed it. |
| 9.13 | Browser lifecycle invariant on the owner machine: one research job = one worker + one PagentOS-profile Chrome process + one window; every command proves reuse (`session_uid`, `browser_pid`, `worker_pid`, bounded `tab_count`); clean exit leaves no PagentOS-profile Chrome; a hard-killed worker takes its Chrome tree with it | `PROVEN_REAL` | **PROVEN_REAL 2026-09-04 (owner run of `real-browser-smoke.ps1 -UpdateAgentFirst -Mode lifecycle`):** installed/live worker release 0.3.0, `browser.search` schema 2, lifecycle identity present, all twelve operations reused one session (`session_uid_same=True`, `browser_pid_same=True`), one PagentOS Chrome process and window, tabs bounded and back to one, session close succeeded, PagentOS-profile Chrome processes left 0, owner's own Chrome windows before=1 after=1, `REAL BROWSER SMOKE: PASS`. OWNER_ACTIONS item 13: `real-browser-smoke.ps1 -UpdateAgentFirst -Mode lifecycle`. **Root causes of the 2026-09-03 incidents (both proven by experiment under a desktop window monitor, not inferred):** (1) `browser_agent.detect` ran `chrome.exe --version` to read the version; on Windows that starts the full browser with the default profile, the window outlives the caller (probe: 1 Chrome for Testing root, 2 visible windows, 11 processes still alive after the probe exited), and with the owner's Chrome running it opens a new window in the OWNER's Chrome - this ran on every worker start, self-check, companion restart and test fixture (the "Chrome for Testing" windows were the browser test suite's worker fixtures, 9 visible windows in one run). (2) A second `launch_persistent_context` on the dedicated profile while a PagentOS Chrome already holds it fails within 0.1 s AND opens a new window in that Chrome; an orphaned worker Chrome plus any retry source added one window per attempt. **Fixes:** detection reads the PE version resource and never starts a browser (`tests/unit/test_detect_no_launch.py`); single owned research browser with orphan reaping, OS-level launch lock (named mutex), launch-rate circuit breaker with durable fault, Windows Job Object kill-on-close, ownership record, `browser_lifecycle_violation` instead of retried launches, bounded tabs, companion/installer cleanup of profile-bound Chrome only; browser tests are headless-only outside `live` and reap their own Chromium (`services/browser/tests/conftest.py`). **Headless regression evidence (2026-09-03):** `tests/browser/test_lifecycle_e2e.py` - 20 sequential ops on one `browser_pid`/`session_uid`, tab budget refused before opening, second session id refused, foreign holder reaped then exactly one recovery launch, breaker trips and blocks clean launches durably, timeout + cancellation keep one browser, second worker process refused by the lock, `taskkill /F` on the worker removes the Chrome tree; whole suite run under the window monitor with 0 visible Chrome for Testing windows and 0 processes after. **Local real-chain evidence (harness run 15, 2026-09-03 21:49-21:58):** `scripts/e2e-m13-research.ps1 -Synthesis deterministic` PASS on every step (dev stack, agent build + self-check, Cloud Core, enrolment, companion online, selection, browser smoke, Google-primary search smoke, real research through Chrome + live Internet 303 s, artifact + memory, recovery with a Cloud Core restart mid-job 156 s) while a 2 s process/window sampler (220 samples) saw at most 1 PagentOS-profile Chrome root and 1 window, the owner's own Chrome constant at 1 window, 0 Chrome for Testing processes, and 0 PagentOS Chrome after the run; the installed DeviceService (running) and Session Companion (restarted through its logon task after the owner's containment, IPC pipe present) were verified afterwards. |
| 9.14 | Provider abstraction on the owner machine: Google is the primary requested provider, Google is actually attempted in the installed owner-session worker, CAPTCHA/interstitial detection, deterministic DuckDuckGo fallback, fallback reason/evidence recorded | `PROVEN_REAL` | Owner run 2026-09-04: worker 0.3.0, schema 2, `requested_provider=google`, `provider=duckduckgo`, `fallback=True`, `fallback_reason=google:captcha`, attempt detail `captcha interstitial detected; not answered`, DuckDuckGo organic results; no retry loop, no bypass. |
| 9.15 | Owner verification handoff on the owner machine: on a Google interstitial the PagentOS Chrome window comes to the front, the page stays as it is, the SAME session (`session_uid`, `browser_pid`) stays alive while it is polled, and then EITHER the owner completes it and the pending search is retried exactly once, OR the timeout expires and the owner's chosen fallback is recorded as `provider=duckduckgo, fallback=true, fallback_reason=google:verification_timeout, verification.outcome=timeout` with organic results and a clean session close | `DEFERRED` (optional path) | **Not a Research blocker (owner decision, 2026-09-04):** the handoff is implemented, headless-proven and preserved; it is exercised only when the owner asks for the Google path. Partially observed 2026-09-04 (owner run, 600 s): interactive mode entered, interstitial detected, `WAITING_FOR_OWNER_VERIFICATION`, window brought to the front, the same session stayed alive while `browser.wait for=verification_cleared` polled, no new browser lifecycle - then the smoke failed building the fallback payload (duplicate `interstitial` key, ADR-0050 item 18), so the fallback path itself was never exercised. Fixed in worker 0.4.0 / search schema 3 with fixed-key evidence builders; headless proxies: `tests/browser/test_google_ui_e2e.py` (captcha and consent: pending, cleared, resume, timeout fallback, repeat fallback, owner declines, interstitial named exactly once) and `scripts/tests/browser-smoke-evidence.tests.ps1` (9 cases, duplicate keys structurally impossible). Owner action: item 14 with a short `-HandoffTimeoutSec`. 
| 9.16 | Research on the owner machine with the production policy: DuckDuckGo discovery (`requested_provider=duckduckgo`, `provider=duckduckgo`, `fallback=false`), one persistent owner-session Chrome for the job, real source pages opened and extracted (not snippets), dedup across repeated coverage, publication-date awareness,every source keeping title/URL/publisher, executive summary first, why each finding matters, durable command/trace evidence, clean session close, and NO deployment when the installed release already matches | `PROVEN_REAL` | Second owner attempt 2026-09-04 reached ranking again (238 discovered, 12 fetched/ranked, 0 quarantined) and failed on `KeyError: 'label'` - a model statement without its provenance label read by raw key (ADR-0050 item 21). Fixed with named, versioned entity schemas (required/optional/derived), per-item quarantine for statements and detail sections, and a boundary audit that forbids raw indexing of producer payloads anywhere in the research package; research policy version 3. First owner attempt 2026-09-04 (`f6eb5021`) reached ranking with 243 candidates discovered and 12 fetched/ranked, then failed on a typed-data defect (a model answered `finding.importance` with Turkish prose; ADR-0050 item 20). Fixed with declared field contracts, per-candidate quarantine and a deterministic-synthesis fallback; research policy version 2. OWNER_ACTIONS item 15: `scripts/research/owner-research.ps1`. Local proof 2026-09-04: dev-chain harness (packaged worker, DuckDuckGo policy) and unit/integration suites. | **PROVEN_REAL 2026-09-04 (owner run 17:28-17:33 UTC, `research-1.json`):** `OWNER RESEARCH: PASS`, findings=5, sources=5, distinct publishers=5, PagentOS Chrome before=0 after=0, deployment skipped (installed 0.4.0 matched), 240 discovered, 33 fetched, 28 refused by reason (off_topic 9, interstitial 11, date_uncertain 3, duplicate_event 1, outside_recency_window 4), one Cloud Core release to policy 4. Backlog (non-blocking, ADR-0050 item 24): some accepted findings were broad AI developments rather than agent-specific ones; semantic intent scoring is later work. Third owner attempt 2026-09-04 reached `ready` with 12 fetched/ranked items and produced ZERO findings: an off-topic/old/interstitial mix was ranked as evidence and an empty report was published (ADR-0050 item 22). Fixed with the pre-synthesis quality gate (topic, recency, page validity, duplicates), gate verdicts persisted onto evidence rows, and a findings contract with a floor of 3 attributable findings enforced on every provider; research policy version 4. |
| 9.17 | Deployment is separate from execution: with the installed worker already at this checkout's release, an owner run performs no install, no staging/swap and no service restart, and says so; a genuine difference is named and deployed only then | `PROVEN_REAL` | **PROVEN_REAL 2026-09-04:** the owner run recorded `deployment.checked=true, deployed=false, installed_release_current=true, contract_compatible=true, checkout_release=0.4.0, installed_release=0.4.0` - no install, no staging/swap, no service restart. Proven inside OWNER_ACTIONS item 15's run (the evidence JSON records `deployment.installed_release_current=true, deployed=false`). Headless proxy: `scripts/tests/agent-release-currency.tests.ps1` (7 cases). |
| 9.18 | Evidence contract fault isolation on the owner machine: a candidate or finding that violates its declared field contract is quarantined with its reason (entity, field, expected type, observed value class, stage), its raw evidence is retained, the run continues on the valid remainder, and the run fails only when fewer than 3 valid items remain | `PROVEN_REAL` | **PROVEN_REAL 2026-09-04:** the owner run completed on the contract layer with the quarantine machinery live (`quarantined=0`, nothing malformed reached synthesis; the fault-isolation legs remain covered by the headless suites). Proven inside OWNER_ACTIONS item 15's run (the evidence JSON carries any `quarantined` counts). Headless proxies 2026-09-04: `tests/unit/test_research_contracts.py` (49 cases incl. the incident value, Turkish prose/quotes/dates/numbers-in-text, missing scores, malformed structures and the number-as-title inverse) plus synthesis/evidence pipeline tests. |
| 9.19 | Research quality gate on the owner machine: off-topic, out-of-window, undated, interstitial and duplicate pages are refused BEFORE synthesis with named reasons whose counts appear in the report, the surviving evidence is deduplicated, and the report carries 3-5 findings that each cite evidence and say why they matter - or the run fails as `insufficient_valid_findings` rather than publishing an empty answer | `PROVEN_REAL` | Proven inside OWNER_ACTIONS item 15's run (the evidence JSON carries `stats.rejected_by_reason` and the findings). Headless proxies 2026-09-04: `tests/unit/test_research_eligibility.py` (46 cases), `tests/unit/test_research_regression_20260904.py` (11 cases built from the real failed run: the Granite model card, both arXiv abstracts, the "Bir dakika lutfen..." interstitial, duplicate coverage, and a fluent summary with no findings), and the incident cases in `tests/unit/test_research_browser_activities.py`. | **PROVEN_REAL 2026-09-04 (owner run 17:28-17:33 UTC, `research-1.json`):** `OWNER RESEARCH: PASS`, findings=5, sources=5, distinct publishers=5, PagentOS Chrome before=0 after=0, deployment skipped (installed 0.4.0 matched), 240 discovered, 33 fetched, 28 refused by reason (off_topic 9, interstitial 11, date_uncertain 3, duplicate_event 1, outside_recency_window 4), one Cloud Core release to policy 4. Local proof 2026-09-04: the dev-chain harness against real Chrome and the live web (`scripts/e2e-m13-research.ps1`) - 251 discovered, 24 fetched, 19 refused (off_topic 10, interstitial 8, outside_recency_window 1), 5 evidence, 5 findings each citing a distinct source, plus recovery after a Cloud Core restart mid-job. Three defects that only a live run could show were fixed first (ADR-0050 item 23). |

## Stage 10 — M16 Activity Ledger + Self Explanation + Voice Narration (pre-registered 2026-09-04; nothing proven yet)

Real only: the owner's real Windows machine, microphone and headset, Turkish speech, the
web voice shell against the real Hetzner Cloud Core. The headless proxies (simulated
realtime session over the real control plane, the real 2026-09-04 research run as
evidence) are gates, never acceptance. No seeded or demo events exist.

| # | Criterion | Status | Evidence required |
|---|---|---|---|
| 10.1 | "Son yaptıklarını anlat" is answered from the activity ledger: the briefing names the real research qualification, its counts and its refusals, every sentence a known fact with an evidence reference; nothing invented | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** the voice UI answered evidence-backed self explanation; the owner heard the real research task identity, the discovered/fetched/evidence/rejected counts, installed browser worker 0.4.0, the research policy and that deployment was skipped - all of it from the ledger, none of it seeded. The automated acceptance for this row was brittle (it matched a Turkish sentence prefix); it now verifies STRUCTURE - cited ledger event ids, the research job they belong to, resolvable evidence kinds and numbers equal to the run's own record (ADR-0051 addendum 4). Re-verifiable any time with `owner-explain.ps1 -VerifyOnly`. `explain-1.json` → `activity.tool_calls[activity.explain]` with `level=executive`, `facts ≥ 5`, `uncertainties = 0`, `evidence_count ≥ 1`, `speech_head` starting "Efendim, son ara…"; the `research.qualified` ledger event carries the SHA-256 of the owner's `research-1.json`. Headless proxy 2026-09-04: `tests/unit/test_explain_engine.py` (16, incl. the exact owner sentence and the no-verdict/no-evidence cases), `tests/unit/test_voice_explain_tools.py`. |
| 10.2 | "Araştırmayı detaylandır" reads the actual findings (title, summary, why it matters, source) and "Teknik anlat" the technical evidence (versions, counts, ids) from the same briefing artifact | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** "detaylandır" and "teknik anlat" produced the detailed and technical explanations in the real session. The intents are now normalised whichever tool the provider routes them to. a tool call (`narration.control` or `activity.explain` - the provider chooses the route, the record carries the normalised intent) with `intent=detail`/`technical`, `action=jump_level`, `speech_chars > 0`; the artifact body sections `# Ayrıntı` / `# Teknik`. |
| 10.3 | "Dur" stops speech at once (client stop-first ordering unchanged) and the narration cursor is placed at the first sentence not fully spoken, from the transcript the client reported - never stored | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** "Dur" stopped active speech and the narration cursor was persisted. `activity.client_events`: `spoken(final=0)` immediately before `barge_in_start`, `aligned=1`, `action=paused`, `spoken_chunks`; no `text` key in any audit row; ledger `voice.narration.paused`. Headless proxy: `tests/unit/test_narration_align.py` (7), the spoken-never-audited test; "Dur" idempotence (a second "dur", or one after the answer finished, is a quiet state change, never an error) in `tests/unit/test_voice_explain_tools.py` and the web client's `dur.test.ts`. |
| 10.4 | "Devam et" resumes from that cursor - the same semantic point, not a replayed offset | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** "Devam et" resumed from the paused semantic position. `activity.tool_calls[narration.control]` with `intent=resume`, `narration_state=READING`, `speech_head` equal to the sentence after the last fully spoken one. |
| 10.5 | The ledger is backfilled only from canonical rows (research runs/reports, voice audit, releases, incidents), idempotently, each event referencing its source; a re-run records nothing new | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05** by `owner-explain.ps1 -VerifyOnly` against the real Cloud Core: the backfill examined 33 canonical rows (research_failed 2, research_completed 2, research_quality_gate 4, voice_session 25), created NOTHING and skipped all 33 - `total_created: 0`, which is the criterion. Recorded in `explain-1.json`. Superseded evidence note: | The backfill runs inside the owner command and its report is written to the evidence file; the run that would have captured it failed later, at the session step, so no durable copy reached the repository. `owner-explain.ps1 -VerifyOnly` (about fifteen seconds, no talking) records it. `explain-1.json` → `backfill` report from the real Cloud Core; a second backfill in the same run reporting zero new events. Headless proxies: `tests/unit/test_ledger_service.py`; real dev database `tests/integration/test_ledger_explain_over_real_runs.py` (backfill of the actual research runs, second run creates zero, the briefing states their counts from their rows). |
| 10.6 | The completed session is recorded end to end (explanation, pause, resume) in the ledger and the session activity record; the session closes cleanly | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05** by `owner-explain.ps1 -VerifyOnly` reading the owner's completed session `b09d5cb5` (started 2026-09-04T20:56:28Z): 8 tool calls and 161 client events; the ledger carries `voice.explained` x14 and `voice.narration.paused` x7; session `state=closed`. Superseded evidence note: | The session completed on the owner's machine, but the harness failed before it read the durable record, so the end-to-end evidence was never captured. `owner-explain.ps1 -VerifyOnly` reads the completed session and closes this row without another voice test. `GET /v1/ledger/events?subsystem=voice` with `voice.explained` and `voice.narration.paused`; session `state=closed`. Final line `OWNER EXPLAIN: PASS`. |
| 10.7 | While the assistant speaks, another person's distant speech in the room does NOT stop it; "Dur" (and bekle / sus / kes / bir dakika) stops it at once; a real owner interruption is honoured | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** background human speech in the room did not stop the narration, and explicit "Dur" stopped it immediately. `explain-1.json` → `activity.noise`: `rejected_background_speech ≥ 1` during the distant-voice step, `explicit_stop_command ≥ 1` or an accepted interruption for "Dur", `false_interruption = 0`; the owner's verdict that it kept speaking over the other voice. Headless proxy: the web client's `interruption.test.ts`. |
| 10.8 | Spoken answers respect the listening budgets: executive two to four sentences (~10-20 s), detailed cut at a sentence boundary (~30-60 s), technical concise; only "hepsini oku" reads everything; the cursor keeps section/item position across pause/resume | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** the concise executive briefing and the detailed and technical levels were heard as intended. `activity.tool_calls[activity.explain].speech_chars ≤ 420`; `intent=technical` call `speech_chars ≤ 700`; the owner's verdict on length. Headless proxies: `test_explain_engine.py` (budgets), `test_voice_explain_tools.py` (full read, level routing). |
| 10.9 | "Son yaptıklarını anlat" summarises owner-relevant activity - never the previous explanation or voice bookkeeping - and says whether anything needs the owner | `PROVEN_REAL` | **PROVEN_REAL 2026-09-05 (owner's real session, reported by the owner):** the briefing summarised the research qualification rather than the previous narration. The executive briefing names the research qualification while newer `voice.explained` / `voice.session.*` events exist in the ledger. Headless proxy: `test_the_explanation_itself_never_leads_the_next_explanation`. |

### Stage 10 status after the 2026-09-05 `-VerifyOnly` run

**Owner decision, 2026-09-05:** accept the direct real-session evidence and close these as
`PROVEN_REAL`: Activity Ledger, Self Explanation, Voice Narration, concise executive
narration, detail/technical narration, background-human-speech rejection, explicit `Dur`,
narration cursor persistence, semantic `Devam et`. Each rests on its own durable evidence,
not on the provenance re-check.

The structural-provenance re-check MECHANISM is recorded separately as
`NOT_YET_PROVEN / DEFERRED`, because the only completed owner session predates the
deployed provenance block. That is a gap in the verifier's own coverage and is explicitly
NOT a failure of the capabilities it verifies. No production release is to be performed to
exercise it. When a future legitimate Cloud Core change requires a normal release, the
already-committed provenance implementation ships with it, and the next ORDINARY real
Voice/Self Explanation session after that release closes the mechanism with no dedicated
qualification.

All ten rows are `PROVEN_REAL`. 10.1-10.4 and 10.7-10.9 were closed on the owner's real
session; 10.5 and 10.6 were closed by re-reading that same completed session, with no new
voice session and nothing spoken.

**One acceptance MECHANISM remains unexercised, and it is not a product gap.** The
structural-provenance re-check (ADR-0051 addendum 4) cannot run against session
`b09d5cb5`: the code that emits the `provenance` block on a tool-call record landed in
commit `3603555` at 2026-09-04T21:12Z, and the session started at 2026-09-04T20:56Z -
sixteen minutes earlier. The deployed Cloud Core (`547a5a3`) does not contain that code
either. So the three provenance checks fail on that session for a reason no harness fix can
remove, while every behavioural check in the same run passes. `explain-1.json` records this
as `diagnosis: provenance_not_recorded_by_the_build_that_ran_this_session`.

To exercise it: deploy a Cloud Core built from a commit at or after `3603555`, then let it
ride along with the next ORDINARY voice session. It needs no qualification of its own and
the owner is not to repeat the long test for it.

## Stage 11 — M17 Cognitive Foundations (pre-registered 2026-09-05; nothing PROVEN_REAL yet)

Everything below is `PROVEN_PROXY` at best: automated tests and dev-database runs. None of
it has been heard by the owner in a real Turkish voice session, so none of it is
`PROVEN_REAL`. The proxies are gates, never acceptance. The one thing this stage does NOT
need is another long owner qualification: these rows ride along with the next ordinary
voice session.

| # | Criterion | Status | Evidence required |
|---|---|---|---|
| 11.1 | "Ne öğrendin?" is answered from compiled lessons with the incidents that produced them; a lesson with no evidence is never spoken as fact | `PROVEN_PROXY` | Unit: `test_experience_engine.py` (8), `test_experience_compiler.py` (11), `test_experience_routes.py` (10); the Phase-9 branch in `test_explain_engine.py`. Real: the owner hears a lesson naming a real incident. |
| 11.2 | "Kendi üzerinde ne geliştiriyorsun?" / "Canlıya alınmayı bekleyen ne var?" answer from the opportunity backlog, and a SHADOW_READY candidate is named as ready-but-not-deployed | `PROVEN_PROXY` | Unit: `test_evolution_backlog.py` (216), `test_evolution_boundaries.py`, the evolution branches in `test_explain_engine.py`. Real: the owner hears the shadow candidate and that it awaits approval. |
| 11.3 | The lab cannot reach production by any path: no lab-issued authority holds a production grant, `OWNER_APPROVED` needs an owner-session capability, `LIVE` needs an owner-approved release, root policies are immutable from lab code | `PROVEN_PROXY` | Unit: `test_evolution_authority.py`, `test_evolution_boundaries.py`, `test_evolution_guards.py`, plus the exhaustive illegal-transition matrix. This row is structural: it is proven by the code refusing, not by an owner watching it refuse. |
| 11.4 | Every backlog transition writes exactly one ledger event; no status maps to nothing | `PROVEN_PROXY` | Unit: `test_every_lifecycle_status_writes_exactly_one_ledger_event`, plus the quarantine/superseded/test-failure regression tests (2026-09-05). |
| 11.5 | The world model never reports a source-only fact as installed or runtime truth, and says "I don't know" rather than answering from a stale index | `PROVEN_PROXY` | Unit: `test_worldmodel.py` (19), `test_selfmodel_query.py` (26). Two real defects of this class were found by independent review on 2026-09-05 and fixed with regression tests. |
| 11.6 | The self model indexes only files inside the checkout - a junction or symlink out of the tree is never read or parsed | `PROVEN_PROXY` | Unit: `test_selfmodel_indexer.py` (24), including the containment regression. |
| 11.7 | "Diagnostic Observer'da sorun ne?" and "Bu özelliği neden geliştirdin?" are answered with module provenance, separating known fact from inference | `PROVEN_PROXY` | Unit: `test_selfmodel_routes.py` (13), `test_explain_engine.py`. Real: the owner hears the distinction stated aloud. |
| 11.8 | Goals progress through the cognitive loop with evidence-checked success criteria; the Critic can send a step back; every state change is in the ledger | `PROVEN_PROXY` | Unit: `test_goals_service.py` (26), `test_cognitive_loop.py` (12), `test_goals_routes.py` (15). |
| 11.9 | Every new subsystem publishes truthful UI state (ADR-0052) with no content, no transcript and no audio sample; `progress` is null where unknown | `PROVEN_PROXY` | Unit: `test_uistate.py` (11) plus the per-subsystem publisher tests. The renderer does not exist yet by decision. |
| 11.11 | The Evolution Engine drives a real candidate from IDEA to SHADOW_READY through the real service, records every transition in the ledger, and stops there: not deployed, not approved, wired to nothing | `PROVEN_PROXY` | The Acceptance Wording Guard, built from two real recorded incidents. `services/api/lab/candidates/acceptance_wording_guard/evidence/lifecycle_run.json`: seven `evolution.*` ledger events, six UI states, four evidence refs all `verified: true`, composite 0.577, ending at `evolution.owner_approval_required` with `approved_by: null`. It lives outside `app/`, outside the wheel (`packages = ["app"]`), has no `__init__.py`, and no production module imports it - asserted on the import graph via `ast`, not by grep. Unit: `test_lab_acceptance_wording_guard.py` (42). |
| 11.12 | An in-process caller cannot forge production authority by subclassing, `dataclasses.replace`, pickle, or rebuilding the object around its constructor | `PROVEN_PROXY` | The critical finding of the 2026-09-05 security review, reproduced and then closed. `test_evolution_authority.py`: subclass refused, forged instance refused for every production action, pickle round-trip cannot promote, and a genuine owner authority still works. In-process containment against arbitrary code is explicitly NOT claimed (ADR-0053 addendum 1). |
| 11.10 | No subsystem added in M17 starts a background loop at application startup | `PROVEN_PROXY` | `app/main.py` wires routers only; asserted by inspection and by the absence of any scheduler registration. Re-check on any change to startup. |

### Stage 11 CLOSED — M17 is PROVEN_REAL (2026-09-05)

The owner's third M17 voice run passed: `OWNER EXPLAIN: PASS`, all six cognitive paths
reached by voice and answered from real durable data. `PROVEN_REAL` and closed for:

* Memory + Experience Compiler
* Goal Engine (answering "no active goals", which is the true state)
* World Model (four truth kinds, uncertainty and staleness stated)
* Self Model / Code Intelligence
* Evolution Engine foundation
* production-authority knowledge and boundary
* voice access to all of the above

Not to be reopened without a genuine regression. It took three attempts, and the first two
failed on the CHECKER rather than the product — the record of that is below, because the
defect class it exposed (acceptance depending on metadata the system never wrote down) is
the same one this milestone was built to remove.

### Stage 11 after the owner's first M17 voice run (2026-09-05) — FAILED, and why

The run is worth recording in full, because four of its five findings were defects in the
CHECKER rather than in the product.

Real session `0b186069` (closed cleanly, 5 tool calls, 102 client events):

| Question | what really happened |
|---|---|
| ne öğrendin | answered from 4 real lessons, 19 facts, 4 uncertainties |
| hangi hedeflerin var | "Kayıtlı bir hedefim yok." — correct, no goal exists |
| ne görüyorsun | **`activity.explain` FAILED**, `error_class=internal_bug` |
| kendi kodun | answered, 222 real modules |
| gece ne geliştirdin | answered, the real SHADOW_READY candidate |
| canlıya alabilir misin | **no tool call was made at all** |

Yet the harness reported memory, goals, the self model and evolution as "not reached by
voice". Four separate causes, now fixed:

1. **Routing metadata, not routing.** Every call recorded `query_kind=""`, because the
   field was read from the narration intent resolver — which resolves controls like "dur"
   and returns `None` for a question. The engine knew the kind all along and never wrote it
   down. Briefings now carry a `cognition()` record (query_kind, the subsystem that
   answered, counts, evidence kinds, entity ids) and the durable row reads it from there.
   Nothing infers a subsystem from generated Turkish.
2. **The World Model crash was real**, and was ours: the branch shadowed the briefing's
   `facts` accumulator (a dict) with a local list, so `provenance()` called `dict(<list>)`
   during persistence. `Briefing` now refuses a non-mapping `facts` where it is built.
3. **The sixth question never reached the tool.** "Bunu canlıya alabilir misin?" reads as a
   request for permission, and the model answered it conversationally. Whether this system
   may deploy is a fact about policy and never the model's to assert; the tool contract and
   persona now name authority questions explicitly and forbid answering from belief.
4. **The harness inherited M16 acceptance** — research-job provenance, the listening
   budget, `teknik anlat`, the interruption counters, `dur`, `devam et` — and failed M17 for
   the absence of steps nobody performed. M17 now asserts only its own six capabilities,
   names exactly which question produced no answer, and distinguishes "never routed" from
   "routed and the tool failed".

**Structural re-verification against the live production database (Cloud Core `b6b64ef`):
ALL SIX PASS.** Each routes to the intended kind, is recorded against the intended
subsystem, survives persistence, carries facts or an explicit uncertainty, and cites its
OWN subsystem's records — `world_fact`/`world_snapshot`, `code_module`/`code_index`,
`evolution_opportunity`, `authority_policy`/`root_policy`. No cognitive answer is required
to cite a research job; research provenance belongs to research answers.

`goals` cites nothing and carries one uncertainty. That is the honest shape of an empty
subsystem, and it is a PASS.

### Stage 11 status after the 2026-09-05 production bring-up

M17 is DEPLOYED (Cloud Core `871d9c3`, migrations 0016 and 0017) and every one of the
owner's six questions was answered from the REAL production database, verified by running
the real explain engine inside the deployed container against the live rows:

| Question | kind | answered from | spoken |
|---|---|---|---|
| Son yaşadığın hatalardan ne öğrendin? | `learned` | 4 compiled lessons, from production's own incidents and ledger | 482 chars |
| Şu anda hangi hedeflerin var? | `goals` | the goal engine, which is empty | "Kayıtlı bir hedefim yok." |
| Kendi sisteminde şu anda ne görüyorsun? | `world_state` | 18 observations across ALL FOUR truth kinds, 2 uncertainties | 171 chars |
| Kendi kodun hakkında ne biliyorsun? | `self_code` | 222 real indexed modules, 3813 symbols | 92 chars |
| Gece kendi üzerinde ne geliştirdin? | `evolution` | the real SHADOW_READY Acceptance Wording Guard | 162 chars |
| Bunu canlıya alabilir misin? | `can_deploy` | the authority module itself | 299 chars |

**"Kayıtlı bir hedefim yok" is a PASS, not a gap.** No goal has been created, and inventing
one to make a qualification look better is precisely what this milestone forbids. The check
is that the Goal Engine is reached and answers honestly, which it does.

Everything above is `PROVEN_PROXY` in the strict sense the matrix uses - it is real durable
data, but the owner has not heard it. The one thing structural verification cannot prove is
REACHABILITY: that the owner can ask these questions out loud and be understood. That is
what `owner-explain.ps1 -M17` is for, and it is the only thing it is for.

Not deployed and not approved: the Acceptance Wording Guard remains `SHADOW_READY`.

## Stage 12 — M18 Holographic Core, Active Eye, ambient presence (pre-registered 2026-09-06; nothing PROVEN_REAL yet)

Everything below is `PROVEN_PROXY` at best. This stage is unusual in how far proxy is from
real: every claim here is about the owner's actual room, their actual camera and their
actual production system, and a fixture cannot be wrong about any of those. A green test
suite means the code is honest about what it was told; it says nothing about whether the
camera saw anyone.

Two rows are deliberately structural — proven by code refusing rather than by the owner
watching it refuse — and are marked as such.

| # | Criterion | Status | Evidence required |
|---|---|---|---|
| 12.1 | The Core renders on the owner's machine and shows genuine state: listening, thinking and speaking transitions correspond to what actually happened | `PROVEN_PROXY` | Web: `tests/uistate/visual.test.ts`, `render.test.tsx` — every contract state produces its own visual and no other state's. Real: the owner watches the Core during a real voice turn. |
| 12.2 | Silence is drawn as four different facts — reported-idle, never-told, a claim that aged out, unreachable — and none is a calm breathing core | `PROVEN_PROXY` | Web: the silence block in `visual.test.ts`. Real: the owner sees the difference when Cloud Core is briefly unreachable. |
| 12.3 | Channels do not displace one another: `owner.likely_asleep` does not blank a thinking core, and a deployment in flight is not drawn as the agent's own work | `PROVEN_PROXY` | Web: `tests/uistate/ambient.test.ts`. Structural. |
| 12.4 | Research, memory and evolution state actually appear on the Core while those subsystems run | `PROVEN_PROXY` | API: `test_experience_uistate_signal.py`, `test_research_uistate.py` — the stamped event is read back off a real publisher. Two subsystems could not publish at all before 2026-09-05 and nothing noticed, because both failures were silent. Real: the owner sees memory and research light the Core during a real run. |
| 12.5 | The real camera can be enabled and the indicator is truthful; `untold`, `disabled` and "a state this build cannot read" are three different renderings | `PROVEN_PROXY` | Web: `tests/uistate/ambient-render.test.tsx`. Real: the owner enables the camera and sees the indicator agree with the machine's own camera light. |
| 12.6 | A real presence transition is detected from the owner actually leaving and returning | `PROVEN_PROXY` | API: `test_presence_engine.py` (fusion, sustained evidence, conflicting signals). Real: only the owner leaving the room can prove this. |
| 12.7 | Disabling the Active Eye stops perception immediately — the camera light goes out and camera-sourced observations are refused | `PROVEN_PROXY` | API: `test_presence_routines_wiring.py::test_the_eye_disable_path_stops_camera_observations_immediately`. Real: the owner says `Gözünü kapat` and watches the light. |
| 12.8 | No raw camera archive is created: nothing on disk or in the database holds an image, a frame, or anything derived from one beyond the seven structured fields | `PROVEN_PROXY` | API: `test_presence_observations.py` — refusal by key shape and independently by value shape, and a route-level test that a refused observation never reaches the ledger. Real: an inspection of the machine and the database after the run. |
| 12.9 | Perception is never authentication: no presence observation grants, influences or substitutes for an owner session | `PROVEN_PROXY` | API: `test_presence_never_grants_or_influences_authority` — a confidently established presence state, then an unauthenticated client still refused on every presence route. **Structural**: proven by the code refusing. |
| 12.10 | Presence is stated as an inference with its confidence, never as a fact; a stale observation degrades to unknown rather than to "still present" | `PROVEN_PROXY` | API: `test_presence_states.py`, `test_routines_presence_link.py`. Web: `ambient.test.ts` (the publisher's own `ttl_s` beats the client default). |
| 12.11 | A brief movement at 03:00 does not produce a morning greeting, and evaluating a greeting does not deliver one | `PROVEN_PROXY` | API: `test_presence_greeting.py` (both gates independently), `test_presence_greeting_delivery.py` (asking twice still says yes; only a recorded delivery starts the cooldown). Real: the owner wakes up and is greeted once. |
| 12.12 | A routine can be created and a short test alarm fires; the wake volume ramps and never jumps to full | `PROVEN_PROXY` | API: `test_routines_dispatch.py` (the alarm routes to `desktop.alarm_start` with the firing id, the ramp is re-asserted at dispatch). Windows: `AmbientCapabilityTests` — the ramp starts low and climbs monotonically, a start at or above 0.5 is refused, an end above 0.85 is clamped and reported, the generated samples never exceed the level, the first audio the owner hears is at the start level, an alarm nobody stops stops itself. **Real** needs the next Windows agent install: the installed agent predates the alarm pair. |
| 12.13 | An owner-selected media action executes and plays the item the owner named | `PROVEN_PROXY` | API: `test_routines_dispatch.py` — opens an isolated browser session then navigates to the owner's exact URL, byte for byte; never navigates if the session open fails; a missing URL is refused without touching a device. No CAPTCHA/anti-bot handling exists anywhere in the dispatcher. Real: the owner names a video and it plays on the real chain. |
| 12.14 | A routine asks the Presence Engine rather than its caller, and an unknown presence skips it with a reason naming why | `PROVEN_PROXY` | API: `test_routines_presence_link.py`, `test_presence_routines_wiring.py`. |
| 12.15 | No routine fires without something asking: there is no background timer, and every transition writes exactly one ledger event | `PROVEN_PROXY` | API: `test_routines_service.py`. **Structural.** |
| 12.16 | The Activity Ledger records the whole run: presence transitions, eye enable/disable, routine firings | `PROVEN_PROXY` | API: the per-subsystem route and service tests. Real: the owner asks what happened and hears it. |
| 12.17 | A real SHADOW_READY candidate is visible in the Core, and `Bunu canlıya alabilir misin?` explains the authority model and deploys nothing | `PROVEN_PROXY` | M17 proved the spoken half (Stage 11, `PROVEN_REAL`). The Core half is `ambient-render.test.tsx`: the release band renders no button, form or input. Real: the owner sees the candidate and asks the question. |
| 12.18 | Evolution cannot promote itself, and owner authorisation cannot be minted by Evolution-generated code | `PROVEN_PROXY` | API: `test_evolution_authority.py`, `test_evolution_boundaries.py`, `test_evolution_authorize.py`. **Structural**, and carried forward from Stage 11.12. TWO bypasses have now been found in this kernel, both by someone building against it rather than reading it: the subclass forge (2026-09-05) and, on 2026-09-06, `advance()` classifying a transition by its target alone — so a lab actor could divert an opportunity that was mid-deployment into `quarantined` with only its default `propose_candidate` grant. Both closed with regressions. A third is a reasonable expectation, not a surprise. |
| 12.19 | Display power automation turns a monitor off and never shuts down, reboots, hibernates or suspends the machine | `NOT_YET_PROVEN` | Built and deliberately unreachable, twice: `desktop.display_off` on the companion is advertised and routed only behind `PAGENTOS_AGENT_DisplayPowerEnabled` (default false), and Cloud Core refuses a routine's `display_action` behind `DISPLAY_ACTION_QUALIFIED=False`. A test reads `DisplayPowerController.cs` and fails if any shutdown/suspend/hibernate/logoff API name appears. **This row has its own separate qualification**, excluded from the main M18 run: a wrong inference here interrupts unrelated owner work. |
| 12.20 | No test launches a browser, and no camera fixture contains real owner imagery | `PROVEN_PROXY` | `services/browser/tests/test_test_isolation_guards.py` exists because the browser risk was once realised on this owner's desktop. The Core's own suite is `react-dom/server` only, by decision. |
| 12.21 | Voice connects FROM `/core`: the owner opens one page, and the realtime session, the microphone, the speaker and the narration state are owned there — `/voice` is a diagnostics view of the same session, not a second one | `PROVEN_PROXY` | Web: the voice store's single-instance tests (one rig, one `getUserMedia`, a second `connect()` while one session is live is refused, not stacked). Real: `owner-m18.ps1` v2 `voice.connected_from_core` (a new web session since the baseline that asked `activity.explain`) and `voice.single_session` (no two web sessions of the run open at once, from the sessions' own started/ended instants). |
| 12.22 | The Core's voice states are real: listening is the client's own speech gate opening, speaking is the first generated audio arriving, interrupted stops the motion at once — nothing is synthesised for effect | `PROVEN_PROXY` | Web: `visual.test.ts` — the overlay drives from controller snapshots and the playback analyser, and an `interrupted` snapshot zeroes the pulse. API: `_UI_STATE_BY_EVENT` maps the session's timing events to `agent.listening` / `agent.speaking`. Real: `voice.owner_spoke_core_listened` (`mic_speech_start` on the session), `voice.core_reacted_to_speech` (`first_audio` on the session). |
| 12.23 | A cognitive request spoken at the Core reaches its subsystem through the existing Realtime path, and the engine's own `query_kind` + `subsystem` are on the record — never inferred from prose | `PROVEN_PROXY` | Carried from Stage 11 (`PROVEN_REAL` from `/voice`). Real from `/core`: `voice.cognitive_request_reached_subsystem`, `voice.answered_through_realtime` (a succeeded tool call with spoken characters). |
| 12.24 | `Gözünü kapat` spoken at the Core disables perception durably and the local camera loop stops: the ledger row's reason is `voice:<phrase>`, not the control's `owner_stop` | `PROVEN_PROXY` | API: the realtime service applies `disable_eye` deterministically on the `EYE_DISABLE` intent. Web: `EyeControl` stops the local loop when the bus says `eye.disabled` (`stopLocalOnly`). Real: `eye.disabled_by_voice`, `eye.disabled`. The first real run (2026-09-06 morning) DID disable the eye at 09:46:44 — the harness reported it as never having happened because it gated the disable on a second presence state. Harness defect, fixed and pinned. |
| 12.25 | "Camera enabled" and "a current, valid presence observation exists" are two different facts on the Core: the camera being open never implies the owner is present | `PROVEN_PROXY` | API: `owner.presence` stays `no_observations_yet` with the eye on and nothing observed (`test_worldmodel`); the presence service heartbeats a held state at half its TTL so a live claim is not shown as unknown (`test_presence_heartbeat.py`). Web: `presence-memory.test.ts` replays the first real run — a seated owner who fidgets is present; an empty room is absent at ≤ 0.2. Real: `presence.current_observation`, `world.presence_fact`, `core.presence_event_published`. |

**First real run, 2026-09-06 morning — what was real before the harness crashed:** the Cloud
Core release succeeded (`ROUTINES_VERSION` current), the Windows agent update was verified
and the alarm pair advertised, `/core` answered, the alarm routine triggered on evaluation.
Those are kept as evidence; nothing is redone for its own sake. What the run also showed:
`owner-m18.ps1` v1 crashed on `The property 'Count' cannot be found on this object` — a
one-element `dispatch_results` array unrolled by a parenthesised call inside an
if-expression (`scripts/tests/owner-harness.tests.ps1` pins the exact form, and the fix);
the seated owner was read as `away` at 0.75 for eleven minutes on 1,102 observations
(ADR-0062: the client measured whole-frame mean motion; rewritten); the Core showed
"Sahip durumu bilinmiyor" for ten of those minutes (a held state was never republished;
now heartbeats). And `/core` could not hear the owner at all, because voice lived on
`/voice` (ADR-0061). The second attempt is `owner-m18.ps1` v2, twelve points from `/core`.

## Stage 7 — Real voice

| # | Criterion | Status |
|---|---|---|
| 7.1 | Real STT/TTS providers behind the existing abstraction | `NOT_YET_PROVEN` (blocked on provider keys) |
| 7.2 | Owner speaker enrollment against real samples | `NOT_YET_PROVEN` |
| 7.3 | Turkish STT/TTS quality qualification | `NOT_YET_PROVEN` |

## Stage 8 — Real mobile

| # | Criterion | Status |
|---|---|---|
| 8.1 | Push arrives on a locked phone | `NOT_YET_PROVEN` (server contract is `PROVEN_PROXY`, see M9) |
| 8.2 | Microphone/realtime under OS backgrounding | `NOT_YET_PROVEN` |
| 8.3 | Narration continues across PC and phone | `PROVEN_PROXY` (cross-session cursor resume proven against the real API) |

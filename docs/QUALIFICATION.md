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
| 9.12 | `browser.search` on the installed worker uses Google as the primary provider and returns organic results with provider evidence (`requested_provider=google`, `provider=google`, `fallback=false`), DuckDuckGo only as a recorded fallback | `NOT_YET_PROVEN` | OWNER_ACTIONS item 12: `real-browser-smoke.ps1 -Mode search`; command_id/trace_id correlated; installed worker executed it. |
| 9.13 | Browser lifecycle invariant on the owner machine: one research job = one worker + one PagentOS-profile Chrome process + one window; every command proves reuse (`session_uid`, `browser_pid`, `worker_pid`, bounded `tab_count`); clean exit leaves no PagentOS-profile Chrome; a hard-killed worker takes its Chrome tree with it | `NOT_YET_PROVEN` (owner) / `PROVEN_PROXY` (headless regression suite, 2026-09-03) | OWNER_ACTIONS item 13: `real-browser-smoke.ps1 -UpdateAgentFirst -Mode lifecycle`. **Root causes of the 2026-09-03 incidents (both proven by experiment under a desktop window monitor, not inferred):** (1) `browser_agent.detect` ran `chrome.exe --version` to read the version; on Windows that starts the full browser with the default profile, the window outlives the caller (probe: 1 Chrome for Testing root, 2 visible windows, 11 processes still alive after the probe exited), and with the owner's Chrome running it opens a new window in the OWNER's Chrome - this ran on every worker start, self-check, companion restart and test fixture (the "Chrome for Testing" windows were the browser test suite's worker fixtures, 9 visible windows in one run). (2) A second `launch_persistent_context` on the dedicated profile while a PagentOS Chrome already holds it fails within 0.1 s AND opens a new window in that Chrome; an orphaned worker Chrome plus any retry source added one window per attempt. **Fixes:** detection reads the PE version resource and never starts a browser (`tests/unit/test_detect_no_launch.py`); single owned research browser with orphan reaping, OS-level launch lock (named mutex), launch-rate circuit breaker with durable fault, Windows Job Object kill-on-close, ownership record, `browser_lifecycle_violation` instead of retried launches, bounded tabs, companion/installer cleanup of profile-bound Chrome only; browser tests are headless-only outside `live` and reap their own Chromium (`services/browser/tests/conftest.py`). **Headless regression evidence (2026-09-03):** `tests/browser/test_lifecycle_e2e.py` - 20 sequential ops on one `browser_pid`/`session_uid`, tab budget refused before opening, second session id refused, foreign holder reaped then exactly one recovery launch, breaker trips and blocks clean launches durably, timeout + cancellation keep one browser, second worker process refused by the lock, `taskkill /F` on the worker removes the Chrome tree; whole suite run under the window monitor with 0 visible Chrome for Testing windows and 0 processes after. **Local real-chain evidence (harness run 15, 2026-09-03 21:49-21:58):** `scripts/e2e-m13-research.ps1 -Synthesis deterministic` PASS on every step (dev stack, agent build + self-check, Cloud Core, enrolment, companion online, selection, browser smoke, Google-primary search smoke, real research through Chrome + live Internet 303 s, artifact + memory, recovery with a Cloud Core restart mid-job 156 s) while a 2 s process/window sampler (220 samples) saw at most 1 PagentOS-profile Chrome root and 1 window, the owner's own Chrome constant at 1 window, 0 Chrome for Testing processes, and 0 PagentOS Chrome after the run; the installed DeviceService (running) and Session Companion (restarted through its logon task after the owner's containment, IPC pipe present) were verified afterwards. |

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

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

Last updated: 2026-09-01.

---

## Stage 1 — Session-0 service/companion IPC identity (M1 security finding #1)

The finding: under a real Windows Service the pipe's ACL and name both derive from the
*creating* process, so "ACL it to the current user" stops meaning "the owner" the moment the
service becomes LocalSystem — and there was no mutual authentication beyond that ACL.
Closed by ADR-0028 with four layers: DACL, first-instance creation, kernel-sourced peer
admission, and per-connection freshness.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1.1 | Pipe DACL names the owner account explicitly, not "whoever created me" | `PROVEN_PROXY` | `CompanionPipeServer.CreateServerStream` ACLs SYSTEM + configured `CompanionSid`; exercised in every pipe test, but with both halves running as one user. Real separation needs the service installed. |
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
| 1.16 | The pipe-owner inspector reads a real owner and accepts a trusted one | `PROVEN_PROXY` | `The_owner_inspector_reads_a_real_owner_sid_and_the_policy_accepts_it` proves the real `GetSecurityInfo` path resolves an owner and that a policy trusting it accepts. A test process cannot create a SYSTEM-owned pipe, so acceptance of an actual service-owned pipe stays proxy until the service is installed. |
| 1.11 | Service runs as LocalSystem in Session 0 with the companion in the owner's session | `NOT_YET_PROVEN` | The service is registered as LocalSystem/Auto and **stopped**, because the device is not enrolled — by design, not a failure. Session 0 can only be observed once it runs. |
| 1.17 | Only SYSTEM and Administrators can write to the installed tree | `PROVEN_REAL` | 2026-09-01, owner's machine: `verify-device-service.ps1` checked 201 objects under `C:\Program Files\PagentOS\agent` — no other principal holds write authority and **no empty DACLs remain**. The 425 files the earlier hardening had stripped were repaired by the installer itself, with no manual ACL reset. |
| 1.7b | The pinned companion binary is admin-protected and readable by SYSTEM | `PROVEN_REAL` | Same run: owner `S-1-5-32-544`, 3 inherited ACEs. This is what makes `CompanionImagePath` pinning mean something. |
| 5.2 | The agent owns no inbound listening socket | `PROVEN_REAL` | Same run, against the live companion process (pid checked explicitly, not a vacuous pass). |

## Stage 2 — Windows Service installation

**Nothing in this stage is proven, and two claims made here were disproved by the owner's real
machine.** Both were mine, both were about the installer, and both had passing tests behind
them that tested the wrong thing.

| # | Criterion | Status | Notes |
|---|---|---|---|
| 2.1 | Service installs, starts, survives reboot, reconnects unattended | `NOT_YET_PROVEN` | First real attempt failed at `sc create` with 1639: PowerShell 5.1 does not escape an argument containing quotes, so the `binPath` value split at "Program Files". Fixed, with argv proven by round-trip through a real child process. |
| 2.2a | Companion runs in the owner's interactive session, from the pinned binary | `PROVEN_REAL` | 2026-09-01: session 1, `MAIL\alpak`, `C:\Program Files\PagentOS\agent\companion\PagentOS.SessionCompanion.exe`. |
| 2.2b | Companion auto-starts at a *fresh logon* | `NOT_YET_PROVEN` | The logon task is registered, but the owner cannot sign out right now. Running now ≠ starts at logon; recorded separately rather than folded into 2.2a. |
| 2.2c | The service admits the companion on SID + session + binary | `NOT_YET_PROVEN` | Needs the service running, which needs enrolment. |
| 2.3 | `desktop.open_application` executes in the interactive session from the Session-0 service | `NOT_YET_PROVEN` | |
| 2.4 | The installer is idempotent — a rerun after a partial or failed install succeeds | `NOT_YET_PROVEN` | **Claimed twice, disproved twice, by the owner's real machine.** See the log below. |
| 2.5 | Only SYSTEM and Administrators hold write authority over the installed tree | `NOT_YET_PROVEN` | Enforced by an allowlist (not a denylist of Users/Everyone) and checked independently by `scripts/verify-device-service.ps1` against the ACLs on disk. The same empty-DACL bug would also have prevented the service from starting: SYSTEM cannot read an executable through an empty DACL. |
| 2.6 | Persisted machine material is usable by LocalSystem and closed to ordinary users | `NOT_YET_PROVEN` | Two security domains now stated separately in code (`MachineMaterial`): **service-owned** material gets SYSTEM plus Administrators and nothing else — the device key SYSTEM-read only, since the service never rewrites its identity — while **owner-session** material (the owner credential and the DPAPI secret store) stays owner-scoped and was not touched. No DPAPI is used anywhere in the agent, so there is no CurrentUser-scope secret for LocalSystem to decrypt; that was checked, not assumed. 12 tests, several of them real rather than proxied because the test process genuinely is an ordinary non-elevated user: after protection it can neither read nor write the key it just created. What no test here can do is have SYSTEM open the file, so that half is asserted from the DACL and stays `NOT_YET_PROVEN` until the owner's service starts. |

### The install attempts so far, and what each disproved

Recorded in full because the pattern matters more than any single bug: three real-machine
runs, three defects, and **every one of them was in a code path my tests did not execute**.
None of the three was found by the gate; all three were found by the owner running the thing.

| Attempt | Failure | Cause | What it disproved |
|---|---|---|---|
| 1 | `sc create` exit 1639 | PowerShell 5.1 does not escape a native argument containing quotes, so `binPath` split at "Program Files". Only visible elevated — a non-elevated `sc.exe` fails at `OpenSCManager` first. | That the installer worked at all. |
| 4 | Service starts, then SCM 1067 | `UnauthorizedAccessException` on `device.key` in `DeviceIdentity.LoadOrCreate`. The key was created by an owner-context enrollment run and protected with a DACL naming **only that user**, so LocalSystem could not read its own identity. Service-owned material protected as if it were owner material. | That enrollment produced material the service could use. Nothing about the broker, the IPC layer or enrollment itself was wrong — the process died before reaching any of them. |
| 5 | Credential rotation committed, replacement lost | The wrapper ran `--rotate --json`, the child exited 0, and stdout carried **two** JSON documents: this application logs to stdout, and `bootstrap()` emits `identity_owner_credential_minted` — so the mint itself guaranteed the contamination. `ConvertFrom-Json` refused it and the wrapper threw *after* the rotation had committed. The replacement died with the child process. | That a wrapper checking the exit code is enough. The exit code was 0 and the operation had succeeded; the loss was entirely in the protocol between the two processes. |
| 2 | Access denied rewriting `appsettings.json` | Hardening with `/T` and `(OI)(CI)` grants left every pre-existing file with a protected empty DACL — 425 objects on the owner's machine — denying everyone including SYSTEM. | The idempotency claim. The tests behind it covered the create/reconfigure *decision* and never touched a hardened tree. |
| 3 | `Property 'Count' cannot be found` (`PropertyNotFoundStrict`) | A function returning an empty collection unrolls to `$null`; `.Count` on it throws under StrictMode, which the libraries set and dot-sourcing propagates. Reproduced: 0 → `$null`, 1 → `String`, 2+ → array. | The claim that the partial install was *fully recoverable*. It was not: recovery aborted before it began, and a rerun with exactly one component to restore would have failed too. |

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
| 3.1 | Owner credential minted once, never in logs, source control or plaintext config | `NOT_YET_PROVEN` |
| 3.2 | Host-side recovery rotates the credential without the API | `PROVEN_PROXY` (tested offline; not yet run on the owner's real root) |

## Stage 4 — GitHub private repository and CI

| # | Criterion | Status |
|---|---|---|
| 4.1 | Private repo exists and receives the branch | `NOT_YET_PROVEN` (blocked on `gh auth login`) |
| 4.2 | CI runs the full gate on a clean runner | `NOT_YET_PROVEN` |

## Stage 5 — Tailscale + Hetzner production deployment

| # | Criterion | Status |
|---|---|---|
| 5.1 | Cloud Core runs on the real VPS | `NOT_YET_PROVEN` |
| 5.2 | Windows PC ↔ Cloud over the private network, no public inbound Windows port | `NOT_YET_PROVEN` |

## Stage 6 — Real browser qualification

| # | Criterion | Status |
|---|---|---|
| 6.1 | Owner's enrolled Chrome/Edge session drives a real authenticated site | `NOT_YET_PROVEN` |

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

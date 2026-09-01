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
| 1.2 | Service refuses to share a squatted pipe name (`FILE_FLAG_FIRST_PIPE_INSTANCE`) | `PROVEN_REAL` | `The_service_refuses_to_share_a_pipe_name_someone_else_already_holds` — a real pipe is created first by another process; the service never serves on that name. |
| 1.3 | Companion refuses a pipe not owned by a service account | `PROVEN_REAL` | `The_companion_refuses_a_pipe_that_is_not_owned_by_a_service_account` — a real fake pipe, owned by a standard user, read through the real `GetSecurityInfo` path, refused, and not one frame written to it. **Corrected 2026-09-01:** this row previously said "refused by the production policy" while the shipped companion was in fact running developer trust (see 1.12); the mechanism was real, the claim about the binary was not. |
| 1.12 | The **shipped** companion runs at service-trust strength, not developer trust | `PROVEN_REAL` | `The_shipped_companion_defaults_to_service_trust_not_developer_trust` and `A_runtime_built_without_a_policy_refuses_an_owner_owned_pipe` — the entry point's own policy builder and the runtime's own default are both asserted, because the defect was that neither the class nor its test was wrong; the wiring was. Developer trust now requires `--dev-trust` and is logged as a warning. |
| 1.13 | Service and companion derive the same pipe name from the owner | `PROVEN_REAL` | `The_service_and_the_companion_derive_the_same_pipe_name_from_the_owner`. Under Session 0 the two halves are different accounts; a name derived from "the current user" would leave them on different pipes with nothing in either log saying why. |
| 1.4 | Peer running as another account is refused (wrong SID) | `PROVEN_PROXY` | Policy refuses `SidMismatch`; end-to-end over a real pipe with an injected identity. A second real user account is an owner action. |
| 1.5 | Peer in Session 0 is refused | `PROVEN_PROXY` | `A_peer_in_session_zero_is_refused_even_with_the_right_sid`. Reproducing a real Session-0 client requires the installed service. |
| 1.6 | Peer in a different interactive session is refused | `PROVEN_PROXY` | `A_peer_in_a_different_interactive_session_is_refused_when_the_session_is_pinned`. A second concurrent logon is an owner action. |
| 1.7 | Forged companion (right user, wrong binary) is refused | `PROVEN_PROXY` | `The_right_user_running_the_wrong_binary_is_refused`, plus refusal when the image cannot be read at all. Real proof needs the pinned installed binary. |
| 1.8 | Replayed frame within a connection is refused | `PROVEN_REAL` | `A_replayed_exec_response_does_not_satisfy_a_later_request` — a real captured frame re-sent over a real pipe; the service refuses it and the pending request still times out rather than being answered. |
| 1.9 | Stale credentials from a retired connection are refused | `PROVEN_REAL` | `A_frame_from_an_earlier_connection_is_refused_after_reconnect`, and `A_companion_hello_that_does_not_answer_this_challenge_is_refused` staged over a live pipe. |
| 1.10 | Refusals are audited with their reason and tell the peer nothing | `PROVEN_REAL` | `CompanionPipeServer.RecordRefusal` → log + `ipc_peer_refused` audit row; the peer only ever sees the connection close. |
| 1.11 | Service runs as LocalSystem in Session 0 with the companion in the owner's session | `NOT_YET_PROVEN` | Requires the Windows Service install (one UAC prompt). This is what converts 1.1, 1.4–1.7 from proxy to real. `scripts/verify-device-service.ps1` reports these against the real install. |

## Stage 2 — Windows Service installation

| # | Criterion | Status |
|---|---|---|
| 2.1 | Service installs, starts, survives reboot, reconnects unattended | `NOT_YET_PROVEN` |
| 2.2 | Companion auto-starts in the owner's session and is admitted by SID + session + binary | `NOT_YET_PROVEN` |
| 2.3 | `desktop.open_application` executes in the interactive session from the Session-0 service | `NOT_YET_PROVEN` |

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

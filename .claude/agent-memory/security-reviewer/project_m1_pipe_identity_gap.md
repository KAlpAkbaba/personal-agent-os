---
name: project-m1-pipe-identity-gap
description: Windows agent named-pipe ACL/naming ties Device Service and Session Companion to the SAME OS identity; breaks once Device Service installs as a real Windows Service (SYSTEM/Session 0)
metadata:
  type: project
---

M1 `devices/windows-agent` implements the Device Service <-> Session Companion IPC as a named pipe
(`devices/windows-agent/src/PagentOS.DeviceService/CompanionPipeServer.cs`,
`devices/windows-agent/src/PagentOS.Agent.Core/Ipc/PipeNaming.cs`) whose ACL is
`WindowsIdentity.GetCurrent().User` (whichever identity runs the process that creates the pipe) and whose
default name is `pagentos-companion-{that same identity's SID}`. Per ADR-0017 this is a deliberate M1-dev
choice ("current-user-only"); both processes currently run as console processes under the *same* interactive
user, so it works today (confirmed via `scripts/e2e-m1-device.ps1`, which explicitly shares one `PipeName`
env var between both processes).

**Why this matters going forward:** DEVICE_PROTOCOL.md §9 and CLAUDE.md's Windows Agent rule describe the
target production topology as Device Service running as a **Windows Service in Session 0** (effectively a
different Windows identity, e.g. LocalSystem or a dedicated service account) with the Session Companion
staying in the **owner's interactive session**. Under that topology the two processes have different
`WindowsIdentity.GetCurrent().User` SIDs, so (a) `PipeNaming.DefaultPipeName()` computed independently by
each process yields two *different* pipe names, and (b) even if the name is forced to match via config, the
ACL (current-process-identity-only) would deny the companion (interactive user) access to a pipe created by
the service (SYSTEM). The private key ACL in `DeviceIdentity.cs` (`RestrictToCurrentUser`) has the same
shape of issue for `DataDir` (defaults to `%LOCALAPPDATA%`, which differs between the interactive user and
LocalSystem).

Also: the pipe *client* (Session Companion, `CompanionRuntime.cs`) never authenticates the server beyond
connecting by name — no mutual auth over the pipe. Combined with the ACL/identity gap above, if an operator
"fixes" the identity mismatch by loosening the pipe ACL (e.g. to Authenticated Users) instead of solving the
identity split, this reopens local pipe-squatting/impersonation risk from another local principal.

**How to apply:** Re-check this the moment the M1+ milestone that actually installs `PagentOS.DeviceService`
as a real Windows Service lands (tracked as an owner action requiring UAC per `OWNER_ACTIONS_MINIMAL.md`
item 15). The fix needs an explicit decision, not a default: either (1) run the Device Service under the
owner's own account via a scheduled-task/LogonType instead of LocalSystem so identities match, or (2) keep
SYSTEM and add an explicit mechanism — grant the pipe ACL to the *known owner-session SID* (not
"current process identity") plus a shared-secret/HMAC handshake over the pipe so the companion can verify
it is really talking to the Device Service. Don't accept "loosen the ACL to Everyone/Authenticated Users" as
the fix. See [[m1-device-broker-security-review]] for the full writeup.

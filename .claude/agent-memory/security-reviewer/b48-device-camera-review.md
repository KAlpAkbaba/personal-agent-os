---
name: b48-device-camera-review
description: B48 device camera (presence provider) security review findings, 2026-09-16
metadata:
  type: project
---

Reviewed branch `worktree-agent-a6058a8b78b722474` (B48 device camera: Session Companion
MediaFrameReader+FaceDetector, ambient_policy.camera_mode, desktop.camera_mode). Overall
design is careful: capture confined to Session Companion (never Session-0), frames zeroed
after each sample, Session-0's `HeartbeatStatus.Project` closes the `camera`/`presence`
nested objects to a fixed scalar allowlist (devices/windows-agent/src/PagentOS.Agent.Core/Protocol/HeartbeatStatus.cs),
`desktop.camera_mode` is reachable only from `app/ambient/camera.py::reconcile`, itself only
called from the owner-gated `PUT /v1/ambient/policy` and the `presence.eye` toggle - the
voice tool `ambient.set_policy` (services/api/app/voice/realtime_sessions/tools_ambient.py)
deliberately excludes `camera_mode` from its schema/`_POLICY_ARGUMENTS`, so there is no
model-invocable path to enable the camera. Migration 0059 is a safe expand-only column.

**High finding**: the device-side tray veto (`CameraPresenceMonitor._vetoed`,
`TrayCameraIndicator._vetoed`) is pure in-memory state with no persistence. Program.cs builds
a fresh `CameraPresenceMonitor` on every companion start (mode=off, vetoed=false). Cloud
Core's `ambient_policy.camera_mode` is untouched by an on-device veto, so any companion
restart (self-update, crash, reboot, logoff/logon) silently drops the veto and the next
heartbeat reconcile re-opens the camera per the owner's last cloud-set mode - violating "the
Cloud cannot silently reopen it after an owner device-side close". No test constructs a new
monitor after a veto to catch this.

**Medium finding**: `CameraPresenceMonitor.SafeDeniedBy()` swallows every exception from
`ICameraConsent.DeniedBy()` and returns null (= allowed), so an unreadable Windows privacy
registry key (`WindowsCameraConsent`, unprotected `Registry.OpenSubKey` calls) fails OPEN,
not closed - contradicts "denied -> blocked" and is untested (no throwing-consent fake).

Both findings and remediation detail are in the review transcript this memory points to (no
separate findings file was written per house rule). See [[junction-escape-recurring-pattern]]
for the general pattern of "reviewed the happy path, the restart/exception path was
untested" recurring across this codebase's device features.

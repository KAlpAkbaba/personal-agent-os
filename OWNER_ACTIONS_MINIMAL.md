# Owner Actions — Minimum Manual Intervention

The engineering agent should complete everything else autonomously before asking for these.

## One-time local actions that may be unavoidable

1. Approve Windows UAC for Docker/WSL/runtime installation if requested.
2. Reboot Windows only if WSL/virtualization/driver installation requires it.
3. Sign into Claude Code, GitHub and Tailscale when an OAuth/browser login is required.
4. Allow microphone and notification permissions in Windows/browser/mobile.
5. If local NVIDIA acceleration is enabled later, install/approve the required GPU driver if not already present.

## One-time cloud actions that may be unavoidable

1. Create or sign into a Hetzner account.
2. Add payment/billing method.
3. Create a Hetzner project.
4. Create API credentials through the provider UI.
5. Create/sign into Tailscale and enroll the cloud VM/phone/PC.
6. Create voice-provider accounts/API keys for whichever providers survive the Turkish benchmark.

Claude must never ask the owner to paste secret values into chat history. The repository should provide a local secure secret-entry mechanism.

## One-time voice actions

- Record several owner speech samples in quiet, normal office and mildly noisy conditions.
- Complete a short pronunciation preference A/B test for Turkish TTS voices.

## Authorized security assets

A company/server/network target needs to be enrolled once in the Authorized Asset Registry. After that, routine tests within the recorded scope should not ask again unless the requested action exceeds the stored scope.

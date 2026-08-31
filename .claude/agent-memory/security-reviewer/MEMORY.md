# Memory Index

- [M1 device broker security review](m1-device-broker-security-review.md) — summary + pointer to full findings for the M1 device broker/Windows agent review (2026-08-31)
- [M1 pipe identity gap](project-m1-pipe-identity-gap.md) — named-pipe ACL/naming breaks once Device Service becomes a real Windows Service (SYSTEM); recheck at that milestone
- [M2 browser agent security review](m2-browser-agent-security-review.md) — summary + pointer to full findings for the M2 browser backend/enrollment/commands review (2026-08-31); High: loopback-check startswith("127.") bypass
- [M3 artifact/research security review](m3-artifact-research-security-review.md) — summary + pointer to full findings for M3 research/artifacts, CORS, desktop.open_artifact review (2026-08-31); Medium: unsanitized HTML render, junction escape gap
- [M4 voice/narration security review](m4-voice-narration-security-review.md) — pointer to M4 findings (2026-08-31); High: speaker-verify device_trusted is caller-self-asserted + unprotected owner re-enrollment

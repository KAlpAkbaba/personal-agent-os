# Memory Index

- [M1 device broker security review](m1-device-broker-security-review.md) — summary + pointer to full findings for the M1 device broker/Windows agent review (2026-08-31)
- [M1 pipe identity gap](project-m1-pipe-identity-gap.md) — named-pipe ACL/naming breaks once Device Service becomes a real Windows Service (SYSTEM); recheck at that milestone
- [M2 browser agent security review](m2-browser-agent-security-review.md) — summary + pointer to full findings for the M2 browser backend/enrollment/commands review (2026-08-31); High: loopback-check startswith("127.") bypass
- [M3 artifact/research security review](m3-artifact-research-security-review.md) — summary + pointer to full findings for M3 research/artifacts, CORS, desktop.open_artifact review (2026-08-31); Medium: unsanitized HTML render, junction escape gap
- [M4 voice/narration security review](m4-voice-narration-security-review.md) — pointer to M4 findings (2026-08-31); High: speaker-verify device_trusted is caller-self-asserted + unprotected owner re-enrollment
- [M6 self-healing security review](m6-selfhealing-security-review.md) — pointer to M6 findings (2026-09-01); Critical: unescaped string-literal injection in patch generator -> RCE + malicious active-release promotion via unauthenticated ingest+pipeline/run
- [M8 security agent review](m8-security-agent-review.md) — pointer to M8 findings (2026-09-01); High: RegistryAuthorizationProvider never wired into EvolutionRuntime (M7 gap not actually closed); Medium: junction escape in checks.py collector
- [Junction escape recurring pattern](junction-escape-recurring-pattern.md) — feedback: leaf-only is_symlink() checks miss Windows junctions on ancestor dirs; verify with a real junction, not reasoning. Seen in M3 and M8.
- [M9 identity/API auth security review](m9-identity-security-review.md) — pointer to M9 findings (2026-09-01); Medium: announcer stamps announced_at before delivery (can lose a push on crash); Medium: require_scope never wired into any route
- [M12 voice Arbor/noise security review](m12-voice-arbor-noise-security-review.md) — pointer to M12 findings (2026-09-02); no Critical/High; Low: fail-open voice validation via getattr duck-typing if a future provider lacks require_supported_voice

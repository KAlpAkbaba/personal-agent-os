# Memory Index

- [M12 realtime voice state](project_m12_realtime_state.md) — A+E+B done 2026-09-02; C/D open; simulator dev-only gate; owner credential step = realtime_smoke.py
- [M12 noise gate state](project_m12_noise_gate.md) — ADR-0044 web mic/noise package shipped 2026-09-02; Layer 4 (provider VAD) server-side only; owner actions pending
- [ADR-0047 latency state](project_adr0047_latency_state.md) — 2026-09-03 K66-driven client optimisation; stop_cmd_ms is forbidden (pcm); owner rerun pending
- [ADR-0063 action receipts state](project_adr0063_action_receipts.md) — 2026-09-06 Cloud Core half shipped: state.now/eye.*/release.promote, receipts; owner re-run pending
- [M18 eye action seam](project_m18_eye_action_seam.md) — 2026-09-06 EyeStore does the durable eye POST on the voice path too; the server honours `observed_after.local.changed` (resolved at merge: the client now relays `changed`)
- [Worktree pnpm gates](reference_worktree_pnpm_gates.md) — fresh worktree has no node_modules; one offline install first, never two pnpm exec in parallel

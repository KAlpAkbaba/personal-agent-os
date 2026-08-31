# Local Environment Report

Generated: 2026-08-31 (M-1 preflight). Machine-readable raw data: `docs/LOCAL_ENV_REPORT.generated.json` (gitignored, regenerate with `scripts/preflight.ps1`).

## Hardware

| Component | Value | Assessment |
|---|---|---|
| OS | Windows 10 Home Single Language, build 19045 (22H2) | OK. Not Windows 11, but all required tooling (WSL2, Docker Desktop, UI Automation) works on 19045. |
| CPU | Intel Core i7-14700KF (20 cores / 28 threads) | Well above the 8-core recommendation. |
| RAM | 48 GB | Meets the 32 GB minimum, near the 64 GB preferred tier. |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16 GB VRAM, driver 581.29 | Meets the "16 GB+ VRAM recommended" tier for future local STT/TTS/vision. Not required before M10. |
| Disk C: | 447 GB total, **24 GB free** | **Risk.** Docker Desktop WSL data and WSL distros default to C:. See risks below. |
| Disk E: | 3.7 TB total, 3.4 TB free | Primary development drive (repository lives here). Ample. |
| Disk K: | 932 GB total, 792 GB free | Available for backups/artifacts if needed. |

## Virtualization / containers

- WSL2: installed and functional (`docker-desktop` distro, WSL default version 2).
- Docker Desktop 28.3.2: installed, Linux engine confirmed RUNNING; `docker run --rm hello-world` **passed** (M-1 acceptance).

## Toolchain

| Tool | Status | Version / location |
|---|---|---|
| Git | present | 2.49.0.windows.1 |
| Windows PowerShell 5.1 | present | 5.1.19041 — native build shell confirmed working |
| PowerShell 7 (pwsh) | missing | optional; scripts are kept 5.1-compatible. Machine-scope MSI needs UAC, deferred. |
| Python | present | 3.14.7 (also 3.12, 3.8 on PATH). M0 services will pin their own interpreter via `uv`. |
| uv | **installed during M-1** | 0.12.7, user scope via winget (`%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_...`) |
| Node.js | present | v24.15.0 (current LTS line) |
| pnpm | **installed during M-1** | 11.24.0, user scope via `npm install -g` (`%APPDATA%\npm`) |
| .NET | runtime only, **no SDK** | Required from M1 (Windows agent). Will be installed user-scope via official `dotnet-install.ps1` (no UAC) when M1 starts. |
| GitHub CLI (gh) | **installed during M-1** | 2.98.0, user scope via winget. Not yet authenticated (owner OAuth needed later). |
| Tailscale | missing | Needed at M1 (cloud/device link). Machine-scope install + login → consolidated owner action. |
| winget | present | v1.29.290 |
| Docker CLI/engine | present | 28.3.2 |
| nvidia-smi | present | driver 581.29 |
| Chocolatey, CMake, ffmpeg, Tesseract, eSpeak NG, Ollama, OpenSSL | present | pre-existing; may be useful for voice/vision milestones |

Note: freshly installed user-scope tools (uv, gh) modify the user PATH; already-open shells must be restarted to see them. `scripts/preflight.ps1` probes absolute fallback paths so it does not depend on PATH freshness.

## Test command entry points

- `scripts/preflight.ps1` — non-destructive environment inventory; writes `docs/LOCAL_ENV_REPORT.generated.json`. Hardened with per-command timeouts and absolute-path fallbacks (the original version could hang and depended on PATH).
- `scripts/quality-gate.ps1` — deterministic quality gate. Currently the bootstrap variant (required-file + secret hygiene checks); will grow into build/unit/integration/E2E orchestration during M0.

## Risks and observations

1. **C: drive has only 24 GB free.** Docker Desktop stores its WSL VHDX on C:. M0 images (PostgreSQL, Redis, MinIO, Temporal) fit for now, but the owner should eventually move Docker Desktop's disk image location to E: (Docker Desktop → Settings → Resources → Advanced → Disk image location). Non-urgent; flagged as a recommended owner action, not blocking.
2. **This session's spawned-shell PATH is partially broken** (a literal `%PATH%` first entry; `System32` absent). Repository scripts therefore avoid PATH reliance and probe absolute locations.
3. No GPU work is needed before M10; the RTX 5060 Ti (16 GB) is already sufficient for the local-voice fallback tier when that milestone arrives.
4. Windows 10 Home: Hyper-V unavailable, but Docker Desktop uses the WSL2 backend, which works on Home. No blocker.

## Consolidated owner actions (none blocking M-1/M0)

Nothing requires the owner right now. These will be needed later and are batched here per policy:

1. **Before pushing to GitHub (end of M0):** run `gh auth login` once (browser OAuth) so CI/remote can be created.
2. **At M1 (cloud/device link):** approve Tailscale installation (UAC) and sign in; approve Hetzner account/billing/API key creation.
3. **Recommended, any time:** move Docker Desktop disk image location from C: to E: to relieve C: free-space pressure.

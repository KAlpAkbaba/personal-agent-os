# Local Development & Hardware Guide

## 1. Does Claude Code need a GPU?

No. Claude Code itself does not run the Claude foundation model on your GPU. The heavy model inference is remote. A GPU does not materially speed up normal Claude Code reasoning/editing.

The official Claude Code system requirements are modest (4 GB+ RAM and x64/ARM64 CPU), but this project runs Docker, databases, Windows UI tests and several services, so practical requirements are higher.

## 2. Recommended development tiers

### Tier A — Cloud-heavy, no local AI inference

Good for M-1 through M7 if STT/TTS are cloud APIs.

- CPU: modern 8-core class or better
- RAM: 32 GB minimum; 64 GB preferred
- Storage: 1 TB NVMe minimum; 2 TB preferred
- GPU: optional/integrated GPU acceptable
- Network: stable broadband

### Tier B — Hybrid local voice

For local Whisper, speaker models, optional local TTS.

- CPU: modern 8-16 core class
- RAM: 64 GB preferred
- Storage: 2 TB NVMe
- NVIDIA GPU: 12 GB VRAM workable; **16 GB+ preferred**

### Tier C — Local LLM/vision experimentation

Not required for v1.

- RAM: 64-128 GB
- fast 2 TB+ NVMe
- NVIDIA 24/32 GB VRAM class for larger local models and concurrent voice/vision

## 3. Why 8 GB GPU can still be useful

Faster-Whisper's published benchmark demonstrates large Whisper-class GPU transcription on an RTX 3070 Ti 8 GB, with roughly 4.5-6 GB VRAM in tested configurations. Therefore an 8 GB NVIDIA GPU can accelerate local STT. We still prefer 12-16 GB+ headroom when diarization, TTS or vision may run concurrently.

## 4. Recommended OS strategy

**Primary:** Native Windows Claude Code.

Reason:

- Windows Device Agent must use PowerShell, Win32/COM and UI Automation;
- interactive desktop behavior is easiest to inspect natively;
- Claude Code currently supports native Windows directly.

**Secondary:** WSL2 Ubuntu + Docker Desktop backend.

Use for:

- PostgreSQL/Redis/Temporal/dev services;
- Linux toolchains;
- sandboxed tests;
- future local model containers.

## 5. Repository location

Recommended:

`C:\AI\personal-agent-os`

Avoid:

- OneDrive-synchronized project directory;
- network share;
- path with unusual permission inheritance;
- root of system drive.

The main checkout should be on Windows because the Windows Agent toolchain is first-class. Linux containers mount the repository only when needed.

## 6. Development prerequisites

Claude should detect and install/prepare current stable versions of:

- Git for Windows
- PowerShell 7
- WSL2 + Ubuntu LTS
- Docker Desktop using WSL2 backend
- .NET current LTS SDK
- Python 3.12+ supported by chosen dependencies
- `uv` Python package/project manager
- Node.js current LTS
- `pnpm`
- GitHub CLI (`gh`)
- Tailscale
- optionally OpenTofu

Versions must be written to `docs/LOCAL_ENV_REPORT.md` and pinned through lock files/tool config where appropriate.

## 7. Preflight checks

Claude must check:

```text
Windows version/build
CPU model / logical processors
RAM total/free
system drive free space
project drive free space
GPU model / VRAM
nvidia-smi if available
virtualization firmware flag
WSL status/version
docker version / docker info
git version
pwsh version
python version
uv version
node version
pnpm version
dotnet --info
gh version
tailscale version
```

No destructive benchmark in M-1.

## 8. Docker resource guidance

For a 64 GB host:

- allow Docker/WSL roughly 16-24 GB during development;
- do not reserve the whole machine;
- cap heavy dev services where possible.

For a 32 GB host:

- start fewer services simultaneously;
- defer full observability stack until needed;
- use cloud services selectively.

## 9. Windows test strategy

### Unit/integration

Run headless where possible.

### Interactive UI tests

Use harmless test targets first:

- Notepad;
- Calculator;
- a dedicated test browser profile;
- generated sample documents.

Never use the owner's real files as initial mutation tests.

### Dedicated Windows test environment later

Recommended after M2:

- separate Windows VM or test device;
- snapshot/restore capability;
- dedicated test account;
- enrolled as `test-device`;
- safe for destructive installer/update scenarios.

## 10. GPU installation decision

Do not block M0-M4 on GPU availability.

Decision sequence:

1. Build cloud-provider voice benchmark.
2. Measure cost/latency/privacy.
3. If local fallback is valuable, detect existing GPU.
4. Only recommend hardware purchase if actual local workload cannot meet target on current system.

## 11. Local speech stack later

Potential local fallback components:

- Faster-Whisper for STT;
- local VAD/noise preprocessing;
- speaker embedding/verification model;
- local TTS selected only after Turkish benchmark.

All local models must be replaceable adapters.

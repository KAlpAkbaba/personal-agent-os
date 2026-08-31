# Platform Matrix

| Concern | Initial choice | Why | Later alternative |
|---|---|---|---|
| Development agent | Claude Code native Windows | Windows-native automation + strong coding agent | Claude headless/Agent SDK workers |
| Source | GitHub Private | CI/Windows runners/GHCR | self-hosted Git if needed |
| CI | GitHub Actions | cross-platform runners | self-hosted runners |
| Registry | GHCR | integrated with repo | private registry |
| Cloud VM | Hetzner NBG1 | simple EU VM, cost/performance | Azure/AWS/GCP |
| Private network | Tailscale | owner devices + cloud | WireGuard manual |
| Orchestration | Docker Compose | single-user simplicity | Kubernetes only if justified |
| Workflow | Temporal | durable long-running tasks | alternative durable engine |
| Database | PostgreSQL + pgvector | canonical relational + vector | managed PostgreSQL later |
| Cache | Redis | ephemeral/cache | Valkey/other |
| Artifact storage | S3 abstraction | portable | any S3-compatible provider |
| Dev object storage | MinIO | local S3 behavior | local filesystem for tiny tests |
| Prod object storage | Hetzner Object Storage | same provider/location | another S3 provider |
| Browser | Playwright/MCP | semantic automation | direct Playwright SDK |
| Windows automation | custom .NET + UFO evaluation | native control + reference framework | other UI automation |
| Coding backend | Claude Agent SDK | self-extension v1 | provider/local adapters |
| Realtime voice | benchmark router | quality changes over time | multi-provider |
| Narration TTS | benchmark router | Turkish quality critical | local TTS later |
| STT fallback | Faster-Whisper | local option | other local/cloud STT |

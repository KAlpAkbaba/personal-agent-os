# Source Notes — Checked 2026-08-31

These links are references for Claude to re-check during implementation. Do not assume APIs/versions remain unchanged.

## Claude Code official

- Advanced setup / Windows / system requirements: https://code.claude.com/docs/en/setup
- Subagents: https://code.claude.com/docs/en/sub-agents
- Hooks: https://code.claude.com/docs/en/hooks
- Programmatic/headless: https://code.claude.com/docs/en/headless
- Agent SDK quickstart: https://code.claude.com/docs/en/agent-sdk/quickstart
- Permission modes: https://code.claude.com/docs/en/permission-modes

Key notes at package time:

- Native Windows and WSL2 supported.
- Official minimum hardware lists 4 GB+ RAM; project practical requirement is higher.
- Subagents support `memory`, `isolation: worktree`, models and permission modes.
- `TaskCompleted` hook can block completion when tests fail.
- Programmatic mode supports JSON/stream-json and session resume.
- `bypassPermissions` is documented for isolated containers/VMs only; Auto mode is preferred on the normal workstation.

## Tailscale

- Grants: https://tailscale.com/docs/features/access-control/grants
- Serve: https://tailscale.com/docs/features/tailscale-serve

## Hetzner

- Cloud: https://www.hetzner.com/cloud/
- Server overview: https://docs.hetzner.com/cloud/servers/overview/
- Object Storage: https://docs.hetzner.com/storage/object-storage/
- Object Storage overview/endpoints: https://docs.hetzner.com/storage/object-storage/overview/
- S3 tools: https://docs.hetzner.com/storage/object-storage/getting-started/using-s3-api-tools/

## Temporal

- https://docs.temporal.io/

## Microsoft UFO

- https://github.com/microsoft/UFO
- UFO3 device architecture: https://github.com/microsoft/UFO/blob/main/documents/docs/infrastructure/agents/overview.md
- Device WebSocket quick start: https://github.com/microsoft/UFO/blob/main/documents/docs/server/quick_start.md

## Playwright MCP

- https://github.com/microsoft/playwright-mcp
- Browser connection docs: https://github.com/microsoft/playwright.dev/blob/main/mcp/configuration/browser-extension.mdx

## Memory/screen

- Mem0: https://github.com/mem0ai/mem0
- Screenpipe: https://github.com/screenpipe/screenpipe

## Voice

- ElevenLabs TTS: https://elevenlabs.io/docs/overview/capabilities/text-to-speech
- ElevenLabs models: https://elevenlabs.io/docs/overview/models
- Azure Speech language/voice support: https://learn.microsoft.com/azure/ai-services/speech-service/language-support
- OpenAI models/realtime audio: https://developers.openai.com/api/docs/models
- Faster-Whisper: https://github.com/SYSTRAN/faster-whisper

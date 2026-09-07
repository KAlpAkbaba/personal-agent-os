# Third-Party Components to Evaluate

Before integration, verify latest version, license and security status.

## Claude Code / Claude Agent SDK

Role: development and Evolution Engine coding backend v1.

Useful capabilities in current official docs:

- native Windows and WSL support;
- programmatic `-p` execution with JSON/streaming output;
- custom subagents;
- persistent subagent memory;
- worktree isolation;
- hooks including TaskCompleted/Stop;
- Auto permission mode.

## Microsoft UFO / UFO3

Role: Windows and multi-device automation reference/integration candidate.

Current UFO3 docs describe Windows Device Agents using HostAgent/AppAgent hierarchy, UI Automation, screenshots/UI tree and multi-device orchestration.

## Playwright MCP

Role: semantic browser control and Claude-development integration.

Current docs support connecting to running Chrome/Edge through CDP/channel and extension mode reusing logged-in tabs/cookies/extensions.

## Screenpipe

Role: optional local screen/audio history adapter.

Current project describes local capture of what the user sees/says/does into searchable memory/automation. Check current license before bundling/distribution.

## Mem0

Role: optional memory extraction/retrieval accelerator.

Current open-source stack supports self-hosting and PostgreSQL + pgvector. Do not make it the only canonical store.

## Temporal

Role: durable workflow engine. Use self-hosted service initially unless a managed choice is intentionally selected later.

## Faster-Whisper

Role: local STT fallback/benchmark candidate.

## Voice providers

- ElevenLabs
- Azure Speech
- OpenAI realtime/TTS

Selection depends on Turkish benchmark by use case.

## Document fixture generators (dev only, M20)

- `openpyxl` (MIT) and `python-pptx` (MIT): generate the committed XLSX/PPTX fixtures under `services/api/tests/fixtures/documents/` via `scripts/tests/make-document-fixtures.py`; dev group only, never in the production image. `python-docx` (MIT) and `fpdf2` (LGPL-3.0, already a runtime dependency for M13 artifacts) generate the DOCX/PDF fixtures.

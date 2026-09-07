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

## Document parsers in the Windows agent (M20, ADR-0083)

Role: the per-format providers behind `PagentOS.SessionCompanion/Documents/` (`IDocumentExtractor`); they run on the owner's machine only, inside the authorised roots, and never in Cloud Core. Both are pinned in `PagentOS.SessionCompanion.csproj`.

- `DocumentFormat.OpenXml` **3.5.1** (MIT; Microsoft, https://github.com/dotnet/Open-XML-SDK) — DOCX / XLSX / PPTX. Parts are read through the SDK's package model; the zip's central directory is read once by the companion (`System.IO.Compression.ZipArchive`, nothing inflated) to bound decompression before the SDK is entered (ADR-0083 addendum 3). Pulls `DocumentFormat.OpenXml.Framework` (same version, MIT).
- `PdfPig` **0.1.16** (Apache-2.0; UglyToad, https://github.com/UglyToad/PdfPig) — PDF page text in content order (`ContentOrderTextExtractor`) and the information dictionary's title. Opened with the companion's `BoundedFilterProvider` through `ParsingOptions.FilterProvider`, which wraps the library's own Flate / LZW / RunLength filters with a streaming length count so no stream inflates past the companion's bounds. Pulls its own `PdfPig.*` assemblies (same version, Apache-2.0); no native code.

Upgrade rule: bump the pin, run the documents lab (`dotnet test … --filter FullyQualifiedName~Documents`: every fixture against its expected extract), then the whole agent project.

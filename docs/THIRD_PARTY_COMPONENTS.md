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

- `python-docx` (MIT) and `fpdf2` (LGPL-3.0, already a runtime dependency for M13 artifacts) generate the DOCX/PDF fixtures under `services/api/tests/fixtures/documents/` via `scripts/tests/make-document-fixtures.py`.

## Artifact Factory renderers + validators (M22, ADR-0085, runtime dependencies)

Role: `services/api/app/artifacts/renderers.py` (XlsxRenderer/PptxRenderer, spec §2) and
`services/api/app/artifacts/validation.py` (the independent re-readers, spec §3) run in
the Cloud Core API image on the owner's create → render → validate path, not only in
tests — moved out of the M20-era dev-only fixture-generator group for exactly that
reason.

- `openpyxl` **3.1.5** (MIT; https://foss.heptapod.net/openpyxl/openpyxl) — writes and
  independently re-reads XLSX. `data_only=False` on read so a formula cell is compared
  by its raw formula text, never a possibly-stale cached value.
- `python-pptx` **1.0.2** (MIT; https://github.com/scanny/python-pptx) — writes and
  independently re-reads PPTX (slide title shape + every text-frame paragraph).
- `pypdf` **6.18.0** (BSD-3-Clause; https://github.com/py-pdf/pypdf) — new dependency;
  independent PDF text extraction for validation only (the Cloud Core never WRITES
  PDF through pypdf — PDF authoring stays on the existing `fpdf2` M13 `PdfRenderer`).
  `app.artifacts.validation` bounds page count (`MAX_PDF_PAGES`) and extracted-text
  length (`MAX_PDF_PAGE_TEXT_CHARS`/`MAX_PDF_TOTAL_TEXT_CHARS`) before/while calling
  it — pypdf has no PdfPig-style per-filter streaming counter, so these are the bounds
  this module itself enforces, not a claim about pypdf's own internals.
- `defusedxml` **0.7.1** (BSD-derived — PSF-2.0; https://github.com/tiran/defusedxml)
  — LOW security-review finding (ADR-0085 addendum 6): `openpyxl.xml.functions`
  imports `defusedxml.ElementTree`/`.cElementTree` when present and otherwise falls
  BACK, silently, to the stdlib's `xml.etree` (vulnerable to entity-expansion/billion-
  laughs on a hostile XLSX `validate()` reopens). It was already present
  TRANSITIVELY (pulled in by `fpdf2`), which made the fallback look closed by
  accident — pinned here explicitly as its own runtime dependency so an unrelated
  package's own version bump can never silently drop it again.
  `tests/unit/test_artifact_validation.py::test_defusedxml_is_active_in_this_environment`
  asserts `openpyxl.xml.functions.DEFUSEDXML is True`; a second test proves an
  entity-expansion XLSX payload is refused/bounded rather than expanded.

Upgrade rule: bump the pin, run `tests/unit/test_artifact_renderers.py` and
`tests/unit/test_artifact_validation.py` (every fixture spec × format, the lying-
renderer catches, the resource-bound cases) before shipping.

## God's Eye View (owner addition 3, 2026-09-21, ADR-0197)

Role: an owner-facing third-party UI (a photorealistic 3D globe with live aircraft, ships,
satellites, earthquakes, cameras) the owner asked to reach whenever they want. Runs as the
Cloud Core's `aux` compose workload (`infra/docker/godseye/Dockerfile`), never inside the
api image.

- `gods-eye-view` (MIT; Bilawal Sidhu, https://github.com/bilawalsidhu/gods-eye-view),
  pinned to commit `0dbde1e36c0177b7664b47702d77ba50f11ddadc` (2026-09-21) in the
  Dockerfile's `GEV_COMMIT`. Its own dependencies are locked by its `package-lock.json`
  (`npm ci`); Puppeteer's Chromium download is skipped (tests only). Runs the upstream's
  one documented mode - the Vite dev server that also hosts its key broker - bound to the
  container and published only on the host's Tailscale address (`:4173`).
- Keys: none required. The optional provider keys (Cesium ion, Google Maps, OpenAI,
  AISStream, FIRMS, TomTom) are the owner's, go in the root-only `/opt/pagentos/godseye.env`
  on the host, and never in the tree, the image or the compose file. The per-IP throttles
  its SECURITY.md asks for are set (`GODSEYE_RATELIMIT_*`).

Upgrade rule: bump `GEV_COMMIT` to a commit you have read the diff of, rebuild with the
next release (`aux_up` in `release-cloud-core-bluegreen.sh`), open the page.

## MediaPipe Tasks Vision in the web shell (owner addition 4, 2026-09-21, ADR-0198)

Role: in-browser hand-landmark detection for the hand-gesture control. Runs only in the
owner's browser tab; frames never leave it, and only gesture NAMES reach the Cloud Core.

- `@mediapipe/tasks-vision` (Apache-2.0; Google, https://github.com/google-ai-edge/mediapipe)
  pinned in `apps/web/package.json`; its WASM runtime and the `hand_landmarker.task` model
  (float16) are fetched into `apps/web/public/mediapipe/` by
  `apps/web/scripts/fetch-mediapipe-assets.mjs` from pinned URLs with sha256 checks, and
  are gitignored. Nothing is loaded from a CDN at run time.

Upgrade rule: bump the pin, re-pin the asset URLs/hashes, run the gesture recogniser
tests and the eye/perception tests, then a real camera run.

## Document parsers in the Windows agent (M20, ADR-0083)

Role: the per-format providers behind `PagentOS.SessionCompanion/Documents/` (`IDocumentExtractor`); they run on the owner's machine only, inside the authorised roots, and never in Cloud Core. Both are pinned in `PagentOS.SessionCompanion.csproj`.

- `DocumentFormat.OpenXml` **3.5.1** (MIT; Microsoft, https://github.com/dotnet/Open-XML-SDK) — DOCX / XLSX / PPTX. Parts are read through the SDK's package model; the zip's central directory is read once by the companion (`System.IO.Compression.ZipArchive`, nothing inflated) to bound decompression before the SDK is entered (ADR-0083 addendum 3). Pulls `DocumentFormat.OpenXml.Framework` (same version, MIT).
- `PdfPig` **0.1.16** (Apache-2.0; UglyToad, https://github.com/UglyToad/PdfPig) — PDF page text in content order (`ContentOrderTextExtractor`) and the information dictionary's title. Opened with the companion's `BoundedFilterProvider` through `ParsingOptions.FilterProvider`, which wraps the library's own Flate / LZW / RunLength filters with a streaming length count so no stream inflates past the companion's bounds. Pulls its own `PdfPig.*` assemblies (same version, Apache-2.0); no native code.

Upgrade rule: bump the pin, run the documents lab (`dotnet test … --filter FullyQualifiedName~Documents`: every fixture against its expected extract), then the whole agent project.

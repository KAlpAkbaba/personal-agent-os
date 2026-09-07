---
name: m20-documents-lab
description: M20 track A (device half) facts - where the documents family lives, the oracle seam, the not_found/validation_error mapping, search records without sha256, tooling traps hit on 2026-09-08
metadata:
  type: project
---

M20 track A (2026-09-08, branch `m20-device`, worktree `E:\AI\pagentos-wt-m20-device`) put the documents family in `PagentOS.SessionCompanion/Documents/` with its lab in `tests/PagentOS.Agent.Tests/Documents/` (40 tests, all nine fixtures match `expected/*.extract.json` through the real dispatcher).

Facts that are NOT obvious from the code and cost time:
- The taxonomy had no `not_found`; spec §2 names it, so it was added with `unsupported_format` to ProtocolConstants, the schema and `frames.py` (ADR-0083 addendum 1). The spec's `invalid_argument` is `validation_error` - do not mint a second name. Cloud Core (track B) should map exactly these.
- `file.search` records carry no `sha256` (a search never opens a file); single-file capabilities hash under 8 MiB. Secret-bearing names are not even LISTED by a search.
- Both families (operator 32 + documents 6) are advertised by the ONE `OperatorEnabled` flag; `Compose(..., operatorEnabled: true)` appends documents after operator. The M19 advertisement test's `TakeLast(Operator.Count)` had to become `TakeLast(38).Take(32)`.
- The fixture folder `services/api/tests/fixtures/documents/` contains a `.gitattributes` (LF pin); the lab must not copy it or a `*` search counts 10 files, not 9. `expected/` and `truth.json` stay in the repo (a search for "sozlesme" would otherwise hit the expected JSON names).
- PdfPig 0.1.16 is one package that ships `UglyToad.PdfPig.DocumentLayoutAnalysis.dll` (`ContentOrderTextExtractor.GetText(page, false)` gives Turkish text with spaces; fpdf2 PDFs decode fine). DocumentFormat.OpenXml 3.5.1: `CellValues`/`PlaceholderValues` compare with `==`; `PackageProperties.Title` carries the generator's title.
- `dotnet package search X --exact-match` needs `--source https://api.nuget.org/v3/index.json` when run outside the windows-agent folder (its NuGet.config clears sources).
- `System.Text.Json` writes 1.0 as 1; the JSON extractor writes numbers with `WriteRawValue(GetRawText())` to match the generator's `1.0`.
- The worktree isolation guard refuses Bash heredocs (`cat >> file <<EOF`) and multi-`&&` chains touching another worktree; one `cd <wt> && <git command>` per call works, and `git commit -F <scratchpad file>` avoids quoting.

**Why:** each of these produced a red gate or a wrong assumption the first time.
**How to apply:** when track B / the integrator wires Cloud Core to the device, read `DEVICE_PROTOCOL.md` §6j first; when extending the lab, keep the oracle in the repo and the copies under `%TEMP%\pagentos-operator-fixture\documents\<run-id>`.

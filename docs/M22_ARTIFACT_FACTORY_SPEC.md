# M22 — Artifact Factory

Status: CLOSED 2026-09-08 (ADR-0085; QUALIFICATION Stage 20 with 20.8 PROVEN_REAL; Cloud Core c2f1904 released to production 08:13Z through the blue/green path, LAST_KNOWN_GOOD 571ddb2). Decision record: ADR-0085.
Predecessors: M13 artifacts (`app/artifacts`: canonical Markdown → PDF/DOCX/HTML/TXT through the `Renderer` Protocol, `artifacts` / `artifact_versions` / `artifact_renders` tables, the object store, `POST /v1/artifacts/{id}/renders`), M20 documents (the reference scheme `p<n>` / `sheet:<name>!A<r>:B<r>` / `s<n>`, the device's `document.extract` and `file.open`), M19 `object_focus`.

The owner's rule, in one line: **an artifact the assistant made is not done when it is written — it is done when it has been reopened by an independent parser and found to say what was asked, and the owner can open it with a word.**

## 1. What is made, from what

An `ArtifactSpec` is a structured, validated request (never free prose handed to a renderer): `{kind: document | spreadsheet | presentation | dataset | page, title, language: tr, sections | sheets | slides | rows, style?}`.

| kind | formats | the structure |
|---|---|---|
| document | DOCX, PDF, HTML, MD, TXT | `sections: [{heading, level, paragraphs, bullets?, table?}]` |
| spreadsheet | XLSX, CSV | `sheets: [{name, columns, rows, totals?: {column: sum|avg}, formulas?}]` |
| presentation | PPTX | `slides: [{title, bullets, notes?}]` |
| dataset | CSV, JSON | `columns`, `rows` |
| page | HTML, MD | sections as document |

The spec is produced by the assistant from the owner's words (the deterministic part: kind, title, the explicit values the owner said — "kira 12000, maaş 45000"), with the cognitive backend filling prose only where the owner asked for prose; every number the owner said is carried literally and validated literally.

## 2. Renderers (behind the M13 `Renderer` Protocol, extended)

`Renderer.render(spec) -> bytes` per (kind, format): the M13 Markdown renderers stay for `document`/`page`; new `XlsxRenderer` (openpyxl — moved from the dev group to runtime), `CsvRenderer`, `PptxRenderer` (python-pptx — runtime), `JsonRenderer`; every renderer deterministic (fixed document properties, sorted zip entries, LF) so a re-render of the same spec is byte-identical (`content_hash` already exists on renders).

## 3. Validation: reopen, parse, compare

Every render is followed by `validate(render) -> ValidationReport` using an INDEPENDENT reader (never the writer's object model): DOCX through `python-docx` reading the bytes back, XLSX through `openpyxl` (data_only=False for formulas, values compared literally), PPTX through `python-pptx`, PDF through `pypdf` (new runtime dependency, BSD), CSV/JSON/MD/HTML/TXT through the standard library. The report lists every spec element with `{ref, expected, found, ok}` using M20's reference scheme (`p3`, `sheet:Ozet!B5`, `s4`, `h2:Giriş`), `ok` only when every element is found; totals recomputed from the rows and compared to the rendered total; a formula cell compared by formula text. A render whose validation fails is kept with state `invalid` and the receipt says which ref failed — never presented as done.

## 4. Presenting and opening

- `artifact_renders.validation_json` (expand-only migration) and `state = valid | invalid`; the Cockpit "Üretilenler" panel lists the last artifacts with format, size, validation result and a download link (owner-session gated, as M13's render download).
- Opening on the owner's machine (device track): a new companion capability `file.fetch {url, name}` (interactive family, `OperatorEnabled`-gated) downloads a Cloud Core render URL — the Cloud Core's origin only (the origin the device dialled, ADR-0069's rule), owner-session-signed, ≤ 50 MiB — into the Downloads root with a unique name, resolve-then-contain, `sha256` verified against the render's `content_hash` before the file is kept; then M19's `file.open` opens it. "Bunu aç" after "Bana bir bütçe tablosu yap" = the current `artifact` focus → fetch + open, receipted with the observed window.
- Focus kinds `artifact` (a render) on the M19 stack; "önceki" works.

## 5. Voice

Tools: `artifact.create {kind, title, spec}` (the router extracts the deterministic part; the receipt names the file, the format(s), the validation verdict and the ref of any failure), `artifact.render {target, format}`, `artifact.validate {target}` (re-validate on demand), `artifact.open {target}` (fetch + open on the device; `capability_missing` with the 0.1.0 agent), `artifact.list {}`. Intents `ARTIFACT_CREATE` ("Bana bir bütçe tablosu yap: kira 12000, maaş 45000, yazılım 8000", "Toplantı notlarını Word belgesi yap", "Üç slaytlık bir sunum hazırla: giriş, bulgular, sonuç", "Bunu PDF yap"), `ARTIFACT_OPEN` ("Bunu aç", "Son ürettiğin dosyayı aç"), `ARTIFACT_LIST` ("Neler ürettin?"), `ARTIFACT_VALIDATE` ("Bu dosya doğru mu?"). Corpus category `artifacts` ≥ 100 (values carried literally; negatives: "Bunu aç" with nothing produced → clarification; a spec with a number the owner did not say is never invented — the test asserts the rendered numbers ⊆ the spoken numbers; "Sil" reaches no tool).

## 6. Fixtures and the automated qualification

`services/api/tests/fixtures/artifacts/specs/*.json` — one spec per kind with expected refs (`truth.json`): the budget spreadsheet (three rows, a total 65000, a formula `=SUM(B2:B4)`), a five-section document with a table, a three-slide presentation, a dataset, a page. Tests: every renderer × validator round trip on this machine (PROVEN_AUTOMATED, real bytes reopened); determinism (two renders, one hash); an injected corruption (a renderer that drops a row) caught by validation; the device lab: `file.fetch` from a local HTTP server standing in for the Cloud Core origin (a wrong origin refused, a wrong hash refused and the file removed, the size bound), then `file.open`; the corpus; UI contract v7 `artifact.factory` state (`{title, format, verdict}`), the Cockpit panel tests.

## 7. Marks sought

Renderers + validation PROVEN_AUTOMATED (real files reopened by independent parsers); the device fetch/open PROVEN_REAL on this machine and the runner / PROVEN_PROXY on the deployed agent (item 28); voice PROVEN_AUTOMATED; loopback PROVEN_PROXY (or PROVIDER_UNAVAILABLE while item 29 is open); the Cloud Core half PROVEN_REAL (release).

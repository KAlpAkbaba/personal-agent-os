---
name: m20-file-document-intelligence-review
description: Findings from the M20 File & Document Intelligence device-half review (commits 0d3d8de/a2504d4/f49042f/c453bc5, merged into main at ce4378d, 2026-09-08); High - OOXML decompression bomb crashes the Session Companion via OOM (verified live); Low - secret-name existence oracle, text-extraction memory amplification
metadata:
  type: project
---

Reviewed git diff cb35298..ce4378d -- devices packages services/api/app/broker (the Documents
family in the Windows agent: FileIdentity, FileKinds/SecretNames/TextFold, TextFileReader,
DocumentExtractor, OpenXmlExtractor, PdfPigExtractor, TextLikeExtractor, FileSearch,
DocumentCompare, DocumentCapabilities; the CompanionRuntime/Program/InteractiveCapabilityExecutor
wiring; the broker/schema taxonomy additions). See [[junction-escape-recurring-pattern]] and
[[m19-digital-operator-security-review]] (the resolve-then-contain fix this module reuses
unchanged via AuthorisedRoots).

High - a small OOXML file (well inside the 50 MiB container bound) can crash the Session
Companion via an unbounded decompression bomb; the per-request timeout cannot stop it.
OpenXmlExtractor.InspectDocx/ExtractDocx/InspectXlsx/ExtractXlsx/InspectPptx/ExtractPptx
(devices/windows-agent/src/PagentOS.SessionCompanion/Documents/OpenXmlExtractor.cs) all
unconditionally touch document.MainDocumentPart.Document.Body (or .Worksheet/.Presentation)
before any per-element/cancellation check runs. DocumentCapabilities.RequireReadable
(DocumentCapabilities.cs:554-562) only checks FileInfo.Length - the on-disk, COMPRESSED
container size - never anything about a part's decompressed size; DocumentFormat.OpenXml and
System.IO.Compression.DeflateStream impose no decompression-ratio or output-size cap of their own.
Verified live: built a 2 MiB .docx ([Content_Types].xml + _rels/.rels + word/document.xml
containing one w:t run of about 2 GiB of a repeated character, DEFLATE level 9) and opened it with
DocumentFormat.OpenXml 3.5.1 (the pinned version) in a throwaway console app mirroring ExtractDocx's
exact call shape - process memory went 22 MiB, 803 MiB, 2.1 GiB, 3.5 GiB in about 3 seconds and
crashed with OutOfMemoryException, well before the per-element loop (where the only
cancellationToken.ThrowIfCancellationRequested() calls live) ever ran. The 30 s
DocumentsTimeoutCap budget (wired through a linked CancellationTokenSource in
DocumentCapabilities.ExecuteAsync) cannot help: the vulnerable call is a single synchronous SDK
property getter with no cancellation awareness, and Task.Run cannot be forcibly aborted, so an
abandoned task keeps allocating in the background even after the caller sees a timeout error.
file.inspect, document.extract and file.compare (which calls ExtractDocument on both sides for
same-kind extractable pairs) all reach this path. Reach: any file the owner has read access to in
an authorised root - an email attachment saved to Downloads (M13's browser.download already writes
attacker-influenced files there), a synced cloud-drive file under Documents - crashes the
interactive Session Companion the next time it (or Cloud Core on the owner's behalf, e.g. a voice
"bu dosyayi ozetle") is asked to inspect/extract/compare it. The Session Companion also hosts the
M19 Digital Operator (UI Automation, keyboard/pointer, screen capture), so this is a DoS of more
than the documents feature. Fix: before handing a path to DocumentFormat.OpenXml/PdfPig, open the
container as a ZipArchive and reject (unsupported_format) any package whose summed uncompressed
entry length exceeds a fixed cap, or whose per-entry compression ratio exceeds a threshold;
additionally run extraction in a Windows-Job-Object-bounded child process so a bomb that slips past
the ratio check kills a disposable process, not the interactive companion. PdfPig is
architecturally lazier (per-page, not whole-document DOM) so the equivalent bomb there is scoped to
one page's content-stream decompression - not separately verified live, flagged as residual risk.

Low - secret-bearing-name existence oracle via error-class distinction on non-search paths.
DocumentCapabilities.ConfineFile/ResolveTarget (DocumentCapabilities.cs:454-548): an existing
secret-bearing file (e.g. .env) inside an authorised root answers permission_denied /
secret_bearing_name on file.locate/file.read/etc., but the SAME path when the file does not exist
answers not_found (the parent-resolves-but-leaf-missing branch runs before any secret-name check) -
the error class alone reveals whether a specific secret-bearing name exists, narrower than but
analogous to the existence-oracle the addendum otherwise explicitly closes for out-of-root paths.
file.search itself is NOT affected - it filters secret names out of listings entirely, not just
their hash. Fix: check SecretNames.IsSecretBearing on the requested leaf name lexically (from the
file name portion of the raw path, before resolution) so a secret-bearing name always answers
permission_denied regardless of existence.

Low - text-like read/extract fully materializes the file in memory before the 64 KB truncation,
plus a stat-then-read TOCTOU on the 50 MiB bound. TextFileReader.Read (File.ReadAllBytes,
TextFileReader.cs:67) and TextLikeExtractor's Csv/Json/Source/Markdown paths all decode and
split-lines/parse-json the WHOLE file before any block-budget check; RequireReadable checks the
FileInfo snapshot taken when the Target was built, not the byte count actually read. Bounded to a
small constant multiple of the existing 50 MiB cap (not unbounded like the OOXML finding), so low
severity in this single-owner model; noted because the review explicitly asked for it.

Verified sound:
- Path confinement is resolve-then-contain everywhere (search roots, file_id memo lookups, both
  sides of file.compare, extract, read, inspect, locate) via the SAME AuthorisedRoots.Confine /
  ResolveFinal the M19 review forced (OperatorNative.FinalPath opens a real handle before any
  comparison); re-confirmed by reading AuthorisedRoots.cs end to end (no new lexical shortcut for
  M20) and by the lab's CompareIdentityConfinementTests / SearchInspectReadTests building a REAL
  junction (JunctionFixture.CreateJunction) inside the authorised root and asserting refusal.
- file_id / doc_id derived only from the RESOLVED path, never caller spelling; the _known memo
  re-runs Roots.Confine at resolution time rather than trusting the cached path.
- 8.3 short names, trailing dot/space, UNC and extended-length device paths, case folding: all
  handled by OS-level resolution (GetFullPath + OperatorNative.FinalPath), not string matching;
  Turkish I-to-dotted-i fold explicitly avoided in FileIdentity.Casefold.
- Secret names checked on the RESOLVED final leaf name before any hashing/extractor call; a search
  filters them out of listings entirely (existence/size not leaked via search).
- Every bound (search's 200/20000/10s, read/extract's 65536 chars, PDF's 200 pages, XLSX's 2000
  rows) answers truncated: true honestly, never a silent cut; proven against real files including
  a call-counting extractor proving a 60 MiB file is never opened.
- Authority: documents family double-gated exactly like M19's operator family (Device Service AND
  Session Companion both check OperatorEnabled / documentCapabilities.Enabled before any I/O);
  nothing writes/moves/deletes (confirmed no such call anywhere in Documents/*.cs); no
  delete/move/write capability name exists.
- Broker/schema taxonomy additions (unsupported_format, not_found) are purely additive, cross-
  checked directly against the schema file and frames.py by DocumentAdvertisementTests.
- Every documents result passes the browser family's forbidden-key scan before leaving the
  companion; audit log records capability/outcome/duration only, never a path or file name.
- The lab: fixtures copied into a per-run temp dir under the fixture root, removed on Dispose;
  tests that touch outside paths/junctions clean up in finally blocks; no test weakens a product
  assertion or reads outside its own fixture/temp directories.

Residual risk: the OOXML bomb (High) is the significant open item - recommend a decompression-
ratio/size guard and/or job-object memory cap before any wider rollout of -Operator mode.
XLSX/PPTX were not separately bombed empirically (only DOCX) but share the same DOM-materializing
DocumentFormat.OpenXml pattern, so the same fix should cover all three.

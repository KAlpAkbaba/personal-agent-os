---
name: m22-artifact-factory-device-review
description: M22 Artifact Factory device-half review findings (file.fetch, commits 328a887/6ee99cc/9e1b2ea on m22-device, 2026-09-08). High (verified live) - the 30s cap does not bound a stalled download because the body read uses a synchronous Stream.Read overload that ignores its CancellationToken. Medium - TOCTOU on the rename; incomplete executable-extension blocklist plus no Mark-of-the-Web. Low - proxy default left on; Low - no Unicode bidi-override filtering in names.
metadata:
  type: project
---

Reviewed git diff 2d442b2..HEAD in worktree pagentos-wt-m22-device (M22 device half, file.fetch):
FileFetch.cs, DocumentCapabilities.cs, HttpOrigin.cs, PipeMessages.cs, CompanionPipeServer.cs,
InteractiveCapabilityExecutor.cs, CompanionRuntime.cs, OperatorCapabilities and OperatorOptions
and AuthorisedRoots.cs, DEVICE_PROTOCOL.md section 6k, ADR-0085 plus addendum 1, and
FileFetchTests.cs. Continues the method from [[m20-file-document-intelligence-review]] and
[[m21-mail-calendar-security-review]] (bound-at-the-parse-boundary pattern). AuthorisedRoots was
reused unchanged and is re-confirmed sound; see [[junction-escape-recurring-pattern]] (not
triggered here).

High (verified live) - the documented 30s cap does not bound a stalled body read.
FileFetch.StreamToTemp uses response.Content.ReadAsStream(token) then the synchronous
Stream.Read(byte[],int,int) overload. Built a throwaway .NET console harness with a raw
TcpListener that sends headers plus 10 bytes then sleeps 60s: a 3s CancellationToken had no
effect on the blocked Read call, which only returned when the peer closed at the 60s mark.
Swapping the identical call to the async ReadAsync(buffer, token) overload correctly threw
OperationCanceledException within about 3ms of the deadline, confirming both the bug and the
fix direction. The headers phase (HttpClient.Send with HttpCompletionOption.
ResponseHeadersRead) is properly cancellable; the gap is specific to body streaming.
DEVICE_PROTOCOL.md section 6k explicitly claims a stalled origin answers timeout, retryable,
nothing kept - false for a mid-body stall. FileFetchTests.cs has no stall or hang test, only
static assertions that the configured cap constant is 30s. Effect: a peer at the pinned
origin can hold a companion thread and a temp file indefinitely past the stated cap; Cloud
Core sees a clean timeout from its own pipe wait, masking the still-stuck companion thread.
Fix: rewrite StreamToTemp to use async ReadAsync throughout, and add the stalled-connection
lab test the docs already claim exists.

Medium - TOCTOU between hash verification and File.Move to the final name (FileFetch.cs,
MoveToFinalName). The temp file exclusive handle closes when StreamToTemp returns, after the
hash is verified in memory; Run then calls File.Move afterward. A co-resident process
running as the same Windows user can swap the temp file bytes in that window, violating the
stated invariant that nothing with the requested name exists unless it has the requested
hash. Bounded to a local-code-execution-as-same-user threat model but real. Fix: keep one
open handle across verify-and-rename, or re-hash the final path immediately after the move.

Medium - OperatorCapabilities.ExecutableExtensions (shared by file.fetch ValidateName and
file.open RequireAuthorisedPath) lists exe bat cmd com scr ps1 psm1 vbs vbe js jse wsf wsh
msi msp lnk hta reg cpl inf but is missing the macro-enabled Office formats (docm dotm xlsm
xlsb xltm xlam pptm potm ppam sldm), url, jar, pif. Compounding: no NTFS Zone.Identifier
Mark-of-the-Web stream is ever written for a fetched file anywhere in the diff, so even
non-executable documents lose Office Protected View the way a browser download gets it.
Since Cloud Core fully controls the name and content (only origin and hash are checked,
never content type), a renderer bug or compromise there can place live macro content into
Downloads under a currently unblocked extension and, with open true, have it opened with no
Protected View gate. Fix: extend the blocklist with the macro-enabled family plus url, jar,
pif, and write the Zone.Identifier stream on every kept file.

Low - FileFetch.NewPinnedClient sets AllowAutoRedirect false, UseCookies false,
AutomaticDecompression None but never UseProxy false, so system or environment proxy
settings still apply to the request. Origin pinning plus the sha256 check prevent a rogue
proxy from redirecting or tampering undetected, but it can still observe the fetched content
in transit, inconsistent with the otherwise fully pinned handler. Fix: add UseProxy false.

Low - ValidateName's invalid-character check (Path.GetInvalidFileNameChars) covers ASCII
control characters and separator or wildcard punctuation but not Unicode bidi override
characters such as U+202E, so a name can be crafted to display misleadingly in Explorer. The
real extension enforcement operates on the true character sequence and is not bypassed, so
this is display spoofing only, not a boundary bypass. Fix: reject Unicode formatting-control
category characters too.

Verified sound (origin pinning, tested empirically with a standalone Uri-parsing harness,
not just read): a trailing dot keeps the dot and fails the comparison closed; an uppercase
host is auto-lowercased; default versus explicit port 443 normalize identically; a
non-default port is correctly a different origin; IPv6 literals preserved correctly;
userinfo in either ordering gets embedded into GetLeftPart's output so the comparison fails
closed even before FileFetch.ParseUrl's own explicit UserInfo-length rejection runs, two
independent layers; a raw Unicode hostname is not punycoded by Uri.Host but the same Uri
instance is used for both the compare and the outbound request so there is no divergence
risk; dot-dot and percent-encoded dot-dot in the path are canonicalized by Uri before the
path-prefix check and before the request is sent. No combination produced a false accept.
Redirects: AllowAutoRedirect false plus explicit 3xx handling. TLS: no trust-bypass callback
anywhere. Handshake: an older service with no broker_origin field leaves FetchOrigin null
and every fetch is refused. Pipe identity is the pre-existing unchanged four-layer model;
file.fetch adds no new pipe verb. Authority: OperatorEnabled double-gated on both the Device
Service and the companion, matching the M20 pattern. Path confinement: the Downloads root is
resolved via AuthorisedRoots.Confine at fetch time, not just startup, and the final path is
re-confined again after the write. Bounds: Content-Length versus size checked before
reading; the running byte count is enforced during streaming regardless of the header; true
streaming with a 64 KiB buffer, never buffered whole. Name policy is otherwise thorough.
Lab: LocalOrigin binds 127.0.0.1 only; DocumentLab.Dispose deletes its temp tree and kills
only processes it started that are literally named notepad.

Residual risk: the High slow-loris finding is the significant open item since it needs no
compromise anywhere, only a stalled or slow connection, unlike the two Medium findings which
assume Cloud Core is already buggy or compromised, or that another process already runs as
the same user. DocumentCapabilities has no equivalent of OperatorCapabilities' single gate
semaphore, so concurrent stalled file.fetch calls tie up one thread each rather than
serializing, mildly amplifying the High finding's ceiling.

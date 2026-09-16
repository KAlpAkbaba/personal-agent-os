using System.Collections.Concurrent;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Documents;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// The M22 device lab (M22_ARTIFACT_FACTORY_SPEC.md §4/§6, ADR-0085 decision 4,
/// DEVICE_PROTOCOL.md §6k): a local <see cref="HttpListener"/> on 127.0.0.1 stands in for the
/// Cloud Core origin the device dialled, the lab pins the real <see cref="DocumentCapabilities"/>
/// to it the way the pipe challenge would, and every refusal is proven with nothing left
/// behind in the run's Downloads root: a foreign origin, a wrong hash, a Content-Length over
/// the bound, a chunked body that keeps sending past it (the abort is counted on the server),
/// a redirect off-origin, a name that is not a plain file name, a collision. The happy path
/// keeps a 200 KB DOCX, verified, marked as from the web, and — on a host with a desktop —
/// opens a .txt variant in Notepad through the operator's real <c>file.open</c> and observes
/// its window. Since ADR-0085 addendum 3 a raw <see cref="RawOrigin"/> (a <c>TcpListener</c>,
/// no HTTP stack) also stands in for a peer that sends its headers and ten bytes and then
/// goes quiet: the fetch is <c>timeout</c> within the cap, the connection is dropped, the
/// thread and the slot are free for the next fetch, and a third fetch beside two stalled ones
/// is refused as busy. Nothing here touches the owner's real Downloads folder: the lab's root
/// is <c>%TEMP%\pagentos-operator-fixture\documents\&lt;run-id&gt;\Downloads</c>.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class FileFetchTests : IDisposable
{
    private const string RenderPath = "/v1/artifacts/a-1/renders/r-1/download";
    private const string Signature = "?sig=owner-session-signed&exp=1900000000";

    private readonly LocalOrigin _origin = new();
    private readonly DocumentLab _lab;
    private readonly ITestOutputHelper _output;

    public FileFetchTests(ITestOutputHelper output)
    {
        _output = output;
        _lab = new DocumentLab(fetchOrigin: _origin.Origin);
    }

    public void Dispose()
    {
        _lab.Dispose();
        _origin.Dispose();
    }

    private static string Sha256Of(byte[] bytes) => Convert.ToHexStringLower(SHA256.HashData(bytes));

    private static JsonObject Payload(string url, string name, byte[] bytes, bool? open = null, string? application = null)
    {
        var payload = new JsonObject
        {
            ["url"] = url,
            ["name"] = name,
            ["sha256"] = Sha256Of(bytes),
            ["size"] = bytes.Length,
        };
        if (open is not null)
        {
            payload["open"] = open.Value;
        }

        if (application is not null)
        {
            payload["application"] = application;
        }

        return payload;
    }

    private string RenderUrl(string path = RenderPath) => _origin.Origin + path + Signature;

    /// <summary>Every entry in the Downloads root — the final files AND any <c>.part</c> a refusal might have left.</summary>
    private string[] DownloadsEntries() => Directory.GetFileSystemEntries(_lab.Downloads).Select(e => Path.GetFileName(e)).ToArray();

    /// <summary>
    /// The committed <c>sozlesme.docx</c> (37 KB) padded to about 200 KB with an unreferenced,
    /// incompressible media part that <c>[Content_Types].xml</c> declares — still a package
    /// the M20 extractor opens and titles, so the round trip proves a genuine DOCX arrived.
    /// </summary>
    private static byte[] PaddedDocx(int padBytes)
    {
        var source = Path.Combine(DocumentLab.FixtureSource, "sozlesmeler", "2026", "sozlesme.docx");
        using var memory = new MemoryStream();
        memory.Write(File.ReadAllBytes(source));
        using (var zip = new ZipArchive(memory, ZipArchiveMode.Update, leaveOpen: true))
        {
            var types = zip.GetEntry("[Content_Types].xml")!;
            string xml;
            using (var reader = new StreamReader(types.Open(), Encoding.UTF8, leaveOpen: false))
            {
                xml = reader.ReadToEnd();
            }

            if (!xml.Contains("Extension=\"png\"", StringComparison.OrdinalIgnoreCase))
            {
                xml = xml.Replace("</Types>", "<Default Extension=\"png\" ContentType=\"image/png\"/></Types>", StringComparison.Ordinal);
                using var rewrite = types.Open();
                rewrite.SetLength(0);
                var bytes = new UTF8Encoding(false).GetBytes(xml);
                rewrite.Write(bytes, 0, bytes.Length);
            }

            var padding = zip.CreateEntry("word/media/image1.png", CompressionLevel.NoCompression);
            using var stream = padding.Open();
            var random = new byte[padBytes];
            new Random(20260908).NextBytes(random);
            stream.Write(random, 0, random.Length);
        }

        return memory.ToArray();
    }

    // ------------------------------------------------------------- (a) the happy path

    [Fact]
    public void A_render_from_the_dialled_origin_is_fetched_into_the_downloads_root_verified_and_identified()
    {
        var docx = PaddedDocx(170 * 1024);
        Assert.InRange(docx.Length, 190 * 1024, 230 * 1024);
        _origin.ServeBytes(RenderPath, docx);

        var result = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "sözleşme-2026.docx", docx));

        Assert.True(result["verified"]!.GetValue<bool>());
        Assert.Equal((long)docx.Length, result["bytes"]!.GetValue<long>());
        Assert.Equal(Sha256Of(docx), result["sha256"]!.GetValue<string>());
        var path = result["path"]!.GetValue<string>();
        Assert.Equal(Path.Combine(_lab.Downloads, "sözleşme-2026.docx"), path, ignoreCase: true);
        Assert.True(File.Exists(path));
        Assert.Equal(docx, File.ReadAllBytes(path));
        Assert.Equal(["sözleşme-2026.docx"], DownloadsEntries());

        // The kept file carries the Mark-of-the-Web (Internet zone, nothing else — no URL).
        Assert.True(result["mark_of_the_web"]!.GetValue<bool>());
        Assert.Equal(FileFetch.ZoneIdentifierContent, File.ReadAllText(path + ":" + FileFetch.ZoneIdentifierStream));
        Assert.Equal("[ZoneTransfer]\r\nZoneId=3\r\n", File.ReadAllText(path + ":Zone.Identifier"));

        // The record is the family's own (resolved path, location identity, content hash).
        var record = (JsonObject)result["file"]!;
        Assert.Equal(path, record["path"]!.GetValue<string>());
        Assert.Equal("sözleşme-2026.docx", record["name"]!.GetValue<string>());
        Assert.Equal(".docx", record["extension"]!.GetValue<string>());
        Assert.Equal(Sha256Of(docx), record["sha256"]!.GetValue<string>());
        Assert.StartsWith("file:", record["file_id"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Null(result["opened"]);

        // The file_id resolves again, and what arrived is a DOCX the M20 extractor titles.
        var inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["file_id"] = record["file_id"]!.GetValue<string>() });
        Assert.Equal("docx", inspected["kind"]!.GetValue<string>());
        Assert.Equal("Hizmet Sözleşmesi", inspected["title"]!.GetValue<string>());

        // Exactly one request, carrying the signed query verbatim and nothing of the owner's.
        var request = Assert.Single(_origin.Requests);
        Assert.Equal(RenderPath, request.Path);
        Assert.Equal(Signature, request.Query);
        Assert.Null(request.Authorization);
        Assert.False(request.HasCookie);
        Assert.Equal((long)docx.Length, _origin.BytesServed);
    }

    [LabFact]
    public void With_open_true_the_txt_variant_opens_in_notepad_and_the_window_is_observed()
    {
        using var lab = new DocumentLab(withOperator: true, fetchOrigin: _origin.Origin);
        var text = Encoding.UTF8.GetBytes(string.Concat(Enumerable.Repeat("Bütçe tablosu — kira 12000, maaş 45000, yazılım 8000\r\n", 3800)));
        Assert.InRange(text.Length, 190 * 1024, 230 * 1024);
        _origin.ServeBytes("/v1/artifacts/a-2/renders/r-2/download", text);

        var result = lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/a-2/renders/r-2/download" + Signature, "butce.txt", text, open: true, application: "notepad"));
        var path = result["path"]!.GetValue<string>();
        Assert.True(File.Exists(path));
        Assert.True(result["verified"]!.GetValue<bool>());

        var opened = (JsonObject)result["opened"]!;
        Assert.True(opened["opened"]!.GetValue<bool>());
        Assert.Equal(path, opened["path"]!.GetValue<string>());
        var pid = opened["pid"]!.GetValue<int>();
        Assert.True(opened["observed"]!["window_appeared"]!.GetValue<bool>(), "Notepad showed no window within the wait");
        var windowId = opened["window_id"]!.GetValue<string>();
        Assert.StartsWith("w-", windowId, StringComparison.Ordinal);
        Assert.Equal("notepad.exe", opened["observed"]!["window"]!["image"]!.GetValue<string>());
        Assert.Contains(pid, lab.Operator!.StartedPids);

        // Close what the lab opened, through the operator, and verify it is gone.
        var closed = lab.Operator.ExecuteAsync(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true }, TimeSpan.FromSeconds(15), CancellationToken.None).GetAwaiter().GetResult();
        Assert.True(closed["closed"]!.GetValue<bool>());
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(5)));
    }

    [Fact]
    public void Open_true_without_the_operator_is_capability_missing_before_any_request_and_a_failed_open_is_reported_inside_the_result()
    {
        var bytes = Encoding.UTF8.GetBytes("merhaba");
        _origin.ServeBytes(RenderPath, bytes);

        var missing = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "a.txt", bytes, open: true));
        Assert.Equal(ErrorClasses.CapabilityMissing, missing.ErrorClass);
        Assert.Empty(_origin.Requests);
        Assert.Empty(DownloadsEntries());

        var stray = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "a.txt", bytes, application: "notepad"));
        Assert.Equal(ErrorClasses.ValidationError, stray.ErrorClass);
        Assert.Contains("payload.application", stray.Message, StringComparison.Ordinal);

        // With the operator: an application it does not allowlist fails the OPEN, not the fetch —
        // the file is kept and the refusal travels inside `opened`.
        using var lab = new DocumentLab(withOperator: true, fetchOrigin: _origin.Origin);
        var result = lab.Exec(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "a.txt", bytes, open: true, application: "regedit"));
        Assert.True(File.Exists(result["path"]!.GetValue<string>()));
        var opened = (JsonObject)result["opened"]!;
        Assert.False(opened["opened"]!.GetValue<bool>());
        Assert.Equal(ErrorClasses.PermissionDenied, opened["error"]!["class"]!.GetValue<string>());
        Assert.Empty(lab.Operator!.StartedPids);
    }

    // ------------------------------------------------------------- (b) origin

    [Fact]
    public void A_url_on_another_origin_or_off_the_artifact_path_is_permission_denied_before_any_request_and_nothing_is_written()
    {
        using var foreign = new LocalOrigin();
        var bytes = Encoding.UTF8.GetBytes("not yours");
        foreign.ServeBytes(RenderPath, bytes);
        _origin.ServeBytes(RenderPath, bytes);

        var other = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(foreign.Origin + RenderPath + Signature, "x.txt", bytes));
        Assert.Equal(ErrorClasses.PermissionDenied, other.ErrorClass);
        Assert.False(other.Retryable);
        Assert.Contains("payload.url", other.Message, StringComparison.Ordinal);
        Assert.Contains("beklenen köken değil", other.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("127.0.0.1", other.Message, StringComparison.Ordinal);
        Assert.DoesNotContain(foreign.Origin, other.Message, StringComparison.OrdinalIgnoreCase);

        // Same host, another scheme: another origin. Same origin, another path: not a render.
        var scheme = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload("https" + _origin.Origin[4..] + RenderPath, "x.txt", bytes));
        Assert.Equal(ErrorClasses.PermissionDenied, scheme.ErrorClass);
        var path = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/devices/d-1/commands", "x.txt", bytes));
        Assert.Equal(ErrorClasses.PermissionDenied, path.ErrorClass);
        Assert.Contains(DocumentCapabilityNames.FetchPathPrefix, path.Message, StringComparison.Ordinal);
        var traversal = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/../devices/d-1", "x.txt", bytes));
        Assert.Equal(ErrorClasses.PermissionDenied, traversal.ErrorClass);

        // A companion never told an origin refuses everything, on the same origin included.
        using var untold = new DocumentLab(fetchOrigin: null);
        Assert.Null(untold.Documents.FetchOrigin);
        var none = untold.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "x.txt", bytes));
        Assert.Equal(ErrorClasses.PermissionDenied, none.ErrorClass);
        Assert.Contains("beklenen köken değil", none.Message, StringComparison.Ordinal);

        Assert.Empty(foreign.Requests);
        Assert.Empty(_origin.Requests);
        Assert.Empty(DownloadsEntries());
        Assert.Empty(Directory.GetFileSystemEntries(untold.Downloads));
    }

    [Fact]
    public void A_downloads_root_outside_the_authorised_roots_refuses_every_fetch_before_any_request()
    {
        var outside = Path.Combine(Path.GetTempPath(), "pagentos-fetch-outside-" + _lab.RunId);
        Directory.CreateDirectory(outside);
        try
        {
            using var lab = new DocumentLab(fetchOrigin: _origin.Origin, downloadsRoot: outside);
            var bytes = Encoding.UTF8.GetBytes("x");
            _origin.ServeBytes(RenderPath, bytes);
            var refused = lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "x.txt", bytes));
            Assert.Equal(ErrorClasses.PermissionDenied, refused.ErrorClass);
            Assert.Contains("Downloads root", refused.Message, StringComparison.Ordinal);
            Assert.DoesNotContain(outside, refused.Message, StringComparison.OrdinalIgnoreCase);
            Assert.Empty(_origin.Requests);
            Assert.Empty(Directory.GetFileSystemEntries(outside));
        }
        finally
        {
            Directory.Delete(outside, recursive: true);
        }
    }

    // ------------------------------------------------------------- (c) hash

    [Fact]
    public void A_wrong_hash_or_a_short_body_is_postcondition_failed_and_nothing_is_left_behind()
    {
        var docx = PaddedDocx(170 * 1024);
        _origin.ServeBytes(RenderPath, docx);

        var payload = Payload(RenderUrl(), "sözleşme.docx", docx);
        payload["sha256"] = Sha256Of(Encoding.UTF8.GetBytes("something else"));
        var mismatch = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, payload);
        Assert.Equal(ErrorClasses.PostconditionFailed, mismatch.ErrorClass);
        Assert.False(mismatch.Retryable);
        Assert.Equal(FileFetch.Sha256Mismatch, mismatch.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("payload.sha256", mismatch.Message, StringComparison.Ordinal);
        Assert.Empty(DownloadsEntries());
        Assert.Single(_origin.Requests);

        // A body that ends before payload.size (chunked, so no Content-Length disagrees first).
        _origin.ServeBytes("/v1/artifacts/short", docx[..1000], chunked: true);
        var shortPayload = Payload(_origin.Origin + "/v1/artifacts/short", "kisa.docx", docx);
        var ended = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, shortPayload);
        Assert.Equal(ErrorClasses.PostconditionFailed, ended.ErrorClass);
        Assert.Contains("1000 bytes", ended.Message, StringComparison.Ordinal);
        Assert.Empty(DownloadsEntries());
    }

    // ------------------------------------------------------------- (d) size

    [Fact]
    public void A_content_length_over_the_bound_is_refused_unread_and_a_chunked_stream_past_it_is_aborted_early()
    {
        // The bound itself: a payload over 50 MiB is refused before any request.
        var over = Payload(RenderUrl(), "buyuk.bin", []);
        over["size"] = DocumentCapabilityNames.MaxFetchBytes + 1;
        over["sha256"] = new string('0', 64);
        var tooLarge = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, over);
        Assert.Equal(ErrorClasses.ValidationError, tooLarge.ErrorClass);
        Assert.Contains("payload.size", tooLarge.Message, StringComparison.Ordinal);
        Assert.Empty(_origin.Requests);

        // Content-Length over the (maximal) declared size: refused on the headers, no body read.
        _origin.ServeDeclared("/v1/artifacts/declared", DocumentCapabilityNames.MaxFetchBytes + 1);
        var declared = Payload(_origin.Origin + "/v1/artifacts/declared", "buyuk.bin", []);
        declared["size"] = DocumentCapabilityNames.MaxFetchBytes;
        declared["sha256"] = new string('0', 64);
        var refused = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, declared);
        Assert.Equal(ErrorClasses.ValidationError, refused.ErrorClass);
        Assert.Contains("Content-Length", refused.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was read", refused.Message, StringComparison.Ordinal);
        Assert.Empty(DownloadsEntries());

        // A chunked body with no Content-Length that keeps sending: aborted just past the
        // bound, counted on the server — it never got to send what it had.
        const long ServerWouldSend = 80L * 1024 * 1024;
        _origin.ServeEndless("/v1/artifacts/endless", ServerWouldSend);
        var endless = Payload(_origin.Origin + "/v1/artifacts/endless", "sonsuz.bin", []);
        endless["size"] = DocumentCapabilityNames.MaxFetchBytes;
        endless["sha256"] = new string('0', 64);
        var aborted = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, endless);
        Assert.Equal(ErrorClasses.ValidationError, aborted.ErrorClass);
        Assert.Contains("aborted", aborted.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was kept", aborted.Message, StringComparison.Ordinal);
        Assert.Empty(DownloadsEntries());

        var served = _origin.WaitForServingToStop("/v1/artifacts/endless", TimeSpan.FromSeconds(20));
        Assert.True(served >= DocumentCapabilityNames.MaxFetchBytes, $"the client aborted before the bound was reached ({served} bytes served)");
        Assert.True(served < ServerWouldSend, $"the server sent everything it had ({served} bytes); the client did not abort");
    }

    // ------------------------------------------------------------- (e) redirects and status

    [Fact]
    public void A_redirect_is_dependency_unavailable_never_followed_and_a_non_2xx_answer_is_reported()
    {
        using var elsewhere = new LocalOrigin();
        var bytes = Encoding.UTF8.GetBytes("moved");
        elsewhere.ServeBytes(RenderPath, bytes);
        _origin.ServeRedirect("/v1/artifacts/moved", elsewhere.Origin + RenderPath);
        _origin.ServeRedirect("/v1/artifacts/moved-here", _origin.Origin + RenderPath);
        _origin.ServeBytes(RenderPath, bytes);
        _origin.ServeStatus("/v1/artifacts/gone", 410);
        _origin.ServeStatus("/v1/artifacts/broken", 503);

        var redirected = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/moved", "x.txt", bytes));
        Assert.Equal(ErrorClasses.DependencyUnavailable, redirected.ErrorClass);
        Assert.False(redirected.Retryable);
        Assert.Contains("redirect", redirected.Message, StringComparison.Ordinal);
        Assert.Empty(elsewhere.Requests);

        // Even a redirect onto the same origin is not followed: the URL Cloud Core signed is the URL.
        var same = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/moved-here", "x.txt", bytes));
        Assert.Equal(ErrorClasses.DependencyUnavailable, same.ErrorClass);
        Assert.Equal(0, _origin.RequestCount(RenderPath));

        var gone = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/gone", "x.txt", bytes));
        Assert.Equal(ErrorClasses.DependencyUnavailable, gone.ErrorClass);
        Assert.False(gone.Retryable);
        Assert.Contains("HTTP 410", gone.Message, StringComparison.Ordinal);
        var broken = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/broken", "x.txt", bytes));
        Assert.Equal(ErrorClasses.DependencyUnavailable, broken.ErrorClass);
        Assert.True(broken.Retryable);

        // An origin that is not listening at all.
        using var lab = new DocumentLab(fetchOrigin: "http://127.0.0.1:1");
        var dead = lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload("http://127.0.0.1:1" + RenderPath, "x.txt", bytes));
        Assert.Equal(ErrorClasses.DependencyUnavailable, dead.ErrorClass);
        Assert.True(dead.Retryable);
        Assert.DoesNotContain("127.0.0.1", dead.Message, StringComparison.Ordinal);

        Assert.Empty(DownloadsEntries());
    }

    // ------------------------------------------------------------- (f) names and shape

    [Theory]
    [InlineData(@"..\x.txt")]
    [InlineData("../x.txt")]
    [InlineData(@"C:\x.txt")]
    [InlineData("/x.txt")]
    [InlineData(@"alt\x.txt")]
    [InlineData("con.txt")]
    [InlineData("NUL")]
    [InlineData("lpt1.docx")]
    [InlineData("x.txt.")]
    [InlineData(".")]
    [InlineData("a..b.txt")]
    [InlineData("x\u0000.txt")]
    [InlineData("x\n.txt")]
    [InlineData("x?.txt")]
    [InlineData("x.exe")]
    [InlineData("x.ps1")]
    [InlineData("x.lnk")]
    [InlineData(".env")]
    [InlineData("server.pem")]
    [InlineData("secrets.json")]
    [InlineData(".pagentos-fetch-abc.part")]
    [InlineData(" ")]
    // ADR-0085 addendum 3: the macro-enabled Office family and the launchers the first list missed.
    [InlineData("makro.docm")]
    [InlineData("sablon.dotm")]
    [InlineData("butce.xlsm")]
    [InlineData("butce.xlsb")]
    [InlineData("sablon.xltm")]
    [InlineData("eklenti.xlam")]
    [InlineData("sunum.pptm")]
    [InlineData("sablon.potm")]
    [InlineData("eklenti.ppam")]
    [InlineData("slayt.sldm")]
    [InlineData("MAKRO.DOCM")]
    [InlineData("link.url")]
    [InlineData("app.jar")]
    [InlineData("eski.pif")]
    // Unicode format characters (Cf) and C1 controls: invisible, or reordering what Explorer shows.
    [InlineData("fdp.\u202Etxt.docx")]
    [InlineData("x\u200E.txt")]
    [InlineData("x\u200F.txt")]
    [InlineData("x\u202A.txt")]
    [InlineData("x\u202D.txt")]
    [InlineData("x\u2066.txt")]
    [InlineData("x\u2069.txt")]
    [InlineData("x\u200B.txt")]
    [InlineData("x\uFEFF.txt")]
    [InlineData("x\u0085.txt")]
    [InlineData("x\u009F.txt")]
    public void A_name_that_is_not_a_plain_file_name_is_validation_error_before_any_request(string name)
    {
        var bytes = Encoding.UTF8.GetBytes("x");
        _origin.ServeBytes(RenderPath, bytes);
        var refused = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), name, bytes));
        Assert.Equal(ErrorClasses.ValidationError, refused.ErrorClass);
        Assert.False(refused.Retryable);
        Assert.Contains("payload.name", refused.Message, StringComparison.Ordinal);
        Assert.Empty(_origin.Requests);
        Assert.Empty(DownloadsEntries());
    }

    [Fact]
    public void A_300_char_name_a_bad_hash_a_bad_size_bad_url_forms_and_a_non_boolean_open_are_validation_errors_before_any_request()
    {
        var bytes = Encoding.UTF8.GetBytes("x");
        _origin.ServeBytes(RenderPath, bytes);

        var longName = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), new string('a', 300) + ".txt", bytes));
        Assert.Equal(ErrorClasses.ValidationError, longName.ErrorClass);
        Assert.Contains("payload.name", longName.Message, StringComparison.Ordinal);
        Assert.Contains("120", longName.Message, StringComparison.Ordinal);

        var okLong = new string('a', 116) + ".txt";
        Assert.Equal(120, okLong.Length);
        Assert.Equal(okLong, FileFetch.ValidateName(okLong));

        // The one transformation the sanitiser makes: surrounding whitespace is trimmed (a
        // trailing space would otherwise be dropped by Windows itself); a trailing dot is refused.
        Assert.Equal("x.txt", FileFetch.ValidateName(" x.txt "));
        Assert.Equal("Bütçe Tablosu (Eylül).xlsx", FileFetch.ValidateName("Bütçe Tablosu (Eylül).xlsx"));
        Assert.Equal("Şirket — Rapor №3 (Ağustos).pdf", FileFetch.ValidateName("Şirket — Rapor №3 (Ağustos).pdf"));
        Assert.Equal("日本語の文書.docx", FileFetch.ValidateName("日本語の文書.docx"));
        Assert.Equal("emoji 📄.txt", FileFetch.ValidateName("emoji 📄.txt"));

        // A lone surrogate is not a well-formed name; a supplementary-plane format character (a tag) is a format character.
        var lone = Assert.Throws<CapabilityException>(() => FileFetch.ValidateName("x\uD800.txt"));
        Assert.Equal(ErrorClasses.ValidationError, lone.ErrorClass);
        var tag = Assert.Throws<CapabilityException>(() => FileFetch.ValidateName("x\U000E0001.txt"));
        Assert.Contains("format characters", tag.Message, StringComparison.Ordinal);
        Assert.True(FileFetch.HasInvisibleOrDirectionalCharacter("a\u202Eb"));
        Assert.False(FileFetch.HasInvisibleOrDirectionalCharacter("Bütçe 📄.xlsx"));

        foreach (var (mutate, field) in new (Action<JsonObject>, string)[]
        {
            (p => p["sha256"] = "abc", "payload.sha256"),
            (p => p["sha256"] = new string('g', 64), "payload.sha256"),
            (p => p.Remove("sha256"), "payload.sha256"),
            (p => p["size"] = 0, "payload.size"),
            (p => p["size"] = -5, "payload.size"),
            (p => p["size"] = 1.5, "payload.size"),
            (p => p["size"] = "1", "payload.size"),
            (p => p.Remove("size"), "payload.size"),
            (p => p["url"] = "ftp://127.0.0.1/v1/artifacts/x", "payload.url"),
            (p => p["url"] = "/v1/artifacts/x", "payload.url"),
            (p => p["url"] = "http://owner:pw@127.0.0.1:9/v1/artifacts/x", "payload.url"),
            (p => p["url"] = 7, "payload.url"),
            (p => p.Remove("url"), "payload.url"),
            (p => p["open"] = "yes", "payload.open"),
            (p => p.Remove("name"), "payload.name"),
        })
        {
            var payload = Payload(RenderUrl(), "x.txt", bytes);
            mutate(payload);
            var refused = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, payload);
            Assert.Equal(ErrorClasses.ValidationError, refused.ErrorClass);
            Assert.Contains(field, refused.Message, StringComparison.Ordinal);
        }

        Assert.Empty(_origin.Requests);
        Assert.Empty(DownloadsEntries());
    }

    // ------------------------------------------------------------- (g) collisions

    [Fact]
    public void A_collision_gets_a_unique_suffix_and_both_files_are_kept_and_an_existing_entry_is_never_replaced()
    {
        var first = Encoding.UTF8.GetBytes("ilk sürüm");
        var second = Encoding.UTF8.GetBytes("ikinci sürüm");
        _origin.ServeBytes("/v1/artifacts/v1", first);
        _origin.ServeBytes("/v1/artifacts/v2", second);

        var one = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/v1", "rapor.txt", first));
        var two = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/v2", "rapor.txt", second));
        Assert.Equal(Path.Combine(_lab.Downloads, "rapor.txt"), one["path"]!.GetValue<string>(), ignoreCase: true);
        Assert.Equal(Path.Combine(_lab.Downloads, "rapor (2).txt"), two["path"]!.GetValue<string>(), ignoreCase: true);
        Assert.Equal(first, File.ReadAllBytes(one["path"]!.GetValue<string>()));
        Assert.Equal(second, File.ReadAllBytes(two["path"]!.GetValue<string>()));
        Assert.NotEqual(one["file"]!["file_id"]!.GetValue<string>(), two["file"]!["file_id"]!.GetValue<string>());
        Assert.Equal(FileFetch.ZoneIdentifierContent, File.ReadAllText(two["path"]!.GetValue<string>() + ":Zone.Identifier"));

        // A directory squatting the next name is an existing entry too: skipped, never opened.
        Directory.CreateDirectory(Path.Combine(_lab.Downloads, "rapor (3).txt"));
        var three = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/v1", "rapor.txt", first));
        Assert.Equal(Path.Combine(_lab.Downloads, "rapor (4).txt"), three["path"]!.GetValue<string>(), ignoreCase: true);
        Assert.True(Directory.Exists(Path.Combine(_lab.Downloads, "rapor (3).txt")));

        Assert.Equal(["rapor (2).txt", "rapor (3).txt", "rapor (4).txt", "rapor.txt"], DownloadsEntries().OrderBy(n => n, StringComparer.Ordinal));
    }

    // ------------------------------------------------------------- (i) a stalled origin (ADR-0085 addendum 3, High)

    /// <summary>The stalled route's Content-Length, which the payload's size must match so the headers pass.</summary>
    private static byte[] StalledBody() => new byte[RawOrigin.StallContentLength];

    [Fact]
    public void A_peer_that_sends_headers_and_ten_bytes_then_goes_quiet_is_timeout_within_the_cap_nothing_is_kept_and_the_next_fetch_works()
    {
        using var raw = new RawOrigin();
        var cap = TimeSpan.FromSeconds(3);
        using var lab = new DocumentLab(fetchOrigin: raw.Origin, fetchCap: cap);
        Assert.Equal(cap, lab.Fetch.Cap);
        Assert.Equal(DocumentCapabilityNames.CommandTimeoutCap, _lab.Fetch.Cap);

        var clock = System.Diagnostics.Stopwatch.StartNew();
        var stalled = lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(raw.Origin + RawOrigin.StallPath + Signature, "durgun.bin", StalledBody()));
        clock.Stop();

        Assert.Equal(ErrorClasses.Timeout, stalled.ErrorClass);
        Assert.True(stalled.Retryable);
        Assert.Contains("3 s cap", stalled.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was kept", stalled.Message, StringComparison.Ordinal);
        Assert.InRange(clock.Elapsed, cap - TimeSpan.FromMilliseconds(500), cap + TimeSpan.FromSeconds(5));
        Assert.Empty(Directory.GetFileSystemEntries(lab.Downloads));
        Assert.Equal(1, raw.Connections);
        Assert.Equal(10, raw.BytesSentOnStall);

        // The companion dropped the connection — the peer saw the close — and the slot is free.
        var closedAfter = Assert.Single(raw.WaitForPeerClose(1, TimeSpan.FromSeconds(5)));
        Assert.True(closedAfter < cap + TimeSpan.FromSeconds(5), $"the peer saw no close for {closedAfter}");
        Assert.Equal(0, lab.Fetch.InFlight);
        _output.WriteLine($"stalled origin, cap {cap.TotalSeconds:F0} s: timeout answered after {clock.Elapsed.TotalMilliseconds:F0} ms; the peer saw the close {closedAfter.TotalMilliseconds:F0} ms after it stalled");

        // A real render right after, from the same origin: the thread was freed.
        clock.Restart();
        var kept = lab.Exec(DocumentCapabilityNames.FileFetch, Payload(raw.Origin + RawOrigin.OkPath + Signature, "sonra.txt", RawOrigin.OkBytes));
        Assert.True(clock.Elapsed < TimeSpan.FromSeconds(5));
        Assert.True(kept["verified"]!.GetValue<bool>());
        Assert.Equal(RawOrigin.OkBytes, File.ReadAllBytes(kept["path"]!.GetValue<string>()));
        Assert.Equal(["sonra.txt"], Directory.GetFileSystemEntries(lab.Downloads).Select(e => Path.GetFileName(e)));
        Assert.Equal(2, raw.Connections);
    }

    [Fact]
    public async Task A_third_fetch_beside_two_stalled_ones_is_refused_as_busy_before_any_request_and_the_slots_come_back()
    {
        using var raw = new RawOrigin();
        var cap = TimeSpan.FromSeconds(4);
        using var lab = new DocumentLab(fetchOrigin: raw.Origin, fetchCap: cap);
        var stall = Payload(raw.Origin + RawOrigin.StallPath + Signature, "durgun.bin", StalledBody());
        Assert.Equal(2, FileFetch.MaxConcurrent);

        var first = Task.Run(() => lab.ExpectFailure(DocumentCapabilityNames.FileFetch, stall));
        var second = Task.Run(() => lab.ExpectFailure(DocumentCapabilityNames.FileFetch, stall));
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (lab.Fetch.InFlight < 2 && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(20);
        }

        Assert.Equal(2, lab.Fetch.InFlight);

        // InFlight counts the slot taken, the peer counts sockets it has ACCEPTED: on a loaded
        // runner the listener's continuation can lag the handshake, so the peer's count is read
        // only once it has seen both (CI run 34202322471 read 0 while both were connecting).
        // The fact under test is the third one below: refused with no socket of its own.
        Assert.Equal(2, raw.WaitForConnections(2, TimeSpan.FromSeconds(5)));

        var busy = lab.ExpectFailure(DocumentCapabilityNames.FileFetch, stall);
        Assert.Equal(ErrorClasses.DependencyUnavailable, busy.ErrorClass);
        Assert.True(busy.Retryable);
        Assert.Equal(FileFetch.BusyDetail, busy.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("2 downloads in flight", busy.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was requested", busy.Message, StringComparison.Ordinal);
        Assert.Equal(2, raw.Connections);

        // Both stalled fetches time out on their own cap; nothing is left; the slots are back.
        var outcomes = await Task.WhenAll(first, second).WaitAsync(TimeSpan.FromSeconds(30));
        Assert.Equal(ErrorClasses.Timeout, outcomes[0].ErrorClass);
        Assert.Equal(ErrorClasses.Timeout, outcomes[1].ErrorClass);
        var closes = raw.WaitForPeerClose(2, TimeSpan.FromSeconds(5));
        Assert.Equal(2, closes.Count);
        Assert.Equal(0, lab.Fetch.InFlight);
        _output.WriteLine($"two stalled fetches, cap {cap.TotalSeconds:F0} s: the peer saw the closes after {string.Join(" / ", closes.Select(c => c.TotalMilliseconds.ToString("F0", System.Globalization.CultureInfo.InvariantCulture)))} ms");
        Assert.Empty(Directory.GetFileSystemEntries(lab.Downloads));

        var kept = lab.Exec(DocumentCapabilityNames.FileFetch, Payload(raw.Origin + RawOrigin.OkPath + Signature, "sonra.txt", RawOrigin.OkBytes));
        Assert.True(File.Exists(kept["path"]!.GetValue<string>()));
    }

    // ------------------------------------------------------------- (j) the bytes between the hash and the name (ADR-0085 addendum 3, Medium)

    [Fact]
    public void A_file_altered_under_its_final_name_before_the_final_check_is_refused_and_removed()
    {
        var docx = PaddedDocx(170 * 1024);
        _origin.ServeBytes(RenderPath, docx);

        // Same length, one byte flipped in place — what a same-user process could do in the
        // moment between the rename and the re-check.
        string? seen = null;
        _lab.Fetch.BeforeFinalVerify = path =>
        {
            seen = path;
            using var file = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            file.Position = 100;
            file.WriteByte((byte)(docx[100] ^ 0xFF));
        };
        var flipped = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "takas.docx", docx));
        Assert.Equal(ErrorClasses.PostconditionFailed, flipped.ErrorClass);
        Assert.False(flipped.Retryable);
        Assert.Equal(FileFetch.Sha256MismatchAfterMove, flipped.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("final name", flipped.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was kept", flipped.Message, StringComparison.Ordinal);
        Assert.Equal(Path.Combine(_lab.Downloads, "takas.docx"), seen, ignoreCase: true);
        Assert.Empty(DownloadsEntries());

        // Replaced wholesale under the name.
        _lab.Fetch.BeforeFinalVerify = path =>
        {
            File.Delete(path);
            File.WriteAllBytes(path, Encoding.UTF8.GetBytes("başka bir şey"));
        };
        var replaced = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "takas.docx", docx));
        Assert.Equal(ErrorClasses.PostconditionFailed, replaced.ErrorClass);
        Assert.Equal(FileFetch.Sha256MismatchAfterMove, replaced.Detail[DocumentErrors.DetailKey]);
        Assert.Empty(DownloadsEntries());

        // Held open by something else past the retries: removed, its own detail.
        _lab.Fetch.BeforeFinalVerify = path =>
        {
            var holder = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            _ = Task.Delay(TimeSpan.FromSeconds(3)).ContinueWith(_ => holder.Dispose(), TaskScheduler.Default);
        };
        var held = _lab.ExpectFailure(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "takas.docx", docx));
        Assert.Equal(ErrorClasses.PostconditionFailed, held.ErrorClass);
        Assert.Equal(FileFetch.UnverifiableAfterMove, held.Detail[DocumentErrors.DetailKey]);
        Thread.Sleep(3500);
        Assert.Empty(DownloadsEntries());

        // The seam observing without altering changes nothing: the file is kept and reported.
        _lab.Fetch.BeforeFinalVerify = path => seen = path;
        var kept = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "takas.docx", docx));
        Assert.Equal(kept["path"]!.GetValue<string>(), seen, ignoreCase: true);
        Assert.Equal(docx, File.ReadAllBytes(kept["path"]!.GetValue<string>()));
        Assert.Equal(["takas.docx"], DownloadsEntries());
        _lab.Fetch.BeforeFinalVerify = null;
        Assert.Equal(4, _origin.RequestCount(RenderPath));
    }

    [Fact]
    public async Task The_temp_file_cannot_be_opened_by_anyone_else_while_it_streams_and_the_handle_is_held_across_the_rename()
    {
        var docx = PaddedDocx(170 * 1024);
        using var gate = new ManualResetEventSlim(false);
        _origin.ServeGated("/v1/artifacts/gated", docx, docx.Length / 2, gate);

        var fetch = Task.Run(() => _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/gated", "kapili.docx", docx)));
        string? part = null;
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (part is null && DateTime.UtcNow < deadline)
        {
            part = Directory.GetFiles(_lab.Downloads, FileFetch.TempPrefix + "*" + FileFetch.TempSuffix).FirstOrDefault();
            Thread.Sleep(20);
        }

        Assert.NotNull(part);
        // Mid-stream: no write, no read, by anyone — only the companion's own handle. A
        // co-resident process could at most delete or rename the .part (what the rename needs).
        Assert.Throws<IOException>(() => new FileStream(part, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete).Dispose());
        Assert.Throws<IOException>(() => new FileStream(part, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete).Dispose());
        Assert.False(fetch.IsCompleted);

        gate.Set();
        var result = await fetch.WaitAsync(TimeSpan.FromSeconds(30));
        Assert.Equal(docx, File.ReadAllBytes(result["path"]!.GetValue<string>()));
        Assert.Equal(["kapili.docx"], DownloadsEntries());
    }

    // ------------------------------------------------------------- (k) executables on both halves, the pinned handler

    [Fact]
    public void The_macro_enabled_office_family_and_the_launchers_are_executable_for_file_open_too()
    {
        foreach (var extension in new[] { ".docm", ".dotm", ".xlsm", ".xlsb", ".xltm", ".xlam", ".pptm", ".potm", ".ppam", ".sldm", ".url", ".jar", ".pif" })
        {
            Assert.Contains(extension, PagentOS.SessionCompanion.Operator.OperatorCapabilities.ExecutableExtensions);
        }

        foreach (var document in new[] { ".docx", ".xlsx", ".pptx", ".pdf", ".txt", ".md", ".csv", ".html" })
        {
            Assert.DoesNotContain(document, PagentOS.SessionCompanion.Operator.OperatorCapabilities.ExecutableExtensions);
        }

        using var lab = new DocumentLab(withOperator: true, fetchOrigin: _origin.Origin);
        foreach (var name in new[] { "makro.docm", "butce.xlsm", "kisayol.url", "eski.pif" })
        {
            var planted = Path.Combine(lab.Downloads, name);
            File.WriteAllBytes(planted, [0x50, 0x4B, 0x03, 0x04]);
            var refused = Assert.Throws<CapabilityException>(() => lab.Operator!.ExecuteAsync(
                OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = planted }, TimeSpan.FromSeconds(10), CancellationToken.None).GetAwaiter().GetResult());
            Assert.Equal(ErrorClasses.PermissionDenied, refused.ErrorClass);
            Assert.Contains("executable", refused.Message, StringComparison.Ordinal);
        }

        Assert.Empty(lab.Operator!.StartedPids);
    }

    [Fact]
    public void The_pinned_handler_follows_no_redirect_keeps_no_cookie_uses_no_proxy_and_decompresses_nothing()
    {
        using var handler = FileFetch.NewPinnedHandler();
        Assert.False(handler.AllowAutoRedirect);
        Assert.False(handler.UseCookies);
        Assert.False(handler.UseProxy);
        Assert.Null(handler.Proxy);
        Assert.Equal(DecompressionMethods.None, handler.AutomaticDecompression);
        using var client = FileFetch.NewPinnedClient();
        Assert.Equal(Timeout.InfiniteTimeSpan, client.Timeout);
        Assert.Empty(client.DefaultRequestHeaders);
    }

    // ------------------------------------------------------------- (h) advertisement, the service gate, the pipe

    [Fact]
    public async Task File_fetch_is_advertised_with_the_family_behind_OperatorEnabled_and_the_service_pins_its_origin_before_the_pipe()
    {
        Assert.Equal("file.fetch", DocumentCapabilityNames.FileFetch);
        // B32 appended file.trash after it and B34 the six mutations after that; file.fetch keeps its place as the seventh name.
        Assert.Equal(DocumentCapabilityNames.FileFetch, AgentCapabilities.Documents[6]);
        Assert.Equal(DocumentCapabilityNames.FileTrash, AgentCapabilities.Documents[7]);
        Assert.Equal(DocumentCapabilityNames.FileRestore, AgentCapabilities.Documents[^1]);
        Assert.True(AgentCapabilities.IsDocuments(DocumentCapabilityNames.FileFetch));
        Assert.True(AgentCapabilities.IsInteractive(DocumentCapabilityNames.FileFetch));
        Assert.False(AgentCapabilities.IsOperator(DocumentCapabilityNames.FileFetch));
        Assert.DoesNotContain(DocumentCapabilityNames.FileFetch, AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false));
        Assert.Contains(DocumentCapabilityNames.FileFetch, AgentCapabilities.Compose(browserEnabled: false, operatorEnabled: true));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(DocumentCapabilityNames.FileFetch));
        Assert.Equal(50L * 1024 * 1024, DocumentCapabilityNames.MaxFetchBytes);

        var transport = new CountingTransport();
        var bytes = Encoding.UTF8.GetBytes("x");
        var payload = Payload(RenderUrl(), "x.txt", bytes);

        var off = new InteractiveCapabilityExecutor(transport, operatorEnabled: false, brokerRestUrl: _origin.Origin);
        var missing = await Assert.ThrowsAsync<CapabilityException>(() => off.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, payload), CancellationToken.None));
        Assert.Equal(ErrorClasses.CapabilityMissing, missing.ErrorClass);

        var on = new InteractiveCapabilityExecutor(transport, operatorEnabled: true, brokerRestUrl: _origin.Origin + "/v1/devices");
        Assert.Equal(_origin.Origin, on.BrokerOrigin);
        var foreign = await Assert.ThrowsAsync<CapabilityException>(() => on.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, Payload("http://127.0.0.1:1" + RenderPath, "x.txt", bytes)), CancellationToken.None));
        Assert.Equal(ErrorClasses.PermissionDenied, foreign.ErrorClass);
        Assert.Contains("beklenen köken değil", foreign.Message, StringComparison.Ordinal);
        var malformed = await Assert.ThrowsAsync<CapabilityException>(() => on.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, new JsonObject { ["url"] = "nope" }), CancellationToken.None));
        Assert.Equal(ErrorClasses.ValidationError, malformed.ErrorClass);
        Assert.Equal(0, transport.Calls);

        var unconfigured = new InteractiveCapabilityExecutor(transport, operatorEnabled: true);
        Assert.Null(unconfigured.BrokerOrigin);
        var none = await Assert.ThrowsAsync<CapabilityException>(() => unconfigured.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, payload), CancellationToken.None));
        Assert.Equal(ErrorClasses.PermissionDenied, none.ErrorClass);
        Assert.Equal(0, transport.Calls);

        await on.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, payload, expiresIn: TimeSpan.FromMinutes(10)), CancellationToken.None);
        Assert.Equal(1, transport.Calls);
        Assert.Equal(DocumentCapabilityNames.FileFetch, transport.LastCapability);
        Assert.Equal(TimeSpan.FromSeconds(30), transport.LastTimeout);
    }

    [Fact]
    public async Task The_origin_crosses_the_pipe_in_the_challenge_and_the_whole_chain_fetches_while_an_older_service_leaves_it_unset()
    {
        var docx = PaddedDocx(170 * 1024);
        _origin.ServeBytes(RenderPath, docx);

        // A companion that was never told: nothing configured on its side says where Cloud Core is.
        using var lab = new DocumentLab(fetchOrigin: null);
        Assert.Null(lab.Documents.FetchOrigin);

        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName, brokerOrigin: _origin.Origin + "/v1/devices/connect");
        Assert.Equal(_origin.Origin, server.BrokerOrigin);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Documents);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.Equal(_origin.Origin, lab.Documents.FetchOrigin);
            Assert.Contains(DocumentCapabilityNames.FileFetch, server.CompanionCapabilities!);

            var executor = new InteractiveCapabilityExecutor(server, operatorEnabled: true, brokerRestUrl: _origin.Origin);
            var result = await executor.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "zincir.docx", docx), expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.True(result!["verified"]!.GetValue<bool>());
            Assert.Equal(docx, File.ReadAllBytes(result["path"]!.GetValue<string>()));
            Assert.Equal(["zincir.docx"], Directory.GetFiles(lab.Downloads).Select(f => Path.GetFileName(f)));

            // The typed refusals cross the pipe as themselves.
            var wrongHash = Payload(RenderUrl(), "zincir.docx", docx);
            wrongHash["sha256"] = new string('0', 64);
            var refused = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileFetch, wrongHash), CancellationToken.None));
            Assert.Equal(ErrorClasses.PostconditionFailed, refused.ErrorClass);
            Assert.Equal(["zincir.docx"], Directory.GetFiles(lab.Downloads).Select(f => Path.GetFileName(f)));
            Assert.Empty(server.Refusals);
            Assert.Empty(companion.Refusals);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }

        // An older service — no broker_origin in its challenge — leaves the companion refusing.
        using var untold = new DocumentLab(fetchOrigin: null);
        var oldPipe = IpcTestSupport.NewPipeName();
        var oldServer = IpcTestSupport.NewServer(oldPipe);
        Assert.Null(oldServer.BrokerOrigin);
        await oldServer.StartAsync(CancellationToken.None);
        using var oldCts = new CancellationTokenSource();
        var oldCompanion = NewCompanion(oldPipe, untold.Documents);
        var oldTask = Task.Run(() => oldCompanion.RunAsync(oldCts.Token));
        try
        {
            await WaitForCompanionAsync(oldServer);
            Assert.Null(untold.Documents.FetchOrigin);
            var refused = await Assert.ThrowsAsync<CapabilityException>(() => oldServer.ExecuteCapabilityAsync(
                DocumentCapabilityNames.FileFetch, Payload(RenderUrl(), "eski.docx", docx), TimeSpan.FromSeconds(10), CancellationToken.None));
            Assert.Equal(ErrorClasses.PermissionDenied, refused.ErrorClass);
            Assert.Empty(Directory.GetFileSystemEntries(untold.Downloads));
        }
        finally
        {
            oldCts.Cancel();
            try { await oldTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await oldServer.StopAsync(CancellationToken.None);
        }

        Assert.Equal(2, _origin.RequestCount(RenderPath));
    }

    [Fact]
    public void The_challenge_carries_the_origin_only_when_the_service_has_one_and_an_older_companion_ignores_it()
    {
        var with = PipeJson.Serialize(new ServiceChallenge { ConnectionId = "c", Nonce = "n", BrokerOrigin = "https://core.example:8443" });
        Assert.Equal("https://core.example:8443", JsonNode.Parse(with)!["broker_origin"]!.GetValue<string>());
        Assert.Equal("service_challenge", JsonNode.Parse(with)!["type"]!.GetValue<string>());
        var without = PipeJson.Serialize(new ServiceChallenge { ConnectionId = "c", Nonce = "n" });
        Assert.DoesNotContain("broker_origin", without, StringComparison.Ordinal);
        var parsed = Assert.IsType<ServiceChallenge>(PipeJson.Deserialize(with));
        Assert.Equal("https://core.example:8443", parsed.BrokerOrigin);
        Assert.Null(Assert.IsType<ServiceChallenge>(PipeJson.Deserialize(without)).BrokerOrigin);

        // The property normalises: a full URL becomes its origin; garbage becomes null.
        using var lab = new DocumentLab(fetchOrigin: "https://Core.Example:8443/v1/devices/connect?x=1");
        Assert.Equal("https://core.example:8443", lab.Documents.FetchOrigin);
        lab.Documents.FetchOrigin = "ws://core.example:8443";
        Assert.Null(lab.Documents.FetchOrigin);
        Assert.Equal("http://127.0.0.1:8001", HttpOrigin.Of("http://127.0.0.1:8001"));
        Assert.Equal("https://core.example", HttpOrigin.Of("https://core.example:443/x"));
        Assert.True(HttpOrigin.Same("https://Core.Example", "https://core.example"));
        Assert.False(HttpOrigin.Same("https://core.example", "https://core.example:8443"));
        Assert.False(HttpOrigin.Same(null, "https://core.example"));
    }

    // ------------------------------------------------------------- scaffolding

    private static CompanionRuntime NewCompanion(string pipeName, DocumentCapabilities documents)
        => new(
            pipeName,
            new AppLauncher(AppLauncher.DefaultAllowlist()),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new NoOpFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            documentCapabilities: documents);

    private static async Task WaitForCompanionAsync(CompanionPipeServer server)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!server.CompanionConnected)
        {
            Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
            await Task.Delay(20);
        }
    }

    private sealed class NoOpFileOpener : IFileOpener
    {
        public void Open(string fullPath)
        {
        }
    }

    private sealed class CountingTransport : ICompanionCapabilityTransport
    {
        public int Calls { get; private set; }

        public string? LastCapability { get; private set; }

        public TimeSpan LastTimeout { get; private set; }

        public Task<JsonObject?> ExecuteCapabilityAsync(string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
        {
            Calls++;
            LastCapability = capability;
            LastTimeout = timeout;
            return Task.FromResult<JsonObject?>(new JsonObject());
        }
    }
}

/// <summary>
/// The Cloud Core origin, locally: an <see cref="HttpListener"/> on <c>http://127.0.0.1:&lt;free port&gt;/</c>
/// with a route table the test fills — bytes with a Content-Length, bytes chunked, a declared
/// length whose body is never meant to be read, an endless chunked body (its writes counted
/// until the client's abort makes one fail), a redirect, a bare status. Every request is
/// recorded with its path, its query and whether it carried an <c>Authorization</c> header or a
/// cookie, so a test can assert what the companion sent and what it never sent.
/// </summary>
internal sealed class LocalOrigin : IDisposable
{
    public sealed record SeenRequest(string Path, string Query, string? Authorization, bool HasCookie);

    private readonly HttpListener _listener = new();
    private readonly ConcurrentDictionary<string, Action<HttpListenerContext>> _routes = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, long> _servedByPath = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, int> _activeByPath = new(StringComparer.Ordinal);
    private readonly List<SeenRequest> _requests = new();
    private readonly Task _loop;
    private long _bytesServed;

    public LocalOrigin()
    {
        var port = FreePort();
        Origin = $"http://127.0.0.1:{port}";
        _listener.Prefixes.Add(Origin + "/");
        _listener.Start();
        _loop = Task.Run(LoopAsync);
    }

    public string Origin { get; }

    public long BytesServed => Interlocked.Read(ref _bytesServed);

    public IReadOnlyList<SeenRequest> Requests
    {
        get
        {
            lock (_requests)
            {
                return [.. _requests];
            }
        }
    }

    public int RequestCount(string path) => Requests.Count(r => r.Path == path);

    public void ServeBytes(string path, byte[] bytes, bool chunked = false)
        => _routes[path] = ctx =>
        {
            if (chunked)
            {
                ctx.Response.SendChunked = true;
            }
            else
            {
                ctx.Response.ContentLength64 = bytes.Length;
            }

            ctx.Response.ContentType = "application/octet-stream";
            ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
            Count(path, bytes.Length);
        };

    /// <summary>A Content-Length claim; the body (zeros) is written only as far as the client keeps the connection.</summary>
    public void ServeDeclared(string path, long declaredLength)
        => _routes[path] = ctx =>
        {
            ctx.Response.ContentLength64 = declaredLength;
            WriteUntilAborted(ctx, path, declaredLength);
        };

    /// <summary>Chunked, no Content-Length, up to <paramref name="total"/> bytes — or until the client's abort makes a write fail.</summary>
    public void ServeEndless(string path, long total)
        => _routes[path] = ctx =>
        {
            ctx.Response.SendChunked = true;
            WriteUntilAborted(ctx, path, total);
        };

    public void ServeRedirect(string path, string location)
        => _routes[path] = ctx =>
        {
            ctx.Response.StatusCode = 302;
            ctx.Response.RedirectLocation = location;
        };

    public void ServeStatus(string path, int status)
        => _routes[path] = ctx => ctx.Response.StatusCode = status;

    /// <summary>A Content-Length body sent in two halves: the first <paramref name="firstHalf"/> bytes, then a wait on <paramref name="gate"/>, then the rest.</summary>
    public void ServeGated(string path, byte[] bytes, int firstHalf, ManualResetEventSlim gate)
        => _routes[path] = ctx =>
        {
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.ContentType = "application/octet-stream";
            ctx.Response.OutputStream.Write(bytes, 0, firstHalf);
            ctx.Response.OutputStream.Flush();
            gate.Wait(TimeSpan.FromSeconds(30));
            ctx.Response.OutputStream.Write(bytes, firstHalf, bytes.Length - firstHalf);
            Count(path, bytes.Length);
        };

    /// <summary>Bytes handed to the connection for <paramref name="path"/>, once its handler has stopped (the client aborted, or it finished).</summary>
    public long WaitForServingToStop(string path, TimeSpan wait)
    {
        var deadline = DateTime.UtcNow + wait;
        while (_activeByPath.TryGetValue(path, out var active) && active > 0 && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(50);
        }

        return _servedByPath.TryGetValue(path, out var served) ? served : 0;
    }

    private void WriteUntilAborted(HttpListenerContext ctx, string path, long total)
    {
        var chunk = new byte[1024 * 1024];
        long written = 0;
        while (written < total)
        {
            var count = (int)Math.Min(chunk.Length, total - written);
            try
            {
                ctx.Response.OutputStream.Write(chunk, 0, count);
                ctx.Response.OutputStream.Flush();
            }
            catch (Exception)
            {
                // The client is gone: exactly what the bound is supposed to cause.
                return;
            }

            written += count;
            Count(path, count);
        }
    }

    private void Count(string path, long bytes)
    {
        Interlocked.Add(ref _bytesServed, bytes);
        _servedByPath.AddOrUpdate(path, bytes, (_, sum) => sum + bytes);
    }

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try
            {
                ctx = await _listener.GetContextAsync();
            }
            catch (Exception)
            {
                return;
            }

            _ = Task.Run(() => Handle(ctx));
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        var path = ctx.Request.Url!.AbsolutePath;
        lock (_requests)
        {
            _requests.Add(new SeenRequest(path, ctx.Request.Url.Query, ctx.Request.Headers["Authorization"], ctx.Request.Headers["Cookie"] is not null));
        }

        _activeByPath.AddOrUpdate(path, 1, (_, n) => n + 1);
        try
        {
            if (_routes.TryGetValue(path, out var handler))
            {
                handler(ctx);
            }
            else
            {
                ctx.Response.StatusCode = 404;
            }
        }
        catch (Exception)
        {
            // A client that went away mid-response; the handler's own counting says how far it got.
        }
        finally
        {
            try
            {
                ctx.Response.Close();
            }
            catch (Exception)
            {
            }

            _activeByPath.AddOrUpdate(path, 0, (_, n) => n - 1);
        }
    }

    private static int FreePort()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        return ((IPEndPoint)probe.LocalEndpoint).Port;
    }

    public void Dispose()
    {
        try
        {
            _listener.Stop();
            _listener.Close();
        }
        catch (Exception)
        {
        }

        try
        {
            _loop.Wait(TimeSpan.FromSeconds(2));
        }
        catch (Exception)
        {
        }
    }
}

/// <summary>
/// A peer with no HTTP stack at all (ADR-0085 addendum 3, the High finding): a
/// <c>TcpListener</c> on 127.0.0.1 that answers <see cref="StallPath"/> with
/// <c>HTTP/1.1 200</c>, <c>Content-Length: 1000000</c> and ten bytes, then says nothing more
/// until the client closes — the connection a synchronous read would sit in for as long as
/// the peer liked. It records when the peer (the companion) closed, measured from the stall.
/// <see cref="OkPath"/> answers a small complete body so the same origin can prove the next
/// fetch works.
/// </summary>
internal sealed class RawOrigin : IDisposable
{
    public const string StallPath = "/v1/artifacts/stall";
    public const string OkPath = "/v1/artifacts/ok";
    public const int StallContentLength = 1_000_000;

    public static readonly byte[] OkBytes = Encoding.UTF8.GetBytes("tamamlandı — the next fetch after a stalled one\r\n");

    private readonly TcpListener _listener;
    private readonly CancellationTokenSource _cts = new();
    private readonly Task _loop;
    private readonly ConcurrentQueue<TimeSpan> _peerClosedAfter = new();
    private int _connections;
    private int _bytesSentOnStall;

    public RawOrigin()
    {
        _listener = new TcpListener(IPAddress.Loopback, 0);
        _listener.Start();
        Origin = $"http://127.0.0.1:{((IPEndPoint)_listener.LocalEndpoint).Port}";
        _loop = Task.Run(LoopAsync);
    }

    public string Origin { get; }

    public int Connections => Volatile.Read(ref _connections);

    public int BytesSentOnStall => Volatile.Read(ref _bytesSentOnStall);

    /// <summary>The accepted-socket count once it has reached <paramref name="count"/>, or whatever it is at the deadline.</summary>
    public int WaitForConnections(int count, TimeSpan wait)
    {
        var deadline = DateTime.UtcNow + wait;
        while (Connections < count && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(20);
        }

        return Connections;
    }

    /// <summary>How long after each stall began the peer closed the connection, once at least <paramref name="count"/> have.</summary>
    public IReadOnlyList<TimeSpan> WaitForPeerClose(int count, TimeSpan wait)
    {
        var deadline = DateTime.UtcNow + wait;
        while (_peerClosedAfter.Count < count && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(20);
        }

        return [.. _peerClosedAfter];
    }

    private async Task LoopAsync()
    {
        while (!_cts.IsCancellationRequested)
        {
            TcpClient client;
            try
            {
                client = await _listener.AcceptTcpClientAsync(_cts.Token);
            }
            catch (Exception)
            {
                return;
            }

            Interlocked.Increment(ref _connections);
            _ = Task.Run(() => HandleAsync(client));
        }
    }

    private async Task HandleAsync(TcpClient client)
    {
        using (client)
        {
            try
            {
                var stream = client.GetStream();
                var head = await ReadHeadAsync(stream);
                var path = head.Split(' ', 3) is [_, var target, ..] ? target.Split('?', 2)[0] : "";
                if (path == StallPath)
                {
                    var headers = Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {StallContentLength}\r\n\r\n");
                    await stream.WriteAsync(headers, _cts.Token);
                    var ten = new byte[10];
                    await stream.WriteAsync(ten, _cts.Token);
                    await stream.FlushAsync(_cts.Token);
                    Interlocked.Add(ref _bytesSentOnStall, ten.Length);

                    // Silence. The only thing that ends this is the peer closing (a read
                    // returning 0 or failing) or the origin being disposed.
                    var since = System.Diagnostics.Stopwatch.StartNew();
                    var sink = new byte[64];
                    try
                    {
                        while (await stream.ReadAsync(sink, _cts.Token) > 0)
                        {
                        }
                    }
                    catch (Exception) when (!_cts.IsCancellationRequested)
                    {
                    }

                    if (!_cts.IsCancellationRequested)
                    {
                        _peerClosedAfter.Enqueue(since.Elapsed);
                    }
                }
                else if (path == OkPath)
                {
                    var headers = Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {OkBytes.Length}\r\nConnection: close\r\n\r\n");
                    await stream.WriteAsync(headers, _cts.Token);
                    await stream.WriteAsync(OkBytes, _cts.Token);
                    await stream.FlushAsync(_cts.Token);
                }
                else
                {
                    await stream.WriteAsync(Encoding.ASCII.GetBytes("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"), _cts.Token);
                }
            }
            catch (Exception)
            {
                // The peer went away or the origin is being disposed.
            }
        }
    }

    private async Task<string> ReadHeadAsync(NetworkStream stream)
    {
        var buffer = new byte[8192];
        var total = 0;
        while (total < buffer.Length)
        {
            var read = await stream.ReadAsync(buffer.AsMemory(total), _cts.Token);
            if (read == 0)
            {
                break;
            }

            total += read;
            var text = Encoding.ASCII.GetString(buffer, 0, total);
            if (text.Contains("\r\n\r\n", StringComparison.Ordinal))
            {
                return text;
            }
        }

        return Encoding.ASCII.GetString(buffer, 0, total);
    }

    public void Dispose()
    {
        _cts.Cancel();
        try
        {
            _listener.Stop();
        }
        catch (Exception)
        {
        }

        try
        {
            _loop.Wait(TimeSpan.FromSeconds(2));
        }
        catch (Exception)
        {
        }

        _cts.Dispose();
    }
}

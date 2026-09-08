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

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// The M22 device lab (M22_ARTIFACT_FACTORY_SPEC.md §4/§6, ADR-0085 decision 4,
/// DEVICE_PROTOCOL.md §6k): a local <see cref="HttpListener"/> on 127.0.0.1 stands in for the
/// Cloud Core origin the device dialled, the lab pins the real <see cref="DocumentCapabilities"/>
/// to it the way the pipe challenge would, and every refusal is proven with nothing left
/// behind in the run's Downloads root: a foreign origin, a wrong hash, a Content-Length over
/// the bound, a chunked body that keeps sending past it (the abort is counted on the server),
/// a redirect off-origin, a name that is not a plain file name, a collision. The happy path
/// keeps a 200 KB DOCX, verified, and — on a host with a desktop — opens a .txt variant in
/// Notepad through the operator's real <c>file.open</c> and observes its window. Nothing here
/// touches the owner's real Downloads folder: the lab's root is
/// <c>%TEMP%\pagentos-operator-fixture\documents\&lt;run-id&gt;\Downloads</c>.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class FileFetchTests : IDisposable
{
    private const string RenderPath = "/v1/artifacts/a-1/renders/r-1/download";
    private const string Signature = "?sig=owner-session-signed&exp=1900000000";

    private readonly LocalOrigin _origin = new();
    private readonly DocumentLab _lab;

    public FileFetchTests()
    {
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

        // A directory squatting the next name is an existing entry too: skipped, never opened.
        Directory.CreateDirectory(Path.Combine(_lab.Downloads, "rapor (3).txt"));
        var three = _lab.Exec(DocumentCapabilityNames.FileFetch, Payload(_origin.Origin + "/v1/artifacts/v1", "rapor.txt", first));
        Assert.Equal(Path.Combine(_lab.Downloads, "rapor (4).txt"), three["path"]!.GetValue<string>(), ignoreCase: true);
        Assert.True(Directory.Exists(Path.Combine(_lab.Downloads, "rapor (3).txt")));

        Assert.Equal(["rapor (2).txt", "rapor (3).txt", "rapor (4).txt", "rapor.txt"], DownloadsEntries().OrderBy(n => n, StringComparer.Ordinal));
    }

    // ------------------------------------------------------------- (h) advertisement, the service gate, the pipe

    [Fact]
    public async Task File_fetch_is_advertised_with_the_family_behind_OperatorEnabled_and_the_service_pins_its_origin_before_the_pipe()
    {
        Assert.Equal("file.fetch", DocumentCapabilityNames.FileFetch);
        Assert.Equal(DocumentCapabilityNames.FileFetch, AgentCapabilities.Documents[^1]);
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

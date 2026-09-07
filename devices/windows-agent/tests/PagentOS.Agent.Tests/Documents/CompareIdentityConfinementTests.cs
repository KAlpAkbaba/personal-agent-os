using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// <c>file.compare</c> against <c>truth.json.comparisons</c>; the two identities (a stable
/// location id, a content id that follows the bytes); confinement (outside the roots, through
/// a junction, a secret-bearing name); and the bounds (a read over 65 536 chars is truncated
/// and says so; a 60 MiB file is refused by its size, unopened — proven by an extractor that
/// counts its calls).
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class CompareIdentityConfinementTests : IDisposable
{
    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Fact]
    public void Compare_of_the_two_contracts_matches_the_truth()
    {
        var comparison = (JsonObject)DocumentLab.Truth()["comparisons"]!.AsArray().Single()!;
        var result = _lab.Exec(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = _lab.PathOf(comparison["a"]!.GetValue<string>()) },
            ["b"] = new JsonObject { ["path"] = _lab.PathOf(comparison["b"]!.GetValue<string>()) },
        });

        Assert.False(result["same_content"]!.GetValue<bool>());
        Assert.Equal("docx", result["kind"]!.GetValue<string>());
        Assert.Equal(
            comparison["changed_refs"]!.AsArray().Select(r => r!.GetValue<string>()),
            result["changed_refs"]!.AsArray().Select(r => r!.GetValue<string>()));
        Assert.Equal(
            comparison["unchanged_refs"]!.AsArray().Select(r => r!.GetValue<string>()),
            result["unchanged_refs"]!.AsArray().Select(r => r!.GetValue<string>()));
        Assert.Empty(result["added_refs"]!.AsArray());
        Assert.Empty(result["removed_refs"]!.AsArray());
        Assert.Equal(1, result["size_delta"]!.GetValue<long>());
        Assert.False(result["truncated"]!.GetValue<bool>());
        Assert.Contains("changed=2", result["summary"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal("Hizmet Sözleşmesi", comparison["same_title"]!.GetValue<string>());
        Assert.NotEqual(result["a"]!["file_id"]!.GetValue<string>(), result["b"]!["file_id"]!.GetValue<string>());

        // The changed clause is quotable from both extracts under the same ref.
        var clause = comparison["changed_clause"]!;
        var reference = clause["ref"]!.GetValue<string>();
        var a = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf(comparison["a"]!.GetValue<string>()) });
        var b = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf(comparison["b"]!.GetValue<string>()) });
        Assert.Contains(clause["a_contains"]!.GetValue<string>(), BlockText(a, reference), StringComparison.Ordinal);
        Assert.Contains(clause["b_contains"]!.GetValue<string>(), BlockText(b, reference), StringComparison.Ordinal);
    }

    [Fact]
    public void Compare_of_text_like_files_is_line_by_line_and_of_a_file_with_itself_is_same_content()
    {
        var original = _lab.PathOf("notlar.md");
        var edited = Path.Combine(_lab.Root, "notlar-2.md");
        var lines = File.ReadAllLines(original, Encoding.UTF8).ToList();
        lines[2] = "Tarih: 9 Eylül 2026. Katılımcılar: Alp, sistem.";
        lines.Add("Ek satır.");
        File.WriteAllText(edited, string.Join("\n", lines) + "\n", new UTF8Encoding(false));

        var result = _lab.Exec(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = original },
            ["b"] = new JsonObject { ["path"] = edited },
        });
        Assert.False(result["same_content"]!.GetValue<bool>());
        Assert.Equal("md", result["kind"]!.GetValue<string>());
        Assert.Equal(["L3"], result["changed_refs"]!.AsArray().Select(r => r!.GetValue<string>()));
        Assert.Equal(["L18"], result["added_refs"]!.AsArray().Select(r => r!.GetValue<string>()));
        Assert.Equal(16, result["unchanged_refs"]!.AsArray().Count);

        var same = _lab.Exec(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = original },
            ["b"] = new JsonObject { ["path"] = original },
        });
        Assert.True(same["same_content"]!.GetValue<bool>());
        Assert.Empty(same["changed_refs"]!.AsArray());
        Assert.Equal(0, same["size_delta"]!.GetValue<long>());

        var mixed = _lab.Exec(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = original },
            ["b"] = new JsonObject { ["path"] = _lab.PathOf("rapor.pdf") },
        });
        Assert.Equal("md/pdf", mixed["kind"]!.GetValue<string>());
        Assert.Empty(mixed["changed_refs"]!.AsArray());
        Assert.False(mixed["same_content"]!.GetValue<bool>());

        var missing = _lab.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject { ["a"] = new JsonObject { ["path"] = original } });
        Assert.Equal(ErrorClasses.ValidationError, missing.ErrorClass);
    }

    [Fact]
    public void File_id_is_stable_across_calls_and_differs_between_the_same_named_contracts_and_doc_id_follows_the_bytes()
    {
        var first = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx") });
        var second = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx") });
        var other = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2026/sozlesme.docx") });
        var id = first["file"]!["file_id"]!.GetValue<string>();
        Assert.Equal(id, second["file"]!["file_id"]!.GetValue<string>());
        Assert.NotEqual(id, other["file"]!["file_id"]!.GetValue<string>());
        Assert.Matches("^file:[0-9a-f]{32}$", id);
        Assert.Equal(first["file"]!["name"]!.GetValue<string>(), other["file"]!["name"]!.GetValue<string>());

        // Spelling the same file two ways (case, forward slashes) gives one id.
        var spelled = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx").ToUpperInvariant().Replace('\\', '/') });
        Assert.Equal(id, spelled["file"]!["file_id"]!.GetValue<string>());
        Assert.Equal(_lab.PathOf("sozlesmeler/2025/sozlesme.docx"), spelled["file"]!["path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);

        // A byte-identical copy at another path: another file_id, the same doc_id and sha256.
        var copyDir = Path.Combine(_lab.Root, "kopya");
        Directory.CreateDirectory(copyDir);
        var copy = Path.Combine(copyDir, "notlar.md");
        File.Copy(_lab.PathOf("notlar.md"), copy);
        var original = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("notlar.md") });
        var copied = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = copy });
        Assert.Equal(original["doc_id"]!.GetValue<string>(), copied["doc_id"]!.GetValue<string>());
        Assert.Equal(original["file"]!["sha256"]!.GetValue<string>(), copied["file"]!["sha256"]!.GetValue<string>());
        Assert.NotEqual(original["file"]!["file_id"]!.GetValue<string>(), copied["file"]!["file_id"]!.GetValue<string>());
        Assert.Matches("^doc:[0-9a-f]{64}$", original["doc_id"]!.GetValue<string>());

        // The id resolves again through the memo, and an id never issued is not_found.
        var byId = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = id });
        Assert.Equal(_lab.PathOf("sozlesmeler/2025/sozlesme.docx"), byId["file"]!["path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
        var unknown = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = "file:00000000000000000000000000000000" });
        Assert.Equal(ErrorClasses.NotFound, unknown.ErrorClass);
        Assert.False(unknown.Retryable);
        var malformed = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = "w-1-1" });
        Assert.Equal(ErrorClasses.ValidationError, malformed.ErrorClass);

        // An id whose file has gone is not_found too, and the memo forgets it.
        File.Delete(copy);
        var gone = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = copied["file"]!["file_id"]!.GetValue<string>() });
        Assert.Equal(ErrorClasses.NotFound, gone.ErrorClass);
    }

    [Fact]
    public void Paths_outside_the_roots_through_a_junction_or_secret_bearing_are_refused_before_any_read()
    {
        var outsidePath = @"C:\Windows\System32\drivers\etc\hosts";
        foreach (var capability in new[] { DocumentCapabilityNames.FileLocate, DocumentCapabilityNames.FileInspect, DocumentCapabilityNames.FileRead, DocumentCapabilityNames.DocumentExtract })
        {
            var outside = _lab.ExpectFailure(capability, new JsonObject { ["path"] = outsidePath });
            Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);
            Assert.False(outside.Retryable);
            Assert.DoesNotContain("hosts", outside.Message, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("System32", outside.Message, StringComparison.OrdinalIgnoreCase);
        }

        var outsideDir = Path.Combine(Path.GetTempPath(), "pagentos-doc-outside-" + _lab.RunId);
        Directory.CreateDirectory(outsideDir);
        var secret = Path.Combine(outsideDir, "gizli.txt");
        File.WriteAllText(secret, "outside the root\n");
        var junction = Path.Combine(_lab.Root, "atlama");
        JunctionFixture.CreateJunction(junction, outsideDir);
        try
        {
            var through = Path.Combine(junction, "gizli.txt");
            Assert.StartsWith(_lab.Root, through, StringComparison.OrdinalIgnoreCase);
            var read = _lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = through });
            Assert.Equal(ErrorClasses.PermissionDenied, read.ErrorClass);
            Assert.DoesNotContain("gizli", read.Message, StringComparison.OrdinalIgnoreCase);
            var extract = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = through });
            Assert.Equal(ErrorClasses.PermissionDenied, extract.ErrorClass);
        }
        finally
        {
            Directory.Delete(junction);
            Directory.Delete(outsideDir, recursive: true);
        }

        foreach (var name in new[] { ".env", ".env.local", "sunucu.pem", "anahtar.key", "sertifika.pfx", "kasa.p12", "id_rsa", "id_ed25519.pub", "parolalar.kdbx", "secrets.json" })
        {
            var path = Path.Combine(_lab.Root, name);
            File.WriteAllText(path, "not to be read\n");
            var ex = _lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path });
            Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
            Assert.Equal(SecretNames.Detail, ex.Detail[DocumentErrors.DetailKey]);
            Assert.Contains(SecretNames.Detail, ex.Message, StringComparison.Ordinal);
            var located = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = path });
            Assert.Equal(ErrorClasses.PermissionDenied, located.ErrorClass);
        }

        Assert.False(SecretNames.IsSecretBearing("notlar.md"));
        Assert.False(SecretNames.IsSecretBearing("environment.txt"));
        Assert.False(SecretNames.IsSecretBearing("keynote.pptx"));

        // Inside the roots but not there: not_found, so the planner searches again;
        // a directory, or a relative spelling: validation_error.
        var missing = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = Path.Combine(_lab.Root, "yok.md") });
        Assert.Equal(ErrorClasses.NotFound, missing.ErrorClass);
        var directory = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = Path.Combine(_lab.Root, "sozlesmeler") });
        Assert.Equal(ErrorClasses.ValidationError, directory.ErrorClass);
        var relative = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = "notlar.md" });
        Assert.Equal(ErrorClasses.ValidationError, relative.ErrorClass);
        var nothing = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject());
        Assert.Equal(ErrorClasses.ValidationError, nothing.ErrorClass);
        Assert.Equal(0, _lab.Log.Lines.Count(l => l.Contains("outcome=ok", StringComparison.Ordinal)));
    }

    [Fact]
    public void The_default_roots_admit_the_fixture_root_and_nothing_under_the_profile()
    {
        using var defaults = new DocumentLab(roots: OperatorOptions.DefaultRoots());
        var located = defaults.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = defaults.PathOf("veri.csv") });
        Assert.True(AuthorisedRoots.IsWithin(located["file"]!["path"]!.GetValue<string>(), AuthorisedRoots.ResolveFinal(OperatorOptions.FixtureRoot)!));

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var refused = defaults.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = Path.Combine(profile, "NTUSER.DAT") });
        Assert.Equal(ErrorClasses.PermissionDenied, refused.ErrorClass);
    }

    [Fact]
    public void A_300_KB_text_read_is_truncated_at_65536_chars_and_says_so()
    {
        var big = Path.Combine(_lab.Root, "buyuk.txt");
        var line = "satır metni ğüşöçı 0123456789\n";
        var builder = new StringBuilder();
        while (builder.Length < 300_000)
        {
            builder.Append(line);
        }

        File.WriteAllText(big, builder.ToString(), new UTF8Encoding(false));
        Assert.True(new FileInfo(big).Length > 300 * 1024);

        var read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = big });
        Assert.Equal(DocumentCapabilityNames.MaxReadChars, read["text"]!.GetValue<string>().Length);
        Assert.True(read["truncated"]!.GetValue<bool>());
        Assert.Equal(builder.Length, read["total_chars"]!.GetValue<int>());
        // Under 8 MiB, so the record still carries the content hash.
        Assert.NotNull(read["file"]!["sha256"]);
        Assert.Equal(builder.ToString(0, DocumentCapabilityNames.MaxReadChars), read["text"]!.GetValue<string>());

        var tail = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = big, ["offset"] = builder.Length - 5 });
        Assert.Equal(builder.ToString(builder.Length - 5, 5), tail["text"]!.GetValue<string>());
        Assert.False(tail["truncated"]!.GetValue<bool>());

        var extracted = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = big });
        Assert.True(extracted["truncated"]!.GetValue<bool>());
        Assert.True(extracted["blocks"]!.AsArray().Sum(b => b!["text"]!.GetValue<string>().Length) <= DocumentCapabilityNames.MaxExtractChars);
    }

    [Fact]
    public void A_60_MiB_file_is_refused_by_its_size_without_being_opened()
    {
        var counting = new CountingExtractor();
        using var lab = new DocumentLab(extractors: [counting]);
        var huge = Path.Combine(lab.Root, "dev.txt");
        using (var stream = File.Create(huge))
        {
            stream.SetLength(60L * 1024 * 1024);
        }

        var extract = lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = huge });
        Assert.Equal(ErrorClasses.UnsupportedFormat, extract.ErrorClass);
        Assert.Equal(DocumentErrors.TooLarge, extract.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("50 MiB", extract.Message, StringComparison.Ordinal);
        Assert.Equal(0, counting.ExtractCalls);

        var read = lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = huge });
        Assert.Equal(ErrorClasses.UnsupportedFormat, read.ErrorClass);
        Assert.Equal(DocumentErrors.TooLarge, read.Detail[DocumentErrors.DetailKey]);

        // The record is still honest about it, and inspect says so without opening it.
        var inspected = lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = huge });
        Assert.Equal(60L * 1024 * 1024, inspected["file"]!["size"]!.GetValue<long>());
        Assert.True(inspected["too_large"]!.GetValue<bool>());
        Assert.Null(inspected["file"]!["sha256"]);
        Assert.Equal(0, counting.InspectCalls);

        var compare = lab.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = huge },
            ["b"] = new JsonObject { ["path"] = lab.PathOf("notlar.md") },
        });
        Assert.Equal(ErrorClasses.UnsupportedFormat, compare.ErrorClass);
        Assert.Equal(0, counting.ExtractCalls);
    }

    private static string BlockText(JsonObject extract, string reference)
        => extract["blocks"]!.AsArray().Select(b => (JsonObject)b!).First(b => b["ref"]!.GetValue<string>() == reference)["text"]!.GetValue<string>();

    private sealed class CountingExtractor : IDocumentExtractor
    {
        public int ExtractCalls { get; private set; }

        public int InspectCalls { get; private set; }

        public bool Supports(string kind) => true;

        public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
        {
            InspectCalls++;
            return new JsonObject();
        }

        public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
        {
            ExtractCalls++;
            return new ExtractResult(null, [], new JsonObject(), false);
        }
    }
}

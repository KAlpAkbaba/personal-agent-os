using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// B03 requirement 3/5: what the Cloud Core puts in <c>payload.roots</c>, read by THIS
/// device's real parser.
/// </summary>
/// <remarks>
/// The Cloud Core sent <c>roots: ["documents"]</c> and this device answered
/// <c>payload.roots must be absolute paths</c>. The device was right to refuse a shape it did
/// not admit and the Cloud Core was right that only the device can name the owner's Documents
/// folder - what was missing was a contract, and a test that reads it from both sides. The
/// defect sink filed the refusal on 2026-09-09 (ADR-0102); folder search stayed broken with
/// green suites on both halves until 2026-09-12.
///
/// So <c>packages/protocol/file-search-roots.json</c> names the buckets, the Cloud Core sends
/// only those, and these tests hold this device's table equal to that file and drive the real
/// capability with the shapes it promises.
/// </remarks>
[Collection(DocumentLabCollection.Name)]
public sealed class FileSearchRootsContractTests
{
    private static JsonObject Contract()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", "file-search-roots.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "protocol", "file-search-roots.json");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(path))!;
    }

    [Fact]
    public void The_buckets_this_device_resolves_are_exactly_the_ones_the_contract_names()
    {
        var declared = ((JsonArray)Contract()["buckets"]!)
            .Select(entry => ((JsonObject)entry!)["name"]!.GetValue<string>())
            .ToArray();

        Assert.Equal(declared, WellKnownFolders.Names);
    }

    [Fact]
    public void Every_bucket_the_contract_names_resolves_to_a_real_folder_on_this_machine()
    {
        foreach (var bucket in WellKnownFolders.Names)
        {
            // A CI runner has all six; a machine that has genuinely lost one answers null and
            // the capability refuses it, which is the point of resolving rather than assuming.
            var resolved = WellKnownFolders.Resolve(bucket);
            Assert.False(string.IsNullOrWhiteSpace(resolved), bucket);
        }
    }

    [Fact]
    public void A_bucket_name_is_searched_the_way_an_absolute_path_would_be()
    {
        // The lab's fixture root is inside the default roots; searching by BUCKET must reach
        // the same files as searching by the absolute path, or the contract is decorative.
        using var lab = new DocumentLab(roots: OperatorOptions.DefaultRoots());
        var documents = WellKnownFolders.Resolve("documents");
        Assert.False(string.IsNullOrWhiteSpace(documents));

        var byBucket = lab.Exec(
            DocumentCapabilityNames.FileSearch,
            new JsonObject { ["pattern"] = "*", ["roots"] = new JsonArray("documents"), ["max"] = 1 });

        Assert.NotNull(byBucket["searched_roots"]);
        var searched = ((JsonArray)byBucket["searched_roots"]!)
            .Select(node => node!.GetValue<string>())
            .ToArray();
        Assert.Single(searched);
        Assert.Equal(
            Path.GetFullPath(documents!).TrimEnd(Path.DirectorySeparatorChar),
            searched[0].TrimEnd(Path.DirectorySeparatorChar),
            ignoreCase: true);
    }

    [Fact]
    public void A_bucket_with_relative_segments_resolves_under_that_bucket()
    {
        var documents = WellKnownFolders.Resolve("documents")!;
        var expected = Path.Combine(documents, "PagentOS Projects");
        Assert.Equal(expected, WellKnownFolders.ResolveEntry("documents/PagentOS Projects"));
        Assert.Equal(expected, WellKnownFolders.ResolveEntry(@"Documents\PagentOS Projects"));
    }

    [Fact]
    public void A_relative_folder_that_is_not_a_bucket_is_refused_and_the_message_teaches()
    {
        using var lab = new DocumentLab(roots: OperatorOptions.DefaultRoots());

        var failure = lab.ExpectFailure(
            DocumentCapabilityNames.FileSearch,
            new JsonObject { ["pattern"] = "*", ["roots"] = new JsonArray("Faturalar") });

        Assert.Contains("absolute path", failure.Message, StringComparison.Ordinal);
        foreach (var bucket in WellKnownFolders.Names)
        {
            Assert.Contains(bucket, failure.Message, StringComparison.Ordinal);
        }
    }

    [Fact]
    public void A_bucket_entry_cannot_climb_out_with_dot_dot()
    {
        Assert.Null(WellKnownFolders.ResolveEntry("documents/../../Windows"));
        Assert.Null(WellKnownFolders.ResolveEntry(".."));
    }

    [Fact]
    public void A_bucket_outside_the_owners_authorised_roots_is_still_refused()
    {
        // The narrowed root list an owner can configure: only the lab's own folder. A bucket
        // is resolved, then confined - so it is refused here exactly as an absolute path to
        // the same folder would be. A bucket must never be a way around the root list.
        using var lab = new DocumentLab();

        var failure = lab.ExpectFailure(
            DocumentCapabilityNames.FileSearch,
            new JsonObject { ["pattern"] = "*", ["roots"] = new JsonArray("pictures") });

        Assert.Contains("authorised roots", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void An_absolute_path_still_works_exactly_as_before()
    {
        using var lab = new DocumentLab();

        var result = lab.Exec(
            DocumentCapabilityNames.FileSearch,
            new JsonObject { ["pattern"] = "*", ["roots"] = new JsonArray(lab.Root), ["max"] = 1 });

        Assert.NotNull(result["files"]);
    }

    [Fact]
    public void The_contract_documents_the_shape_rather_than_only_listing_names()
    {
        var contract = Contract();
        var forms = ((JsonArray)contract["roots_field"]!["entry_forms"]!)
            .Select(node => node!.GetValue<string>())
            .ToArray();
        Assert.Equal(3, forms.Length);
        Assert.Contains(forms, form => form.Contains("absolute path", StringComparison.Ordinal));
        Assert.Contains(forms, form => form.Contains("bucket name", StringComparison.Ordinal));
        Assert.NotNull(contract["roots_field"]!["absent_or_empty"]);
    }
}

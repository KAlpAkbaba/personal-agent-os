using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>Test double: records the paths it was asked to open; never launches anything.</summary>
public sealed class RecordingFileOpener : IFileOpener
{
    private readonly List<string> _opened = new();

    public IReadOnlyList<string> Opened => _opened;

    public void Open(string fullPath) => _opened.Add(fullPath);
}

public sealed class ArtifactOpenerTests : IDisposable
{
    private readonly string _root;
    private readonly RecordingFileOpener _opener = new();

    public ArtifactOpenerTests()
    {
        _root = Path.Combine(Path.GetTempPath(), "pagentos-artifact-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_root);
    }

    public void Dispose()
    {
        try
        {
            Directory.Delete(_root, recursive: true);
        }
        catch (Exception)
        {
            // Best-effort cleanup.
        }
    }

    private ArtifactOpener NewOpener(AuditLog? audit = null)
        => new(new[] { _root }, _opener, allowedExtensions: null, audit: audit);

    private string CreateArtifact(string name, string content = "data")
    {
        var path = Path.Combine(_root, name);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content);
        return path;
    }

    private static JsonObject Payload(string? path)
    {
        var obj = new JsonObject();
        if (path is not null)
        {
            obj["path"] = path;
        }

        return obj;
    }

    [Fact]
    public void File_under_root_with_allowed_extension_is_opened()
    {
        var artifact = CreateArtifact("report.pdf");

        var result = NewOpener().Open(Payload(artifact));

        Assert.True(result["opened"]!.GetValue<bool>());
        Assert.Equal("shell-associated", result["handler"]!.GetValue<string>());
        Assert.Equal(Path.GetFullPath(artifact), result["path"]!.GetValue<string>());
        Assert.Equal(new[] { Path.GetFullPath(artifact) }, _opener.Opened);
    }

    [Theory]
    [InlineData("a.pdf")]
    [InlineData("a.docx")]
    [InlineData("a.html")]
    [InlineData("a.htm")]
    [InlineData("a.txt")]
    [InlineData("a.md")]
    public void All_default_extensions_are_accepted(string name)
    {
        var artifact = CreateArtifact(name);
        var result = NewOpener().Open(Payload(artifact));
        Assert.True(result["opened"]!.GetValue<bool>());
    }

    [Fact]
    public void Missing_path_field_fails_with_validation_error()
    {
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(null)));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Relative_path_fails_with_validation_error()
    {
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(@"reports\a.pdf")));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Unc_path_fails_with_validation_error()
    {
        var ex = Assert.Throws<CapabilityException>(
            () => NewOpener().Open(Payload(@"\\server\share\a.pdf")));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Path_outside_root_fails_with_security_scope_error()
    {
        // A well-formed absolute path with an allowed extension, but not under the root.
        var outside = Path.Combine(Path.GetTempPath(), $"outside-{Guid.NewGuid():N}.pdf");
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(outside)));
        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Path_traversal_escaping_root_fails_with_security_scope_error()
    {
        // Absolute path that textually starts under the root but escapes via "..".
        var traversal = Path.Combine(_root, "..", "..", "escaped.pdf");
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(traversal)));
        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Ancestor_directory_junction_leaving_root_fails_with_security_scope_error()
    {
        // M3 security review #2: a directory junction planted INSIDE the root but
        // pointing OUTSIDE must not smuggle an outside file through the textual
        // containment check. Junctions (mklink /J) need no elevation.
        var outside = Path.Combine(Path.GetTempPath(), $"pagentos-outside-{Guid.NewGuid():N}");
        Directory.CreateDirectory(outside);
        File.WriteAllText(Path.Combine(outside, "secret.pdf"), "data");
        var junction = Path.Combine(_root, "linked");

        var mklink = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = $"/c mklink /J \"{junction}\" \"{outside}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        });
        mklink!.WaitForExit();
        try
        {
            // If the environment cannot create a junction, don't produce a flaky failure.
            if (mklink.ExitCode != 0 || !Directory.Exists(junction))
            {
                return;
            }

            var smuggled = Path.Combine(junction, "secret.pdf");
            var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(smuggled)));
            Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
            Assert.Empty(_opener.Opened);
        }
        finally
        {
            try { Directory.Delete(junction); } catch (Exception) { /* best effort */ }
            try { Directory.Delete(outside, recursive: true); } catch (Exception) { /* best effort */ }
        }
    }

    [Fact]
    public void Prefix_sibling_of_root_is_not_treated_as_inside()
    {
        // "<root>-evil" shares the root's string prefix but is a different directory.
        var sibling = _root + "-evil";
        Directory.CreateDirectory(sibling);
        try
        {
            var artifact = Path.Combine(sibling, "a.pdf");
            File.WriteAllText(artifact, "data");
            var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(artifact)));
            Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        }
        finally
        {
            Directory.Delete(sibling, recursive: true);
        }
    }

    [Theory]
    [InlineData("a.exe")]
    [InlineData("a.bat")]
    [InlineData("a.ps1")]
    [InlineData("a.cmd")]
    [InlineData("a.js")]
    public void Executable_extension_inside_root_fails_with_security_scope_error(string name)
    {
        // Even though the file exists inside the root, executables are hard-denied.
        var artifact = CreateArtifact(name);
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(artifact)));
        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Disallowed_document_extension_fails_with_capability_missing()
    {
        var artifact = CreateArtifact("a.rtf");
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(artifact)));
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Nonexistent_file_under_root_fails_with_dependency_unavailable()
    {
        var missing = Path.Combine(_root, "not-there.pdf");
        var ex = Assert.Throws<CapabilityException>(() => NewOpener().Open(Payload(missing)));
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Successful_open_writes_a_jsonl_audit_entry()
    {
        var artifact = CreateArtifact("audited.pdf");
        var auditPath = Path.Combine(_root, "audit", "companion-audit.jsonl");
        var audit = new AuditLog(auditPath);

        NewOpener(audit).Open(new JsonObject { ["path"] = artifact, ["artifact_id"] = "11111111-1111-1111-1111-111111111111" });

        var lines = File.ReadAllLines(auditPath);
        var record = JsonNode.Parse(lines[^1])!.AsObject();
        Assert.Equal("artifact_open", record["event"]!.GetValue<string>());
        Assert.Equal(AgentCapabilities.DesktopOpenArtifact, record["capability"]!.GetValue<string>());
        Assert.Equal(AckStatus.Succeeded, record["status"]!.GetValue<string>());
        Assert.Contains("11111111-1111-1111-1111-111111111111", record["detail"]!.GetValue<string>());
    }

    [Fact]
    public void Rejected_open_writes_a_jsonl_audit_entry_and_does_not_open()
    {
        var auditPath = Path.Combine(_root, "audit", "companion-audit.jsonl");
        var audit = new AuditLog(auditPath);
        var outside = Path.Combine(Path.GetTempPath(), $"outside-{Guid.NewGuid():N}.pdf");

        Assert.Throws<CapabilityException>(
            () => NewOpener(audit).Open(new JsonObject { ["path"] = outside }));

        var record = JsonNode.Parse(File.ReadAllLines(auditPath)[^1])!.AsObject();
        Assert.Equal("artifact_open", record["event"]!.GetValue<string>());
        Assert.Equal(AckStatus.Failed, record["status"]!.GetValue<string>());
        Assert.Contains(ErrorClasses.SecurityScopeError, record["detail"]!.GetValue<string>());
        Assert.Empty(_opener.Opened);
    }

    [Fact]
    public void Default_roots_include_local_appdata_artifacts_and_extra_roots()
    {
        var roots = ArtifactOpener.DefaultRoots(@"D:\extra-artifacts");
        Assert.Contains(roots, r => r.EndsWith(@"PagentOS\agent\artifacts", StringComparison.OrdinalIgnoreCase));
        Assert.Contains(@"D:\extra-artifacts", roots);
    }

    [Fact]
    public void Manifest_advertises_open_artifact_alongside_open_application()
    {
        Assert.Contains(AgentCapabilities.DesktopOpenArtifact, AgentCapabilities.All);
        Assert.Contains(AgentCapabilities.DesktopOpenApplication, AgentCapabilities.All);
    }
}

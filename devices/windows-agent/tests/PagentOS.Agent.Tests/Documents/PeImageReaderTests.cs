using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// M28 row 26.16: what a PE says about itself, read from the file.
/// </summary>
/// <remarks>
/// The real subjects here are binaries this repository did NOT build — the .NET host and
/// whatever else is beside the test assembly — for the same reason
/// <c>app.nativefactory.artifacts</c>'s own reader was qualified against mspaint.exe: a
/// reader that only ever agrees with the thing that produced its input has proven nothing.
/// </remarks>
public sealed class PeImageReaderTests
{
    private static string ThisAssembly => typeof(PeImageReaderTests).Assembly.Location;

    [Fact]
    public void A_real_pe_answers_its_machine_word()
    {
        var facts = PeImageReader.TryRead(ThisAssembly);

        Assert.NotNull(facts);
        var architecture = facts!["architecture"]?.GetValue<string>();
        Assert.False(string.IsNullOrWhiteSpace(architecture));
        // Whatever this runner is, the word is named rather than guessed — and an unnamed
        // machine would come back as its hex value, which is still a fact.
        Assert.Matches("^(x86|x64|arm64|arm|0x[0-9a-f]{4})$", architecture);
    }

    [Fact]
    public void A_managed_assembly_carries_a_version_the_spec_check_can_compare()
    {
        // `validate_against_spec` calls the version comparison "the point of the whole
        // module". If this block cannot carry one, a device-dispatched build can never be
        // more than `unverified`, whatever else the device reports.
        var facts = PeImageReader.TryRead(ThisAssembly);

        Assert.NotNull(facts);
        Assert.True(facts!.ContainsKey("version"), "a built assembly stamps a version resource");
        Assert.False(string.IsNullOrWhiteSpace(facts["version"]?.GetValue<string>()));
    }

    [Fact]
    public void The_field_names_are_the_ones_cloud_core_consumes()
    {
        // Two readers of one format in two languages. Cloud Core builds ArtifactFacts out of
        // this block, so the NAMES are the contract: `version`, `architecture`, `subsystem`.
        // services/api/tests/unit/test_device_pe_contract.py holds the other side.
        var facts = PeImageReader.TryRead(ThisAssembly);

        Assert.NotNull(facts);
        foreach (var key in facts!.Select(pair => pair.Key))
        {
            Assert.Contains(key, new[] { "version", "architecture", "subsystem" });
        }
    }

    [Fact]
    public void A_file_that_is_not_a_pe_answers_nothing_rather_than_a_default()
    {
        var text = Path.Combine(Path.GetTempPath(), $"pagentos-not-a-pe-{Guid.NewGuid():N}.exe");
        File.WriteAllText(text, "this is not a program");
        try
        {
            // Named .exe, and still not a PE. A block here would be a guess, and a guess is
            // what the `verified` stamp this work removed was made of.
            Assert.Null(PeImageReader.TryRead(text));
        }
        finally
        {
            File.Delete(text);
        }
    }

    [Fact]
    public void A_truncated_pe_answers_nothing_and_never_throws()
    {
        // The first two bytes are right and everything after them is missing. An identity
        // reader that threw here would take the whole inspection of a file that really
        // exists down with it.
        var truncated = Path.Combine(Path.GetTempPath(), $"pagentos-truncated-{Guid.NewGuid():N}.exe");
        File.WriteAllBytes(truncated, new byte[] { (byte)'M', (byte)'Z', 0x00, 0x01 });
        try
        {
            Assert.Null(PeImageReader.TryRead(truncated));
        }
        finally
        {
            File.Delete(truncated);
        }
    }

    [Fact]
    public void A_missing_file_answers_nothing_and_never_throws()
    {
        var missing = Path.Combine(Path.GetTempPath(), $"pagentos-absent-{Guid.NewGuid():N}.exe");
        Assert.Null(PeImageReader.TryRead(missing));
    }

    [Fact]
    public void A_pe_that_lies_about_its_header_offset_is_refused_not_followed()
    {
        // e_lfanew pointing past the end of the file. Every offset in a PE comes from the
        // file itself, so each one is bounded against the real length before it is used.
        var hostile = Path.Combine(Path.GetTempPath(), $"pagentos-hostile-{Guid.NewGuid():N}.exe");
        var bytes = new byte[0x80];
        bytes[0] = (byte)'M';
        bytes[1] = (byte)'Z';
        BitConverter.GetBytes(0x7FFFFFFF).CopyTo(bytes, 0x3C);
        File.WriteAllBytes(hostile, bytes);
        try
        {
            Assert.Null(PeImageReader.TryRead(hostile));
        }
        finally
        {
            File.Delete(hostile);
        }
    }
}

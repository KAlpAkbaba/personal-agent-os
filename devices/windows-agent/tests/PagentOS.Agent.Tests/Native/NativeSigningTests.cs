using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §9, ADR-0095 decision 4 — the device RUNS no signer, ever —
/// and B33 req 473 (owner decision 2026-09-16) — the device SIGNS an MSIX in its own process.
///
/// Both hold at once, and these tests are what keeps them compatible. The signing tools stay
/// refused by name; the only signing code is <c>Native/PackageSigner.cs</c> (mssign32's
/// <c>SignerSignEx2</c>), the identity is <c>Native/OwnerSigningIdentity.cs</c>
/// (<c>CertificateRequest</c>) and the per-user install is <c>Native/MsixDeployment.cs</c>
/// (Windows' package manager over its ABI) — and none of them may start a process. These
/// tests read the companion's own sources: a signer cannot be invoked by code that does not
/// name one, and signing code cannot spawn what it does not reference.
///
/// The one exception this test permits by construction is a NAME in a refusal list or a
/// refusal message, which is a mention rather than an invocation, and is exactly what makes
/// the refusals possible.
/// </summary>
public sealed class NativeSigningTests
{
    /// <summary>The files allowed to spell a signer's name at all — the refusal list and the two places that enforce it.</summary>
    private static readonly string[] MayNameASigner =
    [
        Path.Combine("Projects", "ProjectManifest.cs"),
        Path.Combine("Projects", "ProjectRunner.cs"),
        Path.Combine("Native", "NativeTools.cs"),
    ];

    [Fact]
    public void No_companion_source_starts_a_signing_or_certificate_tool()
    {
        var offenders = new List<string>();
        foreach (var file in CompanionSources.AllFiles())
        {
            var text = File.ReadAllText(file);
            foreach (var program in NativeCapabilityNames.ForbiddenPrograms)
            {
                if (!text.Contains(program, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (MayNameASigner.Any(suffix => file.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)))
                {
                    continue;
                }

                offenders.Add($"{Path.GetFileName(file)} names '{program}'");
            }
        }

        Assert.True(
            offenders.Count == 0,
            $"the companion must not name a signing tool outside its refusal list: {string.Join("; ", offenders)}");
    }

    [Fact]
    public void The_three_files_that_may_name_one_only_REFUSE_it()
    {
        foreach (var relative in MayNameASigner)
        {
            var text = CompanionSources.Read(relative);
            foreach (var program in NativeCapabilityNames.ForbiddenPrograms)
            {
                foreach (Match match in Regex.Matches(text, Regex.Escape(program), RegexOptions.IgnoreCase))
                {
                    var line = LineAt(text, match.Index);

                    // Every occurrence must be inside a comment, a refusal list, or a refusal
                    // message — never an argument list, a ProcessStartInfo or a path built to
                    // be executed. `Path.Combine`, `ArgumentList` and `ProcessStartInfo` are
                    // the three shapes that would make it an invocation.
                    Assert.DoesNotContain("ProcessStartInfo", line, StringComparison.Ordinal);
                    Assert.DoesNotContain("ArgumentList", line, StringComparison.Ordinal);
                    Assert.DoesNotContain("Process.Start", line, StringComparison.Ordinal);
                }
            }
        }
    }

    [Fact]
    public void NativeTools_has_no_function_that_returns_a_signer_even_though_it_sits_beside_makeappx()
    {
        // signtool.exe lives in the SAME Windows Kits directory this class walks to find
        // makeappx.exe. The way to keep "the device runs no signer" true is to have no code that
        // could hand a signer its path, so this asserts the absence of the obvious one.
        var text = CompanionSources.Read(Path.Combine("Native", "NativeTools.cs"));
        Assert.Contains("MakeAppxExecutable = \"makeappx.exe\"", text, StringComparison.Ordinal);
        Assert.DoesNotContain("signtool.exe\"", text, StringComparison.Ordinal);
        Assert.DoesNotContain("FindSignTool", text, StringComparison.Ordinal);
    }

    [Fact]
    public void The_packaging_shape_asks_makeappx_NOT_to_validate_because_unsigned_is_the_point()
    {
        // `/nv` skips semantic validation, which is what would refuse an unsigned manifest.
        // Recorded as a test rather than a comment because dropping the flag would turn a
        // deliberate, honest, unsigned package into a build failure nobody could explain.
        Assert.Equal("makeappx pack /d staging /p dist/app.msix /o /nv", NativeLab.PackCommand());
    }

    [Fact]
    public void The_forbidden_list_matches_with_and_without_an_exe_suffix_and_is_case_blind()
    {
        Assert.True(NativeCapabilityNames.IsForbiddenProgram("signtool"));
        Assert.True(NativeCapabilityNames.IsForbiddenProgram("SignTool.exe"));
        Assert.True(NativeCapabilityNames.IsForbiddenProgram("CERTUTIL"));
        Assert.False(NativeCapabilityNames.IsForbiddenProgram(NativeCapabilityNames.DotnetProgram));
        Assert.False(NativeCapabilityNames.IsForbiddenProgram(NativeCapabilityNames.MakeAppxProgram));
        Assert.False(NativeCapabilityNames.IsForbiddenProgram("signtoolkit"));
    }

    /// <summary>The files that sign, hold the identity and deploy — in-process by design.</summary>
    private static readonly string[] InProcessSigning =
    [
        Path.Combine("Native", "PackageSigner.cs"),
        Path.Combine("Native", "OwnerSigningIdentity.cs"),
        Path.Combine("Native", "MsixDeployment.cs"),
        Path.Combine("Native", "NativeSigning.cs"),
    ];

    [Fact]
    public void The_in_process_signing_files_start_no_process_and_write_no_machine_store()
    {
        foreach (var relative in InProcessSigning)
        {
            var text = CompanionSources.Read(relative);
            Assert.DoesNotContain("System.Diagnostics", text, StringComparison.Ordinal);
            Assert.DoesNotContain("Process.", text, StringComparison.Ordinal);
            Assert.DoesNotContain("ProcessStartInfo", text, StringComparison.Ordinal);
            Assert.DoesNotContain("ShellExecute", text, StringComparison.Ordinal);
            Assert.DoesNotContain("powershell", text, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("runas", text, StringComparison.OrdinalIgnoreCase);
            // LocalMachine is READ for trust, never opened for writing.
            foreach (Match match in Regex.Matches(text, @"StoreLocation\.LocalMachine"))
            {
                var after = text[match.Index..Math.Min(text.Length, match.Index + 400)];
                Assert.Contains("OpenFlags.ReadOnly", after, StringComparison.Ordinal);
                Assert.DoesNotContain("OpenFlags.ReadWrite", after, StringComparison.Ordinal);
            }
        }

        // The signing code signs with SHA-256 and asks for no timestamp (no network, recorded).
        var signer = CompanionSources.Read(Path.Combine("Native", "PackageSigner.cs"));
        Assert.Contains("CALG_SHA_256 = 0x0000800c", signer, StringComparison.Ordinal);
        Assert.DoesNotContain("http", signer, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("dwTimestampFlags = ", signer, StringComparison.Ordinal);
        Assert.DoesNotContain("pwszTimestampURL = ", signer, StringComparison.Ordinal);
    }

    [Fact]
    public void The_identity_s_key_is_created_non_exportable_and_only_the_public_certificate_is_written()
    {
        var text = CompanionSources.Read(Path.Combine("Native", "OwnerSigningIdentity.cs"));
        Assert.Contains("ExportPolicy = CngExportPolicies.None", text, StringComparison.Ordinal);
        Assert.Contains("certificate.Export(X509ContentType.Cert)", text, StringComparison.Ordinal);
        Assert.DoesNotContain("X509ContentType.Pfx", text, StringComparison.Ordinal);
        Assert.DoesNotContain("X509ContentType.Pkcs12", text, StringComparison.Ordinal);
        Assert.DoesNotContain("ExportPkcs8PrivateKey", text, StringComparison.Ordinal);
        Assert.DoesNotContain("ExportParameters(true", text, StringComparison.Ordinal);
    }

    private static string LineAt(string text, int index)
    {
        var start = text.LastIndexOf('\n', Math.Min(index, text.Length - 1)) + 1;
        var end = text.IndexOf('\n', index);
        return end < 0 ? text[start..] : text[start..end];
    }
}

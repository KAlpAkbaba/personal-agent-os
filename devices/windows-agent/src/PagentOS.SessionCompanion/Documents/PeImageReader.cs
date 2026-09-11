using System.Diagnostics;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// What a Windows PE image says about itself: its version resource, its machine word and
/// its subsystem. Read from the FILE, never inferred from its name or its directory.
/// </summary>
/// <remarks>
/// <para>
/// M28 row 26.16. A native build that happens on the device is read back by
/// <c>file.inspect</c>, which until 2026-09-11 answered size, hash and kind — and Cloud Core
/// stamped the row <c>verified</c> on that alone. It could not do otherwise: nothing in the
/// device's answer named the artefact's own version, and
/// <c>app.nativefactory.artifacts.validate_against_spec</c> calls that comparison "the point
/// of the whole module" — an artefact that cannot prove which build it is cannot be trusted
/// to be the build that was just made.
/// </para>
/// <para>
/// This is the missing half, and it adds <b>no capability name</b> (ADR-0095 addendum 2 §1)
/// and <b>no file kind</b>: it is an additive block on a result <c>file.inspect</c> already
/// returns, produced only when the file's extension is executable AND its first two bytes
/// really are <c>MZ</c>. A file that is not a PE simply has no block — never a guess, never
/// a default.
/// </para>
/// <para>
/// The version comes from <see cref="FileVersionInfo"/>, the platform's own reader, rather
/// than from a hand-rolled resource walk: there is no format subtlety to get wrong and no
/// second implementation to keep true. The subsystem needs four bytes of the optional header,
/// which is a fixed offset from the PE signature and is read here directly.
/// </para>
/// <para>
/// The field names are deliberately those of <c>app.nativefactory.artifacts.ArtifactFacts</c>
/// — <c>version</c>, <c>architecture</c>, <c>subsystem</c> — because Cloud Core builds that
/// dataclass out of this block. Two readers of one format in two languages is a real risk, so
/// the names are bound by a contract test rather than by hope.
/// </para>
/// <para>
/// Never throws: an unreadable or truncated file answers <c>null</c>, and the caller reports
/// a PE it could not read rather than failing the inspection of a file that is really there.
/// </para>
/// </remarks>
public static class PeImageReader
{
    /// <summary>IMAGE_SUBSYSTEM_WINDOWS_GUI.</summary>
    private const ushort SubsystemGui = 2;

    /// <summary>IMAGE_SUBSYSTEM_WINDOWS_CUI.</summary>
    private const ushort SubsystemConsole = 3;

    /// <summary>
    /// The machine words this reader names. Anything else is reported as its hex value
    /// rather than as a guess — an unnamed machine is a fact, not a failure.
    /// </summary>
    private static string MachineName(ushort machine) => machine switch
    {
        0x014c => "x86",
        0x8664 => "x64",
        0xaa64 => "arm64",
        0x01c4 => "arm",
        _ => $"0x{machine:x4}",
    };

    private static string? SubsystemName(ushort subsystem) => subsystem switch
    {
        SubsystemGui => "windows_gui",
        SubsystemConsole => "windows_console",
        _ => null,
    };

    /// <summary>
    /// The PE facts, or <c>null</c> when this file is not a readable PE image.
    /// </summary>
    public static JsonObject? TryRead(string path)
    {
        try
        {
            using var stream = File.OpenRead(path);
            if (stream.Length < 0x40)
            {
                return null;
            }

            var header = new byte[0x40];
            if (stream.Read(header, 0, header.Length) != header.Length)
            {
                return null;
            }

            if (header[0] != (byte)'M' || header[1] != (byte)'Z')
            {
                return null;
            }

            var peOffset = BitConverter.ToInt32(header, 0x3C);
            // COFF header is 20 bytes after the 4-byte signature; the optional header's
            // Subsystem sits 68 bytes into it, and the Magic that says PE32 vs PE32+ is its
            // first two. Bound every read against the real length rather than trusting the
            // offsets a file supplied.
            if (peOffset < 0 || peOffset + 24 > stream.Length)
            {
                return null;
            }

            stream.Position = peOffset;
            var coff = new byte[24];
            if (stream.Read(coff, 0, coff.Length) != coff.Length)
            {
                return null;
            }

            if (coff[0] != (byte)'P' || coff[1] != (byte)'E' || coff[2] != 0 || coff[3] != 0)
            {
                return null;
            }

            var machine = BitConverter.ToUInt16(coff, 4);
            var optionalHeaderSize = BitConverter.ToUInt16(coff, 20);

            string? subsystem = null;
            if (optionalHeaderSize >= 70 && peOffset + 24 + 70 <= stream.Length)
            {
                stream.Position = peOffset + 24;
                var optional = new byte[70];
                if (stream.Read(optional, 0, optional.Length) == optional.Length)
                {
                    subsystem = SubsystemName(BitConverter.ToUInt16(optional, 68));
                }
            }

            var facts = new JsonObject
            {
                // The names ArtifactFacts uses, bound by a contract test on the Cloud Core side.
                ["architecture"] = MachineName(machine),
            };
            if (subsystem is not null)
            {
                facts["subsystem"] = subsystem;
            }

            var version = ReadVersion(path);
            if (version is not null)
            {
                facts["version"] = version;
            }

            return facts;
        }
        catch (Exception)
        {
            // A locked, vanished or malformed file is "no PE facts", not a failed inspection
            // of a file that is really there.
            return null;
        }
    }

    /// <summary>
    /// The version resource's ProductVersion, falling back to FileVersion.
    /// </summary>
    /// <remarks>
    /// <c>validate_against_spec</c> compares this against the spec's version and its assembly
    /// version, so the value must be the one the build stamped. ProductVersion is preferred
    /// because that is what the .NET SDK writes from <c>&lt;Version&gt;</c>; FileVersion is the
    /// four-part stamp and is the honest fallback when a native toolchain wrote only that.
    /// A resource-less binary answers <c>null</c>, which Cloud Core reports as "carries no
    /// version to check" rather than passing.
    /// </remarks>
    private static string? ReadVersion(string path)
    {
        try
        {
            var info = FileVersionInfo.GetVersionInfo(path);
            var product = info.ProductVersion?.Trim();
            if (!string.IsNullOrEmpty(product))
            {
                return product;
            }

            var file = info.FileVersion?.Trim();
            return string.IsNullOrEmpty(file) ? null : file;
        }
        catch (Exception)
        {
            return null;
        }
    }
}

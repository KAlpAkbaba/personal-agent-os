using System.Diagnostics;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Abstracts the actual "shell open" of a validated document so tests can inject a fake that
/// records the path without popping up a GUI handler. The production implementation is
/// <see cref="ShellFileOpener"/>.
/// </summary>
public interface IFileOpener
{
    /// <summary>
    /// Opens the already-validated absolute file path with its OS-associated application.
    /// The caller (<see cref="ArtifactOpener"/>) has guaranteed the path exists, is inside an
    /// allowlisted artifact root and carries an allowlisted, non-executable extension.
    /// </summary>
    void Open(string fullPath);
}

/// <summary>
/// Production opener: ShellExecute via <c>Process.Start(UseShellExecute = true)</c>, which hands
/// the file to the user's registered handler in the interactive session. Never passes arguments
/// and never launches an executable directly — it opens a document with its associated program.
/// </summary>
public sealed class ShellFileOpener : IFileOpener
{
    public void Open(string fullPath)
    {
        var startInfo = new ProcessStartInfo(fullPath) { UseShellExecute = true };

        // ShellExecute may reuse an already-running handler and return null; that is a successful
        // open, not a failure. Any real failure surfaces as a Win32Exception, which ArtifactOpener
        // maps to a typed error.
        using var process = Process.Start(startInfo);
    }
}

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// Locates the Session Companion's own source files so a test can assert things about the CODE
/// rather than about behaviour it would have to provoke.
///
/// <para>Reading source in a test is a blunt instrument and is used here for exactly one class of
/// claim: "this file cannot do X". Turning a monitor off, suspending a machine or synthesising a
/// keystroke are all things whose absence is far easier to prove by reading than by running — and
/// far more important to keep true as the files grow.</para>
/// </summary>
public static class CompanionSources
{
    /// <summary><c>devices/windows-agent/src/PagentOS.SessionCompanion</c>, found by walking up from the test output.</summary>
    public static string Directory()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null
               && !System.IO.Directory.Exists(System.IO.Path.Combine(dir.FullName, "src", "PagentOS.SessionCompanion")))
        {
            dir = dir.Parent;
        }

        return dir is null
            ? throw new DirectoryNotFoundException("could not locate src/PagentOS.SessionCompanion from the test output")
            : System.IO.Path.Combine(dir.FullName, "src", "PagentOS.SessionCompanion");
    }

    public static string Path(string fileName) => System.IO.Path.Combine(Directory(), fileName);

    public static string Read(string fileName) => File.ReadAllText(Path(fileName));

    /// <summary>Every companion source file, excluding build output.</summary>
    public static IReadOnlyList<string> AllFiles()
        => [.. System.IO.Directory.GetFiles(Directory(), "*.cs", SearchOption.AllDirectories)
            .Where(p => !p.Contains($"{System.IO.Path.DirectorySeparatorChar}bin{System.IO.Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                        && !p.Contains($"{System.IO.Path.DirectorySeparatorChar}obj{System.IO.Path.DirectorySeparatorChar}", StringComparison.Ordinal))
            .OrderBy(p => p, StringComparer.Ordinal)];

    /// <summary>
    /// Every DISPLAY-related companion source: <c>Display*.cs</c> and <c>Monitor*.cs</c>. A glob
    /// rather than a list, so a display file added later is guarded the day it appears instead of
    /// the day someone remembers to add it here.
    /// </summary>
    public static IReadOnlyList<string> DisplayFiles()
        => [.. AllFiles()
            .Where(p =>
            {
                var name = System.IO.Path.GetFileName(p);
                return name.StartsWith("Display", StringComparison.Ordinal)
                       || name.StartsWith("Monitor", StringComparison.Ordinal);
            })];
}

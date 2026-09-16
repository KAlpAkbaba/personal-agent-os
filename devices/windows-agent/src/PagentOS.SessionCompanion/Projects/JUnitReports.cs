using System.Globalization;
using System.Xml;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>
/// B49 (ADR-0161): the test counts of a Gradle run, read from the JUnit XML reports Gradle
/// writes (<c>&lt;module&gt;/build/test-results/&lt;task&gt;/TEST-*.xml</c>). Gradle's console
/// prints no totals, and <c>project.test</c> answers <c>counts_parsed:false</c> when it cannot
/// count — which the Cloud Core rightly refuses to call verified — so the counts come from the
/// reports, and only from reports THIS run wrote.
/// </summary>
/// <remarks>
/// The reports are cleared before the run (<see cref="Clear"/>): an unchanged project would
/// otherwise have its test task skipped as up to date, leave last run's files in place, and
/// have them counted as this run's. Reading is bounded (files, bytes, depth), never resolves a
/// DTD or an external entity, and never follows a reparse point out of the project.
/// </remarks>
public static class JUnitReports
{
    public const int MaxFiles = 512;
    public const long MaxFileBytes = 4L * 1024 * 1024;

    private static readonly string[] BuildFolders = ["build"];

    /// <summary>The report directories of the project folder and of its direct module folders.</summary>
    public static IEnumerable<string> ReportDirectories(string projectFolder)
    {
        var modules = new List<string> { projectFolder };
        try
        {
            modules.AddRange(Directory.EnumerateDirectories(projectFolder).Where(d => !ProjectRoots.IsReparsePoint(d)));
        }
        catch (Exception)
        {
            // An unreadable folder has no modules to count.
        }

        foreach (var module in modules)
        {
            foreach (var build in BuildFolders)
            {
                var results = Path.Combine(module, build, "test-results");
                if (Directory.Exists(results) && !ProjectRoots.IsReparsePoint(results))
                {
                    yield return results;
                }
            }
        }
    }

    /// <summary>Removes the previous run's reports so the test task runs again and nothing old is counted.</summary>
    public static void Clear(string projectFolder)
    {
        foreach (var directory in ReportDirectories(projectFolder).ToList())
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    /// <summary>
    /// (passed, failed) over every report written at or after <paramref name="sinceUtc"/>;
    /// (null, null) when there is none — "nothing was counted" is not "nothing failed".
    /// A failure and an error both count as failed; a skipped test counts as neither.
    /// </summary>
    public static (int? Passed, int? Failed) Count(string projectFolder, DateTime sinceUtc)
    {
        var passed = 0;
        var failed = 0;
        var files = 0;
        foreach (var directory in ReportDirectories(projectFolder))
        {
            IEnumerable<string> reports;
            try
            {
                reports = Directory.EnumerateFiles(directory, "TEST-*.xml", new EnumerationOptions { RecurseSubdirectories = true, MaxRecursionDepth = 3, AttributesToSkip = FileAttributes.ReparsePoint });
            }
            catch (Exception)
            {
                continue;
            }

            foreach (var report in reports)
            {
                var info = new FileInfo(report);
                if (info.LastWriteTimeUtc < sinceUtc || info.Length > MaxFileBytes)
                {
                    continue;
                }

                if (++files > MaxFiles)
                {
                    break;
                }

                var suite = ReadSuite(report);
                if (suite is null)
                {
                    return (null, null);
                }

                failed += suite.Value.Failures + suite.Value.Errors;
                passed += suite.Value.Tests - suite.Value.Failures - suite.Value.Errors - suite.Value.Skipped;
            }
        }

        return files == 0 ? (null, null) : (passed, failed);
    }

    /// <summary>The counts on a report's root <c>testsuite</c> element; null when the file is not one.</summary>
    public static (int Tests, int Failures, int Errors, int Skipped)? ReadSuite(string path)
    {
        try
        {
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = MaxFileBytes };
            using var reader = XmlReader.Create(path, settings);
            if (reader.MoveToContent() != XmlNodeType.Element || reader.LocalName != "testsuite")
            {
                return null;
            }

            int Attr(string name)
                => int.TryParse(reader.GetAttribute(name), NumberStyles.None, CultureInfo.InvariantCulture, out var value) ? value : 0;

            var tests = Attr("tests");
            var failures = Attr("failures");
            var errors = Attr("errors");
            var skipped = Attr("skipped");
            if (failures + errors + skipped > tests)
            {
                return null;
            }

            return (tests, failures, errors, skipped);
        }
        catch (Exception)
        {
            return null;
        }
    }
}

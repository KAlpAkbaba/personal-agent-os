using System.IO;

using PagentOS.Agent.Tests.Support;

using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Reading a log while something else is writing it.
///
/// CI run 34339398396 failed the Windows agent job on
/// <c>IpcWiringTests.The_created_pipes_effective_dacl_names_exactly_the_intended_principals</c>
/// with "the process cannot access the file ... because it is being used by another
/// process": the test read the audit log with <c>File.ReadAllText</c> while the server that
/// writes it was still running. <c>AuditLog</c>'s own comment names that call as the hazard,
/// measured on the runner on 2026-09-08 in the opposite direction.
///
/// These tests do not reproduce the RACE — a race proven by winning it once is not proven.
/// They reproduce the CONDITION the race creates, deterministically: a writer holding the
/// file. Against that, <c>File.ReadAllText</c> fails and <see cref="LiveLog.Read"/> does
/// not, which is the whole of the claim.
/// </summary>
public class LiveLogTests
{
    [Fact]
    public void ReadAllText_fails_while_a_writer_holds_the_file()
    {
        // The behaviour that broke CI, pinned. If a future runtime made ReadAllText
        // tolerant, this test failing is the correct way to find that out - the helper
        // would then be unnecessary rather than silently redundant.
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".jsonl");
        using var writer = new FileStream(
            path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
        writer.Write("{\"event\":\"ipc_pipe_created\"}\n"u8);
        writer.Flush();

        try
        {
            Assert.Throws<IOException>(() => File.ReadAllText(path));
        }
        finally
        {
            writer.Dispose();
            File.Delete(path);
        }
    }

    [Fact]
    public void LiveLog_reads_the_same_file_the_writer_is_holding()
    {
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".jsonl");
        using var writer = new FileStream(
            path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
        writer.Write("{\"event\":\"ipc_pipe_created\",\"sddl\":\"D:(A;;FA;;;S-1-5-21)\"}\n"u8);
        writer.Flush();

        try
        {
            var text = LiveLog.Read(path);
            Assert.Contains("ipc_pipe_created", text, System.StringComparison.Ordinal);
            Assert.Contains("D:(A;;FA;;;S-1-5-21)", text, System.StringComparison.Ordinal);
        }
        finally
        {
            writer.Dispose();
            File.Delete(path);
        }
    }

    [Fact]
    public void A_log_that_does_not_exist_yet_reads_as_empty_not_as_a_throw()
    {
        // The polling call site asks before the first row is ever written, and treated a
        // missing file as "" already. Keeping that behaviour in the helper is what let the
        // call site collapse to one line.
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".jsonl");
        Assert.False(File.Exists(path));
        Assert.Equal(string.Empty, LiveLog.Read(path));
    }
}

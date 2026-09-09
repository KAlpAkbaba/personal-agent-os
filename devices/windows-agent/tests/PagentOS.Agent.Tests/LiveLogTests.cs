using System;
using System.IO;
using System.Threading.Tasks;

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

    [Fact]
    public async Task Waiting_returns_as_soon_as_the_row_arrives()
    {
        // The reason WaitForAsync exists: a test that waited for an IN-MEMORY signal read the
        // audit file microseconds later and found it empty (CI 34363259200). The row is
        // appended by a different path, so the file has to be asked, not assumed.
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".jsonl");
        var writer = Task.Run(async () =>
        {
            await Task.Delay(120);
            File.AppendAllText(path, "{\"event\":\"ipc_pipe_created\"}" + Environment.NewLine);
        });
        try
        {
            var content = await LiveLog.WaitForAsync(path, "ipc_pipe_created", TimeSpan.FromSeconds(10));
            Assert.Contains("ipc_pipe_created", content, System.StringComparison.Ordinal);
        }
        finally
        {
            await writer;
            File.Delete(path);
        }
    }

    [Fact]
    public async Task A_row_that_never_arrives_returns_what_IS_there_so_the_callers_assertion_fails
        ()
    {
        // Never a throw of its own: the caller wrote the assertion, and the caller's message -
        // with the real content - is what should be read when it fails.
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".jsonl");
        File.WriteAllText(path, "{\"event\":\"something_else\"}" + Environment.NewLine);
        try
        {
            var content = await LiveLog.WaitForAsync(path, "never_written", TimeSpan.FromMilliseconds(200));
            Assert.DoesNotContain("never_written", content, System.StringComparison.Ordinal);
            Assert.Contains("something_else", content, System.StringComparison.Ordinal);
        }
        finally
        {
            File.Delete(path);
        }
    }
}

using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Audit;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Pins the audit trail's on-disk schema, because a verifier was written against an
/// imagined one. The first real cloud qualification run reported "no command row found"
/// while real commands were succeeding: the PowerShell verifier read the timestamp from a
/// field named <c>at</c>, which this writer has never emitted. The field is <c>ts</c>.
///
/// These tests write REAL rows through the REAL <see cref="AuditLog"/> and assert the exact
/// key set and shape, so the PowerShell reader (scripts/lib/AgentAudit.ps1) and its test
/// fixture are checked against what the writer actually produces, not against prose.
/// </summary>
public sealed class AuditLogSchemaTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "pagentos-audit-schema", Guid.NewGuid().ToString("N"));

    public AuditLogSchemaTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch (Exception) { }
    }

    [Fact]
    public void A_command_ack_row_carries_ts_event_command_id_trace_id_and_status_and_nothing_imagined()
    {
        var path = Path.Combine(_dir, "agent-audit.jsonl");
        var audit = new AuditLog(path);

        audit.Write("command_received", commandId: "cmd-123", traceId: "trace-abc", capability: "desktop.open_application");
        audit.Write("command_ack", commandId: "cmd-123", traceId: "trace-abc", status: "running");

        var lines = File.ReadAllLines(path);
        Assert.Equal(2, lines.Length);

        var received = JsonNode.Parse(lines[0])!.AsObject();
        var ack = JsonNode.Parse(lines[1])!.AsObject();

        // The exact key set the reader may rely on - order-insensitive, but nothing extra
        // and nothing missing. `at` is deliberately asserted ABSENT.
        Assert.Equal(
            new[] { "capability", "command_id", "event", "trace_id", "ts" },
            received.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(
            new[] { "command_id", "event", "status", "trace_id", "ts" },
            ack.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        Assert.False(ack.ContainsKey("at"), "the timestamp field is `ts`; a reader looking for `at` finds nothing, ever");

        Assert.Equal("command_received", received["event"]!.GetValue<string>());
        Assert.Equal("command_ack", ack["event"]!.GetValue<string>());
        Assert.Equal("cmd-123", ack["command_id"]!.GetValue<string>());
        Assert.Equal("trace-abc", ack["trace_id"]!.GetValue<string>());
        Assert.Equal("running", ack["status"]!.GetValue<string>());

        // `ts` is ISO-8601 round-trip UTC, parseable back to within a minute of now.
        var ts = DateTimeOffset.Parse(ack["ts"]!.GetValue<string>(), System.Globalization.CultureInfo.InvariantCulture);
        Assert.Equal(TimeSpan.Zero, ts.Offset);
        Assert.True((DateTimeOffset.UtcNow - ts).Duration() < TimeSpan.FromMinutes(1));
    }

    [Fact]
    public void One_row_per_line_and_every_line_is_a_complete_json_object()
    {
        // JSONL: a reader that splits on newlines must never see a torn object. The writer
        // appends `json + NewLine` in one call per row.
        var path = Path.Combine(_dir, "agent-audit.jsonl");
        var audit = new AuditLog(path);
        for (var i = 0; i < 25; i++)
        {
            audit.Write("command_ack", commandId: $"cmd-{i}", traceId: $"t-{i}", status: "accepted");
        }

        var lines = File.ReadAllLines(path);
        Assert.Equal(25, lines.Length);
        foreach (var line in lines)
        {
            var node = JsonNode.Parse(line);
            Assert.NotNull(node);
            Assert.True(line.StartsWith('{') && line.EndsWith('}'));
        }
    }

    [Fact]
    public void An_unwritable_audit_path_does_not_throw_into_the_audited_code_path()
    {
        // Non-fatal by design: losing an audit line is strictly smaller than losing the
        // command (or the pipe server - which a throwing logger once killed). What this
        // test does NOT accept is a writer that has no opinion about the failure at all;
        // see AuditLog for where the failure is surfaced.
        // The constructor is allowed to throw - an audit directory that cannot be created is
        // a fail-fast at service start, which is loud. The WRITE path is what must never
        // throw: construct on a valid directory, then make the target path a directory so
        // the append itself fails.
        var auditDirectory = Path.Combine(_dir, "audit");
        var path = Path.Combine(auditDirectory, "agent-audit.jsonl");
        var audit = new AuditLog(path);
        Directory.CreateDirectory(path);   // a directory where the file should be: AppendAllText cannot write here

        var exception = Record.Exception(() => audit.Write("command_ack", commandId: "cmd-x", traceId: "t-x", status: "accepted"));
        audit.Write("command_ack", commandId: "cmd-y", traceId: "t-y", status: "accepted");

        Assert.Null(exception);
        // Non-fatal, but not silent: the failures are counted, so a trail that has stopped
        // being written is an observable fact rather than an absence someone has to notice.
        Assert.Equal(2, audit.FailedWrites);
    }

    [Fact]
    public void A_writable_audit_path_counts_no_failures()
    {
        var audit = new AuditLog(Path.Combine(_dir, "ok", "agent-audit.jsonl"));
        audit.Write("command_received", commandId: "cmd-1", traceId: "t-1", capability: "desktop.open_application");
        Assert.Equal(0, audit.FailedWrites);
    }
}

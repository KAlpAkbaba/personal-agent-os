using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests;

public class SerializationTests
{
    private static string FixturesDir => Path.Combine(AppContext.BaseDirectory, "Fixtures");

    public static TheoryData<string> FixtureFiles()
    {
        var data = new TheoryData<string>();
        foreach (var file in Directory.GetFiles(FixturesDir, "*.json"))
        {
            data.Add(Path.GetFileName(file));
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(FixtureFiles))]
    public void Fixture_round_trips_with_identical_json(string fileName)
    {
        var json = File.ReadAllText(Path.Combine(FixturesDir, fileName));
        var message = ProtocolJson.Deserialize(json);
        var reserialized = ProtocolJson.Serialize(message);
        Assert.True(
            JsonNode.DeepEquals(JsonNode.Parse(json), JsonNode.Parse(reserialized)),
            $"round-trip mismatch for {fileName}:\noriginal: {json}\nreserialized: {reserialized}");

        // Second round trip must be stable too.
        var again = ProtocolJson.Serialize(ProtocolJson.Deserialize(reserialized));
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(reserialized), JsonNode.Parse(again)));
    }

    [Fact]
    public void All_ten_message_types_have_fixtures()
    {
        var types = Directory.GetFiles(FixturesDir, "*.json")
            .Select(file => JsonNode.Parse(File.ReadAllText(file))!["type"]!.GetValue<string>())
            .ToHashSet();
        var expected = new[]
        {
            "hello", "challenge", "auth", "welcome", "heartbeat",
            "heartbeat_ack", "command", "command_ack", "cancel", "error",
        };
        foreach (var type in expected)
        {
            Assert.Contains(type, types);
        }
    }

    [Theory]
    [InlineData("hello.json", new[] { "type", "protocol_version", "device_id", "software_version", "capabilities" })]
    [InlineData("challenge.json", new[] { "type", "nonce" })]
    [InlineData("auth.json", new[] { "type", "signature" })]
    [InlineData("welcome.json", new[] { "type", "session_id", "heartbeat_interval_s" })]
    [InlineData("heartbeat.json", new[] { "type", "seq" })]
    [InlineData("heartbeat_ack.json", new[] { "type", "seq" })]
    [InlineData("cancel.json", new[] { "type", "command_id" })]
    [InlineData("error.json", new[] { "type", "command_id", "error" })]
    public void Serialized_field_names_match_schema(string fileName, string[] expectedFields)
    {
        var json = File.ReadAllText(Path.Combine(FixturesDir, fileName));
        var reserialized = ProtocolJson.Serialize(ProtocolJson.Deserialize(json));
        var fields = JsonNode.Parse(reserialized)!.AsObject().Select(pair => pair.Key).ToHashSet();
        Assert.Equal(expectedFields.ToHashSet(), fields);
    }

    [Fact]
    public void Command_envelope_field_names_match_schema()
    {
        var json = File.ReadAllText(Path.Combine(FixturesDir, "command.json"));
        var reserialized = ProtocolJson.Serialize(ProtocolJson.Deserialize(json));
        var envelope = JsonNode.Parse(reserialized)!["command"]!.AsObject().Select(pair => pair.Key).ToHashSet();
        Assert.Equal(
            new HashSet<string> { "command_id", "idempotency_key", "capability", "payload", "expires_at", "trace_id" },
            envelope);
    }

    [Fact]
    public void Expires_at_accepts_utc_z_suffix()
    {
        var json = """
            {"type":"command","command":{"command_id":"3d2f1a9c-5b6e-47a1-9c3d-2e8f7a6b5c4d",
            "idempotency_key":"key-12345678","capability":"desktop.open_application","payload":{},
            "expires_at":"2026-08-31T12:00:00Z","trace_id":"t1"}}
            """;
        var message = (CommandMessage)ProtocolJson.Deserialize(json);
        Assert.Equal(new DateTimeOffset(2026, 8, 31, 12, 0, 0, TimeSpan.Zero), message.Command.ExpiresAt);
    }

    [Fact]
    public void Unknown_type_discriminator_is_rejected()
    {
        Assert.ThrowsAny<Exception>(() => ProtocolJson.Deserialize("""{"type":"bogus","x":1}"""));
    }

    [Fact]
    public void Missing_required_field_is_rejected()
    {
        // hello without device_id
        Assert.Throws<JsonException>(() => ProtocolJson.Deserialize(
            """{"type":"hello","protocol_version":1,"software_version":"0.1.0","capabilities":[]}"""));
    }

    [Fact]
    public void Type_discriminator_may_appear_out_of_order()
    {
        var message = ProtocolJson.Deserialize("""{"seq":3,"type":"heartbeat"}""");
        Assert.Equal(3, ((HeartbeatMessage)message).Seq);
    }

    [Fact]
    public void Validator_rejects_bad_command_envelopes()
    {
        var badKey = TestCommandWithKey("short");
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateCommandEnvelope(badKey));

        var badCapability = TestCommandWithKey("valid-key-123") with { Capability = "NotValid!" };
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateCommandEnvelope(badCapability));

        var good = TestCommandWithKey("valid-key-123");
        MessageValidator.ValidateCommandEnvelope(good); // no throw
    }

    private static CommandEnvelope TestCommandWithKey(string key) => new()
    {
        CommandId = Guid.NewGuid().ToString(),
        IdempotencyKey = key,
        Capability = "desktop.open_application",
        Payload = [],
        ExpiresAt = DateTimeOffset.UtcNow.AddMinutes(1),
        TraceId = "t",
    };
}

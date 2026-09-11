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
    // Two hello shapes, deliberately. ADR-0118 added `build_id`/`source_revision` as OPTIONAL
    // fields: an agent older than that still handshakes, so the old shape must round-trip with
    // no null keys added to it, and the new shape must actually carry them. One fixture would
    // only ever pin half of that.
    [InlineData("hello.json", new[] { "type", "protocol_version", "device_id", "software_version", "capabilities" })]
    [InlineData("hello_with_build_identity.json", new[] { "type", "protocol_version", "device_id", "software_version", "build_id", "source_revision", "capabilities" })]
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
    public void Every_field_the_agent_puts_in_its_hello_is_one_the_schema_declares()
    {
        // ADR-0118 addendum. The theory above pins what the agent SENDS against a list typed
        // into this file; nothing read the schema that calls itself authoritative, and
        // build_id reached the agent and Cloud Core without ever reaching it. This builds the
        // hello the way AgentConnection does, with every optional field set, and reads the
        // schema for the names.
        var hello = new HelloMessage
        {
            ProtocolVersion = ProtocolConstants.Version,
            DeviceId = Guid.NewGuid().ToString(),
            SoftwareVersion = "0.6.0",
            BuildId = "ac0ad30ca7a0c4a1",
            SourceRevision = "ef5cd12",
            Capabilities = new[] { "desktop.open_application" },
        };
        var sent = JsonNode.Parse(ProtocolJson.Serialize(hello))!.AsObject()
            .Select(pair => pair.Key)
            .ToList();

        var schema = JsonNode.Parse(File.ReadAllText(SchemaPath()))!;
        var declared = schema["$defs"]!["hello"]!["properties"]!.AsObject()
            .Select(pair => pair.Key)
            .ToHashSet();

        Assert.Contains("build_id", sent);
        Assert.Contains("source_revision", sent);
        foreach (var field in sent)
        {
            Assert.Contains(field, declared);
        }
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

    /// <summary>
    /// M18.3 (§6g): the heartbeat's <c>status</c> is OPTIONAL and additive, so both shapes must
    /// round-trip and the one without it must be byte-identical to the frame this agent has
    /// always sent. A heartbeat that grew a <c>"status": null</c> would be a new frame shape for
    /// every device on the old code path, which is not what additive means.
    /// </summary>
    [Fact]
    public void A_heartbeat_without_a_status_is_exactly_the_frame_it_always_was()
    {
        var plain = ProtocolJson.Serialize(new HeartbeatMessage { Seq = 4 });
        Assert.Equal("""{"type":"heartbeat","seq":4}""", plain);

        var withStatus = ProtocolJson.Serialize(new HeartbeatMessage
        {
            Seq = 5,
            Status = new JsonObject { ["display_state"] = "off", ["armed_alarms"] = 1 },
        });
        var fields = JsonNode.Parse(withStatus)!.AsObject().Select(pair => pair.Key).ToHashSet();
        Assert.Equal(new HashSet<string> { "type", "seq", "status" }, fields);

        var parsed = (HeartbeatMessage)ProtocolJson.Deserialize(withStatus);
        Assert.Equal(5, parsed.Seq);
        Assert.Equal("off", parsed.Status!["display_state"]!.GetValue<string>());
        Assert.Null(((HeartbeatMessage)ProtocolJson.Deserialize(plain)).Status);
    }

    /// <summary>
    /// Every key the fixture carries is one the schema's closed <c>deviceStatus</c> object
    /// declares, and every key the code can produce is one of those too. Two lists that must
    /// agree, checked against each other rather than against a comment.
    /// </summary>
    [Fact]
    public void The_heartbeat_status_fixture_uses_only_the_keys_the_schema_declares()
    {
        var fixture = JsonNode.Parse(File.ReadAllText(Path.Combine(FixturesDir, "heartbeat_status.json")))!;
        var status = fixture["status"]!.AsObject();

        Assert.Equal(
            HeartbeatStatus.Fields.OrderBy(f => f, StringComparer.Ordinal),
            status.Select(pair => pair.Key).OrderBy(k => k, StringComparer.Ordinal));

        var schema = JsonNode.Parse(File.ReadAllText(SchemaPath()))!;
        var declared = schema["$defs"]!["deviceStatus"]!["properties"]!.AsObject()
            .Select(pair => pair.Key)
            .OrderBy(k => k, StringComparer.Ordinal);
        Assert.Equal(HeartbeatStatus.Fields.OrderBy(f => f, StringComparer.Ordinal), declared);

        // Closed, on purpose: an unknown key inside status would fail the broker's validation
        // for the whole heartbeat, so nothing may be added here without the schema agreeing.
        Assert.False(schema["$defs"]!["deviceStatus"]!["additionalProperties"]!.GetValue<bool>());
        Assert.Equal("heartbeat", schema["$defs"]!["heartbeat"]!["properties"]!["type"]!["const"]!.GetValue<string>());
        Assert.Equal(
            "#/$defs/deviceStatus",
            schema["$defs"]!["heartbeat"]!["properties"]!["status"]!["$ref"]!.GetValue<string>());

        // ...and the protocol version does not move for an additive, optional field.
        Assert.Equal(1, ProtocolConstants.Version);
    }

    private static string SchemaPath()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "packages", "schemas")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        return Path.Combine(dir!.FullName, "packages", "schemas", "device-protocol.schema.json");
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

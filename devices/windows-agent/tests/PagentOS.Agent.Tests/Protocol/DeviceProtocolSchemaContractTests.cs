using System.Reflection;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests.Protocol;

/// <summary>
/// B02/B03: this device, held to the schema that calls itself authoritative.
/// </summary>
/// <remarks>
/// <para>
/// <c>packages/schemas/device-protocol.schema.json</c> is cited in this project's own comments
/// as the authority for the frames - and until 2026-09-12 nothing on this side was compared
/// against it. The Cloud Core half had a test
/// (<c>test_hello_knows_exactly_the_fields_the_schema_declares</c>); the device half had a
/// sentence. So ADR-0118 could add <c>build_id</c> to the agent and to Cloud Core's model and
/// not to the schema, and the only thing that noticed was a person reading three files.
/// </para>
/// <para>
/// The hello frame is the one that matters most here: it is what the staged updater compares,
/// and it is the frame that actually drifted.
/// </para>
/// </remarks>
public sealed class DeviceProtocolSchemaContractTests
{
    private static JsonObject Schema()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "schemas", "device-protocol.schema.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "schemas", "device-protocol.schema.json");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(path))!;
    }

    private static JsonObject Definition(string name)
        => (JsonObject)Schema()["$defs"]![name]!;

    private static (HashSet<string> All, HashSet<string> Required) WireNames<T>()
    {
        var all = new HashSet<string>(StringComparer.Ordinal);
        var required = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in typeof(T).GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            var attribute = property.GetCustomAttribute<JsonPropertyNameAttribute>();
            if (attribute is null)
            {
                continue;
            }

            all.Add(attribute.Name);
            // A `required` member is one the wire must carry; an optional one is declared
            // nullable and written only when set.
            if (property.GetCustomAttributes().Any(a => a.GetType().Name == "RequiredMemberAttribute"))
            {
                required.Add(attribute.Name);
            }
        }

        return (all, required);
    }

    [Fact]
    public void The_hello_frame_this_device_sends_is_the_one_the_schema_declares()
    {
        var hello = Definition("hello");
        var declared = ((JsonObject)hello["properties"]!).Select(pair => pair.Key).ToHashSet(StringComparer.Ordinal);
        var (mine, _) = WireNames<HelloMessage>();

        // `type` is the discriminator, carried by the base record's polymorphic attribute
        // rather than by a property of its own.
        mine.Add("type");

        Assert.Equal(declared.OrderBy(n => n, StringComparer.Ordinal), mine.OrderBy(n => n, StringComparer.Ordinal));
    }

    [Fact]
    public void Every_field_the_schema_requires_is_one_this_device_always_sends()
    {
        var hello = Definition("hello");
        var schemaRequired = ((JsonArray)hello["required"]!)
            .Select(node => node!.GetValue<string>())
            .Where(name => name != "type")
            .ToHashSet(StringComparer.Ordinal);
        var (_, mineRequired) = WireNames<HelloMessage>();

        var missing = schemaRequired.Except(mineRequired).OrderBy(n => n, StringComparer.Ordinal).ToArray();
        Assert.True(missing.Length == 0, $"the schema requires {string.Join(", ", missing)} and this device may omit them");
    }

    [Fact]
    public void The_build_identity_added_by_adr_0118_is_in_the_schema_too()
    {
        // The exact drift: the field was added to the agent and to Cloud Core's model, and
        // the "authoritative" schema was not told for a day.
        var properties = (JsonObject)Definition("hello")["properties"]!;
        Assert.True(properties.ContainsKey("build_id"), "build_id is on the wire and not in the schema");
        Assert.True(properties.ContainsKey("source_revision"));
    }

    [Fact]
    public void The_hello_frame_stays_closed_so_an_emitter_cannot_invent_a_field()
    {
        var hello = Definition("hello");
        Assert.False(hello["additionalProperties"]!.GetValue<bool>());
    }
}

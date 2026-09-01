using PagentOS.Agent.Core.Enrollment;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests;

public class EnrollmentTests
{
    [Fact]
    public async Task Enroll_posts_contract_fields_and_returns_device_id()
    {
        await using var broker = await FakeBroker.StartAsync();
        using var identity = DeviceIdentity.LoadOrCreate(Path.Combine(TestPaths.NewTempDir(), "device.key"), developerRun: true);
        using var httpClient = new HttpClient();
        var client = new EnrollmentClient(httpClient);

        var deviceId = await client.EnrollAsync(
            broker.RestBase,
            token: "one-time-token-123",
            name: "test-machine",
            identity.PublicKeySpkiBase64,
            AgentCapabilities.All);

        Assert.True(Guid.TryParse(deviceId, out _));

        var request = broker.LastEnrollRequest;
        Assert.NotNull(request);
        Assert.Equal("one-time-token-123", request!["token"]!.GetValue<string>());
        Assert.Equal("test-machine", request["name"]!.GetValue<string>());
        Assert.Equal("windows", request["platform"]!.GetValue<string>());
        Assert.Equal(identity.PublicKeySpkiBase64, request["public_key_spki_b64"]!.GetValue<string>());
        Assert.Equal(
            AgentCapabilities.All,
            request["capabilities"]!.AsArray().Select(node => node!.GetValue<string>()).ToList());
    }

    [Fact]
    public async Task Enrollment_failure_surfaces_as_typed_exception()
    {
        using var httpClient = new HttpClient();
        var client = new EnrollmentClient(httpClient);
        // Nothing listens on this port.
        var deadPort = FakeBroker.GetFreePort();
        await Assert.ThrowsAnyAsync<Exception>(() => client.EnrollAsync(
            new Uri($"http://127.0.0.1:{deadPort}"),
            "token-x",
            "name-x",
            "AAAA",
            AgentCapabilities.All));
    }
}

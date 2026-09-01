using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Enrollment;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Logging;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        var verb = args.Length > 0 ? args[0].ToLowerInvariant() : "run";
        try
        {
            return verb switch
            {
                "enroll" => await EnrollAsync(args.Skip(1).ToArray()).ConfigureAwait(false),
                "run" => await RunAsync(args.Skip(1).ToArray()).ConfigureAwait(false),
                _ => PrintUsage(),
            };
        }
        catch (Exception ex)
        {
            await Console.Error.WriteLineAsync($"fatal: {ex.Message}").ConfigureAwait(false);
            return 1;
        }
    }

    private static int PrintUsage()
    {
        Console.Error.WriteLine("usage:");
        Console.Error.WriteLine("  PagentOS.DeviceService enroll --broker-url <http-base> --token <one-time-token> --name <device-name>");
        Console.Error.WriteLine("  PagentOS.DeviceService run");
        return 2;
    }

    private static IConfigurationRoot BuildConfiguration()
        => new ConfigurationBuilder()
            .SetBasePath(AppContext.BaseDirectory)
            .AddJsonFile("appsettings.json", optional: true)
            .AddEnvironmentVariables("PAGENTOS_AGENT_")
            .Build();

    private static async Task<int> EnrollAsync(string[] args)
    {
        string? brokerUrl = null;
        string? token = null;
        string? name = null;
        for (var i = 0; i < args.Length - 1; i++)
        {
            switch (args[i])
            {
                case "--broker-url":
                    brokerUrl = args[++i];
                    break;
                case "--token":
                    token = args[++i];
                    break;
                case "--name":
                    name = args[++i];
                    break;
                default:
                    break;
            }
        }

        var options = AgentServiceOptions.FromConfiguration(BuildConfiguration());
        brokerUrl ??= options.BrokerRestUrl;
        if (string.IsNullOrWhiteSpace(token) || string.IsNullOrWhiteSpace(name))
        {
            return PrintUsage();
        }

        Directory.CreateDirectory(options.DataDir);
        using var identity = DeviceIdentity.LoadOrCreate(options.KeyFilePath);
        using var httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        var client = new EnrollmentClient(httpClient);
        var deviceId = await client.EnrollAsync(
            new Uri(brokerUrl),
            token,
            name,
            identity.PublicKeySpkiBase64,
            AgentCapabilities.All).ConfigureAwait(false);

        var state = new AgentState
        {
            DeviceId = deviceId,
            Name = name,
            BrokerRestUrl = brokerUrl,
            EnrolledAt = DateTimeOffset.UtcNow,
        };
        state.Save(options.StateFilePath);
        new AuditLog(options.AuditLogPath).Write("enrolled", deviceId: deviceId, detail: $"name={name}");
        Console.WriteLine($"enrolled device_id={deviceId}");
        Console.WriteLine($"state: {options.StateFilePath}");
        return 0;
    }

    /// <summary>
    /// Who is allowed to be the companion. Configured explicitly in a service install, where
    /// the service account and the owner account differ; falls back to this process's own SID
    /// for a developer run, which is the same trust as before and no looser. The fallback is
    /// logged at startup so a service install that forgot to set CompanionSid is visible
    /// rather than silently self-authorizing.
    /// </summary>
    private static CompanionAdmissionPolicy BuildAdmissionPolicy(AgentServiceOptions options)
    {
        var sid = options.CompanionSid;
        if (string.IsNullOrWhiteSpace(sid))
        {
            sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value
                  ?? throw new InvalidOperationException(
                      "CompanionSid is not configured and this process's own SID could not be resolved");
            Console.Error.WriteLine(
                "warning: CompanionSid is not configured; falling back to this process's SID. " +
                "A Windows Service install must set PAGENTOS_AGENT_CompanionSid to the owner's SID.");
        }

        return new CompanionAdmissionPolicy(sid, options.CompanionImagePath, options.CompanionSessionId);
    }

    private static async Task<int> RunAsync(string[] args)
    {
        var settings = new HostApplicationBuilderSettings
        {
            Args = args,
            ContentRootPath = AppContext.BaseDirectory,
        };
        var builder = Host.CreateApplicationBuilder(settings);
        builder.Configuration.AddEnvironmentVariables("PAGENTOS_AGENT_");
        var options = AgentServiceOptions.FromConfiguration(builder.Configuration);

        var state = AgentState.Load(options.StateFilePath);
        if (state is null)
        {
            await Console.Error.WriteLineAsync(
                $"device is not enrolled (no {options.StateFilePath}); run the 'enroll' verb first").ConfigureAwait(false);
            return 2;
        }

        Directory.CreateDirectory(options.DataDir);
        builder.Logging.AddProvider(new FileLoggerProvider(options.LogFilePath));
        builder.Services.AddWindowsService(windowsOptions => windowsOptions.ServiceName = "PagentOSDeviceAgent");

        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton(_ => DeviceIdentity.LoadOrCreate(options.KeyFilePath));
        builder.Services.AddSingleton(new AuditLog(options.AuditLogPath));
        builder.Services.AddSingleton(new IdempotencyStore(options.IdempotencyStorePath));
        var admission = BuildAdmissionPolicy(options);
        builder.Services.AddSingleton(admission);
        builder.Services.AddSingleton(provider => new CompanionPipeServer(
            options.PipeName,
            admission,
            new WindowsPipePeerInspector(),
            provider.GetRequiredService<ILogger<CompanionPipeServer>>(),
            provider.GetRequiredService<AuditLog>()));
        builder.Services.AddSingleton<ICapabilityExecutor>(provider =>
            new InteractiveCapabilityExecutor(provider.GetRequiredService<CompanionPipeServer>()));
        builder.Services.AddSingleton(provider => new CommandDispatcher(
            provider.GetRequiredService<IdempotencyStore>(),
            provider.GetRequiredService<ICapabilityExecutor>(),
            provider.GetRequiredService<AuditLog>(),
            provider.GetRequiredService<ILogger<CommandDispatcher>>()));
        builder.Services.AddSingleton(provider => new AgentConnection(
            new AgentConnectionOptions
            {
                BrokerWsUrl = new Uri(options.BrokerWsUrl),
                DeviceId = state.DeviceId,
                BackoffBaseSeconds = options.BackoffBaseSeconds,
                BackoffMaxSeconds = options.BackoffMaxSeconds,
                HeartbeatIntervalOverrideS = options.HeartbeatIntervalOverrideS,
            },
            provider.GetRequiredService<DeviceIdentity>(),
            provider.GetRequiredService<CommandDispatcher>(),
            provider.GetRequiredService<AuditLog>(),
            provider.GetRequiredService<ILogger<AgentConnection>>()));

        builder.Services.AddHostedService(provider => provider.GetRequiredService<CompanionPipeServer>());
        builder.Services.AddHostedService(provider => new AgentWorker(
            provider.GetRequiredService<AgentConnection>(),
            provider.GetRequiredService<ILogger<AgentWorker>>()));

        using var host = builder.Build();
        var logger = host.Services.GetRequiredService<ILogger<AgentWorker>>();
        logger.LogInformation(
            "starting device service: device_id={DeviceId} broker={Broker} data_dir={DataDir} pipe={Pipe}",
            state.DeviceId,
            options.BrokerWsUrl,
            options.DataDir,
            options.PipeName);
        logger.LogInformation(
            "companion admission: sid={Sid} session={Session} binary={Binary}",
            admission.AuthorizedSid,
            admission.ExpectedSessionId?.ToString() ?? "any interactive",
            admission.ExpectedImagePath ?? "not pinned");
        await host.RunAsync().ConfigureAwait(false);
        return 0;
    }
}

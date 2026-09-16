using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Camera;
using PagentOS.SessionCompanion.Notify;
using Xunit;

namespace PagentOS.Agent.Tests.Camera;

/// <summary>
/// B48: the camera path through the real application objects - the manifest, the heartbeat
/// projection the Session-0 service applies, the schema that calls itself authoritative, the
/// pipe, and the companion's own composition - plus the structural half of "no frame leaves".
/// </summary>
public sealed class CameraWiringTests
{
    // ================================================================ manifest

    [Fact]
    public void Camera_mode_is_an_always_advertised_interactive_name_appended_to_the_ambient_group()
    {
        Assert.Equal("desktop.camera_mode", AgentCapabilities.DesktopCameraMode);
        Assert.Equal(AgentCapabilities.DesktopCameraMode, AgentCapabilities.Ambient[^1]);
        Assert.Contains(AgentCapabilities.DesktopCameraMode, AgentCapabilities.Compose(browserEnabled: false));
        Assert.True(AgentCapabilities.IsInteractive(AgentCapabilities.DesktopCameraMode));
        Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopCameraMode));
    }

    // ================================================================ the service's projection

    [Fact]
    public void The_service_keeps_the_camera_pair_and_strips_anything_that_is_not_a_known_scalar()
    {
        var raw = new JsonObject
        {
            ["input_idle_s"] = 12.5,
            ["camera"] = new JsonObject
            {
                ["mode"] = "periodic",
                ["state"] = "idle",
                ["interval_s"] = 60,
                ["indicator"] = "armed",
                ["last_check_at"] = "2026-09-16T23:00:00.000Z",
                ["error"] = null,
                ["preview"] = "AAAA",
            },
            ["presence"] = new JsonObject
            {
                ["person_present"] = true,
                ["presence_confidence"] = 0.9,
                ["activity_level"] = "none",
                ["posture"] = "resting",
                ["awake_state"] = "resting",
                ["observed_at"] = "2026-09-16T23:00:00.000Z",
                ["source"] = "camera",
                ["frame"] = "iVBORw0KGgo",
            },
        };

        var projected = HeartbeatStatus.Project(raw)!;

        var camera = (JsonObject)projected["camera"]!;
        Assert.Equal(HeartbeatStatus.CameraFields.OrderBy(k => k, StringComparer.Ordinal), camera.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        var presence = (JsonObject)projected["presence"]!;
        Assert.Equal(HeartbeatStatus.PresenceFields.OrderBy(k => k, StringComparer.Ordinal), presence.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        Assert.False(presence.ContainsKey("frame"));
        Assert.False(camera.ContainsKey("preview"));
    }

    [Theory]
    [InlineData("object")]
    [InlineData("array")]
    [InlineData("long")]
    public void A_presence_carrying_anything_but_short_scalars_is_dropped_whole(string smuggled)
    {
        JsonNode payload = smuggled switch
        {
            "object" => new JsonObject { ["bytes"] = "x" },
            "array" => new JsonArray(1, 2, 3),
            _ => new string('A', 4096),
        };
        var raw = new JsonObject
        {
            ["presence"] = new JsonObject
            {
                ["person_present"] = true,
                ["presence_confidence"] = 0.9,
                ["activity_level"] = "none",
                ["posture"] = payload,
                ["awake_state"] = "resting",
                ["observed_at"] = "2026-09-16T23:00:00.000Z",
                ["source"] = "camera",
            },
        };

        var projected = HeartbeatStatus.Project(raw)!;

        Assert.True(projected.ContainsKey("presence"));
        Assert.Null(projected["presence"]);
    }

    // ================================================================ the schema

    private static JsonObject StatusSchema()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "schemas", "device-protocol.schema.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var schema = JsonNode.Parse(File.ReadAllText(Path.Combine(directory!.FullName, "packages", "schemas", "device-protocol.schema.json")))!;
        return (JsonObject)schema["$defs"]!["deviceStatus"]!;
    }

    private static IEnumerable<string> Keys(JsonNode? node)
        => ((JsonObject)node!).Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal);

    private static IEnumerable<string> Enum(JsonNode? node)
        => ((JsonArray)node!["enum"]!).Select(v => v!.GetValue<string>()).OrderBy(k => k, StringComparer.Ordinal);

    [Fact]
    public void The_status_the_service_sends_is_the_one_the_schema_declares_nested_objects_included()
    {
        var status = StatusSchema();
        Assert.Equal(Keys(status["properties"]), HeartbeatStatus.Fields.OrderBy(k => k, StringComparer.Ordinal));

        var camera = status["properties"]!["camera"]!;
        Assert.False(camera["additionalProperties"]!.GetValue<bool>());
        Assert.Equal(Keys(camera["properties"]), HeartbeatStatus.CameraFields.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(Enum(camera["properties"]!["mode"]), CameraModes.All.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(Enum(camera["properties"]!["state"]), CameraStates.All.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(Enum(camera["properties"]!["indicator"]), new[] { "armed", "hidden", "open" });

        var presence = status["properties"]!["presence"]!;
        Assert.False(presence["additionalProperties"]!.GetValue<bool>());
        Assert.Equal(Keys(presence["properties"]), HeartbeatStatus.PresenceFields.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(
            ((JsonArray)presence["required"]!).Select(v => v!.GetValue<string>()).OrderBy(k => k, StringComparer.Ordinal),
            HeartbeatStatus.PresenceFields.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal("camera", presence["properties"]!["source"]!["const"]!.GetValue<string>());
    }

    // ================================================================ the whole device half

    [Fact]
    public async Task Service_pipe_companion_the_mode_command_and_the_heartbeat_carry_the_camera_end_to_end()
    {
        var indicator = new RecordingCameraIndicator();
        var source = new FakeCameraSource(indicator);
        using var monitor = new CameraPresenceMonitor(
            source,
            indicator,
            NullLogger.Instance,
            new CameraOptions { FrameSpacing = TimeSpan.Zero },
            input: new FakeIdle(TimeSpan.FromMinutes(30)));
        var reporter = new ActivityStatusReporter(
            new FakeIdle(TimeSpan.FromMinutes(30)), UnknownDisplayStateObserver.Instance, () => null, camera: monitor);

        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        var companion = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoopOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            activityStatus: reporter,
            camera: monitor);
        using var cts = new CancellationTokenSource();
        var companionTask = Task.Run(() => companion.RunAsync(cts.Token));
        var loop = monitor.RunAsync(cts.Token);
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
                await Task.Delay(20);
            }

            Assert.Contains(AgentCapabilities.DesktopCameraMode, server.CompanionCapabilities!);

            var heartbeat = new CompanionHeartbeatStatusProvider(server);
            var before = (await heartbeat.GetStatusAsync(CancellationToken.None))!;
            Assert.Equal("off", before["camera"]!["mode"]!.GetValue<string>());
            Assert.Null(before["presence"]);

            // The executor Cloud Core's command goes through, exactly as in production.
            var executor = new InteractiveCapabilityExecutor(server, browserEnabled: false);
            var answer = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopCameraMode,
                new JsonObject { ["mode"] = "periodic", ["reason"] = "owner_policy" },
                TimeSpan.FromSeconds(10),
                CancellationToken.None);
            Assert.True(answer!["changed"]!.GetValue<bool>());
            Assert.NotNull(executor);

            deadline = DateTime.UtcNow.AddSeconds(10);
            JsonObject after;
            while (true)
            {
                after = (await heartbeat.GetStatusAsync(CancellationToken.None))!;
                if (after["presence"] is JsonObject)
                {
                    break;
                }

                Assert.True(DateTime.UtcNow < deadline, "no observation reached the heartbeat");
                await Task.Delay(20);
            }

            Assert.Equal("periodic", after["camera"]!["mode"]!.GetValue<string>());
            Assert.Equal("camera", after["presence"]!["source"]!.GetValue<string>());
            Assert.True(after["presence"]!["person_present"]!.GetValue<bool>());
            Assert.Equal(1, source.Opens);
            Assert.Equal(0, source.OpenSessions);

            var bad = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopCameraMode, new JsonObject { ["mode"] = "record" }, TimeSpan.FromSeconds(10), CancellationToken.None));
            Assert.Equal(ErrorClasses.ValidationError, bad.ErrorClass);
        }
        finally
        {
            await cts.CancelAsync();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            try { await loop.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }

        Assert.Equal(0, source.OpenSessions);
    }

    [Fact]
    public async Task A_restarted_companion_with_a_remembered_veto_refuses_the_mode_over_the_pipe_and_its_first_heartbeat_says_vetoed()
    {
        // B48 security review (HIGH), through the production objects: the veto file in the
        // owner's profile (here a temp copy), a fresh monitor, the real pipe and projection.
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-veto-" + Guid.NewGuid().ToString("N"));
        var store = new FileCameraVetoStore(Path.Combine(dir, "camera-veto.json"));
        store.Save(true);
        var indicator = new RecordingCameraIndicator();
        var source = new FakeCameraSource(indicator);
        using var monitor = new CameraPresenceMonitor(
            source, indicator, NullLogger.Instance, new CameraOptions { FrameSpacing = TimeSpan.Zero }, vetoStore: store);
        var reporter = new ActivityStatusReporter(new FakeIdle(null), UnknownDisplayStateObserver.Instance, () => null, camera: monitor);

        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        var companion = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoopOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            activityStatus: reporter,
            camera: monitor);
        using var cts = new CancellationTokenSource();
        var companionTask = Task.Run(() => companion.RunAsync(cts.Token));
        var loop = monitor.RunAsync(cts.Token);
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
                await Task.Delay(20);
            }

            var first = (await new CompanionHeartbeatStatusProvider(server).GetStatusAsync(CancellationToken.None))!;
            Assert.Equal("vetoed", first["camera"]!["state"]!.GetValue<string>());
            Assert.Null(first["presence"]);

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopCameraMode,
                new JsonObject { ["mode"] = "continuous", ["reason"] = "owner_policy" },
                TimeSpan.FromSeconds(10),
                CancellationToken.None));
            Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
            Assert.False(ex.Retryable);
            await Task.Delay(200);
            Assert.Equal(0, source.Opens);
        }
        finally
        {
            await cts.CancelAsync();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            try { await loop.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public async Task A_companion_built_without_a_camera_answers_capability_missing_and_its_heartbeat_has_no_camera_keys()
    {
        var reporter = new ActivityStatusReporter(new FakeIdle(null), UnknownDisplayStateObserver.Instance, () => null);
        var status = reporter.Compose();
        Assert.False(status.ContainsKey(HeartbeatStatus.Camera));
        Assert.False(status.ContainsKey(HeartbeatStatus.Presence));

        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        var companion = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoopOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()));
        using var cts = new CancellationTokenSource();
        var companionTask = Task.Run(() => companion.RunAsync(cts.Token));
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
                await Task.Delay(20);
            }

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopCameraMode, [], TimeSpan.FromSeconds(10), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        }
        finally
        {
            await cts.CancelAsync();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }

    // ================================================================ the companion's composition

    [Fact]
    public void The_shipped_companion_hands_the_runtime_its_camera_its_toast_and_starts_the_camera_loop()
    {
        // B11 req 369, found in B48: desktop.notify was advertised while Program never built a
        // NotifyCapabilities, so every toast answered capability_missing. Composition is a claim
        // about which object reaches which constructor, read here from the one place it is made.
        var source = CompanionSources.Read("Program.cs");
        var call = source[source.IndexOf("var runtime = new CompanionRuntime(", StringComparison.Ordinal)..];
        call = call[..call.IndexOf(");", StringComparison.Ordinal)];

        Assert.Contains("notify: notify", call, StringComparison.Ordinal);
        Assert.Contains("camera: camera", call, StringComparison.Ordinal);
        Assert.Contains("activityStatus: activityStatus", call, StringComparison.Ordinal);
        Assert.Contains("camera.RunAsync(cts.Token)", source, StringComparison.Ordinal);
        Assert.Contains("var notify = BuildNotify(", source, StringComparison.Ordinal);

        // B48 security review: both shipped camera monitors remember the owner's veto in the profile.
        var build = source[source.IndexOf("public static Camera.CameraPresenceMonitor BuildCamera(", StringComparison.Ordinal)..];
        build = build[..build.IndexOf("public static Notify.NotifyCapabilities? BuildNotify(", StringComparison.Ordinal)];
        Assert.Equal(2, build.Split("vetoStore: new Camera.FileCameraVetoStore(Camera.FileCameraVetoStore.DefaultPath())").Length - 1);
        Assert.Contains("alarmArms,\n            camera);", source.Replace("\r\n", "\n", StringComparison.Ordinal), StringComparison.Ordinal);
    }

    [Fact]
    public void The_toast_factory_the_companion_uses_builds_a_capability_that_actually_shows()
    {
        var sink = new CountingSink();
        var notify = SessionCompanion.Program.BuildNotify(sink, NullLoggerFactory.Instance);

        Assert.NotNull(notify);
        var result = notify!.Notify(new JsonObject
        {
            ["notification_id"] = Guid.NewGuid().ToString(),
            ["title"] = "Test",
            ["body"] = "Gövde",
        });
        Assert.True(result["shown"]!.GetValue<bool>());
        Assert.Equal(1, sink.Shown);
        Assert.Null(SessionCompanion.Program.BuildNotify(null, NullLoggerFactory.Instance));
    }

    // ================================================================ the structural half of 328/329

    [Fact]
    public void Nothing_in_the_camera_folder_can_write_encode_upload_or_record_a_frame()
    {
        var files = CompanionSources.AllFiles()
            .Where(p => p.Contains($"{Path.DirectorySeparatorChar}Camera{Path.DirectorySeparatorChar}", StringComparison.Ordinal))
            .ToList();
        Assert.True(files.Count >= 4, $"expected the camera folder to be several files, found {files.Count}");

        string[] forbidden =
        [
            // Files.
            "File.", "FileStream", "StreamWriter", "BinaryWriter", "StorageFile", "StorageFolder", "Path.Combine",
            // Encoders and anything that turns pixels into a transportable picture.
            "BitmapEncoder", "ToBase64", "Base64", "JpegEncoder", "PngEncoder", "ImageEncodingProperties",
            // Recording and photo capture - the path pulls frames and nothing else.
            "StartRecord", "PrepareLowLag", "CapturePhoto", "LowLagPhoto", "MediaFileSink", "StartPreviewToCustomSink",
            // The network.
            "HttpClient", "WebSocket", "Socket", "WebRequest",
        ];

        foreach (var file in files)
        {
            var text = File.ReadAllText(file);
            foreach (var token in forbidden)
            {
                Assert.False(
                    text.Contains(token, StringComparison.Ordinal),
                    $"{Path.GetFileName(file)} names '{token}'; a camera frame is analysed in memory and never leaves it");
            }

            // No log line and no audit row names a frame, its pixels or its faces.
            foreach (var line in text.Split('\n'))
            {
                var writes = line.Contains(".Log", StringComparison.Ordinal) || line.Contains("audit", StringComparison.OrdinalIgnoreCase) && line.Contains(".Write(", StringComparison.Ordinal);
                if (!writes)
                {
                    continue;
                }

                foreach (var content in new[] { "Luma", "Faces", "frame", "bitmap", "pixels" })
                {
                    Assert.False(
                        line.Contains(content, StringComparison.OrdinalIgnoreCase),
                        $"{Path.GetFileName(file)} logs '{content}': {line.Trim()}");
                }
            }
        }
    }

    [Fact]
    public async Task The_on_device_face_pass_runs_on_a_synthetic_bitmap_without_any_camera()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041) || !WindowsFaceAnalyzer.IsSupported)
        {
            // Windows Server without Media Foundation: the face pass is honestly unavailable
            // (the monitor then reports face_detector_unsupported); nothing here to run.
            return;
        }

        var analyzer = await WindowsFaceAnalyzer.CreateAsync();
        using var bitmap = new Windows.Graphics.Imaging.SoftwareBitmap(
            Windows.Graphics.Imaging.BitmapPixelFormat.Bgra8, 320, 240, Windows.Graphics.Imaging.BitmapAlphaMode.Premultiplied);
        var pixels = new byte[320 * 240 * 4];
        for (var i = 0; i < pixels.Length; i += 4)
        {
            // An opaque, flat grey: premultiplied alpha must be 255 or the grey is scaled.
            pixels[i] = pixels[i + 1] = pixels[i + 2] = 90;
            pixels[i + 3] = 255;
        }
        bitmap.CopyFromBuffer(pixels.AsBuffer());

        using var frame = await analyzer.AnalyseAsync(bitmap, DateTimeOffset.UnixEpoch);

        Assert.Empty(frame.Faces);
        Assert.Equal(WindowsFaceAnalyzer.LumaWidth, frame.Width);
        Assert.Equal(WindowsFaceAnalyzer.LumaHeight, frame.Height);
        Assert.All(frame.Luma.ToArray(), b => Assert.InRange(b, (byte)80, (byte)100));
    }

    [Fact]
    public void Downsampling_averages_blocks_and_honours_the_stride()
    {
        // 160x120 plane, stride 164 (4 padding bytes per row set to 255 that must be ignored).
        const int width = 160, height = 120, stride = 164;
        var plane = new byte[stride * height];
        for (var y = 0; y < height; y++)
        {
            for (var x = 0; x < stride; x++)
            {
                plane[(y * stride) + x] = x >= width ? (byte)255 : (byte)(x < width / 2 ? 10 : 200);
            }
        }

        var small = WindowsFaceAnalyzer.Downsample(plane, width, height, stride);

        Assert.Equal(WindowsFaceAnalyzer.LumaWidth * WindowsFaceAnalyzer.LumaHeight, small.Length);
        Assert.Equal(10, small[0]);
        Assert.Equal(200, small[WindowsFaceAnalyzer.LumaWidth - 1]);
    }

    private sealed class NoopOpener : IFileOpener
    {
        public void Open(string path)
        {
        }
    }

    private sealed class CountingSink : IToastSink
    {
        public int Shown { get; private set; }

        public ToastOutcome Show(ToastRequest request)
        {
            Shown++;
            return ToastOutcome.Ok();
        }
    }
}

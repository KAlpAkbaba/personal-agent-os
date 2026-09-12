using System.Diagnostics;
using System.Globalization;
using System.Runtime.Versioning;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Scenes;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>
/// The projects family's dispatch (M23_APP_FACTORY_SPEC.md §3, ADR-0086, DEVICE_PROTOCOL.md
/// §6l): one method per name in <see cref="ProjectCapabilityNames"/>, under the same gate,
/// budget rule, audit shape and forbidden-key scan as the operator and documents families.
/// The order of checks on every name: the payload's shape (<c>validation_error</c>); the
/// project — a <c>project_id</c> is looked up in the Projects root's markers, an unknown one is
/// <c>not_found</c>; the manifest re-parsed from the marker THIS companion wrote — a command
/// that is not on the runtime allowlist is <c>permission_denied</c> before any process exists,
/// whoever edited the marker; only then a process, in its job. Nothing here deletes anything.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class ProjectCapabilities : IDisposable
{
    public const string AuditRequestEvent = "project_request";
    public const int MaxCommandKeyChars = 32;

    private readonly OperatorOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;

    public ProjectCapabilities(OperatorOptions options, ILogger logger, AuditLog? audit = null, ProjectRunner? runner = null, ProjectRoots? roots = null, ProjectRoots? roots3d = null, ProjectRoots? rootsNative = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        Roots = roots ?? new ProjectRoots(options);
        Roots3d = roots3d ?? new ProjectRoots(options.Scene3dOptions());
        RootsNative = rootsNative ?? new ProjectRoots(options.NativeOptions());
        Runner = runner ?? new ProjectRunner(logger);
    }

    /// <summary>The same gate as the operator (<c>PAGENTOS_AGENT_OperatorEnabled</c>).</summary>
    public bool Enabled => _options.Enabled;

    public ProjectRoots Roots { get; }

    /// <summary>M25: the same rules, one directory lower — the 3D root, where and only where the two editors run.</summary>
    public ProjectRoots Roots3d { get; }

    /// <summary>M28: the same rules again — the native root, where and only where a compiler runs.</summary>
    public ProjectRoots RootsNative { get; }

    public ProjectRunner Runner { get; }

    /// <summary>The Projects root as configured (for the log).</summary>
    public string? ProjectsRoot => _options.EffectiveProjectsRoot;

    /// <summary>M25: the 3D root as configured (for the log).</summary>
    public string? ProjectsRoot3d => _options.EffectiveProjectsRoot3d;

    /// <summary>M28: the native root as configured (for the log).</summary>
    public string? ProjectsRootNative => _options.EffectiveProjectsRootNative;

    // ================================================================== entry point

    public async Task<JsonObject> ExecuteAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        if (!ProjectCapabilityNames.IsMember(capability) && !SceneCapabilityNames.IsMember(capability))
        {
            throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' is not a projects or scenes capability", retryable: false);
        }

        var stopwatch = Stopwatch.StartNew();
        using var budgetCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        if (budget > TimeSpan.Zero)
        {
            budgetCts.CancelAfter(budget);
        }

        try
        {
            var result = await DispatchAsync(capability, payload, budget, budgetCts.Token).ConfigureAwait(false);
            var forbidden = BrowserWorkerHost.FindForbiddenKey(result, path: "result");
            if (forbidden is not null)
            {
                throw new CapabilityException(
                    ErrorClasses.SecurityScopeError,
                    $"projects result for {capability} carries a forbidden key ({forbidden}); it does not leave the companion",
                    retryable: false);
            }

            Record(capability, "ok", stopwatch.ElapsedMilliseconds, null);
            return result;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            Record(capability, ErrorClasses.Cancelled, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Cancelled, $"{capability} was cancelled after {stopwatch.ElapsedMilliseconds} ms", retryable: true);
        }
        catch (OperationCanceledException)
        {
            Record(capability, ErrorClasses.Timeout, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Timeout, $"{capability} exceeded its {budget.TotalSeconds:F1} s budget", retryable: true);
        }
        catch (CapabilityException ex)
        {
            Record(capability, ex.ErrorClass, stopwatch.ElapsedMilliseconds, ex.Retryable);
            throw;
        }
        catch (UnauthorizedAccessException ex)
        {
            Record(capability, ErrorClasses.PermissionDenied, stopwatch.ElapsedMilliseconds, false);
            throw new CapabilityException(ErrorClasses.PermissionDenied, $"the file system refused the write: {ex.Message}", retryable: false);
        }
        catch (IOException ex)
        {
            Record(capability, ErrorClasses.DependencyUnavailable, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the project folder could not be written: {ex.Message}", retryable: true);
        }
    }

    private Task<JsonObject> DispatchAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
        => capability switch
        {
            ProjectCapabilityNames.ProjectScaffold => Task.Run(() => Scaffold(payload, cancellationToken), CancellationToken.None),
            ProjectCapabilityNames.ProjectRun => RunAsync(payload, budget, cancellationToken),
            ProjectCapabilityNames.ProjectStatus => Task.Run(() => Status(payload), CancellationToken.None),
            ProjectCapabilityNames.ProjectStop => StopAsync(payload, cancellationToken),
            ProjectCapabilityNames.ProjectTest => TestAsync(payload, budget, cancellationToken),
            SceneCapabilityNames.Inspect => Task.Run(() => Inspect(payload), CancellationToken.None),
            _ => throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' has no dispatch entry", retryable: false),
        };

    // ================================================================== project.scaffold

    private JsonObject Scaffold(JsonObject payload, CancellationToken cancellationToken)
    {
        var request = ProjectScaffold.Parse(payload);
        // M25/M28: the payload's `root` chooses between the three roots this companion HOLDS,
        // never a path — a 3D project is written under the 3D root, a native project under the
        // native root and a web project under the Projects root, and each is parsed against its
        // own part of the runtime allowlist.
        var roots = RootsFor(request.Scope);
        if (request.Scope == ProjectScope.Web && Is3dRootName(request.Slug))
        {
            // The 3D root lives at <Projects root>\3d by default, so a web project called "3d"
            // would be asking to become it. Refused by name, before anything is resolved.
            throw DocumentErrors.Invalid($"payload.slug '{request.Slug}' is the 3D root's own folder; scaffold with root='{SceneCapabilityNames.Root3dFolderName}' to make a 3D project");
        }

        if (request.Scope == ProjectScope.Web && IsNativeRootName(request.Slug))
        {
            // M28: and the same for the native root, one folder over.
            throw DocumentErrors.Invalid($"payload.slug '{request.Slug}' is the native root's own folder; scaffold with root='{NativeCapabilityNames.RootNativeFolderName}' to make a native project");
        }

        var outcome = ProjectScaffold.Write(roots, request, cancellationToken);
        _logger.LogInformation("project.scaffold '{Slug}' ({Scope}) wrote {Count} files ({Bytes} bytes of text)", request.Slug, request.Scope, outcome.Files.Count, request.Files.Sum(f => (long)f.Text.Length));

        var hashes = new JsonObject();
        foreach (var (path, sha256) in outcome.Files)
        {
            hashes[path] = sha256;
        }

        return new JsonObject
        {
            ["project_id"] = request.ProjectId,
            ["slug"] = request.Slug,
            ["root_path"] = outcome.Folder,
            ["files_written"] = outcome.Files.Count,
            ["sha256_by_path"] = hashes,
            ["manifest"] = request.Manifest.Json.DeepClone(),
            ["root"] = ProjectScaffold.ScopeWord(request.Scope),
        };
    }

    /// <summary>The roots object for a scope — the one place the three are chosen between.</summary>
    private ProjectRoots RootsFor(ProjectScope scope)
        => scope switch
        {
            ProjectScope.ThreeD => Roots3d,
            ProjectScope.Native => RootsNative,
            _ => Roots,
        };

    /// <summary>M25: whether a slug names the 3D root's own folder under the Projects root.</summary>
    private bool Is3dRootName(string slug)
        => IsNestedRootName(slug, SceneCapabilityNames.Root3dFolderName, ProjectsRoot3d);

    /// <summary>M28: whether a slug names the native root's own folder under the Projects root.</summary>
    private bool IsNativeRootName(string slug)
        => IsNestedRootName(slug, NativeCapabilityNames.RootNativeFolderName, ProjectsRootNative);

    private bool IsNestedRootName(string slug, string defaultFolderName, string? nestedRoot)
    {
        if (string.Equals(slug, defaultFolderName, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        var root = ProjectsRoot;
        return nestedRoot is not null
            && root is not null
            && string.Equals(Path.GetDirectoryName(nestedRoot), root, StringComparison.OrdinalIgnoreCase)
            && string.Equals(Path.GetFileName(nestedRoot), slug, StringComparison.OrdinalIgnoreCase);
    }

    // ================================================================== project.run

    private async Task<JsonObject> RunAsync(JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        var project = Locate(payload);
        var key = OptionalString(payload, "command_key", MaxCommandKeyChars);
        var command = Pick(project.Manifest.Run, key, "run", project.Slug);
        if (command.IsBatch || project.Manifest.Port == ProjectManifest.NoPort)
        {
            // M25: Blender and Unity are batch runs, not servers. There is no port to bind and
            // nothing to probe: the run WAITS for the exit and answers with it.
            //
            // B03 (2026-09-12): so is any project that declares `port: 0` - "I bind nothing".
            // The CLI-tool template is one: `node cli.js` prints and exits. Waiting for a
            // socket it never opens would have turned every CLI run into a timeout, which is
            // why the template had no port at all and was refused outright instead.
            var batch = await Runner.RunBatchAsync(project, command, budget, cancellationToken).ConfigureAwait(false);
            return new JsonObject
            {
                ["project_id"] = project.ProjectId,
                ["slug"] = project.Slug,
                ["runtime"] = batch.Runtime.ToString().ToLowerInvariant(),
                ["command_key"] = command.Key,
                ["batch"] = true,
                ["exit_code"] = batch.ExitCode,
                ["seconds"] = Math.Round(batch.Seconds, 3),
                ["log_tail"] = batch.LogTail,
                ["log_truncated"] = batch.Truncated,
                ["log_path"] = batch.LogPath,
                ["pid"] = batch.Pid,
            };
        }

        var run = await Runner.StartAsync(project, command, budget, cancellationToken).ConfigureAwait(false);
        return new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["pid"] = run.Pid,
            ["port"] = run.Port,
            ["url"] = $"http://{ProjectManifest.Loopback}:{run.Port.ToString(CultureInfo.InvariantCulture)}/",
            ["started_at"] = Timestamp(run.StartedAt),
            ["command_key"] = command.Key,
            ["log_path"] = run.LogPath,
        };
    }

    // ================================================================== project.status

    private JsonObject Status(JsonObject payload)
    {
        var project = Locate(payload);
        var run = Runner.RunOf(project.ProjectId);
        var result = new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["root_path"] = project.Folder,
            ["port"] = project.Manifest.Port,
        };

        if (run is null)
        {
            // Scaffolded, never run in this companion's life (a run never outlives the
            // companion: kill-on-close).
            result["state"] = "scaffolded";
            result["pid"] = null;
            result["uptime_s"] = 0;
            result["log_tail"] = string.Empty;
            return result;
        }

        result["state"] = run.State;
        result["pid"] = run.Pid;
        result["port"] = run.Port;
        result["uptime_s"] = (int)Math.Max(0, run.Uptime.TotalSeconds);
        result["log_tail"] = run.LogTail;
        result["started_at"] = Timestamp(run.StartedAt);
        result["command_key"] = run.CommandKey;
        if (run.ExitCode is { } exitCode)
        {
            result["exit_code"] = exitCode;
        }

        if (run.StopReason is { } reason)
        {
            result["stop_reason"] = reason;
        }

        if (run.LogTruncated)
        {
            result["log_truncated"] = true;
        }

        return result;
    }

    // ================================================================== project.stop

    private async Task<JsonObject> StopAsync(JsonObject payload, CancellationToken cancellationToken)
    {
        var project = Locate(payload);
        var outcome = await Runner.StopAsync(project.ProjectId, cancellationToken).ConfigureAwait(false);
        if (outcome.WasRunning && !outcome.Exited)
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"'{project.Slug}' (pid {outcome.Pid}) was still alive {ProjectCapabilityNames.StopWait.TotalSeconds:F0} s after its job was ended",
                retryable: true);
        }

        var result = new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["stopped"] = true,
            ["was_running"] = outcome.WasRunning,
        };
        if (outcome.Pid is { } pid)
        {
            result["pid"] = pid;
        }

        return result;
    }

    // ================================================================== project.test

    private async Task<JsonObject> TestAsync(JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        var project = Locate(payload);
        var key = OptionalString(payload, "command_key", MaxCommandKeyChars);
        if (project.Manifest.Test.Count == 0)
        {
            throw DocumentErrors.Invalid($"'{project.Slug}' has no test command in its manifest");
        }

        var command = Pick(project.Manifest.Test, key, "test", project.Slug);
        var outcome = await Runner.TestAsync(project, command, budget, cancellationToken).ConfigureAwait(false);
        return new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["command_key"] = command.Key,
            ["exit_code"] = outcome.ExitCode,
            ["passed"] = outcome.Passed,
            ["failed"] = outcome.Failed,
            ["counts_parsed"] = outcome.Passed is not null || outcome.Failed is not null,
            ["report_tail"] = outcome.ReportTail,
            ["truncated"] = outcome.Truncated,
            ["duration_ms"] = (int)outcome.DurationMs,
            ["log_path"] = outcome.LogPath,
        };
    }

    // ================================================================== scene.inspect

    /// <summary>
    /// M25 (DEVICE_PROTOCOL.md §6m, ADR-0088 decision 2): the tool's own read-back for one 3D
    /// project — the bounded <c>out.json</c> a driver wrote, parsed to prove it IS JSON, and
    /// the render it declares as base64 with its SHA-256 checked against the one the driver
    /// recorded. Only projects under the 3D root have an inspection; a web project is
    /// <c>not_found</c> here, as is an id nobody scaffolded.
    /// </summary>
    private JsonObject Inspect(JsonObject payload)
    {
        var project = Locate3d(payload);
        var inspection = SceneInspection.ReadInspection(project.Folder, project.Slug);
        var render = SceneInspection.ReadRender(project.Folder, project.Slug, inspection);
        _logger.LogInformation(
            "scene.inspect '{Slug}' inspection_keys={Keys} render={Render}",
            project.Slug,
            inspection.Count,
            render is null ? "(none)" : $"{render.RelativePath} {render.Bytes} bytes");

        var result = new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["root_path"] = project.Folder,
            ["inspection"] = inspection,
            ["inspection_path"] = Path.Combine(project.Folder, SceneCapabilityNames.InspectionFileName),
        };

        if (render is null)
        {
            result["render"] = null;
            return result;
        }

        result["render"] = new JsonObject
        {
            ["path"] = render.RelativePath,
            ["bytes"] = render.Bytes,
            ["sha256"] = render.Sha256,
            ["verified"] = true,
            ["png_base64"] = Convert.ToBase64String(render.Content),
        };
        return result;
    }

    // ================================================================== helpers

    /// <summary>
    /// The project a payload names: the id validated, its folder found through the markers —
    /// under the Projects root, else (M25) under the 3D root — and the marker's manifest
    /// re-validated against the scope of the root it was FOUND under (a tampered command, or a
    /// 3D runtime in a web project, is refused HERE, before any process).
    /// </summary>
    private ProjectContext Locate(JsonObject payload)
    {
        var projectId = RequireString(payload, "project_id", ProjectRoots.MaxProjectIdChars);
        ProjectRoots.RequireProjectId(projectId);
        var folder = Roots.Find(projectId);
        var scope = ProjectScope.Web;
        if (folder is null)
        {
            folder = Roots3d.Find(projectId);
            scope = ProjectScope.ThreeD;
        }

        if (folder is null)
        {
            // M28: and last the native root. The order matters only for a folder that could be
            // found twice, which cannot happen: the roots are disjoint directories and a
            // project lives DIRECTLY under exactly one of them.
            folder = RootsNative.Find(projectId) ?? throw DocumentErrors.NotFound("no scaffolded project carries payload.project_id; scaffold it first");
            scope = ProjectScope.Native;
        }

        var marker = ProjectRoots.ReadMarker(folder) ?? throw DocumentErrors.NotFound("the project's marker is gone; scaffold it again");
        var manifest = ProjectManifest.Parse(marker.Manifest, filePaths: null, scope);
        return new ProjectContext(projectId, marker.Slug, folder, manifest, scope);
    }

    /// <summary>M25: the same lookup, restricted to the 3D root — a web project has no scene to inspect and is <c>not_found</c> rather than half-answered.</summary>
    private ProjectContext Locate3d(JsonObject payload)
    {
        var project = Locate(payload);
        if (project.Scope != ProjectScope.ThreeD)
        {
            throw DocumentErrors.NotFound($"'{project.Slug}' is a project of the Projects root, not a 3D project; only a project under the 3D root has a scene to inspect");
        }

        return project;
    }

    private static ProjectCommand Pick(IReadOnlyDictionary<string, ProjectCommand> commands, string? key, string section, string slug)
    {
        if (key is null)
        {
            // The manifest's first command of the section is the default (JsonObject keeps order).
            return commands.Values.First();
        }

        return commands.TryGetValue(key, out var command)
            ? command
            : throw DocumentErrors.Invalid($"payload.command_key '{key}' is not a {section} key of '{slug}' ({string.Join(", ", commands.Keys)})");
    }

    private static string Timestamp(DateTimeOffset at) => at.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);

    private static string RequireString(JsonObject payload, string key, int maxChars)
    {
        var node = payload[key];
        if (node is null || node.GetValueKind() != JsonValueKind.String)
        {
            throw DocumentErrors.Invalid($"payload.{key} is required and must be a string");
        }

        var value = node.GetValue<string>();
        if (string.IsNullOrWhiteSpace(value))
        {
            throw DocumentErrors.Invalid($"payload.{key} must not be empty");
        }

        if (value.Length > maxChars)
        {
            throw DocumentErrors.Invalid($"payload.{key} exceeds {maxChars} characters ({value.Length})");
        }

        return value;
    }

    private static string? OptionalString(JsonObject payload, string key, int maxChars)
        => payload[key] is null ? null : RequireString(payload, key, maxChars);

    private void Record(string capability, string outcome, long durationMs, bool? retryable)
    {
        var detail = $"outcome={outcome} duration_ms={durationMs}" + (retryable is null ? string.Empty : $" retryable={(retryable.Value ? "true" : "false")}");
        _audit?.Write(AuditRequestEvent, capability: capability, status: outcome, detail: detail);
        _logger.LogInformation("project request {Capability} outcome={Outcome} duration_ms={DurationMs}", capability, outcome, durationMs);
    }

    public void Dispose() => Runner.Dispose();
}

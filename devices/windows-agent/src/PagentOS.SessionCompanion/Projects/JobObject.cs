using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32.SafeHandles;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>The bounds a <see cref="JobObject"/> carries, as read back from the kernel.</summary>
public sealed record JobLimits(uint LimitFlags, long JobMemoryLimitBytes, TimeSpan PerJobUserTimeLimit, uint ActiveProcessLimit, uint UiRestrictions)
{
    public bool KillOnJobClose => (LimitFlags & JobObject.LimitKillOnJobClose) != 0;

    public bool BreakawayAllowed => (LimitFlags & (JobObject.LimitBreakawayOk | JobObject.LimitSilentBreakawayOk)) != 0;

    public bool MemoryBounded => (LimitFlags & JobObject.LimitJobMemory) != 0;

    public bool CpuTimeBounded => (LimitFlags & JobObject.LimitJobTime) != 0;
}

/// <summary>
/// A Windows Job Object (ADR-0086 decision 3): the ONE container a project's process lives
/// in. Configured before any process is assigned with <c>JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE</c>
/// (closing the last handle ends every process in it — a companion that dies takes its
/// children with it), a committed-memory bound, a user-mode CPU-time bound, an active-process
/// cap, <c>DIE_ON_UNHANDLED_EXCEPTION</c> (no Werfault dialog on the owner's desk), and every
/// UI restriction (<c>JOB_OBJECT_UILIMIT_ALL</c>: no clipboard, no desktop switch, no
/// <c>ExitWindows</c>, no USER handles of processes outside the job, no system parameters).
/// Breakaway is NOT granted: a child cannot leave. Nothing here ever takes a process by pid —
/// only the job's own members are ever terminated.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class JobObject : IDisposable
{
    public const uint LimitProcessTime = 0x2;
    public const uint LimitJobTime = 0x4;
    public const uint LimitActiveProcess = 0x8;
    public const uint LimitBreakawayOk = 0x800;
    public const uint LimitSilentBreakawayOk = 0x1000;
    public const uint LimitJobMemory = 0x200;
    public const uint LimitDieOnUnhandledException = 0x400;
    public const uint LimitKillOnJobClose = 0x2000;

    public const uint UiLimitAll = 0xFF;

    private const int InfoClassBasicUiRestrictions = 4;
    private const int InfoClassExtendedLimitInformation = 9;

    private readonly SafeFileHandle _handle;
    private bool _disposed;

    private JobObject(SafeFileHandle handle)
    {
        _handle = handle;
    }

    /// <summary>Creates and configures the job. Fails loudly (<see cref="Win32Exception"/>) rather than returning an unbounded job.</summary>
    public static JobObject Create(long memoryLimitBytes, TimeSpan cpuTimeLimit, int activeProcessLimit)
    {
        var handle = CreateJobObjectW(IntPtr.Zero, null);
        if (handle.IsInvalid)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");
        }

        var job = new JobObject(handle);
        try
        {
            var limits = new JobObjectExtendedLimitInformation
            {
                BasicLimitInformation = new JobObjectBasicLimitInformation
                {
                    LimitFlags = LimitKillOnJobClose | LimitJobMemory | LimitJobTime | LimitActiveProcess | LimitDieOnUnhandledException,
                    PerJobUserTimeLimit = cpuTimeLimit.Ticks,
                    ActiveProcessLimit = (uint)activeProcessLimit,
                },
                JobMemoryLimit = (nuint)memoryLimitBytes,
            };
            if (!SetInformationJobObject(handle, InfoClassExtendedLimitInformation, ref limits, Marshal.SizeOf<JobObjectExtendedLimitInformation>()))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject(extended limits) failed");
            }

            var ui = new JobObjectBasicUiRestrictions { UiRestrictionsClass = UiLimitAll };
            if (!SetInformationJobObject(handle, InfoClassBasicUiRestrictions, ref ui, Marshal.SizeOf<JobObjectBasicUiRestrictions>()))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject(UI restrictions) failed");
            }

            return job;
        }
        catch
        {
            job.Dispose();
            throw;
        }
    }

    /// <summary>The bounds the companion's own runs get (<see cref="ProjectCapabilityNames"/>).</summary>
    public static JobObject CreateBounded()
        => Create(ProjectCapabilityNames.MemoryLimitBytes, ProjectCapabilityNames.CpuTimeLimit, ProjectCapabilityNames.MaxProcessesPerJob);

    /// <summary>
    /// M25: the bounds for one runtime. The web runtimes keep M23's 512 MiB / 10 min CPU; the
    /// two editors get their own (Blender 2 GiB and 5 min, Unity 4 GiB and 10 min — an editor
    /// that imports a project is not a static file server), and more room for the processes
    /// they fork (Unity starts a licensing client, a package manager and a compiler). Every
    /// other flag — kill-on-close, no breakaway, every UI restriction, die-on-exception — is
    /// the same, because those are what make the child safe, not what make it small.
    /// </summary>
    public static JobObject CreateBounded(ProjectRuntime runtime)
        => runtime switch
        {
            ProjectRuntime.Blender => Create(SceneCapabilityNames.BlenderMemoryLimitBytes, SceneCapabilityNames.BlenderRunLimit, Max3dProcessesPerJob),
            ProjectRuntime.Unity => Create(SceneCapabilityNames.UnityMemoryLimitBytes, SceneCapabilityNames.UnityRunLimit, Max3dProcessesPerJob),
            // M28: 4 GiB and 64 processes, and a CPU-time bound that is NOT the wall-clock
            // bound. `JOB_OBJECT_LIMIT_JOB_TIME` ends the job when the SUM of its processes'
            // user time passes the limit, and MSBuild compiles in parallel: on an eight-core
            // machine an honest fifteen-minute build burns two hours of user time, so a CPU
            // bound set to the wall bound would kill honest builds and call it a limit. The
            // wall clock is the bound here (ProjectRunner ends the job at NativeLimit); the
            // CPU bound is what that wall clock could legitimately consume on THIS machine.
            // B49: Gradle forks a single-use daemon and the Kotlin compiler daemon, inside the
            // same job and under the same native bounds.
            ProjectRuntime.Dotnet or ProjectRuntime.MakeAppx or ProjectRuntime.Gradle => Create(
                NativeCapabilityNames.MemoryLimitBytes,
                NativeCapabilityNames.CpuTimeLimitFor(Environment.ProcessorCount),
                NativeCapabilityNames.MaxProcessesPerJob),
            _ => CreateBounded(),
        };

    /// <summary>M25: an editor forks more than a file server does (Unity's licensing client, package manager and compilers) — still a fixed, small cap, never a fork bomb.</summary>
    public const int Max3dProcessesPerJob = 32;

    /// <summary>Puts <paramref name="process"/> in the job. Fails loudly; the caller ends a process it could not contain.</summary>
    public void Assign(Process process)
    {
        if (!AssignProcessToJobObject(_handle, process.Handle))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject failed");
        }
    }

    /// <summary>Whether <paramref name="process"/> is a member of THIS job (a test's proof, and the runner's check right after assignment).</summary>
    public bool Contains(Process process)
    {
        if (!IsProcessInJob(process.Handle, _handle, out var result))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "IsProcessInJob failed");
        }

        return result;
    }

    /// <summary>Ends every process in the job now (the kill-on-close would do it at dispose; this makes the stop immediate and observable).</summary>
    public void Terminate(uint exitCode = 1)
    {
        if (_disposed)
        {
            return;
        }

        TerminateJobObject(_handle, exitCode);
    }

    /// <summary>The limits as the kernel holds them — for the log and for a test that asserts the bounds rather than trusting the constructor.</summary>
    public JobLimits ReadLimits()
    {
        var extended = new JobObjectExtendedLimitInformation();
        if (!QueryInformationJobObject(_handle, InfoClassExtendedLimitInformation, ref extended, Marshal.SizeOf<JobObjectExtendedLimitInformation>(), IntPtr.Zero))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryInformationJobObject(extended limits) failed");
        }

        var ui = new JobObjectBasicUiRestrictions();
        if (!QueryInformationJobObject(_handle, InfoClassBasicUiRestrictions, ref ui, Marshal.SizeOf<JobObjectBasicUiRestrictions>(), IntPtr.Zero))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryInformationJobObject(UI restrictions) failed");
        }

        return new JobLimits(
            extended.BasicLimitInformation.LimitFlags,
            (long)extended.JobMemoryLimit,
            TimeSpan.FromTicks(extended.BasicLimitInformation.PerJobUserTimeLimit),
            extended.BasicLimitInformation.ActiveProcessLimit,
            ui.UiRestrictionsClass);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        // KILL_ON_JOB_CLOSE: closing the last handle ends whatever is still in the job.
        _handle.Dispose();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize;
        public nuint MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public nuint ProcessMemoryLimit;
        public nuint JobMemoryLimit;
        public nuint PeakProcessMemoryUsed;
        public nuint PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicUiRestrictions
    {
        public uint UiRestrictionsClass;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateJobObjectW(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(SafeFileHandle job, int informationClass, ref JobObjectExtendedLimitInformation info, int length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(SafeFileHandle job, int informationClass, ref JobObjectBasicUiRestrictions info, int length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(SafeFileHandle job, int informationClass, ref JobObjectExtendedLimitInformation info, int length, IntPtr returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(SafeFileHandle job, int informationClass, ref JobObjectBasicUiRestrictions info, int length, IntPtr returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(SafeFileHandle job, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsProcessInJob(IntPtr process, SafeFileHandle job, out bool result);
}

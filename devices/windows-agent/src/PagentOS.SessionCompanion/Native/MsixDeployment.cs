using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Xml;
using System.Xml.Linq;

namespace PagentOS.SessionCompanion.Native;

/// <summary>What Windows answered for one deployment request.</summary>
public sealed record DeploymentOutcome(int HResult, int ExtendedHResult, string? ErrorText)
{
    public bool Ok => HResult == 0;

    public string HResultHex => $"0x{unchecked((uint)HResult):X8}";
}

/// <summary>
/// The per-user package deployment the install step needs. The real one is
/// <see cref="WindowsPackageDeployer"/>; a lab injects a fake only where the real one cannot be
/// reached without an elevated step the tests never take (a trusted certificate).
/// </summary>
public interface IMsixDeployer
{
    DeploymentOutcome Add(string packagePath);

    DeploymentOutcome Remove(string packageFullName);

    /// <summary>Whether <paramref name="packageFullName"/> is registered for the current user — read from Windows, not from any record of ours.</summary>
    bool IsRegistered(string packageFamilyName, string packageFullName);
}

/// <summary>The identity an MSIX declares, read from the package's OWN manifest (inside the zip), not from the file that was staged.</summary>
public sealed record MsixIdentity(string Name, string Publisher, string Version, string Architecture)
{
    private static readonly XNamespace Foundation = "http://schemas.microsoft.com/appx/manifest/foundation/windows10";

    public static MsixIdentity Read(string packagePath)
    {
        using var zip = ZipFile.OpenRead(packagePath);
        var entry = zip.GetEntry("AppxManifest.xml") ?? throw new InvalidDataException("the package has no AppxManifest.xml");
        using var stream = entry.Open();
        return FromManifest(stream);
    }

    public static MsixIdentity FromManifest(Stream manifest)
    {
        var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null };
        using var reader = XmlReader.Create(manifest, settings);
        var document = XDocument.Load(reader);
        var identity = document.Root?.Element(Foundation + "Identity")
            ?? throw new InvalidDataException("AppxManifest.xml has no foundation Identity element");
        return new MsixIdentity(
            (string?)identity.Attribute("Name") ?? throw new InvalidDataException("Identity has no Name"),
            (string?)identity.Attribute("Publisher") ?? throw new InvalidDataException("Identity has no Publisher"),
            (string?)identity.Attribute("Version") ?? throw new InvalidDataException("Identity has no Version"),
            (string?)identity.Attribute("ProcessorArchitecture") ?? "neutral");
    }
}

/// <summary>
/// The names Windows gives a package — computed by Windows' own <c>PackageFullNameFromId</c> /
/// <c>PackageFamilyNameFromId</c>, never by hashing the publisher here.
/// </summary>
[SupportedOSPlatform("windows")]
public static class MsixPackageNames
{
    public static (string FullName, string FamilyName) For(MsixIdentity identity)
    {
        var name = Marshal.StringToHGlobalUni(identity.Name);
        var publisher = Marshal.StringToHGlobalUni(identity.Publisher);
        try
        {
            var id = new PACKAGE_ID
            {
                processorArchitecture = Architecture(identity.Architecture),
                version = Version(identity.Version),
                name = name,
                publisher = publisher,
            };
            return (Call(ref id, full: true), Call(ref id, full: false));
        }
        finally
        {
            Marshal.FreeHGlobal(name);
            Marshal.FreeHGlobal(publisher);
        }
    }

    public static uint Architecture(string architecture) => architecture.ToLowerInvariant() switch
    {
        "x86" => 0,
        "arm" => 5,
        "x64" => 9,
        "neutral" => 11,
        "arm64" => 12,
        _ => throw new InvalidDataException($"unknown ProcessorArchitecture '{architecture}'"),
    };

    public static ulong Version(string version)
    {
        var parts = version.Split('.');
        if (parts.Length != 4)
        {
            throw new InvalidDataException($"a package version has four parts, not '{version}'");
        }

        ulong value = 0;
        foreach (var part in parts)
        {
            value = (value << 16) | ushort.Parse(part, System.Globalization.CultureInfo.InvariantCulture);
        }

        return value;
    }

    private static string Call(ref PACKAGE_ID id, bool full)
    {
        var buffer = new char[256];
        var length = (uint)buffer.Length;
        var status = full ? PackageFullNameFromId(ref id, ref length, buffer) : PackageFamilyNameFromId(ref id, ref length, buffer);
        if (status != 0)
        {
            throw new InvalidDataException($"Windows could not name the package (error {status})");
        }

        return new string(buffer, 0, (int)Math.Max(0, length - 1));
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PACKAGE_ID
    {
        public uint reserved;
        public uint processorArchitecture;
        public ulong version;
        public IntPtr name;
        public IntPtr publisher;
        public IntPtr resourceId;
        public IntPtr publisherId;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int PackageFullNameFromId(ref PACKAGE_ID packageId, ref uint packageFullNameLength, [Out] char[] packageFullName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int PackageFamilyNameFromId(ref PACKAGE_ID packageId, ref uint packageFamilyNameLength, [Out] char[] packageFamilyName);
}

/// <summary>
/// B33 requirement 468 for an MSIX row: a per-user install and removal through Windows'
/// <c>Windows.Management.Deployment.PackageManager</c>, called over its ABI from this process —
/// no shell, no <c>Add-AppxPackage</c> cmdlet, no child process, no elevation. The interface
/// slots are the SDK header's (<c>windows.management.deployment.h</c>, 10.0.26100.0):
/// <c>IPackageManager</c> {9A7D4B65-…} slot 6 <c>AddPackageAsync</c>, slot 8
/// <c>RemovePackageAsync</c>; the async operation's slot 10 <c>GetResults</c>;
/// <c>IDeploymentResult</c> {2563B9AE-…} slot 6 <c>ErrorText</c>, slot 8
/// <c>ExtendedErrorCode</c>; <c>IAsyncInfo</c> {00000036-…} slot 7 <c>Status</c>, slot 8
/// <c>ErrorCode</c>, slot 9 <c>Cancel</c>, slot 10 <c>Close</c>.
/// </summary>
/// <remarks>
/// Windows itself refuses a package whose signer this machine does not trust — the install
/// step checks trust first so the owner hears the reason and the one command that fixes it,
/// but the operating system's refusal is the backstop, and a lab test exercises exactly that
/// refusal through this class.
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class WindowsPackageDeployer : IMsixDeployer
{
    public static readonly TimeSpan OperationLimit = TimeSpan.FromMinutes(5);

    private static readonly Guid IidPackageManager = new("9a7d4b65-5e8f-4fc7-a2e5-7f6925cb8b53");
    private static readonly Guid IidUriFactory = new("44a9796f-723e-4fdf-a218-033e75b0c084");
    private static readonly Guid IidAsyncInfo = new("00000036-0000-0000-C000-000000000046");

    private const int SlotAddPackageAsync = 6;
    private const int SlotRemovePackageAsync = 8;
    private const int SlotCreateUri = 6;
    private const int SlotGetResults = 10;
    private const int SlotErrorText = 6;
    private const int SlotExtendedErrorCode = 8;
    private const int SlotStatus = 7;
    private const int SlotErrorCode = 8;
    private const int SlotCancel = 9;
    private const int SlotClose = 10;

    private const int AsyncStarted = 0;
    private const int AsyncCompleted = 1;
    private const int ErrorTimeout = unchecked((int)0x800705B4);

    public DeploymentOutcome Add(string packagePath)
    {
        var uriText = new Uri(Path.GetFullPath(packagePath)).AbsoluteUri;
        return OnOwnThread(() =>
        {
            var manager = PackageManager();
            var uri = IntPtr.Zero;
            try
            {
                uri = CreateUri(uriText);
                var hr = Slot<AddPackageFn>(manager, SlotAddPackageAsync)(manager, uri, IntPtr.Zero, 0, out var operation);
                return hr != 0 ? new DeploymentOutcome(hr, 0, null) : Await(operation);
            }
            finally
            {
                Release(uri);
                Release(manager);
            }
        });
    }

    public DeploymentOutcome Remove(string packageFullName) => OnOwnThread(() =>
    {
        var manager = PackageManager();
        var name = HString(packageFullName);
        try
        {
            var hr = Slot<RemovePackageFn>(manager, SlotRemovePackageAsync)(manager, name, out var operation);
            return hr != 0 ? new DeploymentOutcome(hr, 0, null) : Await(operation);
        }
        finally
        {
            _ = WindowsDeleteString(name);
            Release(manager);
        }
    });

    public bool IsRegistered(string packageFamilyName, string packageFullName)
        => RegisteredFullNames(packageFamilyName).Contains(packageFullName, StringComparer.OrdinalIgnoreCase);

    /// <summary>The full names Windows has registered for the current user under one family.</summary>
    public static IReadOnlyList<string> RegisteredFullNames(string packageFamilyName)
    {
        uint count = 0;
        uint bufferLength = 0;
        var status = GetPackagesByPackageFamily(packageFamilyName, ref count, IntPtr.Zero, ref bufferLength, IntPtr.Zero);
        if (status == 0 || count == 0)
        {
            return [];
        }

        if (status != ErrorInsufficientBuffer)
        {
            throw new InvalidOperationException($"Windows could not list the packages of '{packageFamilyName}' (error {status})");
        }

        var names = Marshal.AllocHGlobal(checked((int)count * IntPtr.Size));
        var buffer = Marshal.AllocHGlobal(checked((int)bufferLength * sizeof(char)));
        try
        {
            status = GetPackagesByPackageFamily(packageFamilyName, ref count, names, ref bufferLength, buffer);
            if (status != 0)
            {
                throw new InvalidOperationException($"Windows could not list the packages of '{packageFamilyName}' (error {status})");
            }

            var result = new List<string>((int)count);
            for (var i = 0; i < count; i++)
            {
                result.Add(Marshal.PtrToStringUni(Marshal.ReadIntPtr(names, i * IntPtr.Size)) ?? string.Empty);
            }

            return result;
        }
        finally
        {
            Marshal.FreeHGlobal(names);
            Marshal.FreeHGlobal(buffer);
        }
    }

    // ------------------------------------------------------------------ internals

    /// <summary>A dedicated multi-threaded-apartment thread per request, initialised and uninitialised by us.</summary>
    private static DeploymentOutcome OnOwnThread(Func<DeploymentOutcome> work)
    {
        DeploymentOutcome? outcome = null;
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var initialised = RoInitialize(RoInitMultithreaded);
            try
            {
                outcome = work();
            }
            catch (Exception exception)
            {
                failure = exception;
            }
            finally
            {
                if (initialised >= 0)
                {
                    RoUninitialize();
                }
            }
        })
        {
            IsBackground = true,
            Name = "msix-deployment",
        };
        thread.SetApartmentState(ApartmentState.MTA);
        thread.Start();
        thread.Join();
        if (failure is not null)
        {
            throw failure;
        }

        return outcome!;
    }

    private static IntPtr PackageManager()
    {
        var className = HString("Windows.Management.Deployment.PackageManager");
        try
        {
            Check(RoActivateInstance(className, out var inspectable), "activate the package manager");
            try
            {
                var iid = IidPackageManager;
                Check(Marshal.QueryInterface(inspectable, in iid, out var manager), "query IPackageManager");
                return manager;
            }
            finally
            {
                Release(inspectable);
            }
        }
        finally
        {
            _ = WindowsDeleteString(className);
        }
    }

    private static IntPtr CreateUri(string text)
    {
        var className = HString("Windows.Foundation.Uri");
        var value = HString(text);
        try
        {
            var iid = IidUriFactory;
            Check(RoGetActivationFactory(className, ref iid, out var factory), "get the Uri factory");
            try
            {
                Check(Slot<CreateUriFn>(factory, SlotCreateUri)(factory, value, out var uri), "create the package Uri");
                return uri;
            }
            finally
            {
                Release(factory);
            }
        }
        finally
        {
            _ = WindowsDeleteString(value);
            _ = WindowsDeleteString(className);
        }
    }

    private static DeploymentOutcome Await(IntPtr operation)
    {
        var info = IntPtr.Zero;
        var result = IntPtr.Zero;
        try
        {
            var iid = IidAsyncInfo;
            Check(Marshal.QueryInterface(operation, in iid, out info), "query IAsyncInfo");
            var deadline = DateTime.UtcNow + OperationLimit;
            int status;
            while (true)
            {
                Check(Slot<GetIntFn>(info, SlotStatus)(info, out status), "read the deployment status");
                if (status != AsyncStarted)
                {
                    break;
                }

                if (DateTime.UtcNow >= deadline)
                {
                    _ = Slot<NoArgFn>(info, SlotCancel)(info);
                    return new DeploymentOutcome(ErrorTimeout, 0, $"the deployment did not finish within {OperationLimit.TotalMinutes:0} min");
                }

                Thread.Sleep(100);
            }

            var errorCode = 0;
            if (status != AsyncCompleted)
            {
                _ = Slot<GetIntFn>(info, SlotErrorCode)(info, out errorCode);
            }

            string? text = null;
            var extended = 0;
            if (Slot<GetPtrFn>(operation, SlotGetResults)(operation, out result) == 0 && result != IntPtr.Zero)
            {
                if (Slot<GetPtrFn>(result, SlotErrorText)(result, out var hstring) == 0 && hstring != IntPtr.Zero)
                {
                    var raw = WindowsGetStringRawBuffer(hstring, out var length);
                    text = length == 0 ? null : Marshal.PtrToStringUni(raw, (int)length);
                    _ = WindowsDeleteString(hstring);
                }

                _ = Slot<GetIntFn>(result, SlotExtendedErrorCode)(result, out extended);
            }

            if (status == AsyncCompleted)
            {
                return new DeploymentOutcome(0, extended, text);
            }

            return new DeploymentOutcome(errorCode != 0 ? errorCode : unchecked((int)0x80004004), extended, text);
        }
        finally
        {
            if (info != IntPtr.Zero)
            {
                _ = Slot<NoArgFn>(info, SlotClose)(info);
            }

            Release(result);
            Release(info);
            Release(operation);
        }
    }

    private static T Slot<T>(IntPtr instance, int slot)
        where T : Delegate
    {
        var table = Marshal.ReadIntPtr(instance);
        return Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(table, slot * IntPtr.Size));
    }

    private static IntPtr HString(string value)
    {
        Check(WindowsCreateString(value, (uint)value.Length, out var hstring), "create a string");
        return hstring;
    }

    private static void Release(IntPtr instance)
    {
        if (instance != IntPtr.Zero)
        {
            Marshal.Release(instance);
        }
    }

    private static void Check(int hr, string what)
    {
        if (hr < 0)
        {
            throw new COMException($"Windows could not {what} (0x{unchecked((uint)hr):X8})", hr);
        }
    }

    private const int RoInitMultithreaded = 1;
    private const int ErrorInsufficientBuffer = 122;

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int AddPackageFn(IntPtr self, IntPtr packageUri, IntPtr dependencyPackageUris, uint deploymentOptions, out IntPtr operation);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int RemovePackageFn(IntPtr self, IntPtr packageFullName, out IntPtr operation);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int CreateUriFn(IntPtr self, IntPtr uri, out IntPtr instance);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int GetIntFn(IntPtr self, out int value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int GetPtrFn(IntPtr self, out IntPtr value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int NoArgFn(IntPtr self);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int RoInitialize(int initType);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern void RoUninitialize();

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int RoActivateInstance(IntPtr activatableClassId, out IntPtr instance);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int RoGetActivationFactory(IntPtr activatableClassId, ref Guid iid, out IntPtr factory);

    [DllImport("combase.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int WindowsCreateString([MarshalAs(UnmanagedType.LPWStr)] string sourceString, uint length, out IntPtr hstring);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int WindowsDeleteString(IntPtr hstring);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern IntPtr WindowsGetStringRawBuffer(IntPtr hstring, out uint length);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int GetPackagesByPackageFamily(string packageFamilyName, ref uint count, IntPtr packageFullNames, ref uint bufferLength, IntPtr buffer);
}

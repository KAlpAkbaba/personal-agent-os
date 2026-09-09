using System;
using System.IO;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// M28: <c>app.launch</c> may start an application THIS SYSTEM BUILT, and nothing else that
/// lives outside Program Files / Windows.
///
/// The gap this closes was found by running, not by reading: on the owner's machine on
/// 2026-09-09 the item-28 qualification built a real 162,304-byte EXE and could not start it,
/// refused twice and correctly for two different reasons — <c>file.open</c> answered
/// "'notlarim.exe' is executable; file.open opens documents, app.launch runs programs", and
/// <c>app.launch</c> answered "nor an absolute .exe under Program Files / Windows". A factory
/// that produces an application nothing can run has not finished producing it.
///
/// The rule is deliberately narrow, and these tests are what hold it narrow: ONE root, the
/// native root, compared after every junction is followed. Everything below uses real
/// directories and real files; nothing here starts a process.
/// </summary>
public sealed class NativeLaunchTests : IDisposable
{
    private readonly string _dir;

    public NativeLaunchTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), "pagentos-native-launch-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_dir);
    }

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private string NativeRoot()
    {
        var root = Path.Combine(_dir, "native");
        Directory.CreateDirectory(root);
        return root;
    }

    private static string WriteExe(string directory, string name = "notlarim.exe")
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, name);
        File.WriteAllBytes(path, [0x4D, 0x5A]);  // "MZ": enough to be a file with the right name
        return path;
    }

    [Fact]
    public void An_exe_the_factory_published_under_the_native_root_is_launchable()
    {
        var root = NativeRoot();
        var exe = WriteExe(Path.Combine(root, "notlarim", "out"));

        var resolved = OperatorCapabilities.ResolveNativeBuiltExecutable(exe, root);

        Assert.NotNull(resolved);
        // The RESOLVED path is what comes back, and it is what the caller acts on.
        Assert.Equal(AuthorisedRoots.ResolveFinal(exe), resolved);
    }

    [Fact]
    public void An_exe_anywhere_else_is_not()
    {
        var root = NativeRoot();
        var elsewhere = WriteExe(Path.Combine(_dir, "downloads"));

        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(elsewhere, root));
    }

    [Fact]
    public void A_sibling_whose_name_merely_starts_with_the_roots_is_not_inside_it()
    {
        // C:\...\native2 is not under C:\...\native, however a prefix comparison reads it.
        var root = NativeRoot();
        var sibling = WriteExe(Path.Combine(_dir, "native2"));

        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(sibling, root));
    }

    [Fact]
    public void A_path_that_walks_out_of_the_root_is_refused_however_it_is_spelled()
    {
        var root = NativeRoot();
        var outside = WriteExe(Path.Combine(_dir, "outside"));
        var viaDotDot = Path.Combine(root, "..", "outside", "notlarim.exe");

        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(viaDotDot, root));
    }

    [Fact]
    public void A_junction_planted_inside_the_root_that_points_out_of_it_is_refused()
    {
        // The reason this is resolve-then-contain and not a prefix compare: the native root is
        // owner-writable, so a link inside it can name anything on the machine. What the path
        // REALLY opens is what is compared.
        var root = NativeRoot();
        var target = Path.Combine(_dir, "target");
        var exe = WriteExe(target);
        var link = Path.Combine(root, "escape");
        try
        {
            Directory.CreateSymbolicLink(link, target);
        }
        catch (Exception)
        {
            return;  // symlink creation needs a privilege this machine may not grant; the
                     // ".." case above covers the same rule without one
        }

        var viaLink = Path.Combine(link, Path.GetFileName(exe));
        Assert.True(File.Exists(viaLink), "the link should reach the file, or this proves nothing");
        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(viaLink, root));
    }

    [Fact]
    public void A_path_inside_the_root_that_is_not_there_is_not_launchable()
    {
        var root = NativeRoot();
        var missing = Path.Combine(root, "notlarim", "out", "notlarim.exe");

        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(missing, root));
    }

    [Fact]
    public void With_no_native_root_configured_nothing_is_launchable_by_this_rule()
    {
        var exe = WriteExe(Path.Combine(_dir, "native", "out"));

        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(exe, null));
        Assert.Null(OperatorCapabilities.ResolveNativeBuiltExecutable(exe, "   "));
    }

    [Fact]
    public void The_default_native_root_is_the_one_the_protocol_names()
    {
        // Both halves of §6n must mean the same directory: the qualification builds into
        // <Documents>\PagentOS Projects\native and the device authorises exactly that.
        var expected = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            "PagentOS Projects",
            "native");

        Assert.Equal(expected, OperatorOptions.DefaultNativeRoot(OperatorOptions.DefaultProjectsRoot()));
    }
}

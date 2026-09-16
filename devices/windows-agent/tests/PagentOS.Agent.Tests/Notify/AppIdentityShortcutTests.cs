using PagentOS.SessionCompanion.Notify;
using Xunit;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// B11-toast: the AppUserModelID shortcut, written by the shell's real <c>IShellLinkW</c> into a
/// throwaway "Start Menu" folder and read back through the link's own property store - never
/// the owner's Start Menu.
/// </summary>
public sealed class AppIdentityShortcutTests : IDisposable
{
    private readonly string _programs = Path.Combine(Path.GetTempPath(), "pagentos-aumid-" + Guid.NewGuid().ToString("N"));

    /// <summary>A real PE: <c>IPersistFile.Save</c> reads the target, and a fake stub is refused.</summary>
    private static string Target => Environment.ProcessPath!;

    private static string OtherTarget => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "notepad.exe");

    public void Dispose()
    {
        if (Directory.Exists(_programs))
        {
            Directory.Delete(_programs, recursive: true);
        }
    }

    [Fact]
    public void The_link_carries_the_app_id_and_the_target_and_a_second_start_writes_nothing()
    {
        var created = AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target);
        var path = Path.Combine(_programs, AppIdentityShortcut.ShortcutFileName);
        var written = File.GetLastWriteTimeUtc(path);
        var bytes = File.ReadAllBytes(path);

        var again = AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target);

        Assert.Equal(ShortcutState.Created, created);
        Assert.Equal(ShortcutState.Unchanged, again);
        Assert.Equal(written, File.GetLastWriteTimeUtc(path));
        Assert.Equal(bytes, File.ReadAllBytes(path));
        var (target, appId) = AppIdentityShortcut.Read(_programs)!.Value;
        Assert.Equal("PagentOS.Companion", appId);
        Assert.Equal(Path.GetFullPath(Target), Path.GetFullPath(target), StringComparer.OrdinalIgnoreCase);
    }

    [Fact]
    public void A_link_with_another_app_id_or_target_is_rewritten()
    {
        AppIdentityShortcut.Ensure(_programs, "PagentOS.Old", Target);

        Assert.Equal(ShortcutState.Updated, AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target));
        Assert.Equal("PagentOS.Companion", AppIdentityShortcut.Read(_programs)!.Value.AppUserModelId);

        Assert.Equal(ShortcutState.Updated, AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, OtherTarget));
        Assert.Equal(Path.GetFullPath(OtherTarget), AppIdentityShortcut.Read(_programs)!.Value.Target, StringComparer.OrdinalIgnoreCase);
    }

    [Fact]
    public void A_link_without_an_app_id_is_not_mistaken_for_ours()
    {
        // Written the way B33's native shortcuts are: no AppUserModelID at all.
        var path = Path.Combine(_programs, AppIdentityShortcut.ShortcutFileName);
        Directory.CreateDirectory(_programs);
        var interop = typeof(AppIdentityShortcut).Assembly.GetType("PagentOS.SessionCompanion.Projects.ShellLinkInterop", throwOnError: true)!;
        interop.GetMethod("CreateShortcut", [typeof(string), typeof(string), typeof(string), typeof(string)])!
            .Invoke(null, [path, Target, _programs, "no id"]);
        Assert.Null(AppIdentityShortcut.Read(_programs)!.Value.AppUserModelId);

        Assert.Equal(ShortcutState.Updated, AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target));
        Assert.Equal("PagentOS.Companion", AppIdentityShortcut.Read(_programs)!.Value.AppUserModelId);
    }

    [Fact]
    public void A_corrupt_file_in_the_links_place_is_replaced_by_a_link()
    {
        Directory.CreateDirectory(_programs);
        File.WriteAllBytes(Path.Combine(_programs, AppIdentityShortcut.ShortcutFileName), [0x4C, 0x00, 0x13, 0x37]);

        Assert.Equal(ShortcutState.Updated, AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target));
        Assert.Equal("PagentOS.Companion", AppIdentityShortcut.Read(_programs)!.Value.AppUserModelId);
    }

    [Fact]
    public void A_turkish_lettered_link_name_is_written_by_the_unicode_interface()
    {
        const string name = "PagentOS Bildirimleri ğüşöçıİ.lnk";

        Assert.Equal(ShortcutState.Created, AppIdentityShortcut.Ensure(_programs, "PagentOS.Companion.Tr", Target, name));
        Assert.Equal("PagentOS.Companion.Tr", AppIdentityShortcut.Read(_programs, name)!.Value.AppUserModelId);
        Assert.True(AppIdentityShortcut.Remove(_programs, name));
        Assert.False(AppIdentityShortcut.Remove(_programs, name));
        Assert.Null(AppIdentityShortcut.Read(_programs, name));
    }

    [Theory]
    [InlineData("")]
    [InlineData("has space")]
    [InlineData(".leading")]
    [InlineData("semi;colon")]
    [InlineData("PagentOS\\Companion")]
    public void An_app_id_windows_would_not_take_is_refused_before_anything_is_written(string appId)
    {
        Assert.Throws<ArgumentException>(() => AppIdentityShortcut.Ensure(_programs, appId, Target));
        Assert.False(Directory.Exists(_programs));
    }

    [Fact]
    public void An_app_id_longer_than_windows_allows_is_refused()
    {
        Assert.True(AppIdentityShortcut.IsValidAppId(new string('a', 128)));
        Assert.False(AppIdentityShortcut.IsValidAppId(new string('a', 129)));
    }

    [Fact]
    public void A_missing_target_or_a_bad_link_name_is_refused_before_anything_is_written()
    {
        Assert.Throws<FileNotFoundException>(() => AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Path.Combine(_programs, "gone.exe")));
        Assert.Throws<ArgumentException>(() => AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target, "companion.url"));
        Assert.Throws<ArgumentException>(() => AppIdentityShortcut.Ensure(_programs, AppIdentityShortcut.AppUserModelId, Target, "a|b.lnk"));
        Assert.False(Directory.Exists(_programs));
    }

    [Fact]
    public void The_product_writes_only_into_the_owners_own_start_menu()
    {
        // No all-users folder, no HKLM: the companion runs as the owner and needs no elevation.
        Assert.Equal(Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppIdentityShortcut.DefaultProgramsDirectory());
        var sources = string.Join('\n', Directory.GetFiles(Path.Combine(Support.CompanionSources.Directory(), "Notify"), "*.cs").Select(File.ReadAllText));
        Assert.DoesNotContain("CommonPrograms", sources, StringComparison.Ordinal);
        Assert.DoesNotContain("LocalMachine", sources, StringComparison.Ordinal);
        Assert.DoesNotContain("HKEY_LOCAL_MACHINE", sources, StringComparison.Ordinal);
        Assert.DoesNotContain("Microsoft.Win32", sources, StringComparison.Ordinal);
    }
}

using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// B34 (153–165, 674): the six managed mutations and the backup they take, through the REAL
/// <see cref="DocumentCapabilities"/> dispatcher over the lab's copy of the fixtures. Every
/// assertion below reads the DISK after the call (the record the device answered is checked
/// against what is really there), and every refusal is the typed one the protocol names.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class FileMutationTests : IDisposable
{
    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    private static string Sha(string path) => FileIdentity.Sha256Hex(path);

    [Fact]
    public void Write_creates_a_new_text_file_atomically_and_answers_its_record()
    {
        var path = Path.Combine(_lab.Root, "yeni-not.md");
        var result = _lab.Exec(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = path, ["text"] = "# Yeni\n\nMerhaba dünya.\n" });

        Assert.True(result["written"]!.GetValue<bool>());
        Assert.True(result["created"]!.GetValue<bool>());
        Assert.Equal(FileMutations.MethodAtomicReplace, result["method"]!.GetValue<string>());
        Assert.Null(result["before"]);
        Assert.Null(result["backup"]);
        Assert.Equal("# Yeni\n\nMerhaba dünya.\n", File.ReadAllText(path, Encoding.UTF8));
        Assert.Equal(Sha(path), result["after"]!["sha256"]!.GetValue<string>());
        Assert.Equal(path, result["after"]!["path"]!.GetValue<string>());
        Assert.True(result["observed"]!["exists"]!.GetValue<bool>());
        // No temporary file survives beside the target.
        Assert.Empty(Directory.EnumerateFiles(_lab.Root, ".yeni-not.md.pagentos-tmp-*"));
    }

    [Fact]
    public void Write_over_an_existing_file_backs_it_up_first_and_restore_brings_the_original_back()
    {
        var path = _lab.PathOf("notlar.md");
        var original = File.ReadAllBytes(path);
        var originalSha = Sha(path);

        var written = _lab.Exec(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = path, ["text"] = "değişti", ["expected_sha256"] = originalSha });

        Assert.False(written["created"]!.GetValue<bool>());
        Assert.Equal(originalSha, written["before"]!["sha256"]!.GetValue<string>());
        Assert.Equal(Sha(path), written["after"]!["sha256"]!.GetValue<string>());
        Assert.NotEqual(originalSha, written["after"]!["sha256"]!.GetValue<string>());
        Assert.Equal("değişti", File.ReadAllText(path, Encoding.UTF8));

        var backup = written["backup"]!.AsObject();
        var backupId = backup["backup_id"]!.GetValue<string>();
        Assert.StartsWith(FileMutations.BackupIdPrefix, backupId);
        Assert.Equal(originalSha, backup["sha256"]!.GetValue<string>());
        Assert.Equal(path, backup["source_path"]!.GetValue<string>());
        var backupPath = backup["backup_path"]!.GetValue<string>();
        Assert.StartsWith(Path.Combine(_lab.Root, FileMutations.UndoFolderName), backupPath);
        Assert.Equal(original, File.ReadAllBytes(backupPath));

        // The undo store is the companion's own and never a search hit.
        var search = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["pattern"] = "*.bak" });
        Assert.Empty(search["files"]!.AsArray());

        var restored = _lab.Exec(DocumentCapabilityNames.FileRestore, new JsonObject { ["backup_id"] = backupId });
        Assert.True(restored["restored"]!.GetValue<bool>());
        Assert.Equal(original, File.ReadAllBytes(path));
        Assert.Equal(originalSha, restored["after"]!["sha256"]!.GetValue<string>());
        // What the restore displaced ("değişti") was backed up too, so the restore is undoable.
        Assert.NotNull(restored["backup"]);
        Assert.Equal(written["after"]!["sha256"]!.GetValue<string>(), restored["before"]!["sha256"]!.GetValue<string>());
    }

    [Fact]
    public void Write_refuses_a_changed_file_a_secret_name_an_office_document_and_a_path_outside_the_roots()
    {
        var path = _lab.PathOf("notlar.md");
        var changed = _lab.ExpectFailure(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = path, ["text"] = "x", ["expected_sha256"] = new string('0', 64) });
        Assert.Equal(ErrorClasses.ValidationError, changed.ErrorClass);
        Assert.Contains("changed since it was read", changed.Message);
        Assert.NotEqual("x", File.ReadAllText(path));

        var secret = _lab.ExpectFailure(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = Path.Combine(_lab.Root, ".env"), ["text"] = "KEY=1" });
        Assert.Equal(ErrorClasses.PermissionDenied, secret.ErrorClass);
        Assert.False(File.Exists(Path.Combine(_lab.Root, ".env")));

        var office = _lab.ExpectFailure(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = _lab.PathOf("sunum-q3.pptx"), ["text"] = "x" });
        Assert.Equal(ErrorClasses.UnsupportedFormat, office.ErrorClass);

        var outside = _lab.ExpectFailure(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = Path.Combine(Path.GetTempPath(), "pagentos-outside-" + _lab.RunId + ".txt"), ["text"] = "x" });
        Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);

        var relative = _lab.ExpectFailure(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = "notlar.md", ["text"] = "x" });
        Assert.Equal(ErrorClasses.ValidationError, relative.ErrorClass);
    }

    [Fact]
    public void Append_adds_to_the_end_keeps_a_backup_and_the_hashes_tell_the_story()
    {
        var path = _lab.PathOf("veri.csv");
        var before = Sha(path);
        var result = _lab.Exec(DocumentCapabilityNames.FileAppend, new JsonObject { ["path"] = path, ["text"] = "yeni,satır,1\n" });

        Assert.True(result["appended"]!.GetValue<bool>());
        Assert.Equal(before, result["before"]!["sha256"]!.GetValue<string>());
        Assert.Equal(Sha(path), result["after"]!["sha256"]!.GetValue<string>());
        Assert.EndsWith("yeni,satır,1\n", File.ReadAllText(path, Encoding.UTF8));
        Assert.Equal(before, result["backup"]!["sha256"]!.GetValue<string>());

        var missing = _lab.ExpectFailure(DocumentCapabilityNames.FileAppend, new JsonObject { ["path"] = Path.Combine(_lab.Root, "yok.txt"), ["text"] = "x" });
        Assert.Equal(ErrorClasses.NotFound, missing.ErrorClass);
    }

    [Fact]
    public void Rename_move_and_copy_keep_the_content_and_never_overwrite()
    {
        var path = _lab.PathOf("kod.py");
        var sha = Sha(path);

        var renamed = _lab.Exec(DocumentCapabilityNames.FileRename, new JsonObject { ["path"] = path, ["new_name"] = "kod-yeni.py" });
        var renamedPath = Path.Combine(_lab.Root, "kod-yeni.py");
        Assert.True(renamed["renamed"]!.GetValue<bool>());
        Assert.False(File.Exists(path));
        Assert.Equal(sha, Sha(renamedPath));
        Assert.Equal(renamedPath, renamed["after"]!["path"]!.GetValue<string>());
        Assert.False(renamed["observed"]!["source_exists"]!.GetValue<bool>());
        Assert.True(renamed["observed"]!["target_exists"]!.GetValue<bool>());

        // The new record's id is usable at once.
        var newId = renamed["after"]!["file_id"]!.GetValue<string>();
        var located = _lab.Exec(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = newId });
        Assert.Equal(renamedPath, located["file"]!["path"]!.GetValue<string>());

        var taken = _lab.ExpectFailure(DocumentCapabilityNames.FileRename, new JsonObject { ["path"] = renamedPath, ["new_name"] = "notlar.md" });
        Assert.Equal(ErrorClasses.ValidationError, taken.ErrorClass);
        Assert.Contains("already exists", taken.Message);
        var traversal = _lab.ExpectFailure(DocumentCapabilityNames.FileRename, new JsonObject { ["path"] = renamedPath, ["new_name"] = @"..\kod.py" });
        Assert.Equal(ErrorClasses.ValidationError, traversal.ErrorClass);

        var folder = Path.Combine(_lab.Root, "yedek");
        var moved = _lab.Exec(DocumentCapabilityNames.FileMove, new JsonObject { ["path"] = renamedPath, ["destination_dir"] = folder });
        var movedPath = Path.Combine(folder, "kod-yeni.py");
        Assert.True(moved["moved"]!.GetValue<bool>());
        Assert.False(File.Exists(renamedPath));
        Assert.Equal(sha, Sha(movedPath));

        var outside = _lab.ExpectFailure(DocumentCapabilityNames.FileMove, new JsonObject { ["path"] = movedPath, ["destination_dir"] = Path.GetTempPath() });
        Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);
        Assert.True(File.Exists(movedPath));

        var copied = _lab.Exec(DocumentCapabilityNames.FileCopy, new JsonObject { ["path"] = movedPath, ["destination_dir"] = _lab.Root, ["new_name"] = "kod-kopya.py" });
        var copyPath = Path.Combine(_lab.Root, "kod-kopya.py");
        Assert.True(copied["copied"]!.GetValue<bool>());
        Assert.True(File.Exists(movedPath));
        Assert.Equal(sha, Sha(copyPath));
        Assert.Equal(sha, copied["after"]!["sha256"]!.GetValue<string>());

        var again = _lab.ExpectFailure(DocumentCapabilityNames.FileCopy, new JsonObject { ["path"] = movedPath, ["destination_path"] = copyPath });
        Assert.Equal(ErrorClasses.ValidationError, again.ErrorClass);
    }

    [Fact]
    public void Trash_with_backup_keeps_a_copy_the_restore_can_bring_back()
    {
        var path = Path.Combine(_lab.Root, "silinecek.txt");
        File.WriteAllText(path, "geri gelecek");
        var sha = Sha(path);

        var trashed = _lab.Exec(DocumentCapabilityNames.FileTrash, new JsonObject { ["path"] = path, ["backup"] = true });
        Assert.True(trashed["trashed"]!.GetValue<bool>());
        Assert.False(File.Exists(path));
        var backupId = trashed["backup"]!["backup_id"]!.GetValue<string>();
        Assert.Equal(sha, trashed["backup"]!["sha256"]!.GetValue<string>());

        var restored = _lab.Exec(DocumentCapabilityNames.FileRestore, new JsonObject { ["backup_id"] = backupId });
        Assert.True(restored["restored"]!.GetValue<bool>());
        Assert.Null(restored["before"]);
        Assert.Equal("geri gelecek", File.ReadAllText(path));
        Assert.Equal(sha, Sha(path));

        var unknown = _lab.ExpectFailure(DocumentCapabilityNames.FileRestore, new JsonObject { ["backup_id"] = "bak:0000000000000000" });
        Assert.Equal(ErrorClasses.NotFound, unknown.ErrorClass);
    }

    [Fact]
    public void A_corrupt_backup_is_never_restored()
    {
        var path = _lab.PathOf("ayarlar.json");
        var written = _lab.Exec(DocumentCapabilityNames.FileWrite, new JsonObject { ["path"] = path, ["text"] = "{}" });
        var backupPath = written["backup"]!["backup_path"]!.GetValue<string>();
        File.WriteAllText(backupPath, "bozuk");

        var refused = _lab.ExpectFailure(DocumentCapabilityNames.FileRestore, new JsonObject { ["backup_id"] = written["backup"]!["backup_id"]!.GetValue<string>() });
        Assert.Equal(ErrorClasses.UnsupportedFormat, refused.ErrorClass);
        Assert.Equal("{}", File.ReadAllText(path));
    }

    [Fact]
    public void The_six_are_advertised_after_file_trash_in_the_protocols_order()
    {
        Assert.Equal(
            ["file.write", "file.append", "file.rename", "file.move", "file.copy", "file.restore"],
            DocumentCapabilityNames.All.Skip(8).ToArray());
        Assert.Equal(14, DocumentCapabilityNames.All.Count);
    }
}

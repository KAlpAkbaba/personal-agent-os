---
name: junction-escape-recurring-pattern
description: Recurring bug class in this codebase - path-containment checks that rely on is_symlink() on the leaf entry miss Windows junctions/reparse points on ancestor directories. Actively check for it in every new module that claims root confinement.
metadata:
  type: feedback
---

Whenever a module in this repo claims "cannot read/write outside an authorized root" and the enforcement is `path.is_symlink()` on the entry being touched, verify it by actually creating a Windows NTFS junction (`New-Item -ItemType Junction`, no admin/Developer-Mode privilege required, unlike `os.symlink` which needs elevation on Windows) inside the root pointing outside it, then check whether the traversal/open code follows it.

**Why:** Python's `Path.is_symlink()` (and the .NET `FileInfo.ResolveLinkTarget`-on-leaf-only pattern) checks the reparse tag of the specific entry, but a junction is `IO_REPARSE_TAG_MOUNT_POINT`, not `IO_REPARSE_TAG_SYMLINK` — an ancestor **directory** being a junction is invisible to a leaf-only check, and `Path.rglob()`/directory enumeration transparently follows it. This exact bug class has now been found independently in three unrelated modules (M3, M8, M19):
- M3: `ArtifactOpener.ResolveFinalTarget` (Windows agent, C#) — leaf-only reparse check, ancestor junction inside an artifact root escapes containment. See [[m3-artifact-research-security-review]] finding #2.
- M8: `checks.py::collect_files` (security assessment collector, Python) — same shape, confirmed by actually creating a junction and observing `is_symlink()==False` all the way through while `.resolve()` correctly reveals the escape. See [[m8-security-agent-review]].
- M19: `TerminalRunner.IsUnderAuthorisedRoot` (Windows agent, C#) — same lexical `Path.GetFullPath`+`StartsWith` shape, confirmed live by creating a real junction inside the authorised root and reading the outside file's content through it. Escalated to High because `OperatorRoots` defaults to the whole user profile, which includes Downloads — a location the sibling `browser.download` capability already writes attacker-influenced files into. See [[m19-digital-operator-security-review]].

Every time, the SAME repo has a sibling module (M3: none readily available at review time; M8: `remediation.py::_authorized_file`) that does the correct thing — resolve the full path first, THEN check ancestor containment (`root not in path.resolve().parents`) — proving the fix is well-understood in this codebase, just not applied consistently.

**How to apply:** Any time a new module (Windows agent, security collector, artifact renderer, future file-serving code) claims path/root confinement, don't just read the containment check — grep for whether it resolves-then-contains or contains-then-relies-on-leaf-symlink-check, and if there's any doubt, actually build a junction and prove it one way or the other rather than reasoning about it abstractly. Flag as Medium (not Critical/High) when exploitation requires local write access to the authorized root already (defense-in-depth gap), unless the root is attacker-writable by design (e.g. a downloads folder, an untrusted-provider staging area) in which case escalate.

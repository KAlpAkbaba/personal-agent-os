"""Where a native build is allowed to happen, and what it is allowed to contain
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §5, §9; ADR-0095 decision 4).

The device owns the filesystem and enforces the authorised roots itself
(``OperatorOptions.ProjectsFolderName``), and that is the enforcement that matters. This
module is the Cloud Core's half: it will not ASK the device to write somewhere the rule
does not allow, and it will not hand a compiler a file set containing something that is
not source.

Two guards, and both are the kind that only work if they are applied before anything
happens rather than checked afterwards.

**Containment is resolve-then-contain.** A path is resolved first — symlinks, ``..``, short
names and all — and only then compared against the resolved root. Comparing the strings
first and resolving later is the bug this pattern exists to avoid, and it is the one every
path check in this repository is written the same way to prevent.

**An extension allowlist, because a build EXECUTES what it is given.** M23's
``ProjectFiles`` policy checks bounds, path shape, UTF-8 and secrets, which is right for a
web project whose files are served. A native project is compiled and run, and MSBuild will
happily execute a ``.ps1`` or a ``.bat`` a project file points at. So the native factory
additionally requires that every file it renders is one of a small set of things a build
legitimately contains. A template that grows a script is refused here rather than run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from app.appfactory.generator import ProjectFiles
from app.nativefactory.spec import NativeFactoryError

#: The subdirectory of the device's Projects root that native builds live in. It matches
#: `OperatorOptions`' own `native` folder, and the two are held together by a test that
#: reads the C# source rather than by this comment.
NATIVE_SUBDIR: Final = "native"
PROJECTS_FOLDER: Final = "PagentOS Projects"

#: What a native project may contain. Source, project files, and the manifest this factory
#: writes — nothing that a build step could be pointed at and told to run.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".cs",       # C#
        ".csproj",   # the project file
        ".xaml",     # WPF markup
        ".kt",       # Kotlin
        ".kts",      # Gradle Kotlin DSL
        ".gradle",   # Gradle Groovy DSL
        ".xml",      # Android manifests, resources
        ".json",     # this factory's manifest.json
        ".props",    # MSBuild property sheets
        ".targets",  # MSBuild targets
        ".md",       # a README beside the project
        ".txt",
    }
)

#: Extensions a build could be pointed at and told to EXECUTE. Named explicitly, so the
#: refusal message can say what was wrong rather than "unknown extension" — and so that
#: anyone widening `ALLOWED_EXTENSIONS` has to look at this list first.
EXECUTABLE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".ps1", ".bat", ".cmd", ".sh", ".exe", ".dll", ".com", ".msi", ".vbs", ".js", ".py"}
)

assert not (ALLOWED_EXTENSIONS & EXECUTABLE_EXTENSIONS), "a runnable file is not source"


def projects_root() -> Path:
    """The device's Projects root as the Cloud Core understands it.

    `USERPROFILE` on Windows, `HOME` elsewhere, so the same code is testable off the
    owner's machine — the containment rule is what is being enforced, not the drive letter.
    """
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / "Documents" / PROJECTS_FOLDER


def native_root() -> Path:
    return projects_root() / NATIVE_SUBDIR


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve `candidate`, then require it to be inside the resolved `root`.

    Raises :class:`NativeFactoryError` rather than returning a bool: a containment check
    whose result can be ignored is a containment check that will be.
    """
    resolved_root = root.resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # a path the OS cannot even resolve is not inside anything
        raise NativeFactoryError(
            "path_unresolvable", f"Yol çözümlenemedi efendim: {exc}"
        ) from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise NativeFactoryError(
            "path_outside_root",
            "Bu klasör izin verilen çalışma alanının dışında efendim; derleme orada yapılmaz.",
        )
    return resolved


def build_dir_for(slug: str, *, root: Path | None = None) -> Path:
    """Where this application's project goes. The slug is closed-alphabet by construction,
    so it cannot climb out — and it is still resolved and contained, because "it cannot" is
    a claim about today's validator and containment is a claim about the filesystem."""
    base = root or native_root()
    return resolve_within(base, base / slug)


def check_extensions(project: ProjectFiles) -> None:
    """Every rendered file must be something a build legitimately contains.

    A native project is COMPILED and RUN, unlike M23's web projects, and MSBuild will
    happily execute a script a project file points at. So this refuses before a compiler
    ever sees the tree.
    """
    for file in project.files:
        suffix = Path(file.path).suffix.lower()
        if suffix in EXECUTABLE_EXTENSIONS:
            raise NativeFactoryError(
                "file_not_source",
                f"{file.path} çalıştırılabilir bir dosya efendim; şablon kaynak dışında "
                "bir şey üretmemeli.",
            )
        if suffix not in ALLOWED_EXTENSIONS:
            raise NativeFactoryError(
                "file_not_source",
                f"{file.path} tanınmayan bir dosya türü efendim ({suffix or 'uzantısız'}).",
            )


__all__ = [
    "ALLOWED_EXTENSIONS",
    "EXECUTABLE_EXTENSIONS",
    "NATIVE_SUBDIR",
    "PROJECTS_FOLDER",
    "build_dir_for",
    "check_extensions",
    "native_root",
    "projects_root",
    "resolve_within",
]

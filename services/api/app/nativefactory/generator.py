"""Render a validated :class:`NativeAppSpec` into a project
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §2, §4).

The M23 discipline, unchanged and inherited on purpose: the templates are FILES with
``{{SLOT}}`` markers, every slot is filled from a field a validated spec already
guarantees, and free text never reaches code. What renders here is `ProjectFiles` — the
same structure `app.appfactory.validation` already knows how to refuse — so the native
projects go through one policy rather than a second one written to be more permissive.

Two rules this module holds that the template files cannot hold for themselves:

* **Every slot must be filled.** A `{{SLOT}}` left in a rendered file is a template that
  drifted from the spec, and it is refused loudly rather than shipped to a compiler that
  would report it as a mysterious syntax error.
* **Only known slots may exist.** A template that grows a slot the renderer does not know
  is the same drift from the other side, and it fails here rather than rendering a literal
  `{{WHATEVER}}` into someone's source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from app.appfactory.generator import ProjectFile, ProjectFiles
from app.nativefactory.spec import (
    TEMPLATE_COUNTER_MOBILE,
    TEMPLATE_NOTES_DESKTOP,
    NativeAppSpec,
    NativeFactoryError,
)

TEMPLATE_ROOT: Final = Path(__file__).resolve().parent / "templates"
TEMPLATE_SUFFIX: Final = ".tmpl"

_SLOT_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

#: B49 (req 474): both templates render. `counter-mobile` renders a real Gradle Kotlin
#: project whose structure is checked by the tests; BUILDING it still needs a JDK (owner
#: item 33), and the build step - not the renderer - is where that refusal lives now.
RENDERABLE_TEMPLATES: Final[frozenset[str]] = frozenset(
    {TEMPLATE_NOTES_DESKTOP, TEMPLATE_COUNTER_MOBILE}
)


def _slots_for(spec: NativeAppSpec) -> dict[str, str]:
    """Every value a template may interpolate. Each comes from a validated field.

    `NAMESPACE` is derived rather than taken: a C# namespace has a grammar the slug does
    not (no leading digit, no hyphens), and deriving it here means no spec can name one.
    """
    slug = spec.slug
    namespace = "".join(part.capitalize() for part in slug.split("-") if part) or "PagentOsApp"
    if namespace[0].isdigit():
        namespace = "App" + namespace
    return {
        "SLUG": slug,
        "NAMESPACE": namespace,
        "TITLE": spec.display_title,
        "VERSION": spec.version,
        "ASSEMBLY_VERSION": spec.assembly_version,
        # B49 (req 474): the Android identities, derived - never taken from free text.
        "ANDROID_PACKAGE": android_package(slug),
        "VERSION_CODE": str(android_version_code(spec.version)),
        "TITLE_RESOURCE": android_string_resource(spec.display_title),
    }


#: Java/Kotlin words a package segment may not be.
_RESERVED_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "abstract",
        "as",
        "boolean",
        "break",
        "byte",
        "case",
        "catch",
        "char",
        "class",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extends",
        "false",
        "final",
        "finally",
        "float",
        "for",
        "fun",
        "goto",
        "if",
        "implements",
        "import",
        "in",
        "instanceof",
        "int",
        "interface",
        "is",
        "long",
        "native",
        "new",
        "null",
        "object",
        "package",
        "private",
        "protected",
        "public",
        "return",
        "short",
        "static",
        "super",
        "switch",
        "synchronized",
        "this",
        "throw",
        "throws",
        "transient",
        "true",
        "try",
        "typealias",
        "val",
        "var",
        "void",
        "volatile",
        "when",
        "while",
    }
)


def android_package(slug: str) -> str:
    """``com.pagentos.<slug without hyphens>``: a valid application id for any slug the
    spec accepts - never a leading digit, never a reserved word."""
    segment = "".join(ch for ch in slug.lower() if ch.isascii() and ch.isalnum()) or "app"
    if segment[0].isdigit():
        segment = "app" + segment
    if segment in _RESERVED_SEGMENTS:
        segment += "app"
    return f"com.pagentos.{segment}"


def android_version_code(version: str) -> int:
    """A strictly increasing integer for a semver string (major*1e6 + minor*1e3 + patch + 1):
    Google Play's ``versionCode`` must be at least 1 and grow with every release, and clamping
    0.0.0 up to 1 would have given 0.0.0 and 0.0.1 the same code."""
    parts = [int(p) for p in version.split(".")[:3]] + [0, 0, 0]
    major, minor, patch = parts[0], min(parts[1], 999), min(parts[2], 999)
    return major * 1_000_000 + minor * 1_000 + patch + 1


def android_string_resource(text: str) -> str:
    """A string escaped for an Android ``strings.xml`` value: XML first, then the resource
    format's own apostrophe, quote and backslash rules (an unescaped ``'`` breaks aapt)."""
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return escaped.replace("'", "\\'").replace('"', '\\"')


def render(spec: NativeAppSpec) -> ProjectFiles:
    """The template for this spec, rendered into the structure M23's policy validates."""
    if spec.template not in RENDERABLE_TEMPLATES:
        raise NativeFactoryError(
            "template_unavailable",
            f"{spec.template} şablonu bu makinede üretilemiyor efendim.",
        )
    root = TEMPLATE_ROOT / spec.template
    if not root.is_dir():
        raise NativeFactoryError("template_missing", f"{spec.template} şablonu bulunamadı efendim.")

    slots = _slots_for(spec)
    files: list[ProjectFile] = []
    for source in sorted(root.rglob("*" + TEMPLATE_SUFFIX)):
        relative = source.relative_to(root).as_posix()
        if not relative.endswith(TEMPLATE_SUFFIX):  # pragma: no cover - rglob guarantees it
            continue
        path = _fill(relative[: -len(TEMPLATE_SUFFIX)], slots, where=relative)
        text = _fill(source.read_text(encoding="utf-8"), slots, where=relative)
        files.append(ProjectFile(path=path, text=text))

    if not files:
        raise NativeFactoryError("template_empty", f"{spec.template} şablonu boş efendim.")
    return ProjectFiles(files=tuple(files))


def _fill(text: str, slots: dict[str, str], *, where: str) -> str:
    """Substitute every known slot, and refuse anything that is left.

    Both directions are checked because both directions have gone wrong in this repository:
    a renderer that silently leaves `{{SLOT}}` in a `.csproj` produces a compiler error
    nobody can read, and a template naming a slot the renderer does not know produces a
    literal `{{WHATEVER}}` in shipped source.
    """
    unknown = {name for name in _SLOT_RE.findall(text) if name not in slots}
    if unknown:
        raise NativeFactoryError(
            "template_slot_unknown",
            f"{where} şablonunda tanınmayan alan var: {', '.join(sorted(unknown))}.",
        )
    filled = _SLOT_RE.sub(lambda match: slots[match.group(1)], text)
    leftover = _SLOT_RE.findall(filled)
    if leftover:  # pragma: no cover - unreachable while every match is substituted
        raise NativeFactoryError(
            "template_slot_unfilled",
            f"{where} şablonunda doldurulmamış alan kaldı: {', '.join(sorted(set(leftover)))}.",
        )
    return filled


def template_slot_names(template: str) -> frozenset[str]:
    """Every slot the template's files actually use — for the guard that keeps the
    renderer and the templates from drifting apart in either direction."""
    root = TEMPLATE_ROOT / template
    names: set[str] = set()
    for source in root.rglob("*" + TEMPLATE_SUFFIX):
        names.update(_SLOT_RE.findall(source.relative_to(root).as_posix()))
        names.update(_SLOT_RE.findall(source.read_text(encoding="utf-8")))
    return frozenset(names)


__all__ = [
    "RENDERABLE_TEMPLATES",
    "android_package",
    "android_string_resource",
    "android_version_code",
    "TEMPLATE_ROOT",
    "render",
    "template_slot_names",
]

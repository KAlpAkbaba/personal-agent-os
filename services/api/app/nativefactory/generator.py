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
    TEMPLATE_NOTES_DESKTOP,
    NativeAppSpec,
    NativeFactoryError,
)

TEMPLATE_ROOT: Final = Path(__file__).resolve().parent / "templates"
TEMPLATE_SUFFIX: Final = ".tmpl"

_SLOT_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

#: The only stack with a real template today. `counter-mobile` is Android, and Android
#: cannot build here (no JDK, owner item 33) - so rather than ship a template whose build
#: can only fail, the Android generator is a named refusal until item 33 lands. The
#: template's absence is the honest signal, not a stub that looks like progress.
RENDERABLE_TEMPLATES: Final[frozenset[str]] = frozenset({TEMPLATE_NOTES_DESKTOP})


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
    }


def render(spec: NativeAppSpec) -> ProjectFiles:
    """The template for this spec, rendered into the structure M23's policy validates."""
    if spec.template not in RENDERABLE_TEMPLATES:
        raise NativeFactoryError(
            "template_unavailable",
            f"{spec.template} şablonu bu makinede üretilemiyor efendim; "
            "Android tarafı JDK bekliyor (madde 33).",
        )
    root = TEMPLATE_ROOT / spec.template
    if not root.is_dir():
        raise NativeFactoryError(
            "template_missing", f"{spec.template} şablonu bulunamadı efendim."
        )

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
        raise NativeFactoryError(
            "template_empty", f"{spec.template} şablonu boş efendim."
        )
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
    "TEMPLATE_ROOT",
    "render",
    "template_slot_names",
]

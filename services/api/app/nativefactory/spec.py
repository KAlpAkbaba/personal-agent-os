"""``NativeAppSpec``: the structured, validated input to the Native Application Factory
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §2, ADR-0095).

The same discipline as M22's ``ArtifactSpec`` and M23's ``AppSpec``: the assistant turns
the owner's words into a spec, and a generator only ever sees the spec. Free text never
reaches a project file, a manifest or a command line.

What is different here, and what the whole milestone turns on, is that the OUTPUT is a
binary somebody could double-click. So this module carries three things M23's did not:

* **targets**, because "make me a desktop app" and "give me an installer" are different
  requests over the same project, and a target the local toolchain cannot reach must be
  refused by name rather than attempted;
* **a version**, because an artifact that cannot say which build it is cannot be validated
  against the spec that asked for it (§4 stamps it into the assembly and reads it back OUT
  of the produced file);
* **a closed feature vocabulary**, because a WPF window's controls are code, and code is
  the one place owner text may never land.

Bounds are sanity bounds against a runaway cognitive-backend response, not a claim about
how large a real project may be — the same posture the two specs before this one state.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- targets

TARGET_WINDOWS_EXE: Final = "windows_exe"
TARGET_WINDOWS_PORTABLE: Final = "windows_portable"
TARGET_WINDOWS_MSIX: Final = "windows_msix"
TARGET_ANDROID_APK: Final = "android_apk"
TARGET_ANDROID_AAB: Final = "android_aab"

NATIVE_TARGETS: Final[tuple[str, ...]] = (
    TARGET_WINDOWS_EXE,
    TARGET_WINDOWS_PORTABLE,
    TARGET_WINDOWS_MSIX,
    TARGET_ANDROID_APK,
    TARGET_ANDROID_AAB,
)

#: iOS is deliberately NOT a target value. ADR-0095 decision 3: there is no macOS, no
#: Xcode, and (measured 2026-09-09) no MAUI workload, so not even a shared head compiles
#: here. A target the system can never satisfy is not a target that is "unavailable today"
#: — offering it would generate a project that looks like progress toward something that
#: cannot happen. The refusal is a sentence, in `app.nativefactory.stacks`, not an enum
#: member.
WINDOWS_TARGETS: Final[frozenset[str]] = frozenset(
    {TARGET_WINDOWS_EXE, TARGET_WINDOWS_PORTABLE, TARGET_WINDOWS_MSIX}
)
ANDROID_TARGETS: Final[frozenset[str]] = frozenset({TARGET_ANDROID_APK, TARGET_ANDROID_AAB})

# ---------------------------------------------------------------------------- stacks

STACK_DOTNET_WPF: Final = "dotnet_wpf"
STACK_DOTNET_WINFORMS: Final = "dotnet_winforms"
STACK_TAURI: Final = "tauri"
STACK_ANDROID_KOTLIN: Final = "android_kotlin"

NATIVE_STACKS: Final[tuple[str, ...]] = (
    STACK_DOTNET_WPF,
    STACK_DOTNET_WINFORMS,
    STACK_TAURI,
    STACK_ANDROID_KOTLIN,
)

#: `dotnet_maui` is absent for the same reason iOS is: `dotnet workload list` reports no
#: MAUI workload on this machine, installing one is a download, and the assistant starts
#: no downloads (ADR-0095 decision 1). A spec naming it would be a spec describing a build
#: that cannot run.
assert "dotnet_maui" not in NATIVE_STACKS

STACK_TARGETS: Final[dict[str, frozenset[str]]] = {
    STACK_DOTNET_WPF: WINDOWS_TARGETS,
    STACK_DOTNET_WINFORMS: WINDOWS_TARGETS,
    STACK_TAURI: WINDOWS_TARGETS,
    STACK_ANDROID_KOTLIN: ANDROID_TARGETS,
}

# ------------------------------------------------------------------------- templates

TEMPLATE_NOTES_DESKTOP: Final = "notes-desktop"
TEMPLATE_COUNTER_MOBILE: Final = "counter-mobile"

NATIVE_TEMPLATES: Final[tuple[str, ...]] = (TEMPLATE_NOTES_DESKTOP, TEMPLATE_COUNTER_MOBILE)

#: Every template is pinned to exactly one stack, so a mismatched pair is refused before
#: generation starts (M23's own `TEMPLATE_KIND` rule).
TEMPLATE_STACK: Final[dict[str, str]] = {
    TEMPLATE_NOTES_DESKTOP: STACK_DOTNET_WPF,
    TEMPLATE_COUNTER_MOBILE: STACK_ANDROID_KOTLIN,
}

# -------------------------------------------------------------------------- features

#: The closed feature vocabulary. A WPF window's controls are C#, and code is the one place
#: owner text may never land, so a feature is a NAME the template knows — never a sentence
#: describing what the app should do.
FEATURE_ADD_ITEM: Final = "add_item"
FEATURE_LIST_ITEMS: Final = "list_items"
FEATURE_DELETE_ITEM: Final = "delete_item"
FEATURE_PERSIST_LOCAL: Final = "persist_local"
FEATURE_COUNTER: Final = "counter"
FEATURE_ABOUT: Final = "about"

NATIVE_FEATURES: Final[tuple[str, ...]] = (
    FEATURE_ADD_ITEM,
    FEATURE_LIST_ITEMS,
    FEATURE_DELETE_ITEM,
    FEATURE_PERSIST_LOCAL,
    FEATURE_COUNTER,
    FEATURE_ABOUT,
)

TEMPLATE_FEATURES: Final[dict[str, frozenset[str]]] = {
    TEMPLATE_NOTES_DESKTOP: frozenset(
        {
            FEATURE_ADD_ITEM,
            FEATURE_LIST_ITEMS,
            FEATURE_DELETE_ITEM,
            FEATURE_PERSIST_LOCAL,
            FEATURE_ABOUT,
        }
    ),
    TEMPLATE_COUNTER_MOBILE: frozenset({FEATURE_COUNTER, FEATURE_ABOUT}),
}

PERSISTENCE_NONE: Final = "none"
PERSISTENCE_LOCAL_FILE: Final = "local_file"
PERSISTENCE_KINDS: Final[tuple[str, ...]] = (PERSISTENCE_NONE, PERSISTENCE_LOCAL_FILE)

# ---------------------------------------------------------------------------- bounds

MAX_NAME_CHARS: Final = 60
MAX_TITLE_CHARS: Final = 80
MAX_FEATURES: Final = len(NATIVE_FEATURES)
MAX_TARGETS: Final = len(NATIVE_TARGETS)

#: A display name may carry Turkish letters, spaces, and the punctuation real application
#: names use — "Notlar & Fikirler" is a name, not an attack, and `&` is escaped by every
#: format this value reaches (XAML, AppxManifest, the .csproj's metadata). What it may NOT
#: carry is markup STRUCTURE, or anything that could leave the attribute it lives in.
#: Refused rather than escaped, for those: an owner asking for a `<` in an application name
#: has made a mistake worth telling them about, and silently rewriting their name would put
#: that decision in the wrong place.
_TITLE_FORBIDDEN = re.compile(r"[<>\"{}\\\x00-\x1f]")

#: The identity every generated artefact is named and stamped by: closed alphabet, so it
#: can be a directory name, an assembly name, an MSIX package identity and a file name
#: without any of those needing to escape it.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
SLUG_PATTERN: Final = r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$"
_SLUG_VALID = re.compile(SLUG_PATTERN)

#: `1.2.3` only — no pre-release, no build metadata. It has to be expressible as an
#: assembly version, an MSIX `Version` (which wants four parts and gets `.0` appended) and
#: a semver at once, and the intersection of those three is exactly this.
VERSION_PATTERN: Final = r"^\d{1,4}\.\d{1,4}\.\d{1,5}$"
_VERSION_VALID = re.compile(VERSION_PATTERN)


def slug_for_name(name: str) -> str:
    """A closed-alphabet identity from a display name, or the fallback when nothing of the
    name survives (a name written entirely in a script this slug cannot carry is not an
    error — it just cannot BE the identity)."""
    lowered = name.strip().lower()
    for source, target in (
        ("ı", "i"), ("i̇", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"), ("ö", "o"), ("ç", "c"),
    ):
        lowered = lowered.replace(source, target)
    slug = _SLUG_RE.sub("-", lowered).strip("-")[:40].strip("-")
    return slug if _SLUG_VALID.match(slug) else "pagentos-app"


class NativeFactoryError(ValueError):
    """A spec the factory refuses, with a machine-readable class and a Turkish sentence.

    Never echoes owner text back in the message: the reason names the FIELD and the RULE,
    so a receipt can be spoken without repeating something that might itself be the
    problem.
    """

    def __init__(self, error_class: str, speech: str) -> None:
        super().__init__(speech)
        self.error_class = error_class
        self.speech = speech


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NativeAppSpec(_StrictModel):
    """One application the factory may build. Data, validated, with no free text in it."""

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS)
    template: Literal["notes-desktop", "counter-mobile"]
    stack: Literal["dotnet_wpf", "dotnet_winforms", "tauri", "android_kotlin"] | None = None
    targets: list[str] = Field(min_length=1, max_length=MAX_TARGETS)
    version: str = Field(default="0.1.0", pattern=VERSION_PATTERN)
    persistence: Literal["none", "local_file"] = PERSISTENCE_NONE
    features: list[str] = Field(default_factory=list, max_length=MAX_FEATURES)

    @field_validator("name", "title")
    @classmethod
    def _no_markup(cls, value: str | None) -> str | None:
        if value is not None and _TITLE_FORBIDDEN.search(value):
            raise ValueError(
                "adında XML/kod anlamı olan bir karakter var; düz bir ad kullanın"
            )
        return value

    @field_validator("targets")
    @classmethod
    def _known_targets(cls, value: list[str]) -> list[str]:
        for target in value:
            if target not in NATIVE_TARGETS:
                raise ValueError(f"bilinmeyen hedef: {target}")
        if len(set(value)) != len(value):
            raise ValueError("aynı hedef iki kez verilmiş")
        return value

    @field_validator("features")
    @classmethod
    def _known_features(cls, value: list[str]) -> list[str]:
        for feature in value:
            if feature not in NATIVE_FEATURES:
                raise ValueError(f"bilinmeyen özellik: {feature}")
        if len(set(value)) != len(value):
            raise ValueError("aynı özellik iki kez verilmiş")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> NativeAppSpec:
        pinned = TEMPLATE_STACK[self.template]
        if self.stack is not None and self.stack != pinned:
            raise ValueError(
                f"{self.template} şablonu {pinned} yığınına ait; {self.stack} istenmiş"
            )
        allowed_targets = STACK_TARGETS[pinned]
        for target in self.targets:
            if target not in allowed_targets:
                raise ValueError(f"{pinned} yığını {target} hedefini üretemez")
        allowed_features = TEMPLATE_FEATURES[self.template]
        for feature in self.features:
            if feature not in allowed_features:
                raise ValueError(f"{self.template} şablonunda {feature} özelliği yok")
        if self.persistence == PERSISTENCE_LOCAL_FILE and FEATURE_PERSIST_LOCAL not in (
            allowed_features
        ):
            raise ValueError(f"{self.template} şablonu yerel kalıcılık desteklemiyor")
        return self

    # ------------------------------------------------------------------ derived identity

    @property
    def resolved_stack(self) -> str:
        return self.stack or TEMPLATE_STACK[self.template]

    @property
    def slug(self) -> str:
        return slug_for_name(self.name)

    @property
    def display_title(self) -> str:
        return self.title or self.name

    @property
    def assembly_version(self) -> str:
        """`1.2.3` as the four-part number an assembly and an MSIX manifest both want."""
        return f"{self.version}.0"


def parse_spec(payload: dict) -> NativeAppSpec:
    """Validate an untrusted mapping into a spec, or raise :class:`NativeFactoryError`.

    The one door. A planner, a REST caller and a voice tool all come through here, so
    there is exactly one place where "is this buildable" is decided.
    """
    try:
        return NativeAppSpec.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic's own message is the useful one
        first = str(exc).splitlines()
        detail = next((line.strip() for line in first if "Value error," in line), "")
        detail = detail.split("Value error,", 1)[-1].strip() if detail else ""
        raise NativeFactoryError(
            "invalid_spec",
            f"Uygulama tanımı geçersiz efendim{': ' + detail if detail else ''}.",
        ) from exc


__all__ = [
    "ANDROID_TARGETS",
    "MAX_NAME_CHARS",
    "MAX_TITLE_CHARS",
    "NATIVE_FEATURES",
    "NATIVE_STACKS",
    "NATIVE_TARGETS",
    "NATIVE_TEMPLATES",
    "PERSISTENCE_KINDS",
    "PERSISTENCE_LOCAL_FILE",
    "PERSISTENCE_NONE",
    "SLUG_PATTERN",
    "STACK_ANDROID_KOTLIN",
    "STACK_DOTNET_WINFORMS",
    "STACK_DOTNET_WPF",
    "STACK_TARGETS",
    "STACK_TAURI",
    "TARGET_ANDROID_AAB",
    "TARGET_ANDROID_APK",
    "TARGET_WINDOWS_EXE",
    "TARGET_WINDOWS_MSIX",
    "TARGET_WINDOWS_PORTABLE",
    "TEMPLATE_COUNTER_MOBILE",
    "TEMPLATE_FEATURES",
    "TEMPLATE_NOTES_DESKTOP",
    "TEMPLATE_STACK",
    "VERSION_PATTERN",
    "WINDOWS_TARGETS",
    "NativeAppSpec",
    "NativeFactoryError",
    "parse_spec",
    "slug_for_name",
]

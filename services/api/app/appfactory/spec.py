"""``AppSpec``: the structured, validated input to the App Factory
(docs/M23_APP_FACTORY_SPEC.md §1-§2, ADR-0086 decision 1).

Produced by the assistant from the owner's own words, the same discipline
``app.artifacts.spec.ArtifactSpec`` already established for M22: never free prose handed
straight to a generator. A spec names a ``kind`` (``web_static`` / ``web_api`` / ``cli`` —
desktop/mobile are M28) and a built-in ``template`` (``task-tracker`` / ``static-page`` /
``cli-tool``); each template is pinned to exactly one kind (:data:`TEMPLATE_KIND`), so a
mismatched pair is refused before generation ever starts.

Bounds are sanity bounds against a runaway cognitive-backend response, not a promise that
a real owner project is ever this large — the same posture ``ArtifactSpec``'s own module
docstring states for its own numbers.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- kinds

KIND_WEB_STATIC = "web_static"
KIND_WEB_API = "web_api"
KIND_CLI = "cli"

APP_KINDS: tuple[str, ...] = (KIND_WEB_STATIC, KIND_WEB_API, KIND_CLI)

TEMPLATE_TASK_TRACKER = "task-tracker"
TEMPLATE_STATIC_PAGE = "static-page"
TEMPLATE_CLI_TOOL = "cli-tool"
#: B40 (req 425-434): the composed shape - not a template on disk but a plan the
#: composer builds from the owner's requirements (app.appfactory.composer).
TEMPLATE_COMPOSED = "composed"
#: B40 (req 425-434): the composed shape - not a template on disk but a plan the
#: composer builds from the owner's requirements (app.appfactory.composer).
TEMPLATE_COMPOSED = "composed"
#: B40 (req 425-434): the composed shape - not a template on disk but a plan the
#: composer builds from the owner's requirements (app.appfactory.composer).
TEMPLATE_COMPOSED = "composed"

APP_TEMPLATES: tuple[str, ...] = (
    TEMPLATE_TASK_TRACKER,
    TEMPLATE_STATIC_PAGE,
    TEMPLATE_CLI_TOOL,
    TEMPLATE_COMPOSED,
)

#: Every built-in template is pinned to exactly one kind (spec §1's table: "desktop/mobile
#: are M28" — ``web_api`` names a kind no built-in template renders yet either, reserved
#: for a future template exactly the way ``ArtifactSpec.KIND_FORMATS`` reserves formats a
#: kind may not use today).
TEMPLATE_KIND: dict[str, str] = {
    TEMPLATE_TASK_TRACKER: KIND_WEB_STATIC,
    TEMPLATE_STATIC_PAGE: KIND_WEB_STATIC,
    TEMPLATE_CLI_TOOL: KIND_CLI,
    TEMPLATE_COMPOSED: KIND_WEB_API,
}

MAX_NAME_CHARS = 100
MAX_ENTITIES = 20
MAX_FIELDS_PER_ENTITY = 20
MAX_SCREENS = 20
MAX_COMMANDS = 20
MAX_COMMAND_NAME_CHARS = 40
MAX_TEXT_CHARS = 2_000

_FIELD_TYPES = ("text", "number", "boolean", "date")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
#: B41 (found on the way): a Turkish project name ("Kitaplık") must not become a
#: mangled folder ("kitapl-k"); fold the six letters the way the composer already does.
_TR_FOLD = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")


def slug_for_name(name: str) -> str:
    """A filesystem/identifier-safe slug for ``name`` (the device's ``Projects\\<slug>``
    root, spec §1) — lowercase ASCII, dashes, bounded, never empty. Deterministic:
    the same name always slugifies the same way, the same discipline
    ``app.evolution.skills``'s own ``_ref_slugify`` uses for a skill's directory name."""
    lowered = name.strip().translate(_TR_FOLD).lower()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    if not slug:
        slug = "app"
    return slug[:64]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


#: Every character that can end a line or a statement somewhere a value is spliced: the
#: C0/C1 controls (incl. NUL, CR, LF, TAB), and the Unicode line/paragraph separators
#: U+2028/U+2029, which JavaScript treats as line terminators even inside a comment.
#: The M23 security review (2026-09-08) turned a newline in ``page_title`` into live
#: top-level JavaScript through a ``// {{PAGE_TITLE}}`` comment line: the generator no
#: longer splices free text into code at all, and the spec refuses the characters on top.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _no_control_characters(value: str, field: str, *, allow_newlines: bool = False) -> str:
    text = value.replace("\r\n", "\n").replace("\n", "") if allow_newlines else value
    hit = _CONTROL_RE.search(text)
    if hit is not None:
        raise ValueError(f"{field} must not contain the control character U+{ord(hit.group()):04X}")
    return value


# ------------------------------------------------------------------- task-tracker


class EntityField(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    type: Literal["text", "number", "boolean", "date"] = "text"

    @field_validator("name")
    @classmethod
    def _plain(cls, value: str) -> str:
        return _no_control_characters(value, "field name")


class Entity(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    fields: list[EntityField] = Field(default_factory=list, max_length=MAX_FIELDS_PER_ENTITY)

    @field_validator("name")
    @classmethod
    def _plain(cls, value: str) -> str:
        return _no_control_characters(value, "entity name")


class Screen(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    entity: str | None = None

    @field_validator("name", "entity")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        return None if value is None else _no_control_characters(value, "screen name")


# ------------------------------------------------------------------------ cli-tool


class Command(_StrictModel):
    name: str = Field(min_length=1, max_length=MAX_COMMAND_NAME_CHARS)
    description: str = Field(default="", max_length=200)

    @field_validator("description")
    @classmethod
    def _plain(cls, value: str) -> str:
        return _no_control_characters(value, "command description")

    @model_validator(mode="after")
    def _name_shape(self) -> Command:
        # A command name becomes an argv token and a JS identifier fragment (the
        # generator's own splice point re-checks this — defence in depth, the same
        # discipline app.evolution.skills's token guards document): ASCII
        # letters/digits/underscore/hyphen only.
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", self.name):
            raise ValueError(f"command name {self.name!r} is not a plain identifier")
        return self


# ------------------------------------------------------------------------- the spec


class AppSpec(_StrictModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    kind: Literal["web_static", "web_api", "cli"]
    template: Literal["task-tracker", "static-page", "cli-tool", "composed"]

    # task-tracker
    entities: list[Entity] | None = Field(default=None, max_length=MAX_ENTITIES)
    screens: list[Screen] | None = Field(default=None, max_length=MAX_SCREENS)

    # static-page
    page_title: str | None = Field(default=None, max_length=200)
    page_heading: str | None = Field(default=None, max_length=200)
    page_body: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)

    # cli-tool
    commands: list[Command] | None = Field(default=None, max_length=MAX_COMMANDS)
    #: B40 (req 422): the parsed requirements a composed application is built from
    #: (``app.appfactory.requirements.Requirements.as_dict()``) - what was read from the
    #: owner's sentence, and what could not be.
    requirements: dict[str, Any] | None = None
    #: B40 (req 422): the parsed requirements a composed application is built from
    #: (``app.appfactory.requirements.Requirements.as_dict()``) - what was read from the
    #: owner's sentence, and what could not be.
    requirements: dict[str, Any] | None = None
    #: B40 (req 422): the parsed requirements a composed application is built from
    #: (``app.appfactory.requirements.Requirements.as_dict()``) - what was read from the
    #: owner's sentence, and what could not be.
    requirements: dict[str, Any] | None = None

    @field_validator("name", "page_title", "page_heading")
    @classmethod
    def _single_line(cls, value: str | None) -> str | None:
        # A title is one line of text wherever it lands (an HTML <title>, an <h1>, a
        # Markdown heading): no control character of any kind.
        return None if value is None else _no_control_characters(value, "title")

    @field_validator("page_body")
    @classmethod
    def _body_text(cls, value: str | None) -> str | None:
        # Body text may carry line breaks (it is HTML-escaped into a <div>), nothing else.
        return (
            None
            if value is None
            else _no_control_characters(value, "page body", allow_newlines=True)
        )

    @model_validator(mode="after")
    def _name_shape(self) -> AppSpec:
        """A project name may never carry a path (ADR-0086 decision 2 read onto the
        spec: the device writes source in exactly one root, resolved from the SLUG this
        module derives — a name that already LOOKS like a path is refused here, before
        generation or any device call, rather than silently sanitised into a slug that
        would hide the owner's mistake or a model's injection attempt)."""
        name = self.name
        if "\x00" in name:
            raise ValueError("name must not contain a NUL byte")
        if "/" in name or "\\" in name:
            raise ValueError("name must not contain a path separator")
        if ".." in name:
            raise ValueError("name must not contain '..'")
        if re.match(r"^[A-Za-z]:", name):
            raise ValueError("name must not name a drive")
        return self

    @model_validator(mode="after")
    def _kind_matches_template(self) -> AppSpec:
        expected = TEMPLATE_KIND[self.template]
        if self.kind != expected:
            raise ValueError(
                f"template {self.template!r} requires kind {expected!r}, got {self.kind!r}"
            )
        return self

    @model_validator(mode="after")
    def _template_shape(self) -> AppSpec:
        present = {
            "entities": self.entities is not None,
            "screens": self.screens is not None,
            "page_title": self.page_title is not None,
            "page_heading": self.page_heading is not None,
            "page_body": self.page_body is not None,
            "commands": self.commands is not None,
            "requirements": self.requirements is not None,
        }
        allowed: dict[str, tuple[str, ...]] = {
            TEMPLATE_TASK_TRACKER: ("entities", "screens"),
            TEMPLATE_STATIC_PAGE: ("page_title", "page_heading", "page_body"),
            TEMPLATE_CLI_TOOL: ("commands",),
            TEMPLATE_COMPOSED: ("requirements",),
        }
        ok_fields = allowed[self.template]
        for field_name, is_present in present.items():
            if is_present and field_name not in ok_fields:
                raise ValueError(f"template {self.template!r} must not carry {field_name!r}")
        if self.template == TEMPLATE_COMPOSED:
            entities = (self.requirements or {}).get("entities") or []
            if not entities:
                raise ValueError("composed requires at least one record kind in requirements")
        if self.template == TEMPLATE_COMPOSED:
            entities = (self.requirements or {}).get("entities") or []
            if not entities:
                raise ValueError("composed requires at least one record kind in requirements")
        if self.template == TEMPLATE_COMPOSED:
            entities = (self.requirements or {}).get("entities") or []
            if not entities:
                raise ValueError("composed requires at least one record kind in requirements")
        if self.template == TEMPLATE_CLI_TOOL:
            if not self.commands:
                raise ValueError("cli-tool requires at least one command")
            seen = {c.name for c in self.commands}
            if len(seen) != len(self.commands):
                raise ValueError("cli-tool command names must be unique")
        return self

    def slug(self) -> str:
        return slug_for_name(self.name)


__all__ = [
    "APP_KINDS",
    "APP_TEMPLATES",
    "AppSpec",
    "Command",
    "Entity",
    "EntityField",
    "KIND_CLI",
    "KIND_WEB_API",
    "KIND_WEB_STATIC",
    "MAX_COMMANDS",
    "MAX_ENTITIES",
    "MAX_FIELDS_PER_ENTITY",
    "MAX_NAME_CHARS",
    "MAX_SCREENS",
    "MAX_TEXT_CHARS",
    "Screen",
    "TEMPLATE_CLI_TOOL",
    "TEMPLATE_COMPOSED",
    "TEMPLATE_COMPOSED",
    "TEMPLATE_COMPOSED",
    "TEMPLATE_KIND",
    "TEMPLATE_STATIC_PAGE",
    "TEMPLATE_TASK_TRACKER",
    "slug_for_name",
]

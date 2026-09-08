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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- kinds

KIND_WEB_STATIC = "web_static"
KIND_WEB_API = "web_api"
KIND_CLI = "cli"

APP_KINDS: tuple[str, ...] = (KIND_WEB_STATIC, KIND_WEB_API, KIND_CLI)

TEMPLATE_TASK_TRACKER = "task-tracker"
TEMPLATE_STATIC_PAGE = "static-page"
TEMPLATE_CLI_TOOL = "cli-tool"

APP_TEMPLATES: tuple[str, ...] = (TEMPLATE_TASK_TRACKER, TEMPLATE_STATIC_PAGE, TEMPLATE_CLI_TOOL)

#: Every built-in template is pinned to exactly one kind (spec §1's table: "desktop/mobile
#: are M28" — ``web_api`` names a kind no built-in template renders yet either, reserved
#: for a future template exactly the way ``ArtifactSpec.KIND_FORMATS`` reserves formats a
#: kind may not use today).
TEMPLATE_KIND: dict[str, str] = {
    TEMPLATE_TASK_TRACKER: KIND_WEB_STATIC,
    TEMPLATE_STATIC_PAGE: KIND_WEB_STATIC,
    TEMPLATE_CLI_TOOL: KIND_CLI,
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


def slug_for_name(name: str) -> str:
    """A filesystem/identifier-safe slug for ``name`` (the device's ``Projects\\<slug>``
    root, spec §1) — lowercase ASCII, dashes, bounded, never empty. Deterministic:
    the same name always slugifies the same way, the same discipline
    ``app.evolution.skills``'s own ``_ref_slugify`` uses for a skill's directory name."""
    lowered = name.strip().lower()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    if not slug:
        slug = "app"
    return slug[:64]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------- task-tracker


class EntityField(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    type: Literal["text", "number", "boolean", "date"] = "text"


class Entity(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    fields: list[EntityField] = Field(default_factory=list, max_length=MAX_FIELDS_PER_ENTITY)


class Screen(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    entity: str | None = None


# ------------------------------------------------------------------------ cli-tool


class Command(_StrictModel):
    name: str = Field(min_length=1, max_length=MAX_COMMAND_NAME_CHARS)
    description: str = Field(default="", max_length=200)

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
    template: Literal["task-tracker", "static-page", "cli-tool"]

    # task-tracker
    entities: list[Entity] | None = Field(default=None, max_length=MAX_ENTITIES)
    screens: list[Screen] | None = Field(default=None, max_length=MAX_SCREENS)

    # static-page
    page_title: str | None = Field(default=None, max_length=200)
    page_heading: str | None = Field(default=None, max_length=200)
    page_body: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)

    # cli-tool
    commands: list[Command] | None = Field(default=None, max_length=MAX_COMMANDS)

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
        }
        allowed: dict[str, tuple[str, ...]] = {
            TEMPLATE_TASK_TRACKER: ("entities", "screens"),
            TEMPLATE_STATIC_PAGE: ("page_title", "page_heading", "page_body"),
            TEMPLATE_CLI_TOOL: ("commands",),
        }
        ok_fields = allowed[self.template]
        for field_name, is_present in present.items():
            if is_present and field_name not in ok_fields:
                raise ValueError(f"template {self.template!r} must not carry {field_name!r}")
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
    "TEMPLATE_KIND",
    "TEMPLATE_STATIC_PAGE",
    "TEMPLATE_TASK_TRACKER",
    "slug_for_name",
]

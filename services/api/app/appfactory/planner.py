"""The architecture and project planners (B40 req 423, 424).

``plan_architecture`` reads :class:`Requirements` and decides the shape the composer can
build with what the device may run (docs/M23_APP_FACTORY_SPEC.md §3: one root, a fixed
runtime allowlist, no package install): a stdlib-only Node application - a JSON-file
store with a declared schema, a REST API per record kind, a plain-HTML frontend, an
optional login - and the tests that prove each layer. ``plan_project`` turns that into the
file list the composer writes, in order, with what each file is for and which files a
model may author (never the store, the auth or the tests: those are what the owner's
data and the verification stand on).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.appfactory.requirements import (
    FEATURE_API,
    FEATURE_AUTH,
    FEATURE_DATABASE,
    FEATURE_FRONTEND,
    EntitySpec,
    Requirements,
)

RUNTIME_NODE: Final = "node"
STORAGE_JSON_FILE: Final = "json-file"
DEFAULT_PORT: Final = 8766

LAYER_SCHEMA: Final = "schema"
LAYER_STORE: Final = "store"
LAYER_API: Final = "api"
LAYER_AUTH: Final = "auth"
LAYER_FRONTEND: Final = "frontend"
LAYER_TESTS: Final = "tests"

#: Files a model may author (req 425): the free-text behaviour the owner asked for, as pure
#: functions, and their tests - never the server, the store, the auth or the runner.
MODEL_SLOT_PATHS: Final[tuple[str, ...]] = ("custom.js", "tests/custom.js")


@dataclass(slots=True)
class ArchitecturePlan:
    runtime: str
    storage: str
    port: int
    layers: list[str]
    entities: list[EntitySpec]
    auth: bool
    api: bool
    frontend: bool
    rationale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "storage": self.storage,
            "port": self.port,
            "layers": list(self.layers),
            "entities": [e.as_dict() for e in self.entities],
            "auth": self.auth,
            "api": self.api,
            "frontend": self.frontend,
            "rationale": list(self.rationale),
        }


@dataclass(slots=True)
class FilePlan:
    path: str
    purpose: str
    layer: str
    author: str = "composer"  # composer | model

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "purpose": self.purpose,
            "layer": self.layer,
            "author": self.author,
        }


@dataclass(slots=True)
class ProjectPlan:
    files: list[FilePlan]
    run_command: str
    test_command: str
    entry: str
    port: int
    tests: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [f.as_dict() for f in self.files],
            "run_command": self.run_command,
            "test_command": self.test_command,
            "entry": self.entry,
            "port": self.port,
            "tests": list(self.tests),
        }

    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


def plan_architecture(req: Requirements, *, port: int = DEFAULT_PORT) -> ArchitecturePlan:
    """Req 423: the layers the requirements need, each with its reason."""
    auth = FEATURE_AUTH in req.features
    frontend = FEATURE_FRONTEND in req.features
    api = FEATURE_API in req.features or bool(req.entities)
    layers = [LAYER_SCHEMA, LAYER_STORE]
    rationale = [
        "runtime node: the device runs 'node <entry>' from the manifest and installs nothing, "
        "so the application is stdlib-only",
        "storage json-file: records must survive a restart and no database server may be "
        "installed; "
        "a schema-checked JSON file under the project root is the honest persistent store",
    ]
    if api:
        layers.append(LAYER_API)
        rationale.append(
            "api: every record kind is reachable as /api/<kind> (list, create, update, delete)"
        )
    if auth:
        layers.append(LAYER_AUTH)
        rationale.append(
            "auth: the owner asked for a login; scrypt-hashed password, a session cookie, "
            "every /api/<kind> route behind it"
        )
    if frontend:
        layers.append(LAYER_FRONTEND)
        rationale.append(
            "frontend: a plain HTML page per record kind, driven by the schema the API reports"
        )
    layers.append(LAYER_TESTS)
    rationale.append(
        "tests: unit tests over the schema, integration tests over the running server, "
        "a browser oracle for the device lab"
    )
    return ArchitecturePlan(
        runtime=RUNTIME_NODE,
        storage=STORAGE_JSON_FILE
        if FEATURE_DATABASE in req.features or req.entities
        else STORAGE_JSON_FILE,
        port=port,
        layers=layers,
        entities=list(req.entities),
        auth=auth,
        api=api,
        frontend=frontend,
        rationale=rationale,
    )


def plan_project(arch: ArchitecturePlan, *, model_slots: bool = False) -> ProjectPlan:
    """Req 424: the files, in the order they are written, and the tests that prove them."""
    files: list[FilePlan] = [
        FilePlan(
            "schema.js",
            "the record kinds, their fields and the validation (UMD: browser and node)",
            LAYER_SCHEMA,
        ),
        FilePlan(
            "store.js",
            "the JSON-file store: list/create/update/remove with atomic writes",
            LAYER_STORE,
        ),
    ]
    tests = ["tests/unit.js: schema validation per record kind"]
    if arch.auth:
        files.append(
            FilePlan(
                "auth.js",
                "scrypt password hashing, sessions, /api/setup /api/login /api/logout /api/me",
                LAYER_AUTH,
            )
        )
        tests.append(
            "tests/integration.js: setup, login, a protected route without and with a session"
        )
    files.append(
        FilePlan(
            "server.js", "the HTTP server: static files, /api/schema, /api/<kind> routes", LAYER_API
        )
    )
    tests.append(
        "tests/integration.js: create, list, update, delete over HTTP on an ephemeral port"
    )
    if arch.frontend:
        files.extend(
            [
                FilePlan(
                    "public/index.html",
                    "the page: one section per record kind, a login form when auth",
                    LAYER_FRONTEND,
                ),
                FilePlan("public/app.css", "the stylesheet", LAYER_FRONTEND),
                FilePlan(
                    "public/app.js",
                    "the DOM glue: forms from the schema, lists from the API",
                    LAYER_FRONTEND,
                ),
                FilePlan(
                    "public/schema.js",
                    "the schema served to the page - the same file the tests run",
                    LAYER_FRONTEND,
                ),
            ]
        )
        tests.append("tests/browser-oracle.json: the DOM steps the device lab replays")
    if model_slots:
        files.append(
            FilePlan(
                "custom.js",
                "the owner's free-text behaviour as pure functions (model-authored)",
                LAYER_API,
                author="model",
            )
        )
        files.append(
            FilePlan(
                "tests/custom.js",
                "the tests of custom.js (model-authored)",
                LAYER_TESTS,
                author="model",
            )
        )
    files.extend(
        [
            FilePlan("tests/unit.js", "unit tests over schema.js", LAYER_TESTS),
            FilePlan(
                "tests/integration.js",
                "integration tests over server.js on an ephemeral port",
                LAYER_TESTS,
            ),
            FilePlan(
                "tests/run.js",
                "the runner the manifest names: unit, integration, custom",
                LAYER_TESTS,
            ),
            FilePlan("README.md", "what the application is and how it runs", LAYER_TESTS),
            FilePlan("manifest.json", "entry, run, test, port", LAYER_TESTS),
        ]
    )
    if arch.frontend:
        files.insert(
            len(files) - 2,
            FilePlan("tests/browser-oracle.json", "the DOM oracle for the device lab", LAYER_TESTS),
        )
    return ProjectPlan(
        files=files,
        run_command="node server.js",
        test_command="node tests/run.js",
        entry="server.js",
        port=arch.port,
        tests=tests,
    )


__all__ = [
    "DEFAULT_PORT",
    "LAYER_API",
    "LAYER_AUTH",
    "LAYER_FRONTEND",
    "LAYER_SCHEMA",
    "LAYER_STORE",
    "LAYER_TESTS",
    "MODEL_SLOT_PATHS",
    "RUNTIME_NODE",
    "STORAGE_JSON_FILE",
    "ArchitecturePlan",
    "FilePlan",
    "ProjectPlan",
    "plan_architecture",
    "plan_project",
]

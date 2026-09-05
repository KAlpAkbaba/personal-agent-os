"""Structural guards on the perception boundary.

The behavioural tests in `test_presence_observations.py` prove the screen refuses what it
is given. These prove the shape of the code around it — the things that would let a future
change route *around* the screen rather than defeat it, and which no amount of testing the
screen itself would catch:

* the observation field set is closed at exactly seven, so an eighth field is a deliberate
  schema change and never an accident;
* every path into the fusion engine goes through the screen;
* `app.presence` cannot reach durable blob storage at all, so there is nowhere for an image
  to be written even if one somehow arrived.

Asserted on the import graph and the AST rather than by grepping for strings, the same way
`test_lab_acceptance_wording_guard.py` asserts the lab candidate's isolation.
"""

from __future__ import annotations

import ast
import pathlib

from app.presence import observations as observations_mod

PRESENCE_DIR = pathlib.Path(observations_mod.__file__).parent

#: Exactly the seven fields the spec permits across the boundary (M18 spec §2).
EXPECTED_FIELDS = frozenset(
    {
        "person_present",
        "presence_confidence",
        "activity_level",
        "posture",
        "awake_state",
        "observed_at",
        "source",
    }
)

#: Libraries that can decode, hold or write an image. Forbidden everywhere in the package:
#: an observation is seven numbers and tokens, and nothing here has any business being able
#: to represent a picture, let alone store one.
FORBIDDEN_EVERYWHERE = ("PIL", "cv2", "imageio", "skimage", "boto3", "app.storage")

#: Durable byte storage. Forbidden in the modules that actually handle observations. It is
#: allowed in `routes.py` for one mundane reason: `app.artifacts.runtime.ArtifactRuntime` is
#: this codebase's DB session provider as well as its object-store handle, and every router
#: in the project takes its session from it. That overloading is a smell worth naming, but
#: the honest guard is on the modules that touch perception data, not on the router that
#: opens a transaction.
FORBIDDEN_IN_PERCEPTION = ("app.artifacts",)
PERCEPTION_MODULES = (
    "observations.py",
    "engine.py",
    "service.py",
    "states.py",
    "eye.py",
    "greeting.py",
)


def _module_files() -> list[pathlib.Path]:
    return sorted(p for p in PRESENCE_DIR.glob("*.py") if p.name != "__pycache__")


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_observation_field_set_is_closed_at_seven() -> None:
    assert observations_mod.OBSERVATION_FIELDS == EXPECTED_FIELDS
    assert len(observations_mod.OBSERVATION_FIELDS) == 7


def test_presence_cannot_reach_durable_blob_storage() -> None:
    """Nowhere for an image to be written, even if one somehow arrived."""

    def matches(name: str, prefixes: tuple[str, ...]) -> bool:
        return any(name == p or name.startswith(p + ".") for p in prefixes)

    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            if matches(name, FORBIDDEN_EVERYWHERE):
                offenders.append(f"{path.name} imports {name}")
            elif path.name in PERCEPTION_MODULES and matches(name, FORBIDDEN_IN_PERCEPTION):
                offenders.append(f"{path.name} imports {name}")
    assert offenders == [], (
        "app.presence must not be able to reach durable byte storage: " + "; ".join(offenders)
    )


def test_every_construction_of_an_observation_goes_through_the_screen() -> None:
    """`Observation(...)` may only be built inside the module that screens the payload.

    A second construction site elsewhere in the package would be a way into the fusion
    engine that never saw `screen_observation_payload` - which is exactly how a privacy
    boundary stops being one.
    """
    offenders: list[str] = []
    for path in _module_files():
        if path.name == "observations.py":
            continue  # the screen itself, and the only legitimate constructor
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "Observation":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "an Observation was constructed outside app/presence/observations.py, bypassing "
        "screen_observation_payload: " + ", ".join(offenders)
    )


def test_parse_observation_is_the_only_screened_entry_point() -> None:
    """`ingest_observation` must call `parse_observation`, not build one itself."""
    source = (PRESENCE_DIR / "service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    ingest = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "ingest_observation"
    )
    called = {
        node.func.id
        for node in ast.walk(ingest)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "parse_observation" in called


def test_no_presence_module_names_a_frame_shaped_field() -> None:
    """A field or attribute whose name is image-shaped has no business existing here.

    Not a substitute for the value-shape screen - a rename defeats a name check, which is
    why the runtime screen reads values too - but it catches the honest mistake of someone
    adding `thumbnail` or `snapshot` to a dataclass without thinking about what it means.
    """
    forbidden = ("image", "frame", "snapshot", "thumbnail", "base64", "pixels", "jpeg", "png")
    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id.lower()
                if any(word in name for word in forbidden):
                    offenders.append(f"{path.name}:{node.lineno} {node.target.id}")
    assert offenders == [], "image-shaped field names in app.presence: " + ", ".join(offenders)

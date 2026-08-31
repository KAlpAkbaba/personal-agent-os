"""Reusable-component catalog — resolution step 4, "install/adapt a compatible
reusable component when appropriate" (ACCEPTANCE_TESTS M7 Resolution order).

Deliberately a CURATED, LOCAL, OFFLINE registry: the catalog file ships with the
product, every entry is pinned (name + version + source + digest), and the
loader recomputes each digest from the entry's functional fields and REFUSES a
mismatch. There is no network install path anywhere in this module — "no blind
package installation" (SECURITY_MODEL M7) is satisfied structurally, not by
policy text.

Adapting a component is not a bypass of the gates: the pipeline still generates
a wrapper skill in the sandbox, still runs the generated tests and eval set,
still needs the independent review, and records the component as a pinned
dependency in the capability manifest so the audit trail can answer "what
dependencies were introduced".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.tokens import require_slug, require_version
from app.logging import get_logger

logger = get_logger("app.evolution.components")

CATALOG_PATH = Path(__file__).resolve().parent / "component_catalog.json"

# The only source a component may claim. A catalog entry naming anything else is
# refused at load: there is no code path that fetches from a remote index.
LOCAL_SOURCE = "local-component-catalog"


@dataclass(frozen=True, slots=True)
class Component:
    """One vetted, pinned, locally available reusable component."""

    name: str
    version: str
    source: str
    digest: str
    purpose: str
    operation: str
    requires_inputs: tuple[str, ...] = ()
    provides_outputs: tuple[str, ...] = ()
    license: str = ""
    install_script: str = ""

    def functional_payload(self) -> dict[str, Any]:
        """The fields the digest covers (everything that changes behaviour)."""
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "operation": self.operation,
            "requires_inputs": list(self.requires_inputs),
            "provides_outputs": list(self.provides_outputs),
        }

    def computed_digest(self) -> str:
        canonical = json.dumps(
            self.functional_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    def dependency_record(self) -> dict[str, str]:
        """The pinned record written into the capability manifest."""
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "digest": self.digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.dependency_record(),
            "purpose": self.purpose,
            "operation": self.operation,
            "requires_inputs": list(self.requires_inputs),
            "provides_outputs": list(self.provides_outputs),
            "license": self.license,
        }


@dataclass(slots=True)
class ComponentMatch:
    component: Component
    covered_outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"component": self.component.to_dict(), "covered_outputs": self.covered_outputs}


def _load_entry(raw: Any, index: int) -> Component:
    if not isinstance(raw, dict):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"component catalog entry {index} is not an object",
        )
    unknown = set(raw) - {
        "name",
        "version",
        "source",
        "digest",
        "purpose",
        "operation",
        "requires_inputs",
        "provides_outputs",
        "license",
        "install_script",
    }
    if unknown:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"component catalog entry {index} has unknown keys: {sorted(unknown)}",
        )
    component = Component(
        name=require_slug(raw.get("name"), field=f"component[{index}].name"),
        version=require_version(raw.get("version"), field=f"component[{index}].version"),
        source=str(raw.get("source") or ""),
        digest=str(raw.get("digest") or ""),
        purpose=str(raw.get("purpose") or ""),
        operation=require_slug(raw.get("operation"), field=f"component[{index}].operation"),
        requires_inputs=tuple(
            require_slug(v, field=f"component[{index}].requires_inputs")
            for v in raw.get("requires_inputs") or []
        ),
        provides_outputs=tuple(
            require_slug(v, field=f"component[{index}].provides_outputs")
            for v in raw.get("provides_outputs") or []
        ),
        license=str(raw.get("license") or ""),
        install_script=str(raw.get("install_script") or ""),
    )
    if component.source != LOCAL_SOURCE:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"component {component.name!r} claims a non-local source; refusing to load",
            details={"allowed": LOCAL_SOURCE},
        )
    if component.install_script:
        # SECURITY_MODEL M7: install scripts may not silently expand privileges,
        # so the catalog simply cannot carry one.
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"component {component.name!r} carries an install script; refusing to load",
        )
    if component.digest != component.computed_digest():
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"component {component.name!r} failed its pinned digest check",
            details={"expected": component.computed_digest()},
        )
    return component


class ComponentCatalog:
    """Loads and searches the curated catalog. Offline; no install path."""

    def __init__(self, components: list[Component]) -> None:
        self.components = sorted(components, key=lambda c: (c.name, c.version))

    @classmethod
    def load(cls, path: Path | str | None = None) -> ComponentCatalog:
        path = Path(path or CATALOG_PATH)
        if not path.is_file():
            return cls([])
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "component catalog is not valid JSON"
            ) from exc
        entries = raw.get("components") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "component catalog must hold a list"
            )
        return cls([_load_entry(entry, index) for index, entry in enumerate(entries)])

    def get(self, name: str, version: str | None = None) -> Component | None:
        for component in self.components:
            if component.name == name and (version is None or component.version == version):
                return component
        return None

    def find(
        self, *, available_inputs: list[str], required_outputs: list[str]
    ) -> ComponentMatch | None:
        """The best compatible component, or None.

        Compatible means: everything it needs is already available, and it
        provides EVERY required output (a partial component is not adapted — the
        engine would be inventing the remainder, which is generation).
        """
        goal = set(required_outputs)
        if not goal:
            return None
        available = set(available_inputs)
        for component in self.components:
            if not set(component.requires_inputs) <= available:
                continue
            if not goal <= set(component.provides_outputs):
                continue
            logger.info(
                "component_matched",
                component=component.name,
                version=component.version,
                outputs=sorted(goal),
            )
            return ComponentMatch(component=component, covered_outputs=sorted(goal))
        return None

    def survey(self) -> list[dict[str, Any]]:
        """Evidence for the decision trail: what was actually looked at."""
        return [
            {
                "name": c.name,
                "version": c.version,
                "requires_inputs": list(c.requires_inputs),
                "provides_outputs": list(c.provides_outputs),
            }
            for c in self.components
        ]


__all__ = ["CATALOG_PATH", "LOCAL_SOURCE", "Component", "ComponentCatalog", "ComponentMatch"]

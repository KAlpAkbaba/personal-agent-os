"""Semantic target specification.

A :class:`TargetSpec` describes *what* to interact with in semantic terms —
ARIA role + accessible name, visible text, form label, placeholder, or a
test id. Raw coordinates are deliberately not expressible here; that keeps
the public API on the semantic layers of the control-surface hierarchy
(CLAUDE.md browser rule).

Exactly one primary strategy must be set:

- ``role`` (optionally narrowed by ``name``)   -> ``page.get_by_role``
- ``text``                                     -> ``page.get_by_text``
- ``label``                                    -> ``page.get_by_label``
- ``placeholder``                              -> ``page.get_by_placeholder``
- ``test_id``                                  -> ``page.get_by_test_id``

``name`` is only valid together with ``role``. ``exact`` controls exact
accessible-name/text matching where the strategy supports it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .errors import BrowserError, ErrorClass

if TYPE_CHECKING:
    from playwright.async_api import FrameLocator, Locator, Page

_PRIMARY_FIELDS = ("role", "text", "label", "placeholder", "test_id")


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """Semantic locator for a UI element. See module docstring for rules."""

    role: str | None = None
    name: str | None = None
    text: str | None = None
    label: str | None = None
    placeholder: str | None = None
    test_id: str | None = None
    exact: bool = False

    def validate(self) -> None:
        """Raise ``BrowserError(validation_error)`` if the spec is malformed."""
        set_fields = [f for f in _PRIMARY_FIELDS if getattr(self, f) is not None]
        if len(set_fields) == 0:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"target spec is empty: set exactly one of {', '.join(_PRIMARY_FIELDS)}",
                retryable=False,
                evidence={"target": self.as_dict()},
            )
        if len(set_fields) > 1:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "target spec is ambiguous: "
                f"{', '.join(set_fields)} are all set; set exactly one primary strategy",
                retryable=False,
                evidence={"target": self.as_dict()},
            )
        if self.name is not None and self.role is None:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "target spec: 'name' is only valid together with 'role'",
                retryable=False,
                evidence={"target": self.as_dict()},
            )
        for field_name in (*_PRIMARY_FIELDS, "name"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR,
                    f"target spec: '{field_name}' must be a non-empty string",
                    retryable=False,
                    evidence={"target": self.as_dict()},
                )

    def as_dict(self) -> dict[str, object]:
        """Compact dict (non-None fields only) for logs/evidence."""
        return {k: v for k, v in asdict(self).items() if v is not None and v is not False}

    def to_locator(self, page: Page | FrameLocator) -> Locator:
        """Build the Playwright locator for this (validated) spec.

        ``page`` may be a Page or a FrameLocator root (iframe interaction);
        both expose the same semantic ``get_by_*`` surface.
        """
        self.validate()
        if self.role is not None:
            if self.name is not None:
                return page.get_by_role(self.role, name=self.name, exact=self.exact)  # type: ignore[arg-type]
            return page.get_by_role(self.role)  # type: ignore[arg-type]
        if self.text is not None:
            return page.get_by_text(self.text, exact=self.exact)
        if self.label is not None:
            return page.get_by_label(self.label, exact=self.exact)
        if self.placeholder is not None:
            return page.get_by_placeholder(self.placeholder, exact=self.exact)
        assert self.test_id is not None
        return page.get_by_test_id(self.test_id)


def coerce_target(target: TargetSpec | dict[str, object]) -> TargetSpec:
    """Accept a TargetSpec or a plain dict (e.g. from a command payload)."""
    if isinstance(target, TargetSpec):
        spec = target
    elif isinstance(target, dict):
        allowed = {*_PRIMARY_FIELDS, "name", "exact"}
        unknown = set(target) - allowed
        if unknown:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"target spec has unknown fields: {', '.join(sorted(str(u) for u in unknown))}",
                retryable=False,
                evidence={"target": dict(target)},
            )
        spec = TargetSpec(**target)  # type: ignore[arg-type]
    else:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"target must be a TargetSpec or dict, got {type(target).__name__}",
            retryable=False,
            evidence={"target_type": type(target).__name__},
        )
    spec.validate()
    return spec

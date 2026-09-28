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

Contract v1.6 (ADR-0207) adds two things, both about saying WHICH element:

- ``ref`` (+ ``observation_id``) - the sixth strategy: a reference that a
  ``browser.observe`` handed out (``e12``). The worker resolves it against the
  observation it still holds and refuses one from any other observation, tab
  or page with ``ui_state_changed``. A ``ref`` spec that nobody resolved cannot
  become a locator at all - there is no way to turn the string into a selector
  from a payload.
- ``nth`` - a zero-based index on any of the five semantic strategies, for the
  caller that does not observe first. Without it a target that matches twice
  still resolves to the first match, as it always did.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from .errors import BrowserError, ErrorClass

if TYPE_CHECKING:
    from playwright.async_api import FrameLocator, Locator, Page

_SEMANTIC_FIELDS = ("role", "text", "label", "placeholder", "test_id")
_PRIMARY_FIELDS = (*_SEMANTIC_FIELDS, "ref")
#: What a command payload may name. ``resolved_path`` is deliberately NOT here: it is
#: set by the worker from an observation it took itself, never by a caller.
_PAYLOAD_FIELDS = frozenset({*_PRIMARY_FIELDS, "name", "exact", "nth", "observation_id"})
MAX_NTH = 199


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
    #: v1.6: a reference from ``browser.observe`` and the observation it belongs to.
    ref: str | None = None
    observation_id: str | None = None
    #: v1.6: zero-based index among the matches of a semantic strategy.
    nth: int | None = None
    #: Worker-side only: the structural path the observation recorded for ``ref``, one
    #: segment per shadow boundary. Never accepted from a payload (``coerce_target``).
    resolved_path: tuple[str, ...] | None = None

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
        if self.ref is not None:
            if self.name is not None or self.exact or self.nth is not None:
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR,
                    "target spec: 'ref' names one element; 'name', 'exact' and 'nth' "
                    "do not apply to it",
                    retryable=False,
                    evidence={"target": self.as_dict()},
                )
            if not isinstance(self.observation_id, str) or not self.observation_id.strip():
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR,
                    "target spec: 'ref' needs the 'observation_id' it came from",
                    retryable=False,
                    evidence={"target": self.as_dict()},
                )
        elif self.observation_id is not None:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "target spec: 'observation_id' is only valid together with 'ref'",
                retryable=False,
                evidence={"target": self.as_dict()},
            )
        if self.nth is not None and (
            isinstance(self.nth, bool)
            or not isinstance(self.nth, int)
            or not 0 <= self.nth <= MAX_NTH
        ):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"target spec: 'nth' must be an integer from 0 to {MAX_NTH}",
                retryable=False,
                evidence={"target": self.as_dict()},
            )
        for field_name in (*_PRIMARY_FIELDS, "name", "observation_id"):
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
        return {
            k: v
            for k, v in asdict(self).items()
            if v is not None and v is not False and k != "resolved_path"
        }

    def resolved(self, path: tuple[str, ...]) -> TargetSpec:
        """This ``ref`` spec, bound to the path its observation recorded (worker only)."""
        return replace(self, resolved_path=tuple(path))

    def to_locator(self, page: Page | FrameLocator) -> Locator:
        """Build the Playwright locator for this (validated) spec.

        ``page`` may be a Page or a FrameLocator root (iframe interaction);
        both expose the same semantic ``get_by_*`` surface.
        """
        self.validate()
        if self.ref is not None:
            if not self.resolved_path:
                # Reaching here means a ref went past the worker's resolution step.
                raise BrowserError(
                    ErrorClass.UI_STATE_CHANGED,
                    f"reference {self.ref} is not bound to an observation of this page; "
                    "observe the page again",
                    retryable=True,
                    evidence={"target": self.as_dict()},
                )
            locator = page.locator(f"css={self.resolved_path[0]}")
            for segment in self.resolved_path[1:]:
                locator = locator.locator(f"css={segment}")
            return locator
        locator = self._semantic_locator(page)
        return locator if self.nth is None else locator.nth(self.nth)

    def _semantic_locator(self, page: Page | FrameLocator) -> Locator:
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
        unknown = set(target) - _PAYLOAD_FIELDS
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

"""A fake browser for the task loop (ADR-0207 PR-B).

``FakeBrowser`` implements ``app.webtask.loop.BrowserPort`` over a small site written in
Python: pages with elements, and what happens when one is acted on. It keeps the
promises the real worker keeps, because a fake that is kinder than the device proves
nothing:

* a reference is good for the LAST observation only (``ui_state_changed`` otherwise);
* the value of a field is never in an observation - ``has_value`` / ``empty`` is;
* every click - and since contract v1.8 every fill, select and check - is classified
  from the element by the contract's rule and REFUSED when it is above the ceiling the
  loop gated it at (``security_scope_error``);
* acting on a disabled element, or on one that is gone, fails.

What it records is what the tests assert on: every action that REACHED the site. "The
loop did not buy" is ``"buy" not in browser.done``, not the absence of an exception.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.webtask import risk
from app.webtask.loop import BrowserPortError
from app.webtask.sites import host_of
from app.webtask.types import (
    ACTION_BACK,
    ACTION_CHECK,
    ACTION_CLICK,
    ACTION_FILL,
    ACTION_NAVIGATE,
    ACTION_SCROLL,
    ACTION_SELECT,
    Element,
    Observation,
    Step,
    risk_rank,
)

Effect = Callable[["FakeBrowser"], None]


@dataclass
class El:
    """One element of a fake page."""

    role: str
    name: str
    #: What acting on it does to the browser. ``None``: nothing happens.
    effect: Effect | None = None
    #: A tag the test reads in ``browser.done`` when the element was acted on.
    does: str = ""
    href: str | None = None
    submits: bool = False
    in_form: bool = False
    sensitive: bool = False
    disabled: bool = False
    #: Shown only while this returns True (a banner that goes away, a cart badge).
    when: Callable[[FakeBrowser], bool] | None = None
    #: The device cannot tell this element from another (an ambiguous shadow path).
    not_unique: bool = False
    field_key: str = ""
    tag: str = ""
    onclick: bool = False


@dataclass
class Page:
    title: str
    elements: list[El]
    text: str | Callable[[FakeBrowser], str] = ""
    kind: str | Callable[[FakeBrowser], str] = "ok"
    injection_markers: int = 0


@dataclass
class FakeBrowser:
    pages: dict[str, Page]
    url: str
    #: What the owner has typed or the loop has filled, by ``field_key``.
    fields: dict[str, str] = field(default_factory=dict)
    checked: dict[str, bool] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    #: Every action that reached the site, as the ``does`` tag of its element.
    done: list[str] = field(default_factory=list)
    #: Every command the loop issued: (capability-ish, detail).
    commands: list[tuple[str, str]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    observations: int = 0
    fail_observe: str = ""
    fail_act: str = ""
    _last_id: str = ""
    _last_refs: dict[str, El] = field(default_factory=dict)

    # ------------------------------------------------------------- the port

    def observe(self, *, task_id: str, key: str) -> Observation:
        self._key(key)
        self.commands.append(("observe", self.url))
        if self.fail_observe:
            raise BrowserPortError(self.fail_observe, retryable=True)
        page = self.pages[self.url]
        self.observations += 1
        self._last_id = f"obs-{self.observations}"
        self._last_refs = {}
        elements: list[Element] = []
        for el in page.elements:
            if el.when is not None and not el.when(self):
                continue
            ref = f"e{len(elements) + 1}"
            self._last_refs[ref] = el
            state: list[str] = []
            if el.disabled:
                state.append("disabled")
            if el.role in ("checkbox", "radio", "switch"):
                state.append(
                    "checked" if self.checked.get(el.field_key or el.name) else "not_checked"
                )
            elif el.role in risk.FIELD_ROLES:
                state.append("has_value" if self.fields.get(el.field_key or el.name) else "empty")
            observed = Element(
                ref=ref,
                role=el.role,
                name=el.name,
                tag=el.tag or {"link": "a", "button": "button"}.get(el.role, "input"),
                state=tuple(state),
                in_form=el.in_form or el.submits,
                submits=el.submits,
                href_host=host_of(el.href) if el.href else None,
                sensitive=el.sensitive,
                # What the worker alone can see: whether a link carries a handler.
                risk_hint="REVERSIBLE_WRITE" if el.onclick else "NAVIGATE",
            )
            hint = risk.classify_element(observed)
            if el.role == "link" and el.onclick:
                hint = "REVERSIBLE_WRITE"
            elements.append(Element.from_dict({**observed.as_dict(), "risk_hint": hint}))
        text = page.text(self) if callable(page.text) else page.text
        kind = page.kind(self) if callable(page.kind) else page.kind
        return Observation(
            observation_id=self._last_id,
            url=self.url,
            title=page.title,
            page_kind=kind,
            elements=tuple(elements),
            text=text,
            injection_markers=page.injection_markers,
        )

    def act(
        self, *, task_id: str, key: str, step: Step, observation_id: str, risk_ceiling: str
    ) -> dict[str, Any]:
        self._key(key)
        self.commands.append((step.action, step.ref or step.url or step.direction or ""))
        if self.fail_act:
            raise BrowserPortError(self.fail_act, retryable=True)
        if step.action == ACTION_NAVIGATE:
            assert step.url is not None
            return self._goto(step.url)
        if step.action == ACTION_BACK:
            if self.history:
                self.url = self.history.pop()
            return {"url": self.url}
        if step.action == ACTION_SCROLL:
            self.flags["scrolled"] = int(self.flags.get("scrolled", 0)) + 1
            return {"at_end": True}

        if observation_id != self._last_id:
            raise BrowserPortError("ui_state_changed", reason="other_observation", retryable=True)
        el = self._last_refs.get(step.ref or "")
        if el is None:
            raise BrowserPortError("validation_error", "unknown reference")
        if el.not_unique:
            raise BrowserPortError("ui_state_changed", reason="not_unique", retryable=True)
        if el.disabled:
            raise BrowserPortError("ui_state_changed", reason="disabled")

        if step.action == ACTION_CLICK:
            # The device classifies the click from the ELEMENT and refuses above the
            # ceiling it was told - whatever the loop believed.
            observed = Element(
                ref="x",
                role=el.role,
                name=el.name,
                submits=el.submits,
                href_host=host_of(el.href) if el.href else None,
                risk_hint="REVERSIBLE_WRITE" if el.onclick else "NAVIGATE",
            )
            actual = self.flags.get("device_class") or risk.classify_element(observed)
            if risk_rank(actual) > risk_rank(risk_ceiling):
                raise BrowserPortError("security_scope_error", f"{actual} above {risk_ceiling}")
            if el.does:
                self.done.append(el.does)
            if el.effect is not None:
                el.effect(self)
            if el.href and el.effect is None:
                self._goto(el.href)
            return {"clicked": True}
        # Contract v1.8: a write is classified from its element too, and refused above
        # the ceiling BEFORE anything is typed, chosen or ticked.
        written = Element(
            ref="x",
            role=el.role,
            name=el.name,
            submits=el.submits,
            in_form=el.in_form or el.submits,
        )
        actual = self.flags.get("device_class") or risk.classify_step(step.action, written)
        if risk_rank(actual) > risk_rank(risk_ceiling):
            raise BrowserPortError("security_scope_error", f"{actual} above {risk_ceiling}")
        if step.action in (ACTION_FILL, ACTION_SELECT):
            if el.sensitive:
                # The loop must never get here; the test reads this tag to prove it.
                self.done.append(f"TYPED_INTO_SENSITIVE:{el.name}")
            self.fields[el.field_key or el.name] = step.value or ""
            self.done.append(f"fill:{el.name}")
            return {"ok": True, "risk_class": actual}
        if step.action == ACTION_CHECK:
            self.checked[el.field_key or el.name] = bool(step.checked)
            self.done.append(f"check:{el.name}")
            return {"ok": True, "risk_class": actual}
        raise BrowserPortError("capability_missing", step.action)

    # ------------------------------------------------------------- helpers

    def _key(self, key: str) -> None:
        assert key not in self.keys, f"an idempotency key was reused: {key}"
        self.keys.append(key)

    def _goto(self, url: str) -> dict[str, Any]:
        if url not in self.pages:
            raise BrowserPortError("dependency_unavailable", "no such page", retryable=True)
        self.history.append(self.url)
        self.url = url
        return {"url": url}

    def goto(self, url: str) -> Effect:
        def effect(browser: FakeBrowser) -> None:
            browser._goto(url)

        return effect


def goto(url: str) -> Effect:
    return lambda browser: browser._goto(url) and None


def set_flag(name: str, value: Any = True) -> Effect:
    def effect(browser: FakeBrowser) -> None:
        browser.flags[name] = value

    return effect


def flag(name: str) -> Callable[[FakeBrowser], bool]:
    return lambda browser: bool(browser.flags.get(name))


def not_flag(name: str) -> Callable[[FakeBrowser], bool]:
    return lambda browser: not browser.flags.get(name)


class Clock:
    """Monotonic seconds that advance only when a test says so."""

    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now

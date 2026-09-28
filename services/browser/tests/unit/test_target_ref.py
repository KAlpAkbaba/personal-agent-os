"""Contract v1.6 items 2 and 3: targets that say WHICH element (ADR-0207).

``ref`` names the element an observation numbered; ``nth`` picks among the matches of a
semantic target. What is held here, without a browser:

* a payload can name a ``ref`` and can NOT name the path it resolves through;
* a ``ref`` that nobody bound to an observation cannot become a locator;
* the worker refuses a ``ref`` - ``ui_state_changed``, retryable, with the reason - when
  it belongs to any observation but the session's last, to another tab, or to a document
  that has navigated since. Those four refusals happen BEFORE the page is touched, so
  they are tested here with a page that raises if anything touches it.

What needs a real DOM - the element gone, changed, or no longer unique - is
``tests/browser/test_observe_e2e.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from browser_agent import observe
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.targets import MAX_NTH, TargetSpec, coerce_target
from browser_agent.worker import Worker

PATH = "html:nth-of-type(1) > body:nth-of-type(1) > button:nth-of-type(2)"


# ------------------------------------------------------------------ the spec


def test_a_ref_is_a_sixth_strategy_and_needs_its_observation() -> None:
    spec = coerce_target({"ref": "e12", "observation_id": "obs-1"})
    assert spec.ref == "e12" and spec.observation_id == "obs-1"
    assert spec.as_dict() == {"ref": "e12", "observation_id": "obs-1"}
    with pytest.raises(BrowserError) as caught:
        coerce_target({"ref": "e12"})
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR
    assert "observation_id" in caught.value.message


@pytest.mark.parametrize(
    "target",
    [
        {"ref": "e1", "observation_id": "o", "role": "button"},
        {"ref": "e1", "observation_id": "o", "text": "Sepete ekle"},
        {"ref": "e1", "observation_id": "o", "name": "Sepete ekle"},
        {"ref": "e1", "observation_id": "o", "exact": True},
        {"ref": "e1", "observation_id": "o", "nth": 0},
        {"ref": "", "observation_id": "o"},
        {"ref": "e1", "observation_id": " "},
        {"role": "button", "observation_id": "o"},
    ],
)
def test_a_ref_names_one_element_and_nothing_else_applies_to_it(target: dict[str, Any]) -> None:
    with pytest.raises(BrowserError) as caught:
        coerce_target(target)
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


@pytest.mark.parametrize(
    "smuggled",
    ["resolved_path", "selector", "css", "xpath", "path", "x", "y", "coordinates"],
)
def test_a_payload_can_never_supply_a_path(smuggled: str) -> None:
    with pytest.raises(BrowserError) as caught:
        coerce_target({"ref": "e1", "observation_id": "o", smuggled: [PATH]})
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR
    assert "unknown fields" in caught.value.message


def test_the_bound_path_never_appears_in_evidence() -> None:
    bound = coerce_target({"ref": "e1", "observation_id": "o"}).resolved((PATH,))
    assert bound.resolved_path == (PATH,)
    assert "resolved_path" not in bound.as_dict()
    assert PATH not in str(bound.as_dict())


def test_a_ref_nobody_bound_cannot_become_a_locator() -> None:
    class _Page:
        def locator(self, selector: str) -> Any:  # pragma: no cover - must not be reached
            raise AssertionError(f"an unbound ref reached the page: {selector}")

    with pytest.raises(BrowserError) as caught:
        coerce_target({"ref": "e1", "observation_id": "o"}).to_locator(_Page())  # type: ignore[arg-type]
    assert caught.value.error_class == ErrorClass.UI_STATE_CHANGED
    assert caught.value.retryable is True


def test_a_bound_ref_resolves_through_its_path_one_segment_per_shadow_boundary() -> None:
    calls: list[str] = []

    class _Locator:
        def locator(self, selector: str) -> _Locator:
            calls.append(selector)
            return self

    class _Page(_Locator):
        pass

    bound = coerce_target({"ref": "e1", "observation_id": "o"}).resolved((PATH, "div > button"))
    bound.to_locator(_Page())  # type: ignore[arg-type]
    assert calls == [f"css={PATH}", "css=div > button"]


@pytest.mark.parametrize("ref", ["12", "e0", "e", "E1", "e1 ", "e12345", "e1;x", "#e1", 7, None])
def test_a_reference_has_one_shape(ref: Any) -> None:
    with pytest.raises(BrowserError) as caught:
        observe.validate_ref(ref)
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


@pytest.mark.parametrize(
    "path",
    [
        ("body > button",),
        ("html:nth-of-type(1) > body:nth-of-type(1) > button:nth-of-type(2), a",),
        ("html:nth-of-type(1) >> script",),
        ("button:nth-of-type(1)[onclick]",),
        ("xpath=//button",),
        ("",),
        (),
    ],
)
def test_a_stored_path_that_is_not_a_structural_path_is_refused(path: tuple[str, ...]) -> None:
    element = observe.ObservedElement(
        ref="e1",
        role="button",
        name="x",
        tag="button",
        state=(),
        in_form=False,
        submits=False,
        href_host=None,
        in_viewport=True,
        sensitive=False,
        risk_hint="REVERSIBLE_WRITE",
        path=path,
    )
    with pytest.raises(BrowserError) as caught:
        observe.selector_for(element)
    assert caught.value.error_class == ErrorClass.UI_STATE_CHANGED


# ------------------------------------------------------------------ nth


def test_nth_picks_among_the_matches_of_a_semantic_target() -> None:
    picked: list[int] = []

    class _Locator:
        def nth(self, index: int) -> str:
            picked.append(index)
            return f"match {index}"

    class _Page:
        def get_by_role(self, role: str, **_: Any) -> _Locator:
            return _Locator()

    spec = coerce_target({"role": "button", "name": "Sepete ekle", "nth": 1})
    assert spec.to_locator(_Page()) == "match 1"  # type: ignore[arg-type,comparison-overlap]
    assert picked == [1]
    assert spec.as_dict() == {"role": "button", "name": "Sepete ekle", "nth": 1}


def test_without_nth_a_target_is_what_it_always_was() -> None:
    class _Locator:
        def nth(self, index: int) -> Any:  # pragma: no cover - must not be reached
            raise AssertionError("nth was applied to a target that did not ask for it")

    class _Page:
        def get_by_text(self, text: str, **_: Any) -> _Locator:
            return _Locator()

    spec = coerce_target({"text": "Sepete ekle"})
    assert isinstance(spec.to_locator(_Page()), _Locator)  # type: ignore[arg-type]
    assert spec == TargetSpec(text="Sepete ekle")


@pytest.mark.parametrize("nth", [-1, MAX_NTH + 1, 1.0, "1", True, None])
def test_nth_is_a_small_non_negative_integer(nth: Any) -> None:
    if nth is None:
        assert coerce_target({"role": "button", "nth": None}).nth is None
        return
    with pytest.raises(BrowserError) as caught:
        coerce_target({"role": "button", "nth": nth})
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


# ------------------------------------------------------------------ the worker's refusals


class _UntouchablePage:
    """A page that fails the test if the resolution step reaches for it."""

    def __init__(self, url: str = "https://shop.example/urun") -> None:
        self.url = url

    def locator(self, selector: str) -> Any:  # pragma: no cover - must not be reached
        raise AssertionError(f"a stale reference reached the page: {selector}")


def _observation(observation_id: str = "obs-held") -> observe.Observation:
    records = [
        {
            "order": i,
            "tag": "button",
            "role": "button",
            "name": f"Düğme {i}",
            "visible": True,
            "in_viewport": True,
            "path": [f"html:nth-of-type(1) > body:nth-of-type(1) > button:nth-of-type({i})"],
        }
        for i in (1, 2)
    ]
    return observe.reduce_elements(records, observation_id=observation_id)


def _state(page: Any, **overrides: Any) -> Any:
    state = SimpleNamespace(
        browser_session=SimpleNamespace(backend=SimpleNamespace(current_page=page)),
        observation=_observation(),
        observation_page=page,
        observation_url=page.url,
        observation_navigations=3,
        navigations_seen=3,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def _bind(worker: Worker, state: Any, target: dict[str, Any]) -> TargetSpec:
    return asyncio.run(worker._bound_target(state, target))


@pytest.fixture()
def bare_worker() -> Worker:
    """The binding step reads the SESSION's state and nothing of the worker's own, so it
    is exercised on a worker that was never started: no profile, no browser, no stdio."""
    return Worker.__new__(Worker)


def _refused(worker: Worker, state: Any, target: dict[str, Any]) -> BrowserError:
    with pytest.raises(BrowserError) as caught:
        _bind(worker, state, target)
    return caught.value


def test_a_semantic_target_passes_through_the_binding_step_untouched(bare_worker: Worker) -> None:
    page = _UntouchablePage()
    spec = _bind(bare_worker, _state(page, observation=None), {"role": "button", "name": "Ara"})
    assert spec == TargetSpec(role="button", name="Ara")


@pytest.mark.parametrize(
    ("overrides", "target", "reason"),
    [
        ({"observation": None}, {"ref": "e1", "observation_id": "obs-held"}, "no_observation"),
        ({}, {"ref": "e1", "observation_id": "obs-older"}, "other_observation"),
        (
            {"observation_page": _UntouchablePage()},
            {"ref": "e1", "observation_id": "obs-held"},
            "other_tab",
        ),
        ({"navigations_seen": 4}, {"ref": "e1", "observation_id": "obs-held"}, "navigated"),
        (
            {"observation_url": "https://shop.example/baska"},
            {"ref": "e1", "observation_id": "obs-held"},
            "navigated",
        ),
    ],
)
def test_a_reference_from_anywhere_but_this_observation_of_this_page_is_refused(
    bare_worker: Worker, overrides: dict[str, Any], target: dict[str, Any], reason: str
) -> None:
    error = _refused(bare_worker, _state(_UntouchablePage(), **overrides), target)
    assert error.error_class == ErrorClass.UI_STATE_CHANGED
    assert error.retryable is True
    assert error.evidence["reason"] == reason
    assert error.evidence["ref"] == "e1"
    assert "observe the page again" in error.message


def test_a_reference_the_observation_never_handed_out_is_a_validation_error(
    bare_worker: Worker,
) -> None:
    error = _refused(
        bare_worker, _state(_UntouchablePage()), {"ref": "e3", "observation_id": "obs-held"}
    )
    assert error.error_class == ErrorClass.VALIDATION_ERROR
    assert error.retryable is False


def test_the_refusal_never_carries_the_url_s_query(bare_worker: Worker) -> None:
    page = _UntouchablePage("https://shop.example/urun?session=abc123&token=xyz")
    error = _refused(
        bare_worker,
        _state(page, navigations_seen=9),
        {"ref": "e1", "observation_id": "obs-held"},
    )
    assert "abc123" not in str(error.evidence) and "xyz" not in str(error.evidence)

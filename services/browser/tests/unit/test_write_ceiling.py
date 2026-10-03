"""Contract v1.8: the class of a WRITE, on the worker's side (ADR-0207 PR-C).

``fill``, ``select_option`` and ``set_checked`` are classified from the element they
change - its name, whether it submits, whether it sits in a form - by the rule the Cloud
Core's gate uses (``services/api/app/webtask/risk.py::classify_step``). Two halves that
each agree with themselves is how the contract drifts, so the SAME table is run through
the Cloud Core's function, loaded from its source file, and both must give the same class
for every row.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from browser_agent import observe
from browser_agent.policy import RiskClass, classify_write

REPO_ROOT = Path(__file__).resolve().parents[4]
CLOUD_APP = REPO_ROOT / "services" / "api" / "app"

#: (action, field role, accessible name, submits, in_form, class)
TABLE: tuple[tuple[str, str, str, bool, bool, RiskClass], ...] = (
    ("fill", "textbox", "Ad Soyad", False, True, RiskClass.REVERSIBLE_WRITE),
    ("fill", "textbox", "Ad Soyad", False, False, RiskClass.REVERSIBLE_WRITE),
    ("select_option", "combobox", "Hesabı sil", False, False, RiskClass.HIGH_IMPACT),
    ("select_option", "combobox", "Teslimat günü", False, True, RiskClass.REVERSIBLE_WRITE),
    ("set_checked", "checkbox", "Beni hatırla", True, True, RiskClass.EXTERNAL_COMMUNICATION),
    ("set_checked", "checkbox", "", False, True, RiskClass.EXTERNAL_COMMUNICATION),
    ("set_checked", "checkbox", "", False, False, RiskClass.REVERSIBLE_WRITE),
    ("set_checked", "checkbox", "Yorumu yayınla", False, False, RiskClass.EXTERNAL_COMMUNICATION),
    ("select_option", "combobox", "★", False, True, RiskClass.EXTERNAL_COMMUNICATION),
    ("fill", "textbox", "", False, True, RiskClass.REVERSIBLE_WRITE),
    ("fill", "textbox", "Kalıcı olarak sil", False, True, RiskClass.HIGH_IMPACT),
    # "silver" is not "sil": whole words only, as for a click.
    ("set_checked", "checkbox", "Silver plan", False, True, RiskClass.REVERSIBLE_WRITE),
)


@pytest.mark.parametrize(("action", "role", "name", "submits", "in_form", "expected"), TABLE)
def test_the_class_of_a_write(
    action: str, role: str, name: str, submits: bool, in_form: bool, expected: RiskClass
) -> None:
    del role
    assert classify_write(name, submits, in_form, action) == expected


def test_an_unknown_write_is_not_a_safe_one() -> None:
    assert classify_write("Ad", False, False, "type") == RiskClass.HIGH_IMPACT


# ------------------------------------------------------------------ the other half


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cloud_risk() -> Any:
    """``app.webtask.risk`` from the Cloud Core's SOURCE, without the Cloud Core's
    environment: its two imports are stdlib-only modules, loaded the same way. The
    ``app`` names are removed again afterwards - this process has no ``app`` of its own."""
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    try:
        for package, path in (("app", CLOUD_APP), ("app.webtask", CLOUD_APP / "webtask")):
            shell = types.ModuleType(package)
            shell.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[package] = shell
        _load("app.protocol_files", CLOUD_APP / "protocol_files.py")
        _load("app.webtask.types", CLOUD_APP / "webtask" / "types.py")
        yield _load("app.webtask.risk", CLOUD_APP / "webtask" / "risk.py")
    finally:
        for key in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[key]
        sys.modules.update(saved)


@pytest.mark.parametrize(("action", "role", "name", "submits", "in_form", "expected"), TABLE)
def test_both_sides_give_the_same_class_for_the_same_write(
    cloud_risk: Any,
    action: str,
    role: str,
    name: str,
    submits: bool,
    in_form: bool,
    expected: RiskClass,
) -> None:
    types_ = sys.modules["app.webtask.types"]
    # The hint the observation would carry is the worker's OWN (observe.risk_hint); the
    # gate never goes below it.
    hint = observe.risk_hint({"role": role, "submits": submits}, name)
    element = types_.Element(
        ref="e1", role=role, name=name, submits=submits, in_form=in_form, risk_hint=hint
    )
    cloud = cloud_risk.classify_step(action, element)
    assert cloud == classify_write(name, submits, in_form, action) == expected.value

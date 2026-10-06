"""Browser contract v1.9: a cloud task writes on the owner's allow-listed sites.

Two halves, built by two cards that run side by side: the worker's (the cloud device's
clamp and the worker's keeper, ``services/browser``) and Cloud Core's (the payload
``app/webtask/device_port.py`` sends and the gate). This file reads the document and BOTH
sources, so the field names and the refusal's reason cannot drift apart while each half's
own suite stays green. The behaviour is the worker's own tests
(``services/browser/tests/unit/test_cloud_task_writes.py``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "protocol" / "BROWSER_CAPABILITIES.md"
BROWSER = REPO / "services" / "browser" / "browser_agent"
WORKER = BROWSER / "worker.py"
CLAMP = BROWSER / "cloud" / "policy.py"
WORKER_ALLOWLIST = BROWSER / "cloud_allowlist.py"
CORE_ALLOWLIST = REPO / "services" / "api" / "app" / "execution" / "allowlist.py"
CORE_EDITOR = REPO / "services" / "api" / "app" / "execution" / "allowlist_store.py"
CORE_SITES = REPO / "services" / "api" / "app" / "webtask" / "sites.py"
CORE_TYPES = REPO / "services" / "api" / "app" / "webtask" / "types.py"
DEVICE_PORT = REPO / "services" / "api" / "app" / "webtask" / "device_port.py"

FIELDS = ("cloud_task", "owner_allow_list")
REASON = "not_on_owner_allow_list"
WRITES = ("fill", "select_option", "set_checked")


def _op_body(source: str, name: str) -> str:
    start = source.index(f"async def _op_{name}(")
    return source[start : source.index("    async def _op_", start + 10)]


def _constant(path: Path, name: str) -> str:
    for node in ast.parse(path.read_text("utf-8")).body:
        target = getattr(node, "target", None) or (getattr(node, "targets", None) or [None])[0]
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(target, "id", "") == name:
            return str(ast.literal_eval(node.value))
    raise AssertionError(f"{path.name} has no {name}")  # pragma: no cover


def test_the_document_says_v1_9_and_writes_the_annex() -> None:
    contract = CONTRACT.read_text("utf-8")
    assert "Status: contract **v1.9**" in contract
    assert "- **v1.9 (" in contract
    start = contract.index("## 4b.")
    section = " ".join(contract[start : contract.index("\n## 5.", start)].split())
    for phrase in (
        "`cloud_task: true`",
        "`owner_allow_list",
        '{"reason":"not_on_owner_allow_list","site":<registrable domain of the page>}',
        "`EXTERNAL_COMMUNICATION` and `HIGH_IMPACT` are refused on the cloud ALWAYS",
        "A `session_open` without `cloud_task` is clamped as before v1.9",
    ):
        assert phrase in section, phrase


@pytest.mark.parametrize("name", WRITES)
def test_each_write_asks_the_keeper_right_after_the_deny_list(name: str) -> None:
    """A source check, named as one."""
    lines = [line.strip() for line in _op_body(WORKER.read_text("utf-8"), name).splitlines()]
    deny = lines.index(f'_refuse_on_denied_site(state, "browser.{name}")')
    assert lines[deny + 1] == f'_refuse_unless_owner_allow_listed(state, "browser.{name}")'


def test_a_click_asks_the_keeper_once_its_class_is_known() -> None:
    body = _op_body(WORKER.read_text("utf-8"), "click")
    keeper = body.index('_refuse_unless_owner_allow_listed(state, "browser.click")')
    assert body.index("policy.classify_click(resolved)") < keeper < body.index("locator.click(")
    assert "policy.RiskClass.REVERSIBLE_WRITE" in body[:keeper]


def test_the_refusal_says_the_same_reason_on_both_sides() -> None:
    assert _constant(WORKER_ALLOWLIST, "NOT_ON_ALLOW_LIST") == REASON
    assert _constant(CORE_ALLOWLIST, "NOT_ON_ALLOW_LIST") == REASON
    worker = WORKER.read_text("utf-8")
    start = worker.index("def _refuse_unless_owner_allow_listed(")
    keeper = worker[start : worker.index("\ndef ", start + 10)]
    assert "cloud_allowlist.acting_allowed(url, extra_sites=state.owner_allow_list)" in keeper
    assert 'evidence={"reason": reason, "site": site}' in keeper


def test_the_clamp_names_the_classes_as_cloud_core_does() -> None:
    clamp = CLAMP.read_text("utf-8")
    for field in FIELDS:
        assert f'"{field}"' in clamp, field
    names = re.findall(r'RISK_[A-Z_]+: Final = "([A-Z_]+)"', CORE_TYPES.read_text("utf-8"))
    assert {"READ", "NAVIGATE", "REVERSIBLE_WRITE"} <= set(names)
    assert "policy.RiskClass.REVERSIBLE_WRITE" in clamp


def _table(path: Path, name: str) -> object:
    """A module-level ``frozenset({...})`` / ``re.compile("...")`` constant, read by ast
    (the worker's package is not importable from this venv, and neither should be run)."""
    for node in ast.parse(path.read_text("utf-8")).body:
        target = getattr(node, "target", None) or (getattr(node, "targets", None) or [None])[0]
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(target, "id", "") == name:
            value = node.value
            if isinstance(value, ast.Call):
                value = value.args[0]
            return ast.literal_eval(value)
    raise AssertionError(f"{path.name} has no {name}")  # pragma: no cover


# The cloud clamp refuses a whole cloud session_open on one entry it judges malformed, so
# its copy of the editor's rule must not be NARROWER than the editor (a site the editor
# lists would stop every cloud task). Each table of the worker's copy against its source.
TABLES = (
    ("_SECOND_LEVEL", CORE_SITES),
    ("_SECOND_LEVEL", CORE_EDITOR),
    ("_COUNTRY_WITH_SECOND_LEVEL", CORE_SITES),
    ("_OPEN_TLDS", CORE_EDITOR),
    ("_LABEL", CORE_EDITOR),
)


@pytest.mark.parametrize(("name", "source"), TABLES, ids=[f"{n}-{p.stem}" for n, p in TABLES])
def test_the_workers_site_rule_is_the_editors_table_for_table(name: str, source: Path) -> None:
    assert _table(WORKER_ALLOWLIST, name) == _table(source, name)


def test_the_api_halves_agree_on_the_second_level_table() -> None:
    assert _table(CORE_SITES, "_SECOND_LEVEL") == _table(CORE_EDITOR, "_SECOND_LEVEL")


@pytest.mark.parametrize("field", FIELDS)
def test_cloud_core_sends_the_fields_by_the_workers_names(field: str) -> None:
    """Until ``cloud-task-loop-core`` lands, ``device_port`` sends neither field and this
    passes as "the api half is not here yet". Once it sends ``owner_allow_list`` it must
    send both, spelt as the worker reads them - the case tightens by itself."""
    port = DEVICE_PORT.read_text("utf-8")
    if "owner_allow_list" not in port:
        return  # the api half is not here yet
    assert f'"{field}"' in port, field
    assert f'"{field}"' in WORKER.read_text("utf-8") or f'"{field}"' in CLAMP.read_text("utf-8")

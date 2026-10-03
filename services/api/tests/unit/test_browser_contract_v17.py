"""Browser contract v1.7 (ADR-0207, PR-B): the device's half of the task gate.

Two rules live on both sides: the task deny-list and the order of the risk classes a
ceiling is compared in. This file holds the Cloud Core's half to the shared file and to
the WORKER'S SOURCE, read as text - two suites that each agree with themselves is how
the halves drift.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.protocol_files import protocol_file
from app.webtask import risk, sites
from app.webtask.types import RISK_ORDER

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "protocol" / "BROWSER_CAPABILITIES.md"
DENYLIST = REPO / "packages" / "protocol" / "browser-task-denylist.json"
RISK_MARKERS = REPO / "packages" / "protocol" / "browser-risk-markers.json"
BROWSER = REPO / "services" / "browser" / "browser_agent"
WORKER = BROWSER / "worker.py"
POLICY = BROWSER / "policy.py"
TASK_DENYLIST_PY = BROWSER / "task_denylist.py"


def _shared() -> dict[str, Any]:
    # No skip when the file is missing (test_contract_falsification's rule).
    return json.loads(DENYLIST.read_text(encoding="utf-8"))


def _worker_categories() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """``CATEGORIES`` out of the worker's source, without importing the worker."""
    tree = ast.parse(TASK_DENYLIST_PY.read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "CATEGORIES":
            assert node.value is not None
            return ast.literal_eval(node.value)
    raise AssertionError("the worker's CATEGORIES could not be found")


# ------------------------------------------------------------------ one list


def test_both_sides_deny_the_same_sites() -> None:
    shared = _shared()["categories"]
    cloud = {c.name: (c.domains, c.host_label_parts) for c in sites.categories()}
    worker = _worker_categories()
    expected = {
        name: (tuple(body["domains"]), tuple(body["host_label_parts"]))
        for name, body in shared.items()
    }
    assert cloud == expected
    assert worker == expected
    assert list(worker) == list(shared)  # the first category that matches is the one named


def test_the_cloud_reads_the_bundled_copy_and_it_is_the_shared_file() -> None:
    assert protocol_file("browser-task-denylist.json") == sites.DENYLIST_PATH
    assert sites.DENYLIST_PATH.read_bytes() == DENYLIST.read_bytes()
    assert protocol_file("browser-risk-markers.json") == risk.MARKERS_PATH
    assert risk.MARKERS_PATH.read_bytes() == RISK_MARKERS.read_bytes()


@pytest.mark.parametrize(
    "url",
    [
        "https://sube.isbank.com.tr/hesap",
        "https://www.foodbank.example.org/",
        "https://giris.turkiye.gov.tr/",
        "https://www.paypal.com/checkout",
        "https://vault.bitwarden.com/",
        "https://mail.turka.com/owa",
        "https://app.kolaymonitor.com/",
        "https://www.trendyol.com/",
        "https://paypal.com.evil.example/",
        "https://example.org/?next=https://www.paypal.com/",
        "https://mail.proton.me/u/0/inbox",
        "about:blank",
        "",
    ],
)
def test_both_sides_answer_the_same_for_the_same_address(url: str) -> None:
    """The worker's matcher is run FROM ITS SOURCE, not re-implemented here."""
    namespace: dict[str, Any] = {}
    exec(compile(TASK_DENYLIST_PY.read_text("utf-8"), str(TASK_DENYLIST_PY), "exec"), namespace)  # noqa: S102
    assert namespace["denied"](url) == sites.denied(url)


# ------------------------------------------------------------------ the ceiling


def test_both_sides_order_the_risk_classes_the_same_way() -> None:
    policy = POLICY.read_text("utf-8")
    block = re.search(r"RISK_ORDER: tuple\[RiskClass, \.\.\.\] = \((.*?)\n\)", policy, re.DOTALL)
    assert block is not None, "the worker's RISK_ORDER could not be found"
    assert re.findall(r"RiskClass\.([A-Z_]+)", block.group(1)) == list(RISK_ORDER)


def test_the_worker_applies_the_ceiling_after_the_policy_and_before_the_click() -> None:
    """A source check, named as one: the behaviour is the worker's own end-to-end test
    (a HIGH_IMPACT element under a REVERSIBLE_WRITE ceiling is not clicked)."""
    worker = WORKER.read_text("utf-8")
    start = worker.index("async def _op_click(")
    body = worker[start : worker.index("    async def _op_", start + 10)]
    policy_at = body.index(
        'policy.enforce(state.policy_allowed, risk_class, capability="browser.click")'
    )
    ceiling_at = body.index("policy.enforce_ceiling(")
    assert policy_at < ceiling_at
    assert 'payload.get("risk_ceiling")' in body
    assert ".click(" not in body[:ceiling_at]


def test_every_writing_operation_of_the_worker_asks_the_deny_list_first() -> None:
    worker = WORKER.read_text("utf-8")
    for name in ("click", "fill", "select_option", "set_checked", "download", "upload"):
        assert f'_refuse_on_denied_site(state, "browser.{name}")' in worker, name
    assert worker.count("_refuse_on_denied_site(state, ") == 6


# ------------------------------------------------------------------ the document


def test_the_document_says_v1_7_and_what_it_is_made_of() -> None:
    contract = CONTRACT.read_text("utf-8")
    # v1.8 is the current version (test_browser_contract_v18.py); v1.7 stays in the log.
    assert "- **v1.7 (2026-09-29, ADR-0207, PR-B)" in contract
    assert "## 4a. The risk ceiling and the task deny-list (contract v1.7, ADR-0207)" in contract
    for phrase in (
        "`risk_ceiling`",
        '"reason":"above_ceiling"',
        '"reason":"denied_site"',
        "A ceiling never WIDENS anything",
        "the host, never the URL",
        "elements inside frames",
    ):
        assert phrase in contract, phrase
    worker = WORKER.read_text("utf-8") + POLICY.read_text("utf-8")
    assert '"reason": "above_ceiling"' in worker and '"reason": "denied_site"' in worker


def test_v1_7_adds_no_operation_name() -> None:
    policy = POLICY.read_text("utf-8")
    block = re.search(r"CAPABILITIES: tuple\[str, \.\.\.\] = \((.*?)\n\)", policy, re.DOTALL)
    assert block is not None
    names = re.findall(r'"(browser\.[a-z_]+)"', block.group(1))
    assert len(names) == 30 and names[-1] == "browser.observe"

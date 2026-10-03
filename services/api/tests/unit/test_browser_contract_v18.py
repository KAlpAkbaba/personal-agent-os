"""Browser contract v1.8 (ADR-0207, PR-C): the ceiling on a write.

``risk_ceiling`` was a click's alone in v1.7; v1.8 carries it on ``fill``,
``select_option`` and ``set_checked``. This file holds the document and the worker's
source to that; the behaviour (a refused write leaves the page unchanged) is the
worker's end-to-end test, ``services/browser/tests/browser/test_write_ceiling_e2e.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "protocol" / "BROWSER_CAPABILITIES.md"
BROWSER = REPO / "services" / "browser" / "browser_agent"
WORKER = BROWSER / "worker.py"
POLICY = BROWSER / "policy.py"
DEVICE_PORT = REPO / "services" / "api" / "app" / "webtask" / "device_port.py"

WRITES = (
    ("fill", "state.browser_session.fill("),
    ("select_option", "state.browser_session.select_option("),
    ("set_checked", "state.browser_session.set_checked("),
)


def _section_4a(contract: str) -> str:
    start = contract.index("## 4a.")
    return contract[start : contract.index("\n## 5.", start)]


def test_the_document_says_v1_8_and_names_the_three_writes_in_4a() -> None:
    contract = CONTRACT.read_text("utf-8")
    assert "Status: contract **v1.8**" in contract
    assert "- **v1.8 (" in contract
    section = _section_4a(contract)
    for name in ("browser.fill", "browser.select_option", "browser.set_checked"):
        assert f"`risk_ceiling` on `{name}`" in section or name in section, name
    assert (
        "`risk_ceiling` on `browser.fill`, `browser.select_option` and `browser.set_checked`"
        in (section)
    )
    for phrase in ('"reason":"above_ceiling"', "`risk_class`", "as in v1.7", "contract v1.8"):
        assert phrase in section, phrase


@pytest.mark.parametrize(("name", "session_call"), WRITES)
def test_each_write_of_the_worker_enforces_the_ceiling_before_it_writes(
    name: str, session_call: str
) -> None:
    """A source check, named as one: the behaviour is the e2e test's (the page is read
    back unchanged after a refusal)."""
    worker = WORKER.read_text("utf-8")
    start = worker.index(f"async def _op_{name}(")
    body = worker[start : worker.index("    async def _op_", start + 10)]
    # The class is decided in one helper, and the helper is called before the write.
    assert "await self._write_class(" in body, name
    assert body.index("await self._write_class(") < body.index(session_call), name
    helper_start = worker.index("async def _write_class(")
    helper = worker[helper_start : worker.index("\n    async def ", helper_start + 10)]
    policy_at = helper.index("policy.enforce(state.policy_allowed, risk_class")
    ceiling_at = helper.index("policy.enforce_ceiling(risk_class, ceiling")
    assert policy_at < ceiling_at
    assert "_DESCRIBE_ELEMENT_JS" not in helper  # a field's "name" there is its VALUE


def test_v1_8_adds_no_operation_name_and_no_contract_key() -> None:
    policy = POLICY.read_text("utf-8")
    block = re.search(r"CAPABILITIES: tuple\[str, \.\.\.\] = \((.*?)\n\)", policy, re.DOTALL)
    assert block is not None
    names = re.findall(r'"(browser\.[a-z_]+)"', block.group(1))
    assert len(names) == 30 and names[-1] == "browser.observe"
    worker = WORKER.read_text("utf-8")
    contracts = re.search(r"^CONTRACTS[^=]*= \{(.*?)\}", worker, re.DOTALL | re.MULTILINE)
    assert contracts is not None
    assert "browser.fill" not in contracts.group(1)


def test_the_consumer_names_v1_8_when_a_device_cannot_enforce() -> None:
    source = DEVICE_PORT.read_text("utf-8")
    assert "contract v1.8" in source

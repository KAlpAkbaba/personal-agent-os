"""Browser contract v1.6 (ADR-0207, PR-A): the halves agree, read from each other's source.

The contract document, the worker, the companion host, install verification and the
Cloud Core's own allowlist each carry the operation list. ``services/browser`` holds its
four mirrors together; this file holds the Cloud Core to the same list and to the two
JSON files both sides are meant to read. No browser, no C# build: every other side is
read as text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.routines.dispatch import BROWSER_ACTION_ALLOWLIST

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "protocol" / "BROWSER_CAPABILITIES.md"
RISK_MARKERS = REPO / "packages" / "protocol" / "browser-risk-markers.json"
BROWSER = REPO / "services" / "browser" / "browser_agent"
POLICY = BROWSER / "policy.py"
WORKER = BROWSER / "worker.py"
OBSERVE = BROWSER / "observe.py"
TARGETS = BROWSER / "targets.py"
RISK_MARKERS_PY = BROWSER / "risk_markers.py"
PROTOCOL_CS = (
    REPO
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "ProtocolConstants.cs"
)


def _worker_capabilities() -> list[str]:
    source = POLICY.read_text("utf-8")
    block = re.search(r"CAPABILITIES: tuple\[str, \.\.\.\] = \((.*?)\n\)", source, re.DOTALL)
    assert block is not None, "the worker's CAPABILITIES tuple could not be found"
    return re.findall(r'"(browser\.[a-z_]+)"', block.group(1))


def _contract_names() -> set[str]:
    source = CONTRACT.read_text("utf-8")
    start = source.index("## 1. Capability names")
    end = source.index("Names match", start)
    return set(re.findall(r"`(browser\.[a-z_]+)`", source[start:end])) - {"browser.chrome"}


def _python_tuple(source: str, name: str) -> list[str]:
    block = re.search(rf"{name}: Final\[tuple\[str, \.\.\.\]\] = \((.*?)\n\)", source, re.DOTALL)
    assert block is not None, f"{name} could not be found"
    return re.findall(r'^\s+"([^"]+)",$', block.group(1), re.MULTILINE)


# ------------------------------------------------------------------ the operation list


def test_the_contract_the_worker_and_the_cloud_name_the_same_operations() -> None:
    worker = _worker_capabilities()
    assert set(worker) == _contract_names()
    assert {op.removeprefix("browser.") for op in worker} == set(BROWSER_ACTION_ALLOWLIST)
    assert len(worker) == len(set(worker)) == 30


def test_observe_is_in_every_place_an_operation_has_to_be() -> None:
    assert "browser.observe" in _worker_capabilities()
    assert "observe" in BROWSER_ACTION_ALLOWLIST
    assert "| `browser.observe` | READ |" in CONTRACT.read_text("utf-8")
    assert '"browser.observe": Worker._op_observe' in WORKER.read_text("utf-8")
    assert '"browser.observe": RiskClass.READ' in POLICY.read_text("utf-8")
    assert 'public const string Observe = "browser.observe";' in PROTOCOL_CS.read_text("utf-8")


def test_the_additions_are_appended_so_a_manifest_diff_reads_as_an_addition() -> None:
    assert _worker_capabilities()[-1] == "browser.observe"
    assert _worker_capabilities()[-2] == "browser.media_stop"


# ------------------------------------------------------------------ the contract's own text


def test_the_document_says_v1_6_and_what_it_is_made_of() -> None:
    contract = CONTRACT.read_text("utf-8")
    # The document's status moved on with v1.7 (test_browser_contract_v17); what v1.6
    # is made of stays in it.
    assert "- **v1.6 (2026-09-28, ADR-0207, PR-A)" in contract
    assert "## 3c. Observation (contract v1.6, ADR-0207)" in contract
    for phrase in (
        "The value of a field is never returned",
        "Nothing is written into the page",
        "A reference lives as long as its observation",
        "the markers are whole words, from one file",
        '"browser.observe": 1',
    ):
        assert phrase in contract, phrase


def test_the_document_and_the_worker_agree_on_the_bounds() -> None:
    contract = CONTRACT.read_text("utf-8")
    observe = OBSERVE.read_text("utf-8")
    targets = TARGETS.read_text("utf-8")
    assert "MAX_ELEMENTS_CEILING: Final = 120" in observe and "`max_elements` ≤ 120" in contract
    assert "MAX_TEXT_CHARS_CEILING: Final = 6_000" in observe
    assert "`max_text_chars` ≤ 6000" in contract
    assert "MAX_NAME_CHARS: Final = 80" in observe and "at most 80 characters" in contract
    assert "MAX_OBSERVATION_BYTES: Final = 40 * 1024" in observe and "40 KiB" in contract
    assert "MIN_TEXT_CHARS: Final = 1_000" in observe and "below 1000 characters" in contract
    assert "MAX_NTH = 199" in targets and "integer, 0–199" in contract


def test_every_refusal_reason_the_worker_gives_is_in_the_document() -> None:
    contract = CONTRACT.read_text("utf-8")
    worker = WORKER.read_text("utf-8")
    reasons = set(re.findall(r'stale\("([a-z_]+)"', worker))
    reasons |= {
        part.strip().strip('"')
        for pair in re.findall(r'stale\((".*?" if .*? else ".*?")\)', worker)
        for part in re.split(r" if .*? else ", pair)
    }
    assert reasons >= {"no_observation", "other_observation", "other_tab", "navigated"}
    for reason in reasons:
        assert f"`{reason}`" in contract, f"the contract does not name the reason {reason!r}"


def test_the_hello_contract_is_what_the_document_promises() -> None:
    worker = WORKER.read_text("utf-8")
    assert '"browser.observe": 1' in worker
    assert re.search(r'CONTRACTS: dict\[str, int\] = \{[^}]*"browser\.observe": 1', worker)


# ------------------------------------------------------------------ the risk markers


def test_the_risk_markers_file_is_well_formed_and_the_worker_carries_it_verbatim() -> None:
    shared = json.loads(RISK_MARKERS.read_text("utf-8"))
    # Version 2 (ADR-0207 PR-B) added `payment`: the subset of `high_impact` a browser
    # task never performs at all. The worker reads the two lists it classifies with.
    assert shared["version"] == 2
    assert set(shared) == {
        "version",
        "matching",
        "high_impact",
        "payment",
        "external_communication",
    }
    assert set(shared["payment"]) <= set(shared["high_impact"])
    assert len(shared["payment"]) == len(set(shared["payment"])) > 0
    source = RISK_MARKERS_PY.read_text("utf-8")
    assert _python_tuple(source, "HIGH_IMPACT") == shared["high_impact"]
    assert _python_tuple(source, "EXTERNAL_COMMUNICATION") == shared["external_communication"]
    for markers in (shared["high_impact"], shared["payment"], shared["external_communication"]):
        assert markers and all(isinstance(m, str) and m == m.strip() and m for m in markers)
        assert len(markers) == len(set(markers))
        assert all(m == m.lower() or m != m.casefold() for m in markers)


def test_the_words_the_owner_named_are_where_he_said() -> None:
    shared = json.loads(RISK_MARKERS.read_text("utf-8"))
    high = set(shared["high_impact"])
    for phrase in ("siparişi tamamla", "onayla ve öde", "abone ol", "ödeme yap", "satın al"):
        assert phrase in high, phrase
    # "paylaş" sends something, and is NOT a purchase, a payment or a deletion.
    assert "paylaş" in shared["external_communication"] and "paylaş" not in high
    for not_a_marker in ("silver", "mode", "sepete ekle"):
        assert not_a_marker not in high
        assert not_a_marker not in shared["external_communication"]


def test_the_worker_no_longer_matches_a_marker_as_a_substring() -> None:
    policy = POLICY.read_text("utf-8")
    assert "_HIGH_IMPACT_NAME_MARKERS" not in policy
    assert "marker in lowered_name" not in policy
    assert "risk_markers.is_high_impact(" in policy
    markers = RISK_MARKERS_PY.read_text("utf-8")
    assert "(?<![0-9a-z])" in markers and "(?![0-9a-z])" in markers


# ------------------------------------------------------------------ what v1.6 does not add


def test_a_target_still_cannot_be_a_selector_or_a_coordinate() -> None:
    targets = TARGETS.read_text("utf-8")
    payload = re.search(r"_PAYLOAD_FIELDS = frozenset\(\{(.*?)\}\)", targets, re.DOTALL)
    assert payload is not None
    assert "resolved_path" not in payload.group(1)
    for forbidden in ("selector", "css", "xpath", '"x"', '"y"', "coordinate"):
        assert forbidden not in payload.group(1), forbidden
    contract = CONTRACT.read_text("utf-8")
    assert "There is still no way to pass a selector" in contract


def test_the_collector_writes_nothing() -> None:
    """A source check, and named as one: the BEHAVIOUR is held by the end-to-end test
    that fingerprints the page before and after. This one keeps the obvious ways of
    writing out of the script so that the e2e failure is never the first to notice."""
    observe = OBSERVE.read_text("utf-8")
    start = observe.index('COLLECT_JS: Final = r"""')
    script = observe[start : observe.index('"""', start + 30)]
    for writes in (
        "setAttribute",
        "dataset",
        ".innerHTML =",
        "window.__",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        ".click(",
        ".focus(",
        "fetch(",
        "XMLHttpRequest",
    ):
        assert writes not in script, writes

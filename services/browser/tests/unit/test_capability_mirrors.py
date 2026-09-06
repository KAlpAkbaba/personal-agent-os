"""The capability list exists in four places. They must not drift.

`BROWSER_CAPABILITIES.md` §1 is the contract; `browser_agent.policy.CAPABILITIES`
is what the worker advertises and serves; `BrowserCapabilities.Operations` (C#)
is what the Session Companion's host allows through to the worker; and
`$script:BrowserOperations` (PowerShell) is what install verification proves the
installed agent advertises.

Every one of those is a MIRROR of the same list, and each has its own failure
mode when it falls behind: a name missing from the C# list is refused before it
reaches the worker; a name missing from the PowerShell list silently stops being
verified, so an agent that predates it installs "successfully" and the owner
finds out from an alarm that did not ring. Adding M18.3's four media names meant
touching all four, which is exactly the moment to make the agreement structural
instead of remembered.

These tests read the other three files as text — no C# build, no PowerShell
process, no browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from browser_agent import policy

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_MD = REPO_ROOT / "packages" / "protocol" / "BROWSER_CAPABILITIES.md"
PROTOCOL_CS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "ProtocolConstants.cs"
)
INSTALL_EVIDENCE_PS1 = REPO_ROOT / "scripts" / "lib" / "InstallEvidence.ps1"

FAMILY_MARKER = "browser.chrome"


def _require(path: Path) -> str:
    if not path.exists():  # pragma: no cover - only in a partial checkout
        pytest.skip(f"{path} is not present in this checkout")
    return path.read_text(encoding="utf-8")


def _csharp_operations(source: str) -> list[str]:
    """`Operations = [ SessionOpen, ... ]` resolved through the const declarations."""
    consts = dict(
        re.findall(
            r'public\s+const\s+string\s+(\w+)\s*=\s*"(browser\.[a-z_]+)"\s*;',
            source,
        )
    )
    block = re.search(r"Operations\s*=\s*\[(.*?)\]\s*;", source, re.DOTALL)
    assert block is not None, "the C# Operations list could not be found"
    identifiers = [name.strip() for name in block.group(1).split(",") if name.strip()]
    unknown = [name for name in identifiers if name not in consts]
    assert not unknown, f"Operations names constants that are not declared: {unknown}"
    return [consts[name] for name in identifiers]


def _powershell_operations(source: str) -> list[str]:
    block = re.search(
        r"\$script:BrowserOperations\s*=\s*@\((.*?)\n\)", source, re.DOTALL
    )
    assert block is not None, "the PowerShell BrowserOperations list could not be found"
    return re.findall(r'"(browser\.[a-z_]+)"', block.group(1))


def _contract_section_1_names(source: str) -> set[str]:
    start = source.index("## 1. Capability names")
    end = source.index("Names match", start)
    return set(re.findall(r"`(browser\.[a-z_]+)`", source[start:end]))


def test_the_csharp_host_allowlist_is_the_workers_capability_list_in_order() -> None:
    """Same names, same order. Order is not cosmetic: BrowserDispatchTests pins
    the manifest positionally, and a manifest diff between two agent versions
    should read as an addition rather than a reshuffle."""
    assert _csharp_operations(_require(PROTOCOL_CS)) == list(policy.CAPABILITIES)


def test_install_verification_requires_every_capability_the_worker_serves() -> None:
    """A name the worker serves but install verification does not check is a
    capability the owner is never told is missing."""
    assert _powershell_operations(_require(INSTALL_EVIDENCE_PS1)) == list(policy.CAPABILITIES)


def test_the_contract_document_names_exactly_what_is_implemented() -> None:
    """§1 is the contract; the code is the implementation. Set comparison, since
    §1 groups some names on one row (`back` / `forward`, the tab family)."""
    documented = _contract_section_1_names(_require(CONTRACT_MD))
    assert documented == {FAMILY_MARKER, *policy.CAPABILITIES}


def test_the_media_family_is_present_in_all_four_places() -> None:
    """The specific check M18.3 needed, spelled out so a future reader sees which
    four files must move together."""
    media_names = (
        "browser.media_play",
        "browser.media_volume",
        "browser.media_status",
        "browser.media_stop",
    )
    csharp = _csharp_operations(_require(PROTOCOL_CS))
    powershell = _powershell_operations(_require(INSTALL_EVIDENCE_PS1))
    documented = _contract_section_1_names(_require(CONTRACT_MD))
    for name in media_names:
        assert name in policy.CAPABILITIES, f"{name} is not served by the worker"
        assert name in csharp, f"{name} is not allowed through the companion host"
        assert name in powershell, f"{name} is not verified on the installed agent"
        assert name in documented, f"{name} is not in the contract document"


def test_the_family_marker_is_never_an_operation() -> None:
    """`browser.chrome` says "this device has a worker"; it is never executed."""
    assert FAMILY_MARKER not in policy.CAPABILITIES
    assert FAMILY_MARKER not in _csharp_operations(_require(PROTOCOL_CS))
    assert FAMILY_MARKER not in _powershell_operations(_require(INSTALL_EVIDENCE_PS1))

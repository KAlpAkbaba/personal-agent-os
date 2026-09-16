"""B30 req 117-122: ``packages/protocol/operator-allowlists.json`` is read by both halves.

The Cloud Core's ``app.operator.allowlists`` loads the file at import; the companion keeps
compiled tables and ``OperatorAllowlistsContractTests.cs`` holds them equal to the same
file. This suite is the Python side of that contract AND reads the C# source directly, so
a table that drifts on either side is red here even when the C# suite has not run (the
"contract halves must read each other" discipline, ADR-0102).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.operator import allowlists, plans
from app.operator.capabilities import PLAN_BY_SHELL_QUERY

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "protocol" / "operator-allowlists.json"
COMPANION = REPO / "devices/windows-agent/src/PagentOS.SessionCompanion/Operator"


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text("utf-8"))


# ------------------------------------------------------------------ applications


def test_the_application_allowlist_is_the_contracts_in_its_order(contract: dict) -> None:
    declared = tuple(entry["id"] for entry in contract["applications"])
    assert allowlists.APP_IDS == declared
    assert plans.APP_ALLOWLIST == declared, "plans must read the contract, not keep a copy"
    assert set(allowlists.APP_NAMES_TR) == set(declared)
    assert set(allowlists.APP_IMAGES) == set(declared)
    # The 2026-09-14 drift: mspaint was on the device and not on the cloud.
    assert "mspaint" in declared


def test_every_alias_resolves_and_the_longest_phrase_wins() -> None:
    from app.voice.intents import normalize_transcript

    for alias, app_id in allowlists.APP_ALIAS_PHRASES:
        _, tokens, _ = normalize_transcript(f"{alias} aç")
        assert plans.resolve_app_alias(tokens) == app_id, alias
    lengths = [len(alias) for alias, _ in allowlists.APP_ALIAS_PHRASES]
    assert lengths == sorted(lengths, reverse=True), "longest alias must be tried first"
    _, tokens, _ = normalize_transcript("google chrome aç")
    assert plans.resolve_app_alias(tokens) == "chrome"


def test_the_companions_default_applications_are_the_contracts(contract: dict) -> None:
    """Reads ``DefaultApplications()`` from the C# source: one ``["id"] = ...`` line per
    contract application, and the image file name on that line."""
    source = (COMPANION / "OperatorCapabilities.cs").read_text("utf-8")
    start = source.index("public static Dictionary<string, string> DefaultApplications()")
    body = source[start : source.index("// ================", start)]
    device_ids = re.findall(r'^\s*\["([a-z0-9]+)"\] = ', body, flags=re.MULTILINE)
    assert sorted(device_ids) == sorted(allowlists.APP_IDS), (device_ids, allowlists.APP_IDS)
    terminal = (COMPANION / "TerminalRunner.cs").read_text("utf-8")
    for entry in contract["applications"]:
        line = next(ln for ln in body.splitlines() if f'["{entry["id"]}"] = ' in ln)
        # notepad/calc/mspaint/explorer name the image inline; chrome/msedge reach a variable
        # assigned from a path naming the image earlier in the same body; powershell reaches
        # ``TerminalRunner.DefaultPowerShellPath()``, whose source names it.
        if "DefaultPowerShellPath" in line:
            assert entry["image"] in terminal, entry["image"]
        else:
            assert entry["image"] in body, entry["image"]
        if '"' in line.split("=", 1)[1] and ".exe" in line:
            assert entry["image"] in line, line


# --------------------------------------------------------------------- terminal


def _companion_patterns() -> list[str]:
    source = (COMPANION / "TerminalRunner.cs").read_text("utf-8")
    project_help = re.search(r'ProjectHelpEntry = "([^"]+)"', source).group(1)
    start = source.index("DefaultAllowlist =")
    block = source[start : source.index("];", start)]
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith('"'):
            out.append(line.strip('"'))
        elif line == "ProjectHelpEntry":
            out.append(project_help)
    return out


def test_the_terminal_patterns_are_the_companions_verbatim(contract: dict) -> None:
    assert list(allowlists.TERMINAL_PATTERNS) == contract["terminal"]["patterns"]
    assert _companion_patterns() == list(allowlists.TERMINAL_PATTERNS)


def test_every_shell_query_the_cloud_sends_is_a_command_the_device_admits() -> None:
    assert set(allowlists.SHELL_COMMANDS) == set(PLAN_BY_SHELL_QUERY), (
        "a shell-query kind without a command, or a command without a plan name"
    )
    for kind, command in allowlists.SHELL_COMMANDS.items():
        assert allowlists.command_allowed(command), (kind, command)
        steps = plans.shell_query(kind)
        assert steps[0].payload == {"command": command}
        assert steps[0].capability == "terminal.execute"
    with pytest.raises(ValueError):
        plans.shell_query("uptime")


@pytest.mark.parametrize(
    "command",
    [
        "hostname; whoami",
        "hostname | Out-File x",
        "Get-Process -Name $(whoami)",
        "hostname extra",
        "Remove-Item C:\\Windows\\Temp\\x",
        "",
    ],
)
def test_the_pattern_rule_refuses_composition_and_extra_tokens(command: str) -> None:
    assert not allowlists.command_allowed(command), command


def test_the_pattern_rule_admits_the_wildcard_forms() -> None:
    assert allowlists.command_allowed("Get-Process -Name chrome")
    assert allowlists.command_allowed("Get-ChildItem C:\\Users\\x\\Documents")
    assert allowlists.command_allowed("IPCONFIG"), "the device compares case-insensitively"


# ------------------------------------------------------------ processes / services


def test_the_stop_policy_is_the_contracts_and_never_a_system_process(contract: dict) -> None:
    assert allowlists.STOPPABLE_IMAGES == frozenset(
        i.lower() for i in contract["processes"]["stoppable_images"]
    )
    for system in ("explorer.exe", "powershell.exe", "svchost.exe", "csrss.exe", "winlogon.exe"):
        assert not allowlists.image_stoppable(system), system
    assert allowlists.image_stoppable("chrome.exe")
    assert allowlists.image_stoppable("C:\\Program Files\\Google\\Chrome\\Application\\CHROME.EXE")
    with pytest.raises(ValueError):
        plans.process_stop("powershell.exe")


def test_the_restart_policy_is_the_contracts(contract: dict) -> None:
    assert allowlists.RESTARTABLE_SERVICES == frozenset(
        s.lower() for s in contract["services"]["restartable"]
    )
    assert allowlists.service_restartable("Spooler")
    assert allowlists.service_restartable("spooler")
    assert not allowlists.service_restartable("wuauserv")
    assert not allowlists.service_restartable("")
    with pytest.raises(ValueError):
        plans.service_restart("bthserv")


def test_the_companion_keeps_the_same_two_policies(contract: dict) -> None:
    source = (COMPANION / "OperatorCapabilities.cs").read_text("utf-8")
    start = source.index("DefaultStoppableImages =")
    block = source[start : source.index("];", start)]
    device_images = re.findall(r'"([a-z0-9]+\.exe)"', block)
    assert device_images == contract["processes"]["stoppable_images"]
    start = source.index("DefaultRestartableServices =")
    block = source[start : source.index("];", start)]
    assert re.findall(r'"([A-Za-z0-9]+)"', block) == contract["services"]["restartable"]


def test_the_contract_names_its_readers(contract: dict) -> None:
    readers = "\n".join(contract["read_by"])
    assert "allowlists.py" in readers
    assert "OperatorCapabilities.cs" in readers
    assert "TerminalRunner.cs" in readers

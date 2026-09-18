"""Every browser profile Cloud Core asks for is one the browser agent actually has.

Production, 2026-09-18 ("Chrome'dan YouTube'u aç"): every operator mission's browser step
failed with ``session_open: profile must be one of ['alarm', 'isolated', 'news', 'owner',
'research']``. ``app/operator/mission.py`` fell back to a profile named ``"media"`` - a name
the agent never had. Eight modules send a profile name and each spells it itself; seven
were right. The mission's own test passed because its fake device accepted any name, so
both halves were green while the real pair could not talk.

So this reads BOTH sides from source: the agent's ``PROFILES`` from
``services/browser/browser_agent/media.py``, and every ``"profile": ...`` Cloud Core puts in
a payload, resolving a constant to the literal it is defined as in the same file. A new
sender with a new name fails here instead of in the owner's hands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AGENT_MEDIA = REPO / "services" / "browser" / "browser_agent" / "media.py"
CLOUD_APP = REPO / "services" / "api" / "app"


def agent_profiles() -> frozenset[str]:
    src = AGENT_MEDIA.read_text(encoding="utf-8")
    constants = dict(re.findall(r'^([A-Z_]+_PROFILE)\s*=\s*"([^"]+)"', src, re.M))
    block = re.search(r"^PROFILES\b[^=]*=\s*frozenset\(\s*\{([^}]*)\}", src, re.M)
    assert block, "the agent no longer declares PROFILES the way this test reads it"
    names = [n.strip() for n in block.group(1).split(",") if n.strip()]
    missing = [n for n in names if n not in constants]
    assert not missing, f"PROFILES names constants this test cannot resolve: {missing}"
    return frozenset(constants[n] for n in names)


def cloud_profile_requests() -> list[tuple[str, str]]:
    """(file, profile) for every ``"profile": X`` in Cloud Core, X resolved to its literal."""
    out: list[tuple[str, str]] = []
    for path in sorted(CLOUD_APP.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if '"profile":' not in src:
            continue
        constants = dict(
            re.findall(r'^([A-Z_]+)\s*(?::\s*Final(?:\[str\])?)?\s*=\s*"([^"]*)"', src, re.M)
        )
        for value in re.findall(r'"profile":\s*([A-Z_]+|"[^"]*")', src):
            literal = value.strip('"') if value.startswith('"') else constants.get(value)
            assert literal is not None, (
                f"{path.relative_to(REPO)} sends profile {value}, which this test cannot "
                "resolve to a literal - define it as a module constant"
            )
            out.append((str(path.relative_to(REPO)), literal))
    return out


def test_the_agent_declares_its_profiles() -> None:
    profiles = agent_profiles()
    assert {"owner", "isolated"} <= profiles, profiles


def test_cloud_core_does_ask_for_profiles() -> None:
    # Not vacuous: the scan must actually find the senders - seven on 2026-09-18, after the
    # operator mission stopped opening a separate automation profile at all.
    assert len(cloud_profile_requests()) >= 7


def test_every_profile_cloud_core_asks_for_exists_in_the_agent() -> None:
    profiles = agent_profiles()
    unknown = [(f, p) for f, p in cloud_profile_requests() if p not in profiles]
    assert not unknown, (
        f"Cloud Core asks the browser agent for profiles it does not have: {unknown}; "
        f"the agent has {sorted(profiles)}"
    )

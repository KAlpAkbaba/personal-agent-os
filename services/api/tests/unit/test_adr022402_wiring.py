"""adr0224-02: the lead's wiring of three worker packages into the shared files.

TEAM_PROTOCOL section 4. Layer 3 of ADR-0224, the voice summary of the team's office and the
device stamp of the operator's ledger rows were each built inside their own area; what joins
them to the running application is held here.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.security import step_up
from app.voice.realtime_sessions.tools import default_registry
from app.voice.realtime_sessions.tools_team import TOOL_TEAM_STATUS
from app.voice.understanding import policy as understanding_policy

REPO = Path(__file__).resolve().parents[4]


def test_the_team_status_tool_is_in_the_registry_the_relay_uses_and_is_open() -> None:
    spec = default_registry().get(TOOL_TEAM_STATUS)
    assert spec is not None, "register_team_tools is called by default_registry"
    assert spec.parameters.get("additionalProperties") is False
    # A read with no side effect: without its own tier the shadow mode logged it as sensitive.
    assert step_up._TIERS[TOOL_TEAM_STATUS] == step_up.TIER_OPEN


def test_in_database_mode_the_voice_tool_reads_the_store_the_office_page_reads() -> None:
    """On the Cloud Core there is no team/ folder: a tool that fell back to the files would
    say "Ekip şu an çalışmıyor" while the page showed three workers."""
    app = create_app(Settings(team_store="database"))
    live = app.state.voice_realtime.live_sources()
    assert live.get("team_store") is app.state.team_store
    plain = create_app(Settings())
    assert "team_store" not in plain.state.voice_realtime.live_sources()


def test_the_thresholds_ship_beside_their_one_reader() -> None:
    beside = Path(understanding_policy.__file__).with_name(understanding_policy.THRESHOLDS_FILE)
    assert beside.is_file()
    assert not (REPO / "packages" / "protocol" / "understanding-thresholds.json").exists(), (
        "one reader: package data, not a protocol file (ADR-0224 addendum 2)"
    )

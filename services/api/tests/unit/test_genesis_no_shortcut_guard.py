"""M24 Capability Genesis: no shortcut keyed on the fixtures — the central
prohibition of the milestone (M24_CAPABILITY_GENESIS_SPEC.md §3, §7, §10;
task instructions "the milestone's central prohibition, guard-tested").

Two independent proofs:

1. Static: ``app/genesis/{interface,adapter,service}.py`` carry none of the
   fixtures' own names, paths or operation ids — the generality asserted by
   ``test_genesis_generalisation.py`` cannot be an accident of a hidden
   ``if name == "counterbox"`` branch, because no such literal exists in the
   source at all.
2. Dynamic: deleting the fixture's own ``/spec`` (a fixture already running,
   already researched once) makes a FRESH run fail honestly at
   ``researching`` — nothing about the interface was cached or hardcoded from
   the first run.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.genesis import counterbox_app, lampbox_app
from tests.unit.genesis_stack import make_stack

GENESIS_SOURCE_FILES = (
    Path(__file__).resolve().parents[2] / "app" / "genesis" / "interface.py",
    Path(__file__).resolve().parents[2] / "app" / "genesis" / "adapter.py",
    Path(__file__).resolve().parents[2] / "app" / "genesis" / "service.py",
)

# Names/paths/operation ids unique to one fixture or the other — a literal
# match for ANY of these in the generator/service source would mean the
# "generality" the other tests exercise is faked.
FORBIDDEN_LITERALS = (
    "counterbox",
    "lampbox",
    "Sayaç Kutusu",
    "Test Lambası",
    "/counter",
    "/lamp",
    "increment",
    "toggle",
    "brightness",
)


def test_genesis_source_carries_no_fixture_literal():
    for path in GENESIS_SOURCE_FILES:
        source = path.read_text(encoding="utf-8")
        for literal in FORBIDDEN_LITERALS:
            assert literal not in source, f"{path.name} contains fixture literal {literal!r}"


def test_deleting_spec_fails_a_fresh_run_at_researching(tmp_path):
    stack = make_stack(tmp_path)
    with counterbox_app.serve() as server:
        # Prove the fixture answers first (so the failure below is really
        # about the missing /spec, not a fixture that never worked).
        result = stack.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
        assert result["state"] == "verified"

        server.spec_enabled = False
        fresh = stack.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="reset"
        )
        assert fresh["state"] == "failed"
        assert fresh["error_class"] == "dependency_unavailable"
        # Nothing was cached from the FIRST run: reset never registered.
        assert stack.registry.resolve("counterbox.reset") is None


def test_deleting_spec_fails_lampbox_too(tmp_path):
    """The SAME guard against the OTHER fixture — not counter-specific."""
    stack = make_stack(tmp_path)
    with lampbox_app.serve() as server:
        server.spec_enabled = False
        fresh = stack.service.request(
            interface_name="lampbox", interface_url=server.spec_url, operation_id="state"
        )
        assert fresh["state"] == "failed"
        assert fresh["error_class"] == "dependency_unavailable"
        assert stack.registry.resolve("lampbox.state") is None

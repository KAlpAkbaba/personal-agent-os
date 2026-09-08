"""M24 Capability Genesis: the counter box run through ``GenesisService`` end
to end, real HTTP against the live fixture, real isolated-subprocess dispatch
(M24_CAPABILITY_GENESIS_SPEC.md §7, §10).

No shortcut: the generator is called with the description FETCHED from the
running fixture (``fetch_interface``, a real loopback GET), the rendered
adapter is executed as a real subprocess (``CapabilityDispatcher``), the
counter really increments on the fixture's own in-memory state, and the
read-back verifies against the mutation's own reported value.
"""

from __future__ import annotations

import uuid

from tests.fixtures.genesis import counterbox_app
from tests.unit.genesis_stack import authorized, make_stack


def test_counterbox_increment_end_to_end(tmp_path):
    stack = make_stack(tmp_path, mutation_authorization=authorized("counterbox"))
    with counterbox_app.serve() as server:
        assert server.value == 0

        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 3},
            session_id="test-session",
        )

        assert result["state"] == "verified", result
        assert result["error_class"] is None
        # The release gates (evaluation, the independent reviewer's OWN
        # re-run, shadow, canary) each really call the live fixture too —
        # by design (spec §3's own prohibition on a mocked test), so the
        # counter's absolute value by the time the OWNER's own "by": 3
        # request dispatches is not 3 by itself; what must hold is that the
        # mutation's own reported result, the read-back's independently
        # fetched result and the fixture's real state all agree.
        dispatched_value = result["evidence"]["dispatch"]["output"]["value"]
        assert result["evidence"]["read_back"]["value"] == dispatched_value
        assert server.value == dispatched_value  # the REAL fixture state, not a mock

        resolved = stack.registry.resolve("counterbox.increment")
        assert resolved is not None
        assert resolved["status"] == "production"
        assert resolved["manifest"]["side_effect_class"] == "mutate_external"
        assert resolved["manifest"]["authority_class"] == "mutating_authorized_asset"

        # counterbox.read was built+registered automatically as the evidence
        # contract's read_back operation.
        read_resolved = stack.registry.resolve("counterbox.read")
        assert read_resolved is not None
        assert read_resolved["manifest"]["authority_class"] == "read_only"

        # The gap this run opened is now resolved by generation.
        assert result.get("gap_id")
        gap = stack.gaps.get(uuid.UUID(result["gap_id"]))
        assert gap["resolution"] == "generation"
        assert gap["status"] == "resolved"
        composition_step = next(e for e in gap["decision_trail"] if e["step"] == "composition")
        assert composition_step["evidence"]["attempted"] is True

        # A SECOND identical request resolves through the ALREADY-registered
        # capability — no new run (spec §7).
        runs_before = len(stack.service.list())
        before_value = server.value
        second = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 4},
        )
        assert second["new_run"] is False
        assert second["output"]["value"] == before_value + 4
        assert server.value == before_value + 4
        assert len(stack.service.list()) == runs_before


def test_counterbox_read_only_never_asks_for_approval(tmp_path):
    """A read-only operation never parks at awaiting_approval, even with NO
    mutation authorization recorded anywhere (spec §5: only a MUTATING
    operation against an unauthorized asset asks)."""
    stack = make_stack(tmp_path)  # NullAuthorizationProvider: nothing authorized
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="read",
        )
        assert result["state"] == "verified", result
        assert result["authority_class"] == "read_only"
        assert result["approval_required"] is False

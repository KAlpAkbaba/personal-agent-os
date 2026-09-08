"""M24 Capability Genesis: the SAME ``GenesisService`` code path against a
DIFFERENT fixture application — the lamp box, not the counter box
(M24_CAPABILITY_GENESIS_SPEC.md §7). Proves ``HttpAdapterGenerator`` is
generic over the description rather than counter-shaped: different operation
ids (``state``/``set``/``toggle`` vs. ``read``/``increment``/``reset``),
different output shape (two fields, ``on``+``brightness``, vs. one), a
zero-input mutating operation (``toggle``) and a two-field mutating operation
(``set``) neither of which the counter box exercises.
"""

from __future__ import annotations

from tests.fixtures.genesis import lampbox_app
from tests.unit.genesis_stack import authorized, make_stack


def test_lampbox_toggle_end_to_end(tmp_path):
    stack = make_stack(tmp_path, mutation_authorization=authorized("lampbox"))
    with lampbox_app.serve() as server:
        before_on = server.on

        result = stack.service.request(
            interface_name="lampbox",
            interface_url=server.spec_url,
            operation_id="toggle",
        )

        assert result["state"] == "verified", result
        dispatched = result["evidence"]["dispatch"]["output"]
        assert dispatched["on"] == (not before_on)
        assert result["evidence"]["read_back"] == dispatched
        assert server.on == dispatched["on"]
        assert server.brightness == dispatched["brightness"]

        resolved = stack.registry.resolve("lampbox.toggle")
        assert resolved is not None
        assert resolved["manifest"]["side_effect_class"] == "mutate_external"

        # lampbox.state (the read_back) was auto-registered too.
        assert stack.registry.resolve("lampbox.state") is not None


def test_lampbox_set_two_field_payload(tmp_path):
    """``set`` takes TWO input fields (on, brightness) — proves the adapter
    is not hardcoded to a single-field payload."""
    stack = make_stack(tmp_path, mutation_authorization=authorized("lampbox"))
    with lampbox_app.serve() as server:
        result = stack.service.request(
            interface_name="lampbox",
            interface_url=server.spec_url,
            operation_id="set",
            arguments={"on": True, "brightness": 42},
        )
        assert result["state"] == "verified", result
        assert result["evidence"]["dispatch"]["output"] == {"on": True, "brightness": 42}
        assert server.on is True
        assert server.brightness == 42


def test_lampbox_state_is_read_only_and_two_fields(tmp_path):
    stack = make_stack(tmp_path)  # nothing authorized anywhere
    with lampbox_app.serve() as server:
        result = stack.service.request(
            interface_name="lampbox", interface_url=server.spec_url, operation_id="state"
        )
        assert result["state"] == "verified", result
        assert result["authority_class"] == "read_only"
        output = result["evidence"]["dispatch"]["output"]
        assert set(output) == {"on", "brightness"}

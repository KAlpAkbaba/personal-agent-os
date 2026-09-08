"""M24 Capability Genesis voice tools (docs/M24_CAPABILITY_GENESIS_SPEC.md §6),
through the REAL application object (``create_app``, via
``tests.voice_corpus.harness.build_harness`` — the SAME wiring the corpus and
the REST routes run on).
"""

from __future__ import annotations

from app.genesis.catalogue import CatalogueEntry, OperationAlias, get_catalogue
from tests.fixtures.genesis import counterbox_app
from tests.voice_corpus.harness import build_harness


def _register_counterbox(server) -> None:
    get_catalogue().register(
        CatalogueEntry(
            name="counterbox",
            url=server.spec_url,
            target_phrases=("sayaç kutusu", "sayaç kutusunu", "sayaç"),
            operations=(
                OperationAlias("read", ("kaç",)),
                OperationAlias("increment", ("artır",)),
                OperationAlias("reset", ("sıfırla",)),
            ),
        )
    )


def test_capability_request_read_through_create_app():
    h = build_harness()
    with counterbox_app.serve() as server:
        _register_counterbox(server)
        sid = h.new_session()
        said = h.say(sid, "Sayaç kaç?")
        resolved = said["resolved_intents"][0]
        assert resolved["intent"] == "capability_request"
        assert resolved["capability"] == "capability.request"

        call = h.tool(sid, "c-1", "capability.request", {})
        assert call["status"] == "succeeded", call
        result = call["result"]
        assert result["execution_status"] == "executed"
        assert "Artık yapabiliyorum" in result["speech"] or "efendim" in result["speech"]
        assert result["run"]["state"] == "verified"
        assert result["run"]["capability_id"] == "counterbox.read"


def test_capability_request_mutation_parks_awaiting_approval():
    h = build_harness()
    with counterbox_app.serve() as server:
        _register_counterbox(server)
        sid = h.new_session()
        h.say(sid, "Sayaç kutusunu bir artır.")
        call = h.tool(sid, "c-1", "capability.request", {"arguments": {"by": 1}})
        assert call["status"] == "succeeded", call
        result = call["result"]
        assert result["run"]["state"] == "awaiting_approval"
        assert "onay" in result["speech"].lower()


def test_capability_approve_completes_the_mutation_and_capability_cancel_leaves_nothing():
    h = build_harness()
    with counterbox_app.serve() as server:
        _register_counterbox(server)

        # approve
        sid = h.new_session()
        h.say(sid, "Sayaç kutusunu bir artır.", turn=1)
        h.tool(sid, "c-1", "capability.request", {"arguments": {"by": 1}})
        h.say(sid, "Onaylıyorum.", turn=2)
        approve_call = h.tool(sid, "c-2", "capability.approve", {})
        assert approve_call["status"] == "succeeded", approve_call
        approve_result = approve_call["result"]
        assert approve_result["execution_status"] == "executed"
        assert approve_result["run"]["state"] == "verified"

        # cancel — a SECOND, distinct capability (reset) in a NEW session
        sid2 = h.new_session()
        h.say(sid2, "Sayaç kutusunu sıfırla.", turn=1)
        h.tool(sid2, "c-1", "capability.request", {})
        h.say(sid2, "Vazgeç, yapma.", turn=2)
        cancel_call = h.tool(sid2, "c-2", "capability.cancel", {})
        assert cancel_call["status"] == "succeeded", cancel_call
        cancel_result = cancel_call["result"]
        assert cancel_result["execution_status"] == "executed"
        assert cancel_result["run"]["state"] == "cancelled"
        assert h.genesis.service.registry.resolve("counterbox.reset") is None


def test_capability_status_with_nothing_asked():
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Yeni yetenek ne durumda?")
    call = h.tool(sid, "c-1", "capability.status", {})
    assert call["status"] == "succeeded", call
    assert "Henüz" in call["result"]["speech"]


def test_capability_request_refuses_an_unknown_target():
    """A target outside the catalogue is refused before any research (spec §7);
    since the catalogue is empty here, the utterance never even resolves to
    CAPABILITY_REQUEST at the router."""
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "Google'ı bir artır.")
    resolved = said["resolved_intents"][0]
    assert resolved["intent"] == "none"

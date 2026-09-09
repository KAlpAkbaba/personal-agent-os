# ruff: noqa: F811 - the shared `wired` fixture is imported and named as a parameter
"""The UI-state contract (ADR-0052): truthful, decoupled, content-free, bounded.

These are the rules a future Holographic Core renderer will depend on. They are tested
here rather than in the renderer because the contract is the product, not the animation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.uistate import UI_STATES, UiState, UiStateEvent, ui_state_contract
from app.uistate.contract import CONTRACT_VERSION, SUBSYSTEMS
from app.uistate.publisher import (
    TAIL_SIZE,
    UiStatePublisher,
    is_forbidden_metadata_key,
    publish,
    set_publisher,
)
from tests.unit.test_voice_realtime_sessions import wired  # noqa: F401


@pytest.fixture()
def bus() -> UiStatePublisher:
    publisher = UiStatePublisher(tail_size=8)
    set_publisher(publisher)
    yield publisher
    set_publisher(UiStatePublisher())


def test_the_vocabulary_is_the_one_the_owner_specified() -> None:
    assert set(UI_STATES) == {
        "agent.idle",
        "agent.listening",
        "agent.thinking",
        "agent.speaking",
        "agent.researching",
        "agent.memory_retrieval",
        "agent.tool_running",
        "agent.waiting_owner",
        "agent.goal_completed",
        "agent.error",
        "evolution.researching",
        "evolution.designing",
        "evolution.building",
        "evolution.testing",
        "evolution.shadow_ready",
        # M18: the room, and the owner-authorised release path
        "eye.active",
        "eye.disabled",
        "owner.present",
        "owner.away",
        "owner.returned",
        "owner.resting",
        "owner.likely_asleep",
        "owner.awake",
        "routine.armed",
        "routine.triggered",
        "alarm.triggered",
        "release.owner_approval_required",
        "release.owner_authorized",
        "release.qualifying",
        "release.deploying",
        "release.verifying",
        "release.live",
        "release.rollback",
        # M18.3 (spec §7): the wake alarm's own channel and the ambient display channel.
        # ``alarm.triggered`` above is unchanged — that is the routine engine's "this
        # routine's alarm action fired"; these are the physical wake sequence's own states.
        "alarm.armed",
        "alarm.firing",
        "alarm.playing",
        "alarm.greeting",
        "alarm.snoozed",
        "alarm.stopped",
        "alarm.completed",
        "alarm.failed",
        "display.on",
        "display.off",
        # M19 (Digital Operator spec §4, §7): the operator's own channel.
        "operator.running",
        "operator.verifying",
        "operator.failed",
        # M20 (File & Document Intelligence spec §3, §7): the documents channel.
        "document.analysis",
        # M21 (Mail & Calendar spec §3, §7): the mail/calendar channel.
        "mail.activity",
        "calendar.activity",
        "artifact.factory",
        # M23 (App Factory spec §2, §7, ADR-0086): the app-factory channel.
        "app.factory",
        # M24 (Capability Genesis spec §5, §8, ADR-0087): the genesis channel.
        "capability.genesis",
        # M25 (3D Creation spec §5, §6, ADR-0088): the scene channel.
        "scene.activity",
        # M26 (Executive Autonomy spec §6, §7, ADR-0089): the executive channel.
        "executive.run",
        # M27 (Creative Tools spec §3, §6, ADR-0093): the creative loop's channel.
        "creative.activity",
        # M28 (Native App Factory spec §4, §6, ADR-0095): the native build's channel.
        "native.build",
    }
    contract = ui_state_contract()
    assert contract["contract_version"] == 13
    assert "ambient" in contract["subsystems"]
    assert "operator" in contract["subsystems"]
    assert "documents" in contract["subsystems"]
    assert "mail" in contract["subsystems"]
    assert "calendar" in contract["subsystems"]
    assert "artifacts" in contract["subsystems"]
    assert "appfactory" in contract["subsystems"]
    assert "genesis" in contract["subsystems"]
    assert "nativefactory" in contract["subsystems"]
    assert "audio" in contract["metadata_rules"]["forbidden"]


#: The vocabulary exactly as v12 shipped, in order. Frozen here on purpose: see
#: `test_the_contract_only_ever_grows` below.
_V12_STATES: tuple[str, ...] = (
    "agent.idle",
    "agent.listening",
    "agent.thinking",
    "agent.speaking",
    "agent.researching",
    "agent.memory_retrieval",
    "agent.tool_running",
    "agent.waiting_owner",
    "agent.goal_completed",
    "agent.error",
    "evolution.researching",
    "evolution.designing",
    "evolution.building",
    "evolution.testing",
    "evolution.shadow_ready",
    "eye.active",
    "eye.disabled",
    "owner.present",
    "owner.away",
    "owner.returned",
    "owner.resting",
    "owner.likely_asleep",
    "owner.awake",
    "routine.armed",
    "routine.triggered",
    "alarm.triggered",
    "alarm.armed",
    "alarm.firing",
    "alarm.playing",
    "alarm.greeting",
    "alarm.snoozed",
    "alarm.stopped",
    "alarm.completed",
    "alarm.failed",
    "display.on",
    "display.off",
    "release.owner_approval_required",
    "release.owner_authorized",
    "release.qualifying",
    "release.deploying",
    "release.verifying",
    "release.live",
    "release.rollback",
    "operator.running",
    "operator.verifying",
    "operator.failed",
    "document.analysis",
    "mail.activity",
    "calendar.activity",
    "artifact.factory",
    "app.factory",
    "capability.genesis",
    "scene.activity",
    "executive.run",
    "creative.activity",
)

#: The subsystems exactly as v12 shipped, in order. Same reason.
_V12_SUBSYSTEMS: tuple[str, ...] = (
    "voice",
    "research",
    "browser",
    "memory",
    "experience",
    "goal",
    "cognitive",
    "self_model",
    "evolution",
    "deployment",
    "ledger",
    "system",
    "presence",
    "routine",
    "ambient",
    "operator",
    "documents",
    "mail",
    "calendar",
    "artifacts",
    "appfactory",
    "genesis",
    "creative3d",
    "executive",
    "creative",
)


def test_the_contract_only_ever_grows() -> None:
    """Every version of this contract has been described as "purely ADDITIVE", and every
    released renderer is built on that promise: `MIN_SUPPORTED_CONTRACT_VERSION` is 2 in
    `apps/web/app/lib/uistate/contract.ts`, so a v2 client reads a v13 server by reading
    the subset it knows and ignoring the rest.

    Nothing was holding the promise. A renamed token, a reordered tuple or a deleted state
    would pass every other test in this file — the vocabulary assertion above is rewritten
    with each milestone, which is exactly when a rename would slip through it — and would
    silently break every client older than the change. So the v12 shipped vocabulary is
    frozen here as an ordered PREFIX: new states go on the end, old ones never move and
    never change spelling. Deleting one is a contract BREAK and must bump
    `MIN_SUPPORTED_CONTRACT_VERSION` on the web side deliberately, not by accident here.
    """
    assert UI_STATES[: len(_V12_STATES)] == _V12_STATES, (
        "a state that shipped in v12 changed value, order or spelling - every renderer "
        "built against v2..v12 reads this contract as a superset of its own, and this is "
        "the assumption that makes that true"
    )
    assert SUBSYSTEMS[: len(_V12_SUBSYSTEMS)] == _V12_SUBSYSTEMS, (
        "a subsystem that shipped in v12 changed value or order - the publisher REFUSES "
        "an unknown subsystem, so a rename here silently stops a whole family publishing"
    )
    assert CONTRACT_VERSION >= 13
    assert len(UI_STATES) > len(_V12_STATES), "v13 adds a state; it is not here"


def test_that_freeze_would_notice_a_break() -> None:
    """And the guard proves itself before it is trusted: a renamed or reordered token in
    the frozen prefix must fail the comparison, rather than the comparison being one that
    can only ever pass (the vacuous-guard failure this repo has shipped twice)."""
    renamed = (*_V12_STATES[:-1], "creative.activity_v2")
    assert UI_STATES[: len(renamed)] != renamed
    reordered = (_V12_STATES[1], _V12_STATES[0], *_V12_STATES[2:])
    assert UI_STATES[: len(reordered)] != reordered


def test_an_event_carries_state_identity_and_bounded_numbers(bus: UiStatePublisher) -> None:
    event = publish(
        UiState.RESEARCHING,
        subsystem="research",
        intensity=0.7,
        progress=0.5,
        task_id="task-1",
        label="yapay zeka ajanları",
        metadata={"fetched": 12, "kept": 5},
    )
    assert event is not None
    payload = event.as_dict()
    assert payload["state"] == "agent.researching"
    assert payload["subsystem"] == "research"
    assert payload["intensity"] == 0.7 and payload["progress"] == 0.5
    assert payload["task_id"] == "task-1"
    assert payload["metadata"] == {"fetched": 12, "kept": 5}
    assert payload["at"].endswith("Z")
    assert payload["sequence"] == 1


def test_capability_genesis_publishes_scalar_metadata_only(bus: UiStatePublisher) -> None:
    """M24 (spec §5, §8): capability.genesis carries identity only — capability,
    state, approval_required, and error_class when failed — the same bounded-
    scalar rule every other channel's metadata follows."""
    event = publish(
        UiState.CAPABILITY_GENESIS,
        subsystem="genesis",
        label="counterbox.increment",
        metadata={
            "capability": "counterbox.increment",
            "state": "awaiting_approval",
            "approval_required": True,
        },
    )
    assert event is not None
    payload = event.as_dict()
    assert payload["state"] == "capability.genesis"
    assert payload["subsystem"] == "genesis"
    assert payload["metadata"] == {
        "capability": "counterbox.increment",
        "state": "awaiting_approval",
        "approval_required": True,
    }


def test_content_never_reaches_the_renderer(bus: UiStatePublisher) -> None:
    """A UI event describes state. Transcripts, audio, page text and secrets are refused
    at the boundary, not trusted to callers."""
    event = publish(
        UiState.SPEAKING,
        subsystem="voice",
        metadata={
            "transcript": "Efendim, son araştırma…",
            "audio_level": 0.4,
            "api_key": "sk-live-xxx",
            "page_content": "…",
            "excerpt": "…",
            "turn": 3,
            "nested": {"still": "content"},
        },
    )
    assert event is not None
    assert event.metadata == {"turn": 3}
    for key in ("transcript", "audio_level", "api_key", "page_content", "excerpt", "nested"):
        assert key not in event.metadata
    assert is_forbidden_metadata_key("assistantTranscript")
    assert is_forbidden_metadata_key("API_KEY")
    assert not is_forbidden_metadata_key("turn")


def test_refs_is_the_one_structured_metadata_value_the_bus_allows(bus: UiStatePublisher) -> None:
    """M20 functional gap fix: ``refs`` is defined end to end (model -> contract v5 ->
    the web's ``parseDocumentRefs``) but ``_clean_metadata`` used to drop every list/dict
    outright, so an answer's citations never reached the bus. Now exactly one structured
    key is allowed through, capped at 8 entries, fields whitelisted to ``{ref, path}``
    (each cut to the same 64-char token bound every other value gets) — ``excerpt`` never
    rides the bus even nested one level inside an allowed key, and every OTHER list/dict
    is still dropped exactly as before."""
    event = publish(
        UiState.DOCUMENT_ANALYSIS,
        subsystem="documents",
        label="sozlesme",
        metadata={
            "file": "sozlesme.docx",
            "part": "answer",
            "refs": [
                {"ref": f"p{n}", "path": f"/docs/p{n}.pdf", "excerpt": "gizli metin " * 50}
                for n in range(1, 10)  # 9 entries: the 9th must be dropped
            ]
            + [{"path": "/no/ref/here.pdf"}],  # no `ref` -> not a reference, dropped
            "structure": {"sheets": ["Ozet"]},  # a different list/dict key: still dropped
        },
    )
    assert event is not None
    metadata = event.metadata
    assert metadata["file"] == "sozlesme.docx"
    assert metadata["part"] == "answer"
    assert "structure" not in metadata
    refs = metadata["refs"]
    assert len(refs) == 8  # capped; the 9th (and the ref-less entry) dropped
    assert refs[0] == {"ref": "p1", "path": "/docs/p1.pdf"}
    assert all(set(r) <= {"ref", "path"} for r in refs)
    assert all("excerpt" not in r for r in refs)


def test_a_refs_path_longer_than_the_token_bound_is_cut(bus: UiStatePublisher) -> None:
    event = publish(
        UiState.DOCUMENT_ANALYSIS,
        subsystem="documents",
        metadata={"refs": [{"ref": "p1", "path": "x" * 65}]},
    )
    assert event is not None
    assert event.metadata["refs"][0]["path"] == "x" * 64


def test_refs_with_no_well_formed_entry_is_dropped_entirely(bus: UiStatePublisher) -> None:
    event = publish(
        UiState.DOCUMENT_ANALYSIS,
        subsystem="documents",
        metadata={"file": "x.pdf", "refs": [{"path": "/no/ref.pdf"}, {"ref": ""}, "not-a-dict"]},
    )
    assert event is not None
    assert "refs" not in event.metadata
    assert event.metadata == {"file": "x.pdf"}


def test_out_of_range_numbers_are_clamped_and_unknown_progress_stays_unknown(
    bus: UiStatePublisher,
) -> None:
    event = publish(UiState.THINKING, subsystem="cognitive", intensity=4.2)
    assert event is not None and event.as_dict()["intensity"] == 1.0
    # progress the publisher does not know must not become a fake bar
    assert event.as_dict()["progress"] is None


def test_an_unknown_subsystem_is_refused_rather_than_drawn(bus: UiStatePublisher) -> None:
    assert publish(UiState.IDLE, subsystem="marketing") is None
    assert bus.current() is None


def test_the_tail_is_bounded_and_replayable_for_a_late_client(bus: UiStatePublisher) -> None:
    for n in range(12):
        publish(UiState.TOOL_RUNNING, subsystem="goal", metadata={"n": n})
    assert len(bus.tail()) == 8  # the fixture's bound
    current = bus.current()
    assert current is not None and current.sequence == 12
    after = bus.tail(after_sequence=current.sequence - 3)
    assert [e.sequence for e in after] == [10, 11, 12]
    assert TAIL_SIZE >= 8


def test_a_broken_subscriber_never_breaks_the_work_it_describes(bus: UiStatePublisher) -> None:
    seen: list[str] = []
    bus.subscribe(lambda event: seen.append(event.state.value))

    def explode(_event: UiStateEvent) -> None:
        raise RuntimeError("the renderer fell over")

    bus.subscribe(explode)
    bus.subscribe(lambda event: seen.append("second:" + event.state.value))
    assert publish(UiState.GOAL_COMPLETED, subsystem="goal", goal_id="g1") is not None
    assert seen == ["agent.goal_completed", "second:agent.goal_completed"]


def test_publishing_is_never_able_to_raise_into_the_caller(bus: UiStatePublisher) -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:  # pragma: no cover - defensive
            raise RuntimeError("no")

        def __hash__(self) -> int:  # pragma: no cover
            raise RuntimeError("no")

    assert publish(UiState.IDLE, subsystem="system", metadata={"weird": Hostile()}) is not None


def test_severity_and_timestamps_are_normalised(bus: UiStatePublisher) -> None:
    event = publish(
        UiState.ERROR,
        subsystem="research",
        severity="catastrophic",  # not in the vocabulary
        status="failed",
    )
    assert event is not None and event.severity == "info"
    naive = UiStateEvent(state=UiState.IDLE, subsystem="system", at=datetime.now(UTC))
    assert naive.as_dict()["at"].endswith("Z")


# ------------------------------------------------- the subsystems actually publish


def test_voice_client_events_publish_truthful_states(wired, monkeypatch) -> None:
    """Wiring test: the states a renderer would draw come from what the client really
    reported, not from a timer. Imported here (rather than in the voice suite) because
    it is the CONTRACT that must hold, whatever the voice internals become."""
    from tests.unit.test_voice_explain_tools import _create, _events

    publisher = UiStatePublisher(tail_size=32)
    set_publisher(publisher)
    try:
        client = wired[0]
        sid = _create(client)["session_id"]
        _events(
            client,
            sid,
            [
                {"kind": "mic_speech_start", "t_ms": 100, "turn": 1, "payload": {}},
                {"kind": "end_of_turn", "t_ms": 900, "turn": 1, "payload": {}},
                {"kind": "first_audio", "t_ms": 1400, "turn": 1, "payload": {}},
                {"kind": "response_done", "t_ms": 5000, "turn": 1, "payload": {}},
            ],
        )
        states = [e.state.value for e in publisher.tail()]
        assert states == [
            "agent.listening",
            "agent.thinking",
            "agent.speaking",
            "agent.listening",
        ]
        assert all(e.subsystem == "voice" and e.session_id == sid for e in publisher.tail())
        assert all(e.metadata.get("turn") == 1 for e in publisher.tail())
        # bounded energy, never a sample
        assert all(0.0 <= (e.intensity or 0.0) <= 1.0 for e in publisher.tail())
    finally:
        set_publisher(UiStatePublisher())


def test_the_ui_read_surface_is_owner_gated_and_replayable(wired) -> None:
    client = wired[0]
    publisher = UiStatePublisher(tail_size=32)
    set_publisher(publisher)
    try:
        publish(UiState.RESEARCHING, subsystem="research", task_id="t9", progress=0.4)
        body = client.get("/v1/ui/state").json()
        assert body["current"]["state"] == "agent.researching"
        assert body["current"]["progress"] == 0.4
        assert body["sequence"] == 1
        assert client.get("/v1/ui/state/contract").json()["contract_version"] == CONTRACT_VERSION
        publish(UiState.IDLE, subsystem="system")
        after = client.get("/v1/ui/state", params={"after_sequence": 1}).json()
        assert [e["state"] for e in after["events"]] == ["agent.idle"]
    finally:
        set_publisher(UiStatePublisher())

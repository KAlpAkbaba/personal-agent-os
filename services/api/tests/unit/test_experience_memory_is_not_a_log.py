"""Memory is what the owner would want recalled; a heartbeat is not (ADR-0190).

Measured in production on 2026-09-20, after sixteen days of use: 2316 episodic memories,
of which

    "Sesli oturum oluşturuldu."                                 216
    "owner presence changed to present/away/unknown"            387
    "Sahip klavye/fare kullandı; ekran otomasyonu beklemede."   172
    "Sesli oturum kapandı."                                     136

...and thirteen durable rows, every one of them "Araştırma tamamlandı: <konu>". Nothing
about the owner. The Experience Engine copies EVERY ledger event into memory except four
named types, so the store filled with machine telemetry - which is also what retrieval
returns when the assistant looks for something it knows about its owner.

The ledger keeps all of it either way: this is about which events are worth REMEMBERING.
"""

from __future__ import annotations

import pytest

from app.experience.engine import (
    EXCLUDED_EVENT_TYPES,
    MACHINE_RECORD_EVENT_TYPES,
    TELEMETRY_EVENT_TYPES,
)
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
    EVENT_TYPE_EVOLUTION_IDEA_CREATED,
    EVENT_TYPE_EYE_DISABLED,
    EVENT_TYPE_EYE_ENABLED,
    EVENT_TYPE_MAIL_SENT,
    EVENT_TYPE_OPERATOR_MISSION_ESCALATED,
    EVENT_TYPE_OPERATOR_MISSION_FINISHED,
    EVENT_TYPE_OPERATOR_MISSION_STARTED,
    EVENT_TYPE_OPERATOR_TASK_COMPLETED,
    EVENT_TYPE_OPERATOR_TASK_FAILED,
    EVENT_TYPE_OPERATOR_TASK_STARTED,
    EVENT_TYPE_OWNER_INPUT_ACTIVE,
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_QUALITY_GATE,
    EVENT_TYPE_VOICE_EXPLAINED,
    EVENT_TYPE_VOICE_SESSION_ATTACHED,
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    EVENT_TYPE_WEATHER_QUERIED,
)


@pytest.mark.parametrize(
    "event_type",
    [
        # The four that filled the store, by production volume:
        EVENT_TYPE_PRESENCE_STATE_CHANGED,
        EVENT_TYPE_VOICE_SESSION_CREATED,
        EVENT_TYPE_VOICE_SESSION_CLOSED,
        EVENT_TYPE_VOICE_SESSION_ATTACHED,
        EVENT_TYPE_OWNER_INPUT_ACTIVE,
    ],
)
def test_machine_telemetry_is_not_written_to_memory(event_type: str) -> None:
    assert event_type in TELEMETRY_EVENT_TYPES
    assert event_type in EXCLUDED_EVENT_TYPES


@pytest.mark.parametrize(
    "event_type",
    [
        # What a person would actually want recalled - none of these may be swept up:
        EVENT_TYPE_RESEARCH_COMPLETED,
        EVENT_TYPE_MAIL_SENT,
        EVENT_TYPE_OPERATOR_MISSION_FINISHED,
        EVENT_TYPE_OPERATOR_MISSION_ESCALATED,
    ],
)
def test_what_the_owner_did_is_still_remembered(event_type: str) -> None:
    assert event_type not in EXCLUDED_EVENT_TYPES


@pytest.mark.parametrize(
    "event_type",
    [
        # ADR-0193, measured in production after the heartbeat was gone: 432 of the 1381
        # remaining rows read "operator.key -> succeeded: executed, verified". A receipt is
        # a machine record in a machine's vocabulary - and ADR-0192 had just let
        # "benim hakkımda ne biliyorsun" answer from this store in every mode.
        EVENT_TYPE_ACTION_RECEIPT,
        EVENT_TYPE_OPERATOR_TASK_STARTED,
        EVENT_TYPE_OPERATOR_TASK_COMPLETED,
        EVENT_TYPE_OPERATOR_TASK_FAILED,
        EVENT_TYPE_OPERATOR_MISSION_STARTED,
        EVENT_TYPE_RESEARCH_QUALITY_GATE,
        EVENT_TYPE_VOICE_EXPLAINED,
        EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
        EVENT_TYPE_EVOLUTION_IDEA_CREATED,
        EVENT_TYPE_WEATHER_QUERIED,
        EVENT_TYPE_EYE_ENABLED,
        EVENT_TYPE_EYE_DISABLED,
    ],
)
def test_a_machine_record_is_not_a_memory(event_type: str) -> None:
    assert event_type in MACHINE_RECORD_EVENT_TYPES
    assert event_type in EXCLUDED_EVENT_TYPES


def test_the_exclusion_list_is_the_old_one_plus_the_telemetry() -> None:
    """The four that were already excluded stay excluded: this ADR only adds."""
    from app.ledger.vocabulary import (
        EVENT_TYPE_BRIEFING_DELIVERED,
        EVENT_TYPE_BRIEFING_QUEUED,
        EVENT_TYPE_EXPERIENCE_INGESTED,
        EVENT_TYPE_LEDGER_BACKFILL,
    )

    for event_type in (
        EVENT_TYPE_LEDGER_BACKFILL,
        EVENT_TYPE_BRIEFING_QUEUED,
        EVENT_TYPE_BRIEFING_DELIVERED,
        EVENT_TYPE_EXPERIENCE_INGESTED,
    ):
        assert event_type in EXCLUDED_EVENT_TYPES

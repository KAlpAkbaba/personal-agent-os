"""The memory subsystem's side tables on the REAL database (ADR-0214 addendum 4).

``memory_versions``, ``memory_evidence``, ``memory_audit_events``, ``entities`` and
``entity_edges`` were five of the 51 tables no test under ``tests/integration`` named on
2026-10-01: every write to them had only ever met SQLite, which does not enforce a VARCHAR's
length, has no JSONB, hands back naive datetimes and ignores a foreign key. These tests take
the same writes to the dev stack's PostgreSQL, through the production functions that make
them (``app.memory.service``, ``app.memory.lifecycle``, ``app.memory.graph`` and the REST
surface in front of them) - never through hand-written SQL - with the values SQLite
forgives:

- the longest string each column's own validation allows, and one more where the code
  claims to refuse it (the refusal must come from the code, as a 422 or a typed error, and
  not from the database as a 500);
- a JSONB document with nested Turkish text;
- a timezone-aware timestamp that is not UTC;
- a NULL in every nullable column.

Rows are namespaced with a per-test token; every memory is forgotten and every entity
deleted when its test ends. Audit rows stay: the table is append-only by design.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DataError

from app.config import Settings
from app.memory import graph, lifecycle, service
from app.memory.embedding import DeterministicEmbedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.models import (
    Entity,
    EntityEdge,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.policy import Observation
from app.memory.runtime import MemoryRuntime
from app.memory.service import MemoryLinks
from app.memory.types import ENTITY_KINDS, Actor, MemoryClass
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

EMBEDDER = DeterministicEmbedder()
ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Nested, Turkish, with the value types JSONB stores differently from a JSON string:
#: a null, a boolean, a float and a list inside an object inside a list.
NESTED = {
    "başlık": "Çay — şekersiz, ılık",
    "ayrıntı": {
        "şehir": "İstanbul",
        "ölçü": [1, 2.5, None, True],
        "notlar": ["ğ", {"iç içe": "öğle üstü, Iğdır'dan"}],
    },
    "boş": None,
}


def _token() -> str:
    return uuid.uuid4().hex[:10]


def _exactly(length: int, prefix: str) -> str:
    """Exactly ``length`` CHARACTERS, most of them Turkish.

    PostgreSQL counts a VARCHAR in characters and UTF-8 spends two bytes on each of these
    letters, so an ASCII-only string of the same length would not notice a column sized in
    bytes - or a validation that counted them.
    """
    filler = "ığüşöçİĞÜŞÖÇ"
    text = (prefix + filler * (length // len(filler) + 1))[:length]
    assert len(text) == length
    return text


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def runtime(settings: Settings) -> Iterator[MemoryRuntime]:
    memory = MemoryRuntime(settings, embedder=EMBEDDER)
    # The whole point of this file. A run that reached SQLite would prove nothing here.
    assert memory.engine.dialect.name == "postgresql"
    try:
        yield memory
    finally:
        memory.engine.dispose()


@pytest.fixture()
def made(runtime: MemoryRuntime) -> Iterator[SimpleNamespace]:
    """What a test created, so it is gone afterwards whether the test passed or not."""
    created = SimpleNamespace(memories=[], entities=[])
    try:
        yield created
    finally:
        with runtime.session() as session:
            for memory_id in created.memories:
                try:
                    service.forget_memory(session, memory_id, actor=Actor.OWNER)
                except MemorySubsystemError:
                    session.rollback()  # already forgotten by the test itself
            if created.entities:
                # No production path deletes an entity. The edges go with it by the
                # table's own ON DELETE CASCADE, which only PostgreSQL enforces.
                session.execute(delete(Entity).where(Entity.id.in_(created.entities)))
                session.commit()


# --------------------------------------------------------------- memory_versions


def test_memory_versions_keep_every_edit_at_the_longest_reason_the_surface_allows(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``service.remember_explicit`` writes version 1, ``service.edit_memory`` each one after.

    ``change_reason`` is VARCHAR(512) and ``PATCH /v1/memory/{id}`` allows 512: exactly that
    many characters must fit, in Turkish.
    """
    token = _token()
    #: 03:30 on the night Europe moves its clocks; Istanbul does not. Aware, and not UTC.
    occurred = datetime(2026, 3, 29, 3, 30, tzinfo=ISTANBUL)
    reason = _exactly(512, f"{token} düzeltme: ")
    with runtime.session() as session:
        taught = service.remember_explicit(
            session,
            EMBEDDER,
            text=f"Kahveyi {token} fincanında sade içerim, şekersiz.",
            memory_class=MemoryClass.PREFERENCE,
            key=f"pgcov.{token}",
            value=NESTED,
            links=MemoryLinks(occurred_at=occurred, valid_from=occurred),
        )
        assert taught.action == "created", taught
        made.memories.append(taught.memory_id)
        service.edit_memory(
            session,
            EMBEDDER,
            taught.memory_id,
            actor=Actor.OWNER,
            text=f"Kahveyi {token} fincanında az şekerli içerim.",
            value={"içecek": {"tür": "kahve", "şeker": "az"}},
            change_reason=reason,
        )
        # The default reason is the empty string, in a NOT NULL column.
        service.edit_memory(session, EMBEDDER, taught.memory_id, actor=Actor.OWNER, value={})

    with runtime.session() as fresh:
        rows = (
            fresh.execute(
                select(MemoryVersion)
                .where(MemoryVersion.memory_id == taught.memory_id)
                .order_by(MemoryVersion.version)
            )
            .scalars()
            .all()
        )
        assert [row.version for row in rows] == [1, 2, 3]
        first, second, third = rows
        assert first.change_reason == "created"
        assert first.value_json == NESTED
        assert first.text == f"Kahveyi {token} fincanında sade içerim, şekersiz."
        assert (first.stage, first.edited_by, first.explicit, first.confidence) == (
            "durable",
            "owner",
            True,
            1.0,
        )
        assert second.change_reason == reason and len(second.change_reason) == 512
        assert second.value_json == {"içecek": {"tür": "kahve", "şeker": "az"}}
        assert third.change_reason == "" and third.value_json == {}
        assert all(row.created_at.utcoffset() is not None for row in rows)

        memory = service.get_memory(fresh, taught.memory_id)
        assert memory.occurred_at == occurred and memory.occurred_at.utcoffset() is not None
        assert memory.valid_until is None and memory.project_id is None

        # Forgetting takes the history with it: the row, and every version of it.
        counts = service.forget_memory(fresh, taught.memory_id, actor=Actor.OWNER)
        assert counts["versions"] == 3
        left = fresh.scalar(
            select(func.count())
            .select_from(MemoryVersion)
            .where(MemoryVersion.memory_id == taught.memory_id)
        )
        assert left == 0


def test_a_reason_one_character_too_long_is_refused_by_the_surface_not_by_postgres(
    settings: Settings, runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    token = _token()
    with owner_client(settings) as client:
        created = client.post(
            "/v1/memory/remember",
            json={"text": f"Çayı {token} bardağında demli içerim.", "key": f"pgcov.{token}"},
        )
        assert created.status_code == 201, created.text
        memory_id = uuid.UUID(created.json()["memory_id"])
        made.memories.append(memory_id)

        refused = client.patch(
            f"/v1/memory/{memory_id}",
            json={"text": "Çayı açık içerim.", "change_reason": _exactly(513, token)},
        )
        assert refused.status_code == 422, refused.text
        accepted = client.patch(
            f"/v1/memory/{memory_id}",
            json={"text": "Çayı açık içerim.", "change_reason": _exactly(512, token)},
        )
        assert accepted.status_code == 200, accepted.text

    with runtime.session() as fresh:
        versions = (
            fresh.execute(
                select(MemoryVersion.version)
                .where(MemoryVersion.memory_id == memory_id)
                .order_by(MemoryVersion.version)
            )
            .scalars()
            .all()
        )
        assert versions == [1, 2], "the refused edit must not have left a version behind"


# --------------------------------------------------------------- memory_evidence


def test_memory_evidence_records_each_observation_with_its_source_document(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``service.record_observation`` -> ``lifecycle.add_evidence``: one row per observation,
    its provenance in JSONB, and the third corroboration is what promotes the memory."""
    token = _token()
    source = {
        "kaynak": "sesli konuşma",
        "konuşma": {"başlık": "Sabah brifingi — İstanbul", "sıra": [3, None, 4.5]},
        "cihaz": None,
    }

    def observe(session, source_ref):
        return service.record_observation(
            session,
            EMBEDDER,
            Observation(
                text=f"Sahip {token} raporlarını her zaman sabah okur.",
                memory_class=MemoryClass.SEMANTIC,
                key=f"pgcov.{token}",
                source=source_ref,
            ),
        )

    with runtime.session() as session:
        first = observe(session, source)
        assert (first.action, first.stage) == ("created", "candidate"), first
        made.memories.append(first.memory_id)
        second = observe(session, {})
        third = observe(session, source)
        assert (second.action, second.promoted) == ("corroborated", False)
        assert (third.action, third.promoted, third.stage) == ("corroborated", True, "durable")

    with runtime.session() as fresh:
        rows = (
            fresh.execute(
                select(MemoryEvidence)
                .where(MemoryEvidence.memory_id == first.memory_id)
                .order_by(MemoryEvidence.observed_at)
            )
            .scalars()
            .all()
        )
        assert [row.kind for row in rows] == ["observation", "corroboration", "corroboration"]
        assert [row.source_ref_json for row in rows] == [source, {}, source]
        assert all(row.weight == 1.0 for row in rows)
        assert all(row.observed_at.utcoffset() is not None for row in rows)

        inspected = service.inspect_memory(fresh, first.memory_id)
        assert inspected["evidence_count"] == 3
        assert [e["source_ref"] for e in inspected["evidence"]] == [source, {}, source]


# ----------------------------------------------------------- memory_audit_events


def test_memory_audit_events_take_the_longest_key_and_a_turkish_detail(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``lifecycle.record_audit`` is the one writer; these are its callers in
    ``app.memory.service``. ``key`` is VARCHAR(256) and the REST surface allows 256."""
    token = _token()
    key = _exactly(256, f"pgcov.{token}.")
    with runtime.session() as session:
        taught = service.remember_explicit(
            session,
            EMBEDDER,
            text=f"Toplantı {token} notlarını her zaman Türkçe tutarım.",
            memory_class=MemoryClass.PREFERENCE,
            key=key,
            value={"dil": "Türkçe"},
        )
        made.memories.append(taught.memory_id)
        # An inference that contradicts an explicit memory is recorded, never applied; the
        # value it proposed travels in the audit row's JSONB.
        kept = service.record_observation(
            session,
            EMBEDDER,
            Observation(
                text=f"Toplantı {token} notları İngilizce tutuluyor gibi görünüyor.",
                memory_class=MemoryClass.PREFERENCE,
                key=key,
                value=NESTED,
            ),
        )
        assert kept.action == "contradicted_explicit_kept", kept

    with runtime.session() as fresh:
        rows = (
            fresh.execute(
                select(MemoryAuditEvent)
                .where(MemoryAuditEvent.memory_id == taught.memory_id)
                .order_by(MemoryAuditEvent.id)
            )
            .scalars()
            .all()
        )
        assert [row.action for row in rows] == ["created", "contradicted"]
        created, contradicted = rows
        assert created.key == key and len(created.key) == 256
        assert (created.memory_class, created.actor) == ("preference", "owner")
        assert created.detail_json == {"stage": "durable", "explicit": True}
        assert contradicted.actor == "policy"
        assert contradicted.detail_json["proposed_value"] == NESTED
        assert contradicted.detail_json["proposed_text"].endswith("gibi görünüyor.")
        # No request, so no trace: the column is NULL rather than an empty string.
        assert created.trace_id is None and contradicted.trace_id is None
        assert all(row.created_at.utcoffset() is not None for row in rows)


def test_memory_audit_events_accept_a_null_in_every_nullable_column(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """Two production writers leave columns NULL: a refused secret has no memory and no key,
    and a re-index is about no memory at all (``memory_id``, ``memory_class``, ``key`` and
    ``trace_id`` all NULL in one row)."""
    token = _token()

    class OtherModel(DeterministicEmbedder):
        model_id = f"pgcov-{token}"
        model_version = "pgcov"

    with runtime.session() as session:
        before = session.scalar(select(func.max(MemoryAuditEvent.id))) or 0
        with pytest.raises(MemorySubsystemError) as refused:
            service.record_observation(
                session,
                EMBEDDER,
                Observation(text=f"Kasanın {token} bilgisi şöyle: password: hunter2-gizli"),
            )
        assert refused.value.error_class == MemoryErrorClass.SECRET_REJECTED

        taught = service.remember_explicit(
            session,
            EMBEDDER,
            text=f"Bahçeyi {token} günlerinde akşamüstü sularım.",
            memory_class=MemoryClass.SEMANTIC,
            key=f"pgcov.{token}",
        )
        made.memories.append(taught.memory_id)
        try:
            # One memory, the oldest without a row for this model: enough to make the pass
            # write its audit event, and nothing a later test can trip over.
            assert lifecycle.reindex_missing(session, OtherModel(), limit=1) == 1
        finally:
            session.rollback()
            session.execute(
                delete(MemoryEmbedding).where(MemoryEmbedding.model_id == OtherModel.model_id)
            )
            session.commit()

    with runtime.session() as fresh:
        rows = (
            fresh.execute(
                select(MemoryAuditEvent)
                .where(
                    MemoryAuditEvent.id > before,
                    MemoryAuditEvent.action.in_(("refused_secret", "reindexed")),
                )
                .order_by(MemoryAuditEvent.id)
            )
            .scalars()
            .all()
        )
        refusals = [r for r in rows if r.detail_json == {"pattern": "password_assignment"}]
        assert len(refusals) == 1, rows
        assert (refusals[0].memory_id, refusals[0].key, refusals[0].trace_id) == (None,) * 3
        assert (refusals[0].memory_class, refusals[0].actor) == ("semantic", "policy")

        passes = [r for r in rows if r.detail_json.get("model_id") == OtherModel.model_id]
        assert len(passes) == 1, rows
        reindexed = passes[0]
        assert reindexed.detail_json == {
            "model_id": OtherModel.model_id,
            "model_version": "pgcov",
            "dim": EMBEDDER.dim,
            "rows": 1,
            "only_missing": True,
        }
        assert (
            reindexed.memory_id,
            reindexed.memory_class,
            reindexed.key,
            reindexed.trace_id,
        ) == (None,) * 4
        assert reindexed.actor == "system"


def test_the_surface_holds_the_key_and_the_trace_id_to_what_the_columns_take(
    settings: Settings, runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """Through the real application object: ``trace_id`` is VARCHAR(128) and comes from a
    header the client chooses; the middleware cuts it to 128. ``key`` is refused at 257."""
    token = _token()
    key = _exactly(256, f"pgcov.{token}.")
    trace = (f"pgcov-{token}." + "trace-id." * 30)[:200]
    with owner_client(settings) as client:
        too_long = client.post(
            "/v1/memory/remember",
            json={"text": f"Balkonu {token} sabahları havalandırırım.", "key": key + "x"},
        )
        assert too_long.status_code == 422, too_long.text

        created = client.post(
            "/v1/memory/remember",
            json={"text": f"Balkonu {token} sabahları havalandırırım.", "key": key},
            headers={"X-Trace-Id": trace},
        )
        assert created.status_code == 201, created.text
        memory_id = uuid.UUID(created.json()["memory_id"])
        made.memories.append(memory_id)
        assert created.headers["X-Trace-Id"] == trace[:128]

    with runtime.session() as fresh:
        rows = (
            fresh.execute(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == memory_id))
            .scalars()
            .all()
        )
        assert [row.action for row in rows] == ["created"]
        assert rows[0].trace_id == trace[:128] and len(rows[0].trace_id) == 128
        assert rows[0].key == key
        refused_rows = fresh.scalar(
            select(func.count())
            .select_from(MemoryAuditEvent)
            .where(MemoryAuditEvent.key == key + "x")
        )
        assert refused_rows == 0


# ---------------------------------------------------------------------- entities


def test_entities_take_the_longest_name_and_a_nested_turkish_document(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``service.create_entity``: ``name`` is VARCHAR(512) and the REST surface allows 512;
    ``kind`` is VARCHAR(32) and the service itself closes the vocabulary."""
    token = _token()
    name = _exactly(512, f"pgcov-{token} ")
    kind = max(ENTITY_KINDS, key=len)
    with runtime.session() as session:
        entity = service.create_entity(session, kind=kind, name=name, attrs=NESTED)
        made.entities.append(entity.id)
        bare = service.create_entity(session, kind="person", name=f"pgcov-{token} Şükrü Öğüt")
        made.entities.append(bare.id)
        # Idempotent on (kind, name) - the unique constraint is the database's, and the
        # second call must find the row rather than trip over it. New attributes merge.
        again = service.create_entity(
            session, kind=kind, name=name, attrs={"ek": {"şehir": "Iğdır"}}
        )
        assert again.id == entity.id

        with pytest.raises(MemorySubsystemError) as unknown:
            service.create_entity(session, kind="k" * 33, name=f"pgcov-{token}")
        assert unknown.value.error_class == MemoryErrorClass.VALIDATION_ERROR

    with runtime.session() as fresh:
        stored = fresh.get(Entity, entity.id)
        assert stored.name == name and len(stored.name) == 512
        assert stored.kind == kind
        assert stored.attrs_json == {**NESTED, "ek": {"şehir": "Iğdır"}}
        assert stored.created_at.utcoffset() is not None
        # Aware, both: one is the database's now(), the other the service's own clock.
        assert stored.updated_at.utcoffset() is not None
        assert fresh.get(Entity, bare.id).attrs_json == {}
        same_name = fresh.scalar(
            select(func.count()).select_from(Entity).where(Entity.name == name)
        )
        assert same_name == 1


def test_an_entity_name_one_character_too_long_is_refused_by_the_surface(
    settings: Settings, runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    token = _token()
    name = _exactly(512, f"pgcov-{token} ")
    with owner_client(settings) as client:
        refused = client.post("/v1/memory/entities", json={"kind": "project", "name": name + "x"})
        assert refused.status_code == 422, refused.text
        unknown_kind = client.post(
            "/v1/memory/entities", json={"kind": "k" * 32, "name": f"pgcov-{token}"}
        )
        assert unknown_kind.status_code == 422, unknown_kind.text
        accepted = client.post(
            "/v1/memory/entities", json={"kind": "project", "name": name, "attrs": NESTED}
        )
        assert accepted.status_code == 201, accepted.text
        made.entities.append(uuid.UUID(accepted.json()["entity_id"]))
        assert accepted.json()["attrs"] == NESTED

    with runtime.session() as fresh:
        like = fresh.scalar(
            select(func.count()).select_from(Entity).where(Entity.name.like(f"pgcov-{token}%"))
        )
        assert like == 1, "only the accepted entity was written"


def test_the_graph_producer_writes_its_nodes_and_edges_once(
    runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``graph.sync_from_events`` is the producer that runs in production (behind the
    Experience Engine's clock): nodes and edges from what a ledger event names, and a second
    pass over the same events adds nothing. It reads four attributes of an event and nothing
    else, so the events here are plain objects carrying those four."""
    token = _token()
    job = uuid.uuid4()
    subsystem = f"pgcov-{token}"
    action = f"pgcov.{token}.çalıştırıldı"
    events = [
        SimpleNamespace(
            subsystem=subsystem, action=action, research_job_id=job, related_goal_id=None
        ),
        SimpleNamespace(
            subsystem=subsystem, action=action, research_job_id=None, related_goal_id=None
        ),
    ]
    with runtime.session() as session:
        report = graph.sync_from_events(session, events)
        assert report.errors == 0, report.as_dict()
        names = (subsystem, action, str(job))
        nodes = session.execute(select(Entity).where(Entity.name.in_(names))).scalars().all()
        made.entities.extend(node.id for node in nodes)
        assert {(n.kind, n.name) for n in nodes} == {
            ("system", subsystem),
            ("capability", action),
            ("task", str(job)),
        }
        again = graph.sync_from_events(session, events)
        assert again.errors == 0, again.as_dict()

    with runtime.session() as fresh:
        system = fresh.execute(
            select(Entity).where(Entity.kind == "system", Entity.name == subsystem)
        ).scalar_one()
        edges = service.entity_edges(fresh, system.id)
        assert sorted(edge.relation for edge in edges) == ["performs", "worked_on"]
        assert all(edge.src_id == system.id and edge.attrs_json == {} for edge in edges)
        count = fresh.scalar(select(func.count()).select_from(Entity).where(Entity.name.in_(names)))
        assert count == 3, "the second pass must not have added a node"


# ------------------------------------------------------------------ entity_edges


def test_entity_edges_take_the_longest_relation_and_go_with_their_entity(
    settings: Settings, runtime: MemoryRuntime, made: SimpleNamespace
) -> None:
    """``service.create_edge``: ``relation`` is VARCHAR(64) and the REST surface allows 64.
    The two foreign keys are real here - SQLite never checked them."""
    token = _token()
    relation = _exactly(64, f"pgcov_{token}_")
    with runtime.session() as session:
        src = service.create_entity(session, kind="project", name=f"pgcov-{token} kaynak")
        dst = service.create_entity(session, kind="document", name=f"pgcov-{token} hedef")
        made.entities.extend([src.id, dst.id])
        edge = service.create_edge(
            session, src_id=src.id, dst_id=dst.id, relation=relation, attrs=NESTED
        )
        bare = service.create_edge(session, src_id=dst.id, dst_id=src.id, relation="ilgili")
        again = service.create_edge(session, src_id=src.id, dst_id=dst.id, relation=relation)
        assert again.id == edge.id, "idempotent on (src, dst, relation)"

        # An edge to an entity that does not exist is refused by the code, as a typed error,
        # before the foreign key has to refuse it.
        with pytest.raises(MemorySubsystemError) as missing:
            service.create_edge(session, src_id=src.id, dst_id=uuid.uuid4(), relation="ilgili")
        assert missing.value.error_class == MemoryErrorClass.NOT_FOUND
        src_id, dst_id, edge_id, bare_id = src.id, dst.id, edge.id, bare.id

    with owner_client(settings) as client:
        refused = client.post(
            "/v1/memory/edges",
            json={"src_id": str(src_id), "dst_id": str(dst_id), "relation": relation + "x"},
        )
        assert refused.status_code == 422, refused.text
        read = client.get(f"/v1/memory/entities/{src_id}")
        assert read.status_code == 200, read.text
        assert {e["relation"]: e["attrs"] for e in read.json()["edges"]} == {
            relation: NESTED,
            "ilgili": {},
        }

    with runtime.session() as fresh:
        stored = fresh.get(EntityEdge, edge_id)
        assert stored.relation == relation and len(stored.relation) == 64
        assert stored.attrs_json == NESTED
        assert stored.created_at.utcoffset() is not None
        assert fresh.get(EntityEdge, bare_id).attrs_json == {}

        # ON DELETE CASCADE, on both ends: the edges cannot outlive either entity.
        fresh.execute(delete(Entity).where(Entity.id == dst_id))
        fresh.commit()
        left = fresh.scalar(
            select(func.count())
            .select_from(EntityEdge)
            .where(EntityEdge.id.in_((edge_id, bare_id)))
        )
        assert left == 0


# --------------------------------------------------- what PostgreSQL refuses alone


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): POST /v1/memory/entities answers 500 for a U+0000 in "
        "the name - psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes - "
        "and for one inside attrs - psycopg.errors.UntranslatableCharacter: unsupported "
        "Unicode escape sequence (U+0000 cannot be converted to text)"
    ),
)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": "pgcov sıfır\x00bayt"}, id="name"),
        pytest.param({"name": "pgcov sıfır bayt", "attrs": {"not": "sıfır\x00bayt"}}, id="attrs"),
    ],
)
def test_a_nul_character_is_refused_by_the_surface_not_by_postgres(
    settings: Settings, runtime: MemoryRuntime, made: SimpleNamespace, body: dict
) -> None:
    """PostgreSQL stores no U+0000, in a VARCHAR or inside JSONB; SQLite stores both. JSON
    allows the character, so the request validates and the database is what says no.
    Refusing it and storing the text without it are both answers; a 500 is not."""
    token = _token()
    payload = {"kind": "document", **body, "name": f"{body['name']} {token}"}
    with owner_client(settings) as client:
        answer = client.post("/v1/memory/entities", json=payload)
        if answer.status_code == 201:
            made.entities.append(uuid.UUID(answer.json()["entity_id"]))
            stored = answer.json()
            assert "\x00" not in stored["name"]
            assert "\x00" not in str(stored["attrs"].get("not", ""))
        else:
            assert answer.status_code == 422, answer.text

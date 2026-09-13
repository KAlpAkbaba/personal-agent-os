"""B18 req 47/48: the entity graph, given its first producer.

`Entity`, `EntityEdge`, eight entity kinds, `create_entity` / `create_edge` /
`entity_edges` and a REST surface have existed since M5. Nothing in `app/` has ever called
one of them: the feature matrix's note was exact — *üretimde boş*. A graph nobody writes to
is a schema, not a memory.

**What it is built from, and what it is not.** Only the Activity Ledger, and only fields
the ledger already carries as identity: the `subsystem` that acted, the `action` it
performed, and the research job or goal it was working on. Nothing here parses a sentence,
guesses a person's name, or infers a relationship — this repository's memory subsystem
refuses to store what it cannot evidence, and a graph is no different. `person` entities
therefore have no producer here, and saying so is more useful than a graph of guesses:
there is no field in the ledger that names a person.

**Why this satisfies "device-independent logical entity identity" (req 48).** It is
satisfied by the identity SCHEME rather than by anything built here: `Entity` is unique on
``(kind, name)`` and carries no device column, so the same logical thing observed through
two different devices resolves to one node. That was true when the table was written and
untestable while nothing wrote to it; with a producer, `test_one_entity_however_many_
devices_saw_it` can say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ledger.models import ActivityEventRow
from app.logging import get_logger
from app.memory import service as memory_service

logger = get_logger("app.memory.graph")

#: The relation a subsystem has to a capability it performed.
RELATION_PERFORMS = "performs"
#: The relation a subsystem has to a task it worked on.
RELATION_WORKED_ON = "worked_on"

#: Entity kinds this producer writes. `person`, `document`, `decision` and `project` are
#: deliberately absent: the ledger carries no field that names one, and a node invented
#: from a sentence is exactly what `app.memory`'s write policy exists to refuse.
KIND_SYSTEM = "system"
KIND_CAPABILITY = "capability"
KIND_TASK = "task"


@dataclass(slots=True)
class GraphReport:
    entities: int = 0
    edges: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"entities": self.entities, "edges": self.edges, "errors": self.errors}


def _entity(session: Session, report: GraphReport, *, kind: str, name: str, **attrs: Any):
    """Upsert one node. `create_entity` is already idempotent on ``(kind, name)``; this
    only adds the counting and the "one bad node never sinks the pass" rule."""
    try:
        entity = memory_service.create_entity(session, kind=kind, name=name, attrs=attrs or None)
    except Exception as exc:  # noqa: BLE001 - one node, not the pass
        report.errors += 1
        logger.warning("entity_write_failed", kind=kind, error=f"{type(exc).__name__}: {exc}")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001 - nothing further to do about it
            pass
        return None
    report.entities += 1
    return entity


def _edge(session: Session, report: GraphReport, src: Any, dst: Any, relation: str) -> None:
    if src is None or dst is None:
        return
    try:
        memory_service.create_edge(
            session, src_id=src.id, dst_id=dst.id, relation=relation
        )
    except Exception as exc:  # noqa: BLE001 - one edge, not the pass
        report.errors += 1
        logger.warning("entity_edge_failed", relation=relation, error=f"{type(exc).__name__}")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001 - nothing further to do about it
            pass
        return
    report.edges += 1


def sync_from_events(session: Session, rows: list[ActivityEventRow]) -> GraphReport:
    """Assert the nodes and edges these ledger events already name.

    Idempotent by construction: `create_entity` upserts on ``(kind, name)`` and
    `create_edge` on ``(src, dst, relation)``, so a pass over an overlapping window adds
    nothing. Never raises — this runs behind the Experience Engine on the clock that also
    fires alarms.
    """
    report = GraphReport()
    systems: dict[str, Any] = {}

    for row in rows:
        subsystem = str(row.subsystem or "").strip()
        if not subsystem:
            continue
        if subsystem not in systems:
            node = _entity(session, report, kind=KIND_SYSTEM, name=subsystem)
            if node is None:
                continue
            systems[subsystem] = node
        system = systems[subsystem]

        action = str(row.action or "").strip()
        if action:
            _edge(
                session,
                report,
                system,
                _entity(session, report, kind=KIND_CAPABILITY, name=action),
                RELATION_PERFORMS,
            )

        # A task the event was working on, by the id the ledger already keys it with -
        # never a name parsed out of the summary.
        for task_id in (row.research_job_id, row.related_goal_id):
            if task_id is None:
                continue
            _edge(
                session,
                report,
                system,
                _entity(session, report, kind=KIND_TASK, name=str(task_id)),
                RELATION_WORKED_ON,
            )

    return report


__all__ = [
    "KIND_CAPABILITY",
    "KIND_SYSTEM",
    "KIND_TASK",
    "RELATION_PERFORMS",
    "RELATION_WORKED_ON",
    "GraphReport",
    "sync_from_events",
]

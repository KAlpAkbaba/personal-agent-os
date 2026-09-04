"""Self Model ORM rows (PHASE 6).

Style follows ``app/ledger/models.py`` and ``app/selfhealing/models.py``:
portable column types (generic ``Uuid``/``String``, ``JSON`` with a ``JSONB``
variant) so every unit test can create the tables straight from
``Base.metadata`` on SQLite, and ``CheckConstraint``s spelling out the closed
vocabularies rather than trusting the writer.

No Alembic revision lives here on purpose: the integrator writes one migration
covering every new table of this build. Until then these tables exist only
where they are created from metadata.

The load-bearing design choice is ``module_provenance``. A module has FOUR
independent truths and they are stored as four rows, never as four columns of
one row:

``source``     the checkout on disk says this file exists with this digest;
``installed``  a release/install record says this version was placed somewhere;
``runtime``    something actually running reported this version back;
``evidence``   a gate result (tests passed, review passed, shadow ready).

Collapsing them is exactly the deployment-truthfulness defect
``services/browser/browser_agent/release.py`` documents: the installer said
"INSTALL VERIFIED" from the SOURCE tree while the live worker still ran an old
copy. Source truth is free -- it is just a checkout walk -- so it must never be
allowed to masquerade as runtime truth. ``app.selfmodel.indexer`` therefore
writes ``source`` rows from the filesystem and ``installed``/``runtime`` rows
ONLY from real evidence rows (releases, ledger deployment events, agent audit).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


# ------------------------------------------------------------- vocabularies

#: What kind of thing a ``code_modules`` row is. ``package``/``module`` are the
#: Python import-graph shapes inside ``services/api``; ``service``/``script``/
#: ``client`` are the foreign trees that are indexed by name and path only.
MODULE_KIND_PACKAGE = "package"
MODULE_KIND_MODULE = "module"
MODULE_KIND_SERVICE = "service"
MODULE_KIND_SCRIPT = "script"
MODULE_KIND_CLIENT = "client"

MODULE_KINDS: Final[tuple[str, ...]] = (
    MODULE_KIND_PACKAGE,
    MODULE_KIND_MODULE,
    MODULE_KIND_SERVICE,
    MODULE_KIND_SCRIPT,
    MODULE_KIND_CLIENT,
)

#: How far a module has actually got. Distinct from the ledger's
#: ``production_state`` vocabulary (``app/ledger/vocabulary.py``), which
#: describes an EVENT's stage; this one describes where the CODE currently
#: lives. ``source_only`` is the honest default for anything the indexer found
#: in the checkout and for which no install/runtime evidence exists.
PRODUCTION_STATE_SOURCE_ONLY = "source_only"
PRODUCTION_STATE_INSTALLED = "installed"
PRODUCTION_STATE_RUNNING = "running"
PRODUCTION_STATE_SHADOW = "shadow"
PRODUCTION_STATE_LAB = "lab"
PRODUCTION_STATE_RETIRED = "retired"

PRODUCTION_STATES: Final[tuple[str, ...]] = (
    PRODUCTION_STATE_SOURCE_ONLY,
    PRODUCTION_STATE_INSTALLED,
    PRODUCTION_STATE_RUNNING,
    PRODUCTION_STATE_SHADOW,
    PRODUCTION_STATE_LAB,
    PRODUCTION_STATE_RETIRED,
)

SYMBOL_KIND_CLASS = "class"
SYMBOL_KIND_FUNCTION = "function"
SYMBOL_KIND_METHOD = "method"
SYMBOL_KIND_CONSTANT = "constant"
SYMBOL_KIND_ROUTE = "route"
SYMBOL_KIND_CAPABILITY = "capability"
SYMBOL_KIND_TOOL = "tool"
SYMBOL_KIND_TABLE = "table"

SYMBOL_KINDS: Final[tuple[str, ...]] = (
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_METHOD,
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_ROUTE,
    SYMBOL_KIND_CAPABILITY,
    SYMBOL_KIND_TOOL,
    SYMBOL_KIND_TABLE,
)

EDGE_IMPORTS = "imports"
EDGE_CALLS = "calls"
EDGE_TESTS = "tests"
EDGE_IMPLEMENTS_CAPABILITY = "implements_capability"
EDGE_DOCUMENTED_BY = "documented_by"
EDGE_RELEASED_AS = "released_as"

EDGE_KINDS: Final[tuple[str, ...]] = (
    EDGE_IMPORTS,
    EDGE_CALLS,
    EDGE_TESTS,
    EDGE_IMPLEMENTS_CAPABILITY,
    EDGE_DOCUMENTED_BY,
    EDGE_RELEASED_AS,
)

TRUTH_SOURCE = "source"
TRUTH_INSTALLED = "installed"
TRUTH_RUNTIME = "runtime"
TRUTH_EVIDENCE = "evidence"

TRUTH_KINDS: Final[tuple[str, ...]] = (
    TRUTH_SOURCE,
    TRUTH_INSTALLED,
    TRUTH_RUNTIME,
    TRUTH_EVIDENCE,
)

#: The truths that may only ever be written from a real evidence row. Guarded in
#: ``app.selfmodel.indexer`` and asserted by ``test_selfmodel_indexer.py``.
EVIDENCE_ONLY_TRUTHS: Final[frozenset[str]] = frozenset({TRUTH_INSTALLED, TRUTH_RUNTIME})


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


#: ``module_id`` and ``symbol_id`` are strings a human can read in a log line
#: ("app.selfmodel.query", "app.selfmodel.query::function::module_status"), so
#: they are capped rather than hashed -- but a pathological name must not blow
#: the column, hence :func:`symbol_id_for`.
MODULE_ID_MAX = 200
SYMBOL_ID_MAX = 320


def symbol_id_for(module_id: str, kind: str, name: str) -> str:
    """Deterministic, readable-when-possible id for a symbol.

    Deterministic matters: a re-index of an unchanged module must produce the
    same ids so nothing churns. Overlong ids fall back to a digest suffix
    instead of being silently truncated into a collision.
    """
    readable = f"{module_id}::{kind}::{name}"
    if len(readable) <= SYMBOL_ID_MAX:
        return readable
    digest = hashlib.sha256(readable.encode("utf-8")).hexdigest()[:16]
    return f"{readable[: SYMBOL_ID_MAX - 17]}#{digest}"


# ------------------------------------------------------------------- tables


class CodeModule(Base):
    """One indexable unit of this system: a Python module/package inside
    ``services/api``, or a directory/file of a foreign tree.

    ``purpose`` is the FIRST LINE of the module docstring and nothing more --
    the index stores extracted structure, never file bodies.
    """

    __tablename__ = "code_modules"
    __table_args__ = (
        CheckConstraint(_in_list("kind", MODULE_KINDS), name="ck_code_modules_kind"),
        CheckConstraint(
            _in_list("production_state", PRODUCTION_STATES),
            name="ck_code_modules_production_state",
        ),
        Index("ix_code_modules_kind", "kind"),
        Index("ix_code_modules_owner_area", "owner_area"),
        Index("ix_code_modules_production_state", "production_state"),
    )

    #: "app.research.eligibility" for the API's Python tree,
    #: "services/browser/browser_agent/worker.py" for a foreign tree.
    module_id: Mapped[str] = mapped_column(String(MODULE_ID_MAX), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: repo-relative POSIX path of the file or directory this row stands for.
    path: Mapped[str] = mapped_column(String(400), nullable=False)
    language: Mapped[str] = mapped_column(String(24), nullable=False)
    #: first docstring line; ``None`` when the module has no docstring or the
    #: language is not parsed. Never a summary the indexer invented.
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ["ADR-0050", ...] scraped from docs/DECISIONS.md.
    adr_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: ["docs/M16_ACTIVITY_LEDGER_SPEC.md", ...].
    spec_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: coarse subsystem bucket ("ledger", "voice", "browser", "tests", ...) used
    #: to map release components and ledger subsystems onto modules.
    owner_area: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    production_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PRODUCTION_STATE_SOURCE_ONLY
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: indexer bookkeeping: {"fingerprint": ..., "digest": ..., "size": ...,
    #: "symbol_count": ..., "display_name": ...}. Structure only, never content.
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


class CodeSymbol(Base):
    """A named thing inside a module: class, function, method, constant, route,
    capability, tool, or database table."""

    __tablename__ = "code_symbols"
    __table_args__ = (
        CheckConstraint(_in_list("kind", SYMBOL_KINDS), name="ck_code_symbols_kind"),
        Index("ix_code_symbols_module_id", "module_id"),
        Index("ix_code_symbols_name", "name"),
        Index("ix_code_symbols_kind", "kind"),
    )

    symbol_id: Mapped[str] = mapped_column(String(SYMBOL_ID_MAX), primary_key=True)
    module_id: Mapped[str] = mapped_column(
        String(MODULE_ID_MAX),
        ForeignKey("code_modules.module_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: "def module_status(session, module_key) -> ModuleStatus" or, for a route,
    #: "GET /v1/selfmodel/policy". Signatures only -- never bodies.
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lineno: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: first line of the symbol's docstring, capped; ``None`` when absent.
    docstring_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)


class CodeEdge(Base):
    """A directed relationship between two module keys.

    ``to_module`` is deliberately NOT a foreign key: a ``documented_by`` edge
    points at "docs/DECISIONS.md#ADR-0050" and a ``released_as`` edge at a
    release component, neither of which is a ``code_modules`` row.
    """

    __tablename__ = "code_edges"
    __table_args__ = (
        CheckConstraint(_in_list("kind", EDGE_KINDS), name="ck_code_edges_kind"),
        UniqueConstraint("from_module", "to_module", "kind", name="uq_code_edges_triple"),
        Index("ix_code_edges_from_module", "from_module"),
        Index("ix_code_edges_to_module", "to_module"),
        Index("ix_code_edges_kind", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_module: Mapped[str] = mapped_column(String(MODULE_ID_MAX), nullable=False)
    to_module: Mapped[str] = mapped_column(String(400), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


class ModuleProvenance(Base):
    """One of the four truths about one module -- never a merged "version".

    Exactly one row per ``(module_id, truth_kind)``. ``confidence`` is explicit
    so a reader is never forced to guess how much a row is worth, and
    ``evidence_refs`` must be non-empty for the evidence-only truths (enforced
    by :mod:`app.selfmodel.indexer`, which is the only writer).
    """

    __tablename__ = "module_provenance"
    __table_args__ = (
        CheckConstraint(_in_list("truth_kind", TRUTH_KINDS), name="ck_module_provenance_truth"),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_module_provenance_confidence"
        ),
        UniqueConstraint("module_id", "truth_kind", name="uq_module_provenance_module_truth"),
        Index("ix_module_provenance_module_id", "module_id"),
        Index("ix_module_provenance_truth_kind", "truth_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    module_id: Mapped[str] = mapped_column(String(MODULE_ID_MAX), nullable=False)
    truth_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: content digest (sha256 hex) when one was actually computed.
    digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: [{"kind": "checkout", "ref": "app/selfmodel/query.py"}, ...] -- for
    #: installed/runtime this points at the release/ledger/audit row that said so.
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: set when the observation is known to be older than the current source.
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = [
    "EDGE_CALLS",
    "EDGE_DOCUMENTED_BY",
    "EDGE_IMPLEMENTS_CAPABILITY",
    "EDGE_IMPORTS",
    "EDGE_KINDS",
    "EDGE_RELEASED_AS",
    "EDGE_TESTS",
    "EVIDENCE_ONLY_TRUTHS",
    "MODULE_KINDS",
    "MODULE_KIND_CLIENT",
    "MODULE_KIND_MODULE",
    "MODULE_KIND_PACKAGE",
    "MODULE_KIND_SCRIPT",
    "MODULE_KIND_SERVICE",
    "PRODUCTION_STATES",
    "PRODUCTION_STATE_INSTALLED",
    "PRODUCTION_STATE_LAB",
    "PRODUCTION_STATE_RETIRED",
    "PRODUCTION_STATE_RUNNING",
    "PRODUCTION_STATE_SHADOW",
    "PRODUCTION_STATE_SOURCE_ONLY",
    "SYMBOL_KINDS",
    "SYMBOL_KIND_CAPABILITY",
    "SYMBOL_KIND_CLASS",
    "SYMBOL_KIND_CONSTANT",
    "SYMBOL_KIND_FUNCTION",
    "SYMBOL_KIND_METHOD",
    "SYMBOL_KIND_ROUTE",
    "SYMBOL_KIND_TABLE",
    "SYMBOL_KIND_TOOL",
    "TRUTH_EVIDENCE",
    "TRUTH_INSTALLED",
    "TRUTH_KINDS",
    "TRUTH_RUNTIME",
    "TRUTH_SOURCE",
    "CodeEdge",
    "CodeModule",
    "CodeSymbol",
    "ModuleProvenance",
    "symbol_id_for",
]

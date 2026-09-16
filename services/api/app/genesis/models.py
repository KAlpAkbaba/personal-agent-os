"""``GenesisRun`` ORM model (M24_CAPABILITY_GENESIS_SPEC.md §5) — mirrors the
frozen migration ``20260908_0031_genesis_runs.py``.

One row per owner request against an interface with no existing adapter. Every
state transition is a row update (this table) AND a ledger row
(``genesis.<state>``, ``app.ledger``) AND a UiState publish
(``capability.genesis``, ``app.uistate``) — the three-way discipline M18.4
§15/§16 already established. See ``app.genesis.service.GenesisService``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

#: The state machine (spec §5): a linear happy path with one branch
#: (awaiting_approval, only for a mutating capability against an unauthorized
#: asset) and one universal escape (failed, reachable from any state).
GENESIS_STATES: tuple[str, ...] = (
    "capability_missing",
    "researching",
    "designing",
    "building",
    "testing",
    "classifying",
    "awaiting_approval",
    "rolling_out",
    "registering",
    "available",
    "used",
    "verified",
    "failed",
    "cancelled",
)

AUTHORITY_CLASSES: tuple[str, ...] = (
    "read_only",
    "mutating_authorized_asset",
    "mutating_unauthorized",
)
SIDE_EFFECT_CLASSES: tuple[str, ...] = ("none", "read", "mutate_external")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class GenesisRun(Base):
    __tablename__ = "genesis_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    #: The single operation this run targets — capability_id is always
    #: "<interface.name>.<operation_id>", stored again here so callers never
    #: need to split the dotted string.
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    gap_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="capability_missing")
    #: The parsed InterfaceDescription (app.genesis.interface), as researched.
    interface_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    authority_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side_effect_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The owner-recorded authorization this run resolved against (asset ref
    #: or the M21 Confirmation's own action_id) once approval was granted.
    approval_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skill_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: request payload (target/operation/arguments), dispatch output, read-back
    #: evidence and (while awaiting_approval) the turn it parked on — accrues
    #: across states rather than one column per fact.
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_list("state", GENESIS_STATES), name="ck_genesis_runs_state"),
        CheckConstraint(
            _in_list("authority_class", AUTHORITY_CLASSES) + " OR authority_class IS NULL",
            name="ck_genesis_runs_authority_class",
        ),
        CheckConstraint(
            _in_list("side_effect_class", SIDE_EFFECT_CLASSES) + " OR side_effect_class IS NULL",
            name="ck_genesis_runs_side_effect_class",
        ),
        Index("ix_genesis_runs_capability_state", "capability_id", "state"),
        Index("ix_genesis_runs_created_at", "created_at"),
    )


__all__ = ["AUTHORITY_CLASSES", "GENESIS_STATES", "SIDE_EFFECT_CLASSES", "GenesisRun"]


# ------------------------------------------------------------------ B36: the catalogue

CATALOGUE_SOURCE_OWNER_REST = "owner_rest"
CATALOGUE_SOURCE_OWNER_VOICE = "owner_voice"
CATALOGUE_SOURCE_DISCOVERY = "discovery"
CATALOGUE_SOURCES: tuple[str, ...] = (
    CATALOGUE_SOURCE_OWNER_REST,
    CATALOGUE_SOURCE_OWNER_VOICE,
    CATALOGUE_SOURCE_DISCOVERY,
)


class GenesisCatalogueRow(Base):
    """One controllable interface the OWNER registered (B36 req 562/563): the spoken
    phrases that name it, the URL its description is fetched from, and the verb aliases
    of its operations. The in-memory ``GenesisInterfaceCatalogue`` the router reads is
    built from these rows at startup and after every registration; before B36 the
    catalogue had no registration surface and was empty in every production process."""

    __tablename__ = "genesis_catalogue"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    target_phrases_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    operations_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CATALOGUE_SOURCE_OWNER_REST
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The sha256 of the description last fetched from ``url`` (discovery / refresh).
    spec_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

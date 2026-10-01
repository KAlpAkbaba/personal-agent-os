"""``/v1/team/allowlist``: the owner adds and removes the sites a cloud job may act on (ADR-0218).

GET lists seed + owner sites; POST ``{"site": ...}`` adds one; DELETE ``/{site}`` removes one
the owner added (a seed site is the shared file's, 409). Under the owner session. Every change
is a ledger event recorded BEFORE the row is written: if the ledger refuses, nothing is
written and the owner is told. The rules (registrable domain, bare suffix, deny-list) live in
``app.execution.allowlist_store.validate_site``, not here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.execution import allowlist_store as store
from app.identity.dependencies import require_owner_session
from app.ledger import service as ledger_service
from app.ledger.vocabulary import InvalidVocabulary

router = APIRouter(dependencies=[Depends(require_owner_session)])


class AddSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str


def _bind(request: Request) -> Any:
    """The runtime's session, also handed to the store so ``acting_allowed`` reads the rows."""
    artifacts = request.app.state.artifacts
    store.bind(artifacts.session)
    return artifacts


def _record(session: Any, event_type: str, site: str, verb: str) -> None:
    event = ledger_service.ActivityEvent(
        event_type=event_type,
        subsystem=store.SUBSYSTEM_TEAM,
        action=event_type.replace(".", "_"),
        factual_summary=f"Sahip bulut izin listesine {site} sitesini {verb}.",
        source="team.allowlist",
        source_ref=f"{site}:{event_type}",
        detail_json=store.event_detail(site, channel=store.ADDED_BY_SHELL),
    )
    try:
        ledger_service.record(session, event)
    except InvalidVocabulary as error:
        raise HTTPException(
            503,
            {
                "code": "ledger_refused",
                "message": "Ledger bu olayı kabul etmedi; liste değişmedi.",
                "why": str(error),
            },
        ) from error


def _view(entry: store.Entry) -> dict[str, str]:
    return {
        "site": entry.site,
        "source": entry.source,
        "added_at": entry.added_at,
        "added_by": entry.added_by,
    }


@router.get("/v1/team/allowlist")
async def list_sites(request: Request) -> dict[str, Any]:
    _bind(request)
    entries = await asyncio.to_thread(store.entries)
    return {"sites": [_view(e) for e in entries]}


@router.post("/v1/team/allowlist")
async def add_site(body: AddSiteRequest, request: Request) -> dict[str, Any]:
    artifacts = _bind(request)

    def run() -> dict[str, Any]:
        try:
            site = store.validate_site(body.site)
        except store.Refusal as refusal:
            raise HTTPException(
                422, {"code": refusal.code, "message": refusal.message}
            ) from refusal
        with artifacts.session() as session:
            if store.exists(session, site) or site in {e.site for e in store.entries()}:
                return {"site": site, "already_listed": True}
            _record(session, store.EVENT_SITE_ADDED, site, "ekledi")
            store.add(session, site)
        return {"site": site, "already_listed": False}

    return await asyncio.to_thread(run)


@router.delete("/v1/team/allowlist/{site}")
async def remove_site(site: str, request: Request) -> dict[str, Any]:
    artifacts = _bind(request)
    name = site.strip().strip(".").lower()

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            if not store.exists(session, name):
                if name in {e.site for e in store.entries() if e.source == store.SOURCE_SEED}:
                    raise HTTPException(
                        409,
                        {
                            "code": "seed_site",
                            "message": "Bu site paylaşılan dosyadan geliyor; buradan çıkarılamaz.",
                        },
                    )
                raise HTTPException(404, {"code": "not_listed", "message": f"{name} listede yok."})
            _record(session, store.EVENT_SITE_REMOVED, name, "çıkardı")
            store.remove(session, name)
        return {"site": name, "removed": True}

    return await asyncio.to_thread(run)

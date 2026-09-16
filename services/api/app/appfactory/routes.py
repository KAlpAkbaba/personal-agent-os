"""App Factory REST surface for the Cockpit's "Uygulamalar" panel (docs/M23_APP_FACTORY_SPEC.md
§6, ADR-0086 addendum 3).

- GET  /v1/apps                    the projects as rows (name, template, state, port, the last
                                   test counts) — a read of ``app_projects``, no ledger row per
                                   poll (the panel refreshes; ``app.list`` by voice keeps its row)
- POST /v1/apps/{id}/run|stop|test the SAME ``AppFactoryService`` methods the voice tools call
                                   (``tools_apps``), on the SAME device port every family holds —
                                   the Cockpit's "Çalıştır" and "Uygulamayı çalıştır." can never
                                   disagree about what "running" means. Bodies are ignored: the
                                   id in the path is the whole request.

Owner-gated at the router level (``require_owner_session``) — a process start on the owner's
machine must never be one unauthenticated HTTP call away, any more than "Çalıştır." is one
utterance away from one. A receipt that refused answers 422 with the receipt's own error class
and sentence; an unknown id answers 404; an executed receipt answers 200 with the read-back.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.actions.receipt import EXECUTION_EXECUTED, EXECUTION_NOOP
from app.appfactory.models import AppProjectRow
from app.appfactory.service import AppFactoryService
from app.identity.dependencies import require_owner_session

APPS_ROUTES_VERSION = 1

router = APIRouter(prefix="/v1/apps", tags=["apps"], dependencies=[Depends(require_owner_session)])

#: The three controls the panel may issue (spec §6) — nothing else is a route here: no
#: create by REST (an app is asked for by voice, ADR-0086 decision 5 keeps one path), no
#: delete anywhere.
ACTIONS: tuple[str, ...] = ("run", "stop", "test")


def _service(request: Request) -> AppFactoryService:
    return request.app.state.app_factory_service


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _counts(report: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(report, dict):
        return None
    passed, failed = report.get("passed"), report.get("failed")
    if isinstance(passed, int) and isinstance(failed, int):
        return {"passed": passed, "failed": failed}
    return None


def _row_dict(row: AppProjectRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "kind": row.kind,
        "template": row.template,
        "state": row.state,
        "port": row.run_port,
        "root_path": row.root_path,
        "tests": _counts(row.test_report_json),
        "version": row.version,
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "plan": row.plan_json,
        "reports": row.reports_json,
        "fix": row.fix_json,
        "lifecycle": row.lifecycle_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("")
async def list_projects(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(
                    select(AppProjectRow).order_by(AppProjectRow.created_at.desc()).limit(50)
                )
                .scalars()
                .all()
            )
            return [_row_dict(r) for r in rows]

    return {"projects": await asyncio.to_thread(load)}


def _respond(receipt: dict[str, Any], project_id: uuid.UUID) -> dict[str, Any]:
    """An executed receipt (or an honest no-op such as "zaten çalışmıyor") is the
    read-back; a refusal is the receipt's own error class and sentence as a 422; an
    unknown id is a 404. Never a 200 for something that did not happen."""
    error_class = receipt.get("error_class")
    execution = receipt.get("execution_status")
    if execution not in (EXECUTION_EXECUTED, EXECUTION_NOOP):
        status_code = 404 if error_class == "not_found" else 422
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": error_class or execution or "refused",
                "message": receipt.get("speech"),
            },
        )
    tests = None
    if isinstance(receipt.get("passed"), int) and isinstance(receipt.get("failed"), int):
        tests = {"passed": receipt["passed"], "failed": receipt["failed"]}
    return {
        "project_id": str(project_id),
        "state": receipt.get("state"),
        "port": receipt.get("port"),
        "tests": tests,
        "execution_status": execution,
        "speech": receipt.get("speech"),
        "error_class": error_class,
    }


async def _act(request: Request, project_id: uuid.UUID, action: str) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    device_action = getattr(request.app.state, "device_action", None)
    owner_session_id = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(AppProjectRow, project_id) is None:
                return {
                    "execution_status": "refused",
                    "error_class": "not_found",
                    "speech": "Böyle bir proje yok.",
                }
            method = getattr(service, action)
            return method(
                db, device_action, target=str(project_id), session_id=f"rest:{owner_session_id}"
            )

    return _respond(await asyncio.to_thread(do), project_id)


@router.post("/{project_id}/run")
async def run_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "run")


@router.post("/{project_id}/stop")
async def stop_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "stop")


@router.post("/plan")
async def plan_project(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """B40 (req 423/424): the plan the owner may read before anything is written."""
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_argument", "message": "text is required"}
        )
    return await asyncio.to_thread(_service(request).plan, text)


@router.post("/{project_id}/verify")
async def verify_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "verify")


@router.get("/{project_id}/log")
async def log_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "log")


@router.post("/{project_id}/package")
async def package_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "package")


@router.post("/{project_id}/launch")
async def launch_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "launch")


@router.get("/{project_id}/history")
async def history_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(AppProjectRow, project_id) is None:
                raise HTTPException(
                    status_code=404, detail={"code": "not_found", "message": "Böyle bir proje yok."}
                )
            return service.history(db, target=str(project_id))

    return await asyncio.to_thread(do)


@router.post("/{project_id}/modify")
async def modify_project(
    request: Request, project_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    device_action = getattr(request.app.state, "device_action", None)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_argument", "message": "text is required"}
        )
    owner_session_id = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(AppProjectRow, project_id) is None:
                return {
                    "execution_status": "refused",
                    "error_class": "not_found",
                    "speech": "Böyle bir proje yok.",
                }
            return service.modify(
                db,
                device_action,
                target=str(project_id),
                request=text,
                session_id=f"rest:{owner_session_id}",
            )

    return _respond(await asyncio.to_thread(do), project_id)


@router.post("/{project_id}/fix")
async def fix_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    """B40 (req 435-437): the bounded fix loop over a failed test run."""
    return await _act(request, project_id, "fix")


@router.post("/{project_id}/test")
async def test_project(request: Request, project_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, project_id, "test")

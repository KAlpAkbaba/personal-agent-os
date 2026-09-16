"""The lifecycle half of the App Factory service (B41 req 440-452): verify, log,
package, launch, history, resume, modify. ``AppFactoryService`` calls these; each takes
the service for its receipts, its ledger and its device plumbing, and returns the
receipt dict the tools and the routes hand on unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
)
from app.appfactory import composed_service, lifecycle
from app.appfactory.code_model import CodeModelError
from app.appfactory.composer import TEMPLATE_COMPOSED
from app.appfactory.generator import DeterministicAppGenerator, ProjectFiles
from app.appfactory.models import STATE_FAILED, STATE_TESTED, AppProjectRow
from app.appfactory.oracles import load_oracle
from app.appfactory.spec import AppSpec
from app.appfactory.validation import validate
from app.ledger.models import ActivityEventRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_PROJECT
from app.routines.dispatch import DeviceActionPort

#: The owner-facing error classes of the lifecycle (app/errors/catalog.py names each).
ERROR_RELEASE_CORRUPT = "release_corrupt"
ERROR_MODEL_REFUSED = "model_refused"
ERROR_NOTHING_TO_ADD = "nothing_to_add"

EVENT_VERIFY = "app.project.verify"
EVENT_PACKAGE = "app.project.package"
EVENT_LAUNCH = "app.project.launch"
EVENT_MODIFY = "app.project.modify"

SPEECH_NOT_RUNNING = "Önce uygulamayı çalıştırmalıyım efendim."
SPEECH_NO_RELEASE = "Önce bir sürüm paketi çıkarmalıyım efendim."
SPEECH_NO_STORE = "Sürüm paketi için bir nesne deposu tanımlı değil efendim."


def _lifecycle(project: AppProjectRow) -> dict[str, Any]:
    return dict(project.lifecycle_json or {})


def _save_lifecycle(db: Session, project: AppProjectRow, key: str, value: Any) -> None:
    data = _lifecycle(project)
    data[key] = value
    project.lifecycle_json = data
    project.updated_at = datetime.now(UTC)
    db.commit()


def _files_for(service: Any, project: AppProjectRow) -> tuple[ProjectFiles, dict[str, Any]]:
    """The project's files, regenerated from its own spec (deterministic for a template;
    the composed build with its carried custom files) and validated again."""
    spec_dict = dict(project.spec_json or {})
    if project.template == TEMPLATE_COMPOSED:
        custom = dict((project.reports_json or {}).get("custom_files") or {})
        build = composed_service.build_composed(
            spec_dict, generator=service._composed, custom_files=custom or None
        )
        return build.files, build.manifest
    files = DeterministicAppGenerator().generate(AppSpec.model_validate(spec_dict))
    _report, manifest = validate(files)
    return files, manifest


# ------------------------------------------------------------------- verify (443, 444)


def verify(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    _device_id, missing = service._select_device(device_action)
    if missing is not None:
        return missing
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    if not project.run_port:
        return service._invalid_argument(
            capability="app.verify",
            requested_state="verified",
            speech=SPEECH_NOT_RUNNING,
            db=db,
            session_id=session_id,
        )
    oracle = project.oracle_json or _oracle_or_none(project.template)
    if oracle is None:
        return service._invalid_argument(
            capability="app.verify",
            requested_state="verified",
            speech="Bu uygulamanın arayüz senaryosu yok efendim.",
            db=db,
            session_id=session_id,
        )
    url = f"http://127.0.0.1:{project.run_port}/"

    def restart() -> str | None:
        stopped = service.stop(db, device_action, target=str(project.id), session_id=session_id)
        if stopped.get("execution_status") not in (EXECUTION_EXECUTED, "noop"):
            return None
        started = service.run(db, device_action, target=str(project.id), session_id=session_id)
        if started.get("execution_status") != EXECUTION_EXECUTED:
            return None
        db.refresh(project)
        return f"http://127.0.0.1:{project.run_port}/" if project.run_port else None

    outcome = lifecycle.verify_ui(
        device_action, url=url, oracle=oracle, project_id=str(project.id), restart=restart
    )  # type: ignore[arg-type]
    _save_lifecycle(db, project, "verify", outcome.as_dict())
    service._ledger(
        db,
        event_type=EVENT_VERIFY,
        action="app.project.verify",
        summary=f"app.project.verify -> {project.name}: ui={outcome.verified} "
        f"persistence={outcome.persistence}",
        detail={
            "project_id": str(project.id),
            "verified": outcome.verified,
            "persistence": outcome.persistence,
            "failed_step": outcome.failed_step,
        },
    )
    ok = outcome.verified and outcome.persistence is not False
    if ok:
        speech = f"{project.name} arayüzü doğrulandı efendim: {len(outcome.steps)} adım geçti"
        speech += ", yeniden başlatınca kayıt yerinde." if outcome.persistence else "."
    elif outcome.error:
        speech = f"{project.name} arayüzünü doğrulayamadım efendim: {outcome.error}."
    else:
        speech = (
            f"{project.name} arayüz doğrulaması {outcome.failed_step} adımında takıldı efendim."
        )
    return service._receipt(
        capability="app.verify",
        requested_state="verified",
        execution=EXECUTION_EXECUTED if ok else EXECUTION_REFUSED,
        terminal=TERMINAL_VERIFIED if ok else TERMINAL_FAILED,
        server={
            "verified": outcome.verified,
            "persistence": outcome.persistence,
            "steps": len(outcome.steps),
        },
        speech=speech,
        db=db,
        error_class=None if ok else "verification_failed",
        session_id=session_id,
        extra={"project_id": str(project.id), "verification": outcome.as_dict()},
    )


def _oracle_or_none(template: str) -> dict[str, Any] | None:
    try:
        return load_oracle(template)
    except KeyError:
        return None


# ------------------------------------------------------------------------ log (445)


def log(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    _device_id, missing = service._select_device(device_action)
    if missing is not None:
        return missing
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    if project.root_path is None:
        return service._invalid_argument(
            capability="app.log",
            requested_state="read",
            speech="Önce bir uygulama oluşturmalıyım efendim.",
            db=db,
            session_id=session_id,
        )
    result = lifecycle.read_log(device_action, project_id=str(project.id))  # type: ignore[arg-type]
    _save_lifecycle(db, project, "log", result)
    if not result.get("ok"):
        return service._receipt(
            capability="app.log",
            requested_state="read",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"error_class": result.get("error_class")},
            speech=f"{project.name} günlüğünü okuyamadım efendim.",
            db=db,
            error_class=str(result.get("error_class") or "device_error"),
            session_id=session_id,
            extra={"project_id": str(project.id)},
        )
    lines = int(result.get("lines") or 0)
    last = str(result.get("last_line") or "")
    state = str(result.get("state") or "")
    speech = f"{project.name} günlüğü: {lines} satır"
    if state:
        speech += f", durum {state}"
    speech += (f"; son satır: {last}" if last else "; henüz satır yok") + "."
    return service._receipt(
        capability="app.log",
        requested_state="read",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={"lines": lines, "state": state, "log_path": result.get("log_path")},
        speech=speech + " efendim.",
        db=db,
        session_id=session_id,
        extra={"project_id": str(project.id), "log": result},
    )


# ------------------------------------------------------------- package (441, 446)


def package(
    service: Any, db: Session, *, target: str | None, session_id: str | None
) -> dict[str, Any]:
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    if service._store is None:
        return service._invalid_argument(
            capability="app.package",
            requested_state="packaged",
            speech=SPEECH_NO_STORE,
            db=db,
            session_id=session_id,
        )
    if project.root_path is None:
        return service._invalid_argument(
            capability="app.package",
            requested_state="packaged",
            speech="Önce bir uygulama oluşturmalıyım efendim.",
            db=db,
            session_id=session_id,
        )
    if project.state not in (STATE_TESTED, "running", "stopped"):
        return service._invalid_argument(
            capability="app.package",
            requested_state="packaged",
            speech=f"Testleri geçmemiş bir uygulamayı paketlemem efendim (durum: {project.state}).",
            db=db,
            session_id=session_id,
        )
    try:
        files, manifest = _files_for(service, project)
    except composed_service.ComposedRefusal as exc:
        return service._invalid_argument(
            capability="app.package",
            requested_state="packaged",
            speech=exc.speech,
            db=db,
            session_id=session_id,
        )
    slug = AppSpec.model_validate(project.spec_json).slug()
    artifact = lifecycle.package_release(
        service._store,
        slug=slug,
        version=int(project.version or 1),
        files=files,
        manifest=manifest,
        spec=dict(project.spec_json or {}),
        name=project.name,
    )
    _save_lifecycle(db, project, "release", artifact.as_dict())
    service._ledger(
        db,
        event_type=EVENT_PACKAGE,
        action="app.project.package",
        summary=f"app.project.package -> {project.name} v{artifact.version} ({artifact.build_id}, "
        f"{artifact.bytes} bytes)",
        detail={"project_id": str(project.id), **artifact.as_dict()},
    )
    return service._receipt(
        capability="app.package",
        requested_state="packaged",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=artifact.as_dict(),
        speech=f"{project.name} sürüm {artifact.version} paketlendi efendim: {artifact.files} "
        f"dosya, yapı kimliği {artifact.build_id}.",
        db=db,
        session_id=session_id,
        extra={"project_id": str(project.id), "release": artifact.as_dict()},
    )


# ------------------------------------------------------------------- launch (442)


def launch(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    _device_id, missing = service._select_device(device_action)
    if missing is not None:
        return missing
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    release = _lifecycle(project).get("release")
    if not release or service._store is None:
        return service._invalid_argument(
            capability="app.launch",
            requested_state="launched",
            speech=SPEECH_NO_RELEASE,
            db=db,
            session_id=session_id,
        )
    try:
        files, release_meta = lifecycle.files_from_release(service._store, str(release["key"]))
    except (ValueError, KeyError, StopIteration) as exc:
        return service._receipt(
            capability="app.launch",
            requested_state="launched",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_RELEASE_CORRUPT},
            speech=f"{project.name} sürüm paketi kendi kaydıyla uyuşmuyor efendim; çalıştırmadım.",
            db=db,
            error_class=ERROR_RELEASE_CORRUPT,
            session_id=session_id,
            extra={"project_id": str(project.id), "detail": str(exc)[:200]},
        )
    version = int(release_meta.get("version") or project.version or 1)
    slug = str(release_meta.get("slug") or AppSpec.model_validate(project.spec_json).slug())
    project_id = uuid.uuid4()
    now = datetime.now(UTC)
    row = AppProjectRow(
        id=project_id,
        name=project.name,
        kind=project.kind,
        template=project.template,
        spec_json=dict(project.spec_json or {}),
        device_id=project.device_id,
        state="planned",
        plan_json=project.plan_json,
        reports_json=project.reports_json,
        oracle_json=project.oracle_json,
        version=version,
        parent_id=project.id,
        lifecycle_json={"launched_from": dict(release)},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    result = device_action.run(  # type: ignore[union-attr]
        capability="project.scaffold",
        payload={
            "project_id": str(project_id),
            "slug": f"{slug}-release-v{version}",
            "files": [{"path": f.path, "text": f.text} for f in files.files],
            "manifest": dict(release_meta.get("manifest") or {}),
        },
        idempotency_key=f"appfactory-launch:{project_id}",
        timeout_s=30.0,
    )
    if not result.ok:
        row.state = STATE_FAILED
        row.updated_at = datetime.now(UTC)
        db.commit()
        speech, error_class = service._translate(result.error_class)
        return service._receipt(
            capability="app.launch",
            requested_state="launched",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"error_class": result.error_class},
            speech=speech,
            db=db,
            error_class=error_class,
            session_id=session_id,
            extra={"project_id": str(project_id)},
        )
    row.root_path = str((result.result or {}).get("root_path") or "") or None
    row.state = "scaffolded"
    row.updated_at = datetime.now(UTC)
    db.commit()
    focus_module.set_focus(
        db, FOCUS_KIND_PROJECT, str(project_id), label=project.name, source="app_launch"
    )
    started = service.run(db, device_action, target=str(project_id), session_id=session_id)
    service._ledger(
        db,
        event_type=EVENT_LAUNCH,
        action="app.project.launch",
        summary=f"app.project.launch -> {project.name} v{version} from {release.get('build_id')}: "
        f"{started.get('execution_status')}",
        detail={"project_id": str(project_id), "from": str(project.id), "release": dict(release)},
    )
    if started.get("execution_status") != EXECUTION_EXECUTED:
        return {**started, "launched_project_id": str(project_id)}
    return {
        **started,
        "launched_project_id": str(project_id),
        "release": dict(release),
        "speech": (
            f"{project.name} sürüm {version} paketinden başlatıldı efendim: "
            f"{started.get('port')} portunda."
        ),
    }


# ------------------------------------------------------------ history and resume (447, 448)


def lineage(db: Session, project: AppProjectRow) -> list[AppProjectRow]:
    rows = list(
        db.execute(
            select(AppProjectRow)
            .where(AppProjectRow.name == project.name)
            .order_by(AppProjectRow.created_at.asc())
        ).scalars()
    )
    return rows


def history(
    service: Any, db: Session, *, target: str | None, session_id: str | None
) -> dict[str, Any]:
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    rows = lineage(db, project)
    ids = {str(r.id) for r in rows}
    events = list(
        db.execute(
            select(ActivityEventRow)
            .where(ActivityEventRow.event_type.like("app.project.%"))
            .order_by(ActivityEventRow.occurred_at.desc())
            .limit(400)
        ).scalars()
    )
    mine = [
        {
            "at": e.occurred_at.isoformat() if e.occurred_at else None,
            "event": e.event_type,
            "summary": e.factual_summary,
            "project_id": (e.detail_json or {}).get("project_id"),
        }
        for e in events
        if str((e.detail_json or {}).get("project_id") or "") in ids
    ]
    mine.reverse()
    versions = [
        {
            "project_id": str(r.id),
            "version": r.version,
            "parent_id": str(r.parent_id) if r.parent_id else None,
            "state": r.state,
            "template": r.template,
            "tests": (r.test_report_json or {}).get("passed") if r.test_report_json else None,
            "release": (r.lifecycle_json or {}).get("release"),
            "launched_from": (r.lifecycle_json or {}).get("launched_from"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    latest = rows[-1] if rows else project
    speech = (
        f"{project.name}: {len(versions)} sürüm, {len(mine)} olay; son sürüm {latest.version} "
        f"durumu {latest.state}."
    )
    return {
        "project_id": str(project.id),
        "name": project.name,
        "versions": versions,
        "events": mine,
        "speech": speech + " efendim.",
    }


def resume(
    service: Any, db: Session, *, target: str | None, name: str | None, session_id: str | None
) -> dict[str, Any]:
    project: AppProjectRow | None = None
    if name:
        project = db.execute(
            select(AppProjectRow)
            .where(AppProjectRow.name.ilike(f"%{name.strip()}%"))
            .order_by(AppProjectRow.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if project is None:
            return service._clarification(f"'{name}' adında bir uygulama bulamadım efendim.")
    else:
        project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    latest = lineage(db, project)[-1]
    focus_module.set_focus(
        db, FOCUS_KIND_PROJECT, str(latest.id), label=latest.name, source="app_resume"
    )
    tests = latest.test_report_json or {}
    parts = [f"sürüm {latest.version}", f"durum {latest.state}"]
    if tests:
        parts.append(f"testler {tests.get('passed', 0)} geçti, {tests.get('failed', 0)} kaldı")
    if latest.run_port:
        parts.append(f"{latest.run_port} portunda çalışıyor")
    release = (latest.lifecycle_json or {}).get("release")
    if release:
        parts.append(f"paket v{release.get('version')} hazır")
    speech = f"{latest.name} uygulamasına döndük efendim: " + ", ".join(parts) + ". Ne ekleyelim?"
    return {
        "project_id": str(latest.id),
        "name": latest.name,
        "version": latest.version,
        "state": latest.state,
        "port": latest.run_port,
        "speech": speech,
        "execution_status": EXECUTION_EXECUTED,
    }


# ----------------------------------------------------------------- modify (449, 450)


def modify(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    request: str,
    session_id: str | None,
) -> dict[str, Any]:
    _device_id, missing = service._select_device(device_action)
    if missing is not None:
        return missing
    project = service.resolve_project(db, target)
    if project is None:
        return service._clarification("Hangi uygulama efendim?")
    if project.template != TEMPLATE_COMPOSED:
        return service._invalid_argument(
            capability="app.modify",
            requested_state="modified",
            speech="Şablon uygulamaları sabittir efendim; yalnız bileşik uygulamalara ekleme "
            "yapabilirim.",
            db=db,
            session_id=session_id,
        )
    text = (request or "").strip()
    if not text:
        return service._clarification("Ne ekleyeyim efendim?")
    base = composed_service.requirements_from_spec(dict(project.spec_json or {}))
    addition = lifecycle.parse_addition(text, known=tuple(e.name for e in base.entities))
    merged, changes = lifecycle.merge_requirements(base, addition)
    custom = dict((project.reports_json or {}).get("custom_files") or {})
    note = ""
    if not changes:
        generator = service._composed
        if generator.model_enabled and generator.model is not None:
            try:
                from app.appfactory.planner import plan_architecture, plan_project

                merged.sentence = f"{base.sentence}. Ek istek: {text}"
                arch = plan_architecture(merged)
                custom = generator.model.generate_custom(
                    merged, arch, plan_project(arch, model_slots=True)
                )
                note = "model"
                changes = ["modelin yazdığı özel davranış"]
            except CodeModelError as exc:
                return service._receipt(
                    capability="app.modify",
                    requested_state="modified",
                    execution=EXECUTION_REFUSED,
                    terminal=TERMINAL_FAILED,
                    server={"reason": ERROR_MODEL_REFUSED},
                    speech=f"Bu isteği kayıt türü ya da alan olarak okuyamadım, model de yazamadı "
                    f"efendim: {str(exc)[:120]}.",
                    db=db,
                    error_class=ERROR_MODEL_REFUSED,
                    session_id=session_id,
                    extra={"project_id": str(project.id)},
                )
        else:
            return service._receipt(
                capability="app.modify",
                requested_state="modified",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": ERROR_NOTHING_TO_ADD},
                speech="Bu isteği bir kayıt türü, alan ya da giriş olarak okuyamadım efendim; "
                "model kapalıyken yalnız bunları ekleyebilirim.",
                db=db,
                error_class=ERROR_NOTHING_TO_ADD,
                session_id=session_id,
                extra={"project_id": str(project.id), "unparsed": addition.unparsed},
            )
    spec_dict = {"name": project.name, "requirements": merged.as_dict()}
    try:
        build = composed_service.build_composed(
            spec_dict, generator=service._composed, custom_files=custom or None
        )
    except composed_service.ComposedRefusal as exc:
        return service._receipt(
            capability="app.modify",
            requested_state="modified",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": exc.code, **exc.detail},
            speech=exc.speech,
            db=db,
            error_class=exc.code,
            session_id=session_id,
            extra={"project_id": str(project.id)},
        )
    version = int(project.version or 1) + 1
    new_row = service._scaffold_version(db, device_action, project, build, version)
    if new_row is None:
        return service._receipt(
            capability="app.modify",
            requested_state="modified",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "scaffold_failed"},
            speech="Yeni sürümü cihaza yazamadım efendim.",
            db=db,
            error_class="device_error",
            session_id=session_id,
            extra={"project_id": str(project.id)},
        )
    tested = service.test(db, device_action, target=str(new_row.id), session_id=session_id)
    db.refresh(new_row)
    _save_lifecycle(
        db,
        new_row,
        "modified",
        {
            "request": text[:600],
            "changes": changes,
            "from": str(project.id),
            "generation": note or build.generation_note,
        },
    )
    service._ledger(
        db,
        event_type=EVENT_MODIFY,
        action="app.project.modify",
        summary=f"app.project.modify -> {project.name} v{version}: {', '.join(changes)}",
        detail={
            "project_id": str(new_row.id),
            "from": str(project.id),
            "changes": changes,
            "tests": {"passed": tested.get("passed"), "failed": tested.get("failed")},
        },
    )
    ok = tested.get("execution_status") == EXECUTION_EXECUTED and new_row.state == STATE_TESTED
    speech = f"{project.name} sürüm {version}: {', '.join(changes)} eklendi efendim"
    speech += (
        f"; testler geçti ({tested.get('passed')})."
        if ok
        else f"; testler geçmedi ({tested.get('failed')} başarısız)."
    )
    return service._receipt(
        capability="app.modify",
        requested_state="modified",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if ok else TERMINAL_UNVERIFIED,
        server={
            "version": version,
            "changes": changes,
            "tests": {"passed": tested.get("passed"), "failed": tested.get("failed")},
        },
        speech=speech,
        db=db,
        session_id=session_id,
        extra={
            "project_id": str(project.id),
            "new_project_id": str(new_row.id),
            "version": version,
            "changes": changes,
            "state": new_row.state,
            "passed": tested.get("passed"),
            "failed": tested.get("failed"),
        },
    )


__all__ = ["history", "launch", "lineage", "log", "modify", "package", "resume", "verify"]

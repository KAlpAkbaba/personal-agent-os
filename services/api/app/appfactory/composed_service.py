"""The composed half of the App Factory service (B40): the owner's sentence -> a planned,
composed, linted, scanned and validated application; a plan preview with nothing
written; and the bounded fix loop over a failed test run. ``AppFactoryService`` calls
these; nothing else does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.appfactory.appsecurity import SecurityReport, scan_files
from app.appfactory.code_model import CodeModel, CodeModelError, ModelAssistedGenerator
from app.appfactory.composer import TEMPLATE_COMPOSED
from app.appfactory.fixloop import (
    ERROR_EXHAUSTED,
    ERROR_NO_MODEL,
    ERROR_SAME_FAILURE,
    MAX_FIX_ATTEMPTS,
    FailureAnalysis,
    FixAttempt,
    FixRecord,
    analyze_failures,
)
from app.appfactory.generator import AppGeneratorError, ProjectFile, ProjectFiles
from app.appfactory.lint import LintReport, lint_files
from app.appfactory.models import STATE_FAILED, STATE_TESTED, AppProjectRow
from app.appfactory.planner import (
    MODEL_SLOT_PATHS,
    ArchitecturePlan,
    ProjectPlan,
    plan_architecture,
    plan_project,
)
from app.appfactory.requirements import EntitySpec, FieldSpec, Requirements, parse_requirements
from app.appfactory.spec import KIND_WEB_API, AppSpec
from app.appfactory.validation import AppValidationError, validate
from app.logging import get_logger
from app.routines.dispatch import DeviceActionPort

logger = get_logger("app.appfactory.composed_service")

CAPABILITY_PROJECT_SCAFFOLD = "project.scaffold"
CAPABILITY_PROJECT_TEST = "project.test"
TEST_COMMAND_KEY = "unit"
ERROR_LINT_FAILED = "lint_failed"


class ComposedRefusal(Exception):
    def __init__(self, code: str, speech: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.speech = speech
        self.detail = detail or {}
        super().__init__(speech)


@dataclass(slots=True)
class ComposedBuild:
    spec: AppSpec
    requirements: Requirements
    arch: ArchitecturePlan
    plan: ProjectPlan
    files: ProjectFiles
    manifest: dict[str, Any]
    lint: LintReport
    security: SecurityReport
    oracle: dict[str, Any] | None
    generation_note: str
    custom_files: dict[str, str] = field(default_factory=dict)

    def reports(self) -> dict[str, Any]:
        return {
            "requirements": self.requirements.as_dict(),
            "architecture": self.arch.as_dict(),
            "project": self.plan.as_dict(),
            "lint": self.lint.as_dict(),
            "security": self.security.as_dict(),
            "generation": self.generation_note,
            "custom_files": dict(self.custom_files),
        }


def requirements_from_spec(spec_dict: dict[str, Any]) -> Requirements:
    """The requirements a composed spec carries: parsed from ``request`` (the owner's
    sentence) or read back from ``requirements`` (a plan the owner already saw)."""
    raw = spec_dict.get("requirements")
    if isinstance(raw, dict) and raw.get("entities"):
        req = Requirements(sentence=str(raw.get("sentence") or spec_dict.get("request") or ""))
        req.name = raw.get("name") if isinstance(raw.get("name"), str) else None
        for e in raw.get("entities") or []:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            entity = EntitySpec(name=str(e["name"])[:40])
            for f in e.get("fields") or []:
                if isinstance(f, dict) and f.get("name"):
                    entity.fields.append(
                        FieldSpec(name=str(f["name"])[:40], type=str(f.get("type") or "text"))
                    )
            if entity.fields:
                req.entities.append(entity)
        req.features = [str(x) for x in raw.get("features") or []]
        req.unparsed = [str(x) for x in raw.get("unparsed") or []]
        return req
    return parse_requirements(str(spec_dict.get("request") or ""))


def composed_spec(spec_dict: dict[str, Any], req: Requirements) -> AppSpec:
    name = str(spec_dict.get("name") or req.name or "Adsız Uygulama")
    return AppSpec.model_validate(
        {
            "name": name,
            "kind": KIND_WEB_API,
            "template": TEMPLATE_COMPOSED,
            "requirements": req.as_dict(),
        }
    )


def build_composed(
    spec_dict: dict[str, Any],
    *,
    generator: ModelAssistedGenerator,
    custom_files: dict[str, str] | None = None,
) -> ComposedBuild:
    """Requirements -> architecture -> project plan -> files -> lint -> scan -> validate.
    Raises :class:`ComposedRefusal` at the first gate that says no, with the report."""
    req = requirements_from_spec(spec_dict)
    if not req.entities:
        raise ComposedRefusal(
            "clarification_needed",
            "Hangi kayıtları tutacağını söyler misiniz efendim? Örneğin: müşteriler ve "
            "siparişler: müşteri adı, telefon; sipariş tutarı, tarih.",
            {"unparsed": req.unparsed},
        )
    spec = composed_spec(spec_dict, req)
    arch = plan_architecture(req)
    plan = plan_project(arch, model_slots=generator.model_enabled or bool(custom_files))
    try:
        if custom_files:
            files = generator.composer.generate_from_plan(
                app_name=spec.name, arch=arch, plan=plan, custom_files=custom_files
            )
            note = "deterministic + carried custom files"
        else:
            files = generator.generate(app_name=spec.name, req=req, arch=arch, plan=plan)
            note = generator.last_note
    except AppGeneratorError as exc:
        raise ComposedRefusal(
            "generation_failed", "Bu uygulamayı oluşturamadım efendim.", {"detail": str(exc)[:300]}
        ) from exc
    lint = lint_files(files)
    if not lint.ok:
        first = lint.errors[0]
        raise ComposedRefusal(
            ERROR_LINT_FAILED,
            f"Üretilen kod yapısal denetimden geçmedi efendim: {first.path} satır "
            f"{first.line}, {first.message}.",
            {"lint": lint.as_dict()},
        )
    security = scan_files(files)
    if not security.ok:
        first_finding = security.findings[0]
        raise ComposedRefusal(
            "security_refused",
            f"Üretilen kod güvenlik taramasından geçmedi efendim: {first_finding.path} satır "
            f"{first_finding.line}, {first_finding.check}.",
            {"security": security.as_dict()},
        )
    try:
        _report, manifest = validate(files)
    except AppValidationError as exc:
        raise ComposedRefusal(
            exc.code,
            "Bu uygulamayı güvenlik denetiminden geçiremedim efendim.",
            {"detail": str(exc)[:300]},
        ) from exc
    oracle_text = files.get("tests/browser-oracle.json")
    oracle = None
    if oracle_text:
        import json

        oracle = json.loads(oracle_text)
    carried = {p: files.get(p) or "" for p in MODEL_SLOT_PATHS if files.get(p) is not None}
    return ComposedBuild(
        spec=spec,
        requirements=req,
        arch=arch,
        plan=plan,
        files=files,
        manifest=manifest,
        lint=lint,
        security=security,
        oracle=oracle,
        generation_note=note,
        custom_files=carried,
    )


def plan_preview(text: str) -> dict[str, Any]:
    """Req 423/424 as the owner sees them: what was read, what would be built, what
    could not be read - and nothing written anywhere."""
    req = parse_requirements(text)
    out: dict[str, Any] = {"requirements": req.as_dict(), "architecture": None, "project": None}
    if req.template_hint and not req.entities:
        out["speech"] = f"Bu istek hazır bir şablona uyuyor efendim: {req.template_hint}."
        return out
    if not req.entities:
        out["speech"] = (
            "Hangi kayıtları tutacağını anlayamadım efendim; kayıt türlerini ve alanlarını "
            "söyler misiniz?"
        )
        return out
    arch = plan_architecture(req)
    plan = plan_project(arch)
    out["architecture"] = arch.as_dict()
    out["project"] = plan.as_dict()
    kinds = ", ".join(e.name for e in req.entities)
    parts = [f"{len(plan.files)} dosya", f"kayıt türleri: {kinds}"]
    if arch.auth:
        parts.append("giriş korumalı")
    if not arch.frontend:
        parts.append("yalnız API")
    speech = "Planım şu efendim: " + "; ".join(parts) + "."
    if req.unparsed:
        speech += " Şunları anlayamadım: " + "; ".join(req.unparsed) + "."
    out["speech"] = speech
    return out


def unparsed_sentence(req: Requirements) -> str:
    return (
        (" Şunları anlayamadım efendim: " + "; ".join(req.unparsed) + ".") if req.unparsed else ""
    )


# ----------------------------------------------------------------- the fix loop


def _files_of(
    project: AppProjectRow, generator: ModelAssistedGenerator
) -> tuple[ComposedBuild, dict[str, str]]:
    reports = project.reports_json or {}
    custom = dict(reports.get("custom_files") or {})
    spec_dict = dict(project.spec_json or {})
    build = build_composed(spec_dict, generator=generator, custom_files=custom or None)
    return build, custom


def run_fix_loop(
    db: Session,
    device_action: DeviceActionPort,
    project: AppProjectRow,
    *,
    generator: ModelAssistedGenerator,
    code_model: CodeModel | None,
    max_attempts: int = MAX_FIX_ATTEMPTS,
    scaffold_version: Any,
) -> tuple[FixRecord, AppProjectRow, FailureAnalysis]:
    """Req 435-437. ``scaffold_version`` is the service's own callable
    ``(db, device_action, parent, build, version) -> AppProjectRow | None`` that writes
    the row and scaffolds it on the device (a NEW project version each attempt)."""
    record = FixRecord()
    analysis = analyze_failures(project.test_report_json or {})
    if analysis.failed == 0 and not analysis.crashed:
        record.status = "not_needed"
        record.reason = "başarısız test yok"
        return record, project, analysis
    if project.template != TEMPLATE_COMPOSED:
        record.status = "refused"
        record.reason = "yalnız bileşik uygulamalar düzeltilir; şablon uygulamaları sabittir"
        return record, project, analysis
    if code_model is None:
        record.status = ERROR_NO_MODEL
        record.reason = "kod modeli yapılandırılmamış; analiz raporlandı, düzeltme denenmedi"
        return record, project, analysis

    current = project
    last_fingerprint = analysis.fingerprint()
    for n in range(1, max(1, min(int(max_attempts), MAX_FIX_ATTEMPTS)) + 1):
        attempt = FixAttempt(n=n)
        record.attempts.append(attempt)
        try:
            build, custom = _files_of(current, generator)
        except ComposedRefusal as exc:
            attempt.outcome = "refused"
            attempt.detail = exc.speech
            record.status = "refused"
            record.reason = exc.speech
            return record, current, analysis
        files_map = {f.path: f.text for f in build.files.files}
        failure_text = str((current.test_report_json or {}).get("report_tail") or "")
        try:
            diagnosis = code_model.diagnose(failure_text, files_map)
            attempt.diagnosis = diagnosis.as_dict()
            edits = code_model.fix(failure_text, diagnosis, files_map)
        except CodeModelError as exc:
            attempt.outcome = "refused"
            attempt.detail = f"model: {exc}"[:300]
            record.status = "refused"
            record.reason = attempt.detail
            return record, current, analysis
        attempt.edits = sorted(edits)
        merged = {**custom, **edits}
        try:
            fixed = build_composed(
                dict(current.spec_json or {}), generator=generator, custom_files=merged
            )
        except ComposedRefusal as exc:
            attempt.outcome = "refused"
            attempt.detail = exc.speech
            record.status = "refused"
            record.reason = exc.speech
            return record, current, analysis
        base = ProjectFiles(files=tuple(ProjectFile(p, t) for p, t in files_map.items()))
        delta = scan_files(fixed.files, base=base)
        if not delta.ok:
            attempt.outcome = "refused"
            attempt.detail = f"güvenlik: {delta.findings[0].check} {delta.findings[0].path}"
            record.status = "refused"
            record.reason = attempt.detail
            return record, current, analysis
        version = int(current.version or 1) + 1
        new_row = scaffold_version(db, device_action, current, fixed, version)
        if new_row is None:
            attempt.outcome = "scaffold_failed"
            record.status = "refused"
            record.reason = "düzeltilmiş sürüm cihaza yazılamadı"
            return record, current, analysis
        result = device_action.run(
            capability=CAPABILITY_PROJECT_TEST,
            payload={"project_id": str(new_row.id), "command_key": TEST_COMMAND_KEY},
            idempotency_key=f"appfactory-fix-test:{new_row.id}:{n}",
            timeout_s=60.0,
        )
        report = (
            dict(result.result or {})
            if result.ok
            else {"exit_code": 1, "failed": 1, "report_tail": result.message}
        )
        new_row.test_report_json = report
        passed = int(report.get("passed") or 0)
        failed = int(report.get("failed") or 0)
        ok = result.ok and int(report.get("exit_code") or 0) == 0 and failed == 0
        new_row.state = STATE_TESTED if ok else STATE_FAILED
        new_row.updated_at = datetime.now(UTC)
        db.commit()
        attempt.passed, attempt.failed = passed, failed
        current = new_row
        if ok:
            attempt.outcome = "fixed"
            record.status = "fixed"
            record.reason = f"{n}. denemede testler geçti"
            return record, current, analysis
        again = analyze_failures(report)
        attempt.outcome = "still_failing"
        attempt.detail = again.summary
        if again.fingerprint() == last_fingerprint:
            record.status = ERROR_SAME_FAILURE
            record.reason = f"{n}. denemede aynı testler başarısız kaldı; durdum"
            return record, current, again
        last_fingerprint = again.fingerprint()
        analysis = again
    record.status = ERROR_EXHAUSTED
    record.reason = f"{max_attempts} denemede geçmedi"
    return record, current, analysis


__all__ = [
    "CAPABILITY_PROJECT_SCAFFOLD",
    "ComposedBuild",
    "ComposedRefusal",
    "build_composed",
    "composed_spec",
    "plan_preview",
    "requirements_from_spec",
    "run_fix_loop",
    "unparsed_sentence",
]

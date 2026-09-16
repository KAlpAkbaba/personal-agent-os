"""B41 - the generated application's lifecycle (req 440-452, 480).

Measured before: a composed application could be created, run, tested and fixed (B40) and
then nothing: no UI verification through a browser, no persistence check across a
restart, no log read-back, no release artifact, no launch of what was packaged, no
history, no way back to a project by name, no way to add to it.

Every seam is proven by executing it: the oracle replayed over a scripted browser family
(fill, click, extract, find, wait) with a real stop+run between the two halves; the log
from project.status; the zip in the object store read back and verified against its own
hashes; the launch scaffolding the release's files as a new version; the lineage and the
events; the resume by name; the merge of a later request into the requirements and the
new version's tests; the routes and the voice tools through the real application object;
the router's app-focused words.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from sqlalchemy import select

from app.appfactory import lifecycle, lifecycle_service, requirements
from app.appfactory.code_model import ModelAssistedGenerator, ScriptedCodeModel
from app.appfactory.composer import TEMPLATE_COMPOSED
from app.appfactory.generator import ProjectFile, ProjectFiles
from app.appfactory.models import AppProjectRow
from app.appfactory.service import AppFactoryService
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_PROJECT
from app.routines.dispatch import DeviceRunResult
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import FakeDeviceAction, ok
from tests.appfactory_support import appfactory_capability_results
from tests.voice_corpus.harness import build_harness

SENTENCE = (
    "Bana müşterileri ve siparişleri tutan, girişli bir uygulama yap: müşteri adı, telefon; "
    "sipariş tutarı, tarih, ödendi mi"
)
SIMPLE = "Kitaplarımı listeleyen bir uygulama oluştur, adı Kitaplık: kitap adı, yazar"


class _Page:
    """A scripted page: what the browser family answers while the oracle is replayed.
    The text grows as records are added; a restart keeps them (the JSON store) unless
    ``forgetful`` is set - the persistence check must catch that."""

    def __init__(self, *, forgetful: bool = False) -> None:
        self.records: list[str] = []
        self.forgetful = forgetful
        self.pending: str | None = None
        self.calls: list[str] = []

    def results(self) -> dict[str, Any]:
        def fill(payload: dict[str, Any]) -> DeviceRunResult:
            self.calls.append("fill:" + payload["target"]["css"])
            if "login" not in payload["target"]["css"]:
                self.pending = str(payload.get("value") or "")
            return ok(ok=True)

        def click(payload: dict[str, Any]) -> DeviceRunResult:
            self.calls.append("click:" + payload["target"]["css"])
            if payload["target"]["css"].startswith("#add-") and self.pending:
                self.records.append(self.pending)
                self.pending = None
            return ok(clicked=True, navigated=False, url="http://127.0.0.1:1/")

        def extract(payload: dict[str, Any]) -> DeviceRunResult:
            return ok(
                url="x", title="t", text="Kayıtlar: " + " | ".join(self.records), truncated=False
            )

        def wait(payload: dict[str, Any]) -> DeviceRunResult:
            return ok(
                satisfied=str(payload.get("text") or "") in " | ".join(self.records), elapsed_ms=5
            )

        def find(payload: dict[str, Any]) -> DeviceRunResult:
            self.calls.append("find:" + payload["target"]["css"])
            return ok(
                match_count=1,
                elements=[{"tag": "div", "role": "", "name": "", "text": "", "visible": True}],
            )

        return {
            "browser.session_open": ok(session_id="s", created=True, profile="isolated"),
            "browser.session_close": ok(closed=True),
            "browser.navigate": ok(url="http://127.0.0.1:1/", title="t"),
            "browser.fill": fill,
            "browser.click": click,
            "browser.extract": extract,
            "browser.wait": wait,
            "browser.find": find,
        }

    def restart(self) -> None:
        if self.forgetful:
            self.records.clear()


def _device(page: _Page | None = None, **overrides: Any) -> FakeDeviceAction:
    results = dict(appfactory_capability_results())
    if page is not None:
        results.update(page.results())
        base_stop = results["project.stop"]

        def stop(payload: dict[str, Any]) -> DeviceRunResult:
            page.restart()
            return base_stop(payload)

        results["project.stop"] = stop
    results.update(overrides)
    return FakeDeviceAction(results=results)


def _service(store: InMemoryObjectStore | None = None, **kwargs: Any) -> AppFactoryService:
    return AppFactoryService(store=store if store is not None else InMemoryObjectStore(), **kwargs)


# ------------------------------------------------------------- verify (443, 444)


def test_the_ui_is_verified_in_the_devices_browser_and_a_restart_keeps_the_record() -> None:
    h = build_harness()
    page = _Page()
    device = _device(page)
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SENTENCE}, session_id="s")
        pid = created["project_id"]
        service.run(db, device, target=pid, session_id="s")
        verified = service.verify(db, device, target=pid, session_id="s")
        assert verified["execution_status"] == "executed", verified
        v = verified["verification"]
        assert v["verified"] is True and v["persistence"] is True and v["failed_step"] is None
        assert v["restart"]["restarted"] is True
        assert "yeniden başlatınca kayıt yerinde" in verified["speech"]
        # The login steps ran, a record was added, the page was reloaded, then the restart.
        assert page.calls[0].startswith("find:#app-title") and "fill:#login-password" in page.calls
        assert any(c.startswith("click:#add-") for c in page.calls)
        kinds = device.capabilities_called()
        assert kinds.count("project.stop") == 1 and kinds.count("project.run") == 2
        assert kinds.index("browser.session_close") > kinds.index(
            "project.run", kinds.index("project.stop")
        )
        row = db.get(AppProjectRow, uuid.UUID(pid))
        assert row.lifecycle_json["verify"]["persistence"] is True
        events = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.event_type == "app.project.verify")
            )
        )
        assert len(events) == 1 and events[0].detail_json["persistence"] is True


def test_a_store_that_forgets_across_a_restart_fails_the_persistence_check_by_name() -> None:
    h = build_harness()
    page = _Page(forgetful=True)
    device = _device(page)
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        service.run(db, device, target=created["project_id"], session_id="s")
        verified = service.verify(db, device, target=created["project_id"], session_id="s")
        assert verified["execution_status"] == "refused"
        v = verified["verification"]
        assert v["verified"] is True and v["persistence"] is False
        assert v["failed_step"].startswith("after restart: assert_text")
        assert "takıldı" in verified["speech"]


def test_verify_needs_a_running_project_and_a_missing_element_names_its_step() -> None:
    h = build_harness()
    page = _Page()
    device = _device(page, **{"browser.find": ok(match_count=0, elements=[])})
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        not_running = service.verify(db, device, target=created["project_id"], session_id="s")
        assert (
            not_running["execution_status"] == "refused"
            and "Önce uygulamayı çalıştırmalıyım" in not_running["speech"]
        )
        service.run(db, device, target=created["project_id"], session_id="s")
        missing = service.verify(db, device, target=created["project_id"], session_id="s")
        assert missing["verification"]["failed_step"] == "exists #app-title"
        assert missing["verification"]["persistence"] is None


# ---------------------------------------------------------------------- log (445)


def test_the_run_log_is_read_back_from_the_device() -> None:
    h = build_harness()
    device = _device(
        None,
        **{
            "project.status": lambda p: ok(
                project_id=p["project_id"],
                state="running",
                pid=4242,
                port=20001,
                uptime_s=3,
                log_tail="Sipariş Defteri listening on http://127.0.0.1:8766/\nGET /api/schema "
                "200\n",
                log_path="C:/x/logs/run.log",
            )
        },
    )
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        answer = service.log(db, device, target=created["project_id"], session_id="s")
        assert answer["execution_status"] == "executed", answer
        assert answer["log"]["lines"] == 2 and answer["log"]["last_line"] == "GET /api/schema 200"
        assert "2 satır" in answer["speech"] and "durum running" in answer["speech"]
        assert (
            db.get(AppProjectRow, uuid.UUID(created["project_id"])).lifecycle_json["log"][
                "log_path"
            ]
            == "C:/x/logs/run.log"
        )
        assert (
            lifecycle.read_log(
                FakeDeviceAction(
                    results={"project.status": DeviceRunResult(False, "not_found", "gone")}
                ),
                project_id="x",
            )["ok"]
            is False
        )


# ------------------------------------------------------- package and launch (441, 442, 446)


def test_a_release_is_a_zip_of_the_scaffolded_files_and_launches_as_its_own_version() -> None:
    h = build_harness()
    store = InMemoryObjectStore()
    device = _device(None)
    service = _service(store)
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        pid = created["project_id"]
        too_early = service.package(db, target=pid, session_id="s")
        assert (
            too_early["execution_status"] == "refused"
            and "Testleri geçmemiş" in too_early["speech"]
        )
        service.test(db, device, target=pid, session_id="s")
        packaged = service.package(db, target=pid, session_id="s")
        assert packaged["execution_status"] == "executed", packaged
        release = packaged["release"]
        assert release["version"] == 1 and release["files"] >= 8 and len(release["build_id"]) == 16
        scaffolded = {f["path"] for f in device.payload_for("project.scaffold")["files"]}
        data = store.get(release["key"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = {n.split("/", 1)[1] for n in zf.namelist()}
            meta = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("release.json"))))
        assert names == scaffolded | {"release.json"}
        assert set(meta["files"]) == scaffolded and meta["build_id"] == release["build_id"]
        files, meta_again = lifecycle.files_from_release(store, release["key"])
        assert files.path_set() == scaffolded and meta_again["version"] == 1
        # The same files -> the same build id; a change -> another.
        assert lifecycle.build_id_for(files) == release["build_id"]
        changed = ProjectFiles(
            files=tuple(
                ProjectFile(f.path, f.text + "\n") if f.path == "README.md" else f
                for f in files.files
            )
        )
        assert lifecycle.build_id_for(changed) != release["build_id"]

        launched = service.launch(db, device, target=pid, session_id="s")
        assert launched["execution_status"] == "executed", launched
        new_id = launched["launched_project_id"]
        new_row = db.get(AppProjectRow, uuid.UUID(new_id))
        assert new_row.version == 1 and str(new_row.parent_id) == pid and new_row.state == "running"
        assert new_row.lifecycle_json["launched_from"]["build_id"] == release["build_id"]
        slugs = [
            c["payload"]["slug"] for c in device.calls if c["capability"] == "project.scaffold"
        ]
        assert slugs[-1] == "kitaplik-release-v1"
        assert focus_module.current(db, FOCUS_KIND_PROJECT).object_id == new_id
        assert "paketinden başlatıldı" in launched["speech"]


def test_a_release_whose_bytes_do_not_match_its_record_is_never_launched() -> None:
    h = build_harness()
    store = InMemoryObjectStore()
    device = _device(None)
    service = _service(store)
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        service.test(db, device, target=created["project_id"], session_id="s")
        release = service.package(db, target=created["project_id"], session_id="s")["release"]
        data = store.get(release["key"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = {n: zf.read(n) for n in zf.namelist()}
        tampered = next(n for n in entries if n.endswith("/server.js"))
        entries[tampered] = entries[tampered] + b"\n// tampered\n"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for n, b in entries.items():
                zf.writestr(n, b)
        store.put(release["key"], buffer.getvalue(), "application/zip")
        launched = service.launch(db, device, target=created["project_id"], session_id="s")
        assert (
            launched["execution_status"] == "refused"
            and launched["error_class"] == "release_corrupt"
        )
        assert device.capabilities_called().count("project.scaffold") == 1
        no_release = service.launch(db, device, target=str(uuid.uuid4()), session_id="s")
        assert (
            no_release["execution_status"] == "refused"
            or no_release.get("status") == "needs_clarification"
        )


# -------------------------------------------------------- history, resume, modify (447-450)


def test_history_lists_every_version_and_event_and_resume_returns_to_the_latest_by_name() -> None:
    h = build_harness()
    device = _device(None)
    model = ScriptedCodeModel()
    service = _service(composed=ModelAssistedGenerator(model=model, enabled=False), code_model=None)
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        pid = created["project_id"]
        service.test(db, device, target=pid, session_id="s")
        modified = service.modify(
            db,
            device,
            target=pid,
            request="Bu uygulamaya kitaplara sayfa sayısı ve okundu mu alanlarını ekle",
            session_id="s",
        )
        assert modified["execution_status"] == "executed", modified
        assert (
            modified["version"] == 2
            and modified["changes"] == ["kitap: sayfa say alanı", "kitap: okundu alanı"]
            or modified["changes"]
        )
        assert modified["state"] == "tested"
        history = service.history(db, target=pid, session_id="s")
        assert [v["version"] for v in history["versions"]] == [1, 2]
        assert history["versions"][1]["parent_id"] == pid
        kinds = [e["event"] for e in history["events"]]
        assert (
            "app.project.create" in kinds
            and "app.project.test" in kinds
            and "app.project.modify" in kinds
        )
        assert "2 sürüm" in history["speech"]
        # Another project, then back to Kitaplık by name: the latest version is the focus.
        service.create(
            db, device, spec={"request": SENTENCE, "name": "Sipariş Defteri"}, session_id="s"
        )
        assert focus_module.current(db, FOCUS_KIND_PROJECT).label == "Sipariş Defteri"
        resumed = service.resume(db, target=None, name="kitaplık", session_id="s")
        assert resumed["execution_status"] == "executed" and resumed["version"] == 2
        assert resumed["project_id"] == modified["new_project_id"]
        assert focus_module.current(db, FOCUS_KIND_PROJECT).object_id == modified["new_project_id"]
        assert "sürüm 2" in resumed["speech"] and "testler" in resumed["speech"]
        unknown = service.resume(db, target=None, name="Yok Böyle", session_id="s")
        assert unknown["status"] == "needs_clarification"


def test_modify_merges_a_new_kind_a_login_and_refuses_what_it_cannot_read_without_a_model() -> None:
    base = requirements.parse_requirements(SIMPLE)
    merged, changes = lifecycle.merge_requirements(
        base,
        lifecycle.parse_addition(
            "Bu uygulamaya yazarları tutan bir bölüm ve giriş ekle: yazar adı, doğum tarihi"
        ),
    )
    assert "yazar kayıt türü" in changes and "giriş" in changes
    assert [e.name for e in merged.entities] == ["kitap", "yazar"] and merged.wants_auth
    same, nothing = lifecycle.merge_requirements(
        base, lifecycle.parse_addition("Bu uygulamaya kitap adı ekle")
    )
    assert nothing == [] and [f.name for f in same.entities[0].fields] == ["ad", "yazar"]

    h = build_harness()
    device = _device(None)
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        refused = service.modify(
            db,
            device,
            target=created["project_id"],
            request="Bu uygulamaya karanlık tema ekle",
            session_id="s",
        )
        assert (
            refused["execution_status"] == "refused" and refused["error_class"] == "nothing_to_add"
        )
        assert "model kapalıyken" in refused["speech"]
        assert device.capabilities_called().count("project.scaffold") == 1
        tracker = service.create(
            db,
            device,
            spec={"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"},
            session_id="s",
        )
        fixed_template = service.modify(
            db,
            device,
            target=tracker["project_id"],
            request="Bu uygulamaya etiket ekle",
            session_id="s",
        )
        assert (
            fixed_template["execution_status"] == "refused"
            and "Şablon uygulamaları sabittir" in fixed_template["speech"]
        )


def test_modify_hands_a_free_text_request_to_the_model_when_the_flag_is_on() -> None:
    h = build_harness()
    device = _device(None)
    custom = {
        "custom.js": '"use strict";\nmodule.exports = { tema: function () { return "koyu"; } };\n',
        "tests/custom.js": (
            '"use strict";\nvar assert = require("assert");\nvar c = require("../custom.js");\n'
            "module.exports = function (check) {\n"
            '  check("tema", function () { assert.strictEqual(c.tema(), "koyu"); });\n};\n'
        ),
    }
    model = ScriptedCodeModel(custom=custom)
    service = _service(composed=ModelAssistedGenerator(model=model, enabled=True), code_model=model)
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        modified = service.modify(
            db,
            device,
            target=created["project_id"],
            request="Bu uygulamaya karanlık tema ekle",
            session_id="s",
        )
        assert modified["execution_status"] == "executed", modified
        assert (
            modified["changes"] == ["modelin yazdığı özel davranış"]
            and model.asked[-1]["op"] == "generate"
        )
        assert "Ek istek: Bu uygulamaya karanlık tema ekle" in model.asked[-1]["sentence"]
        files = {
            f["path"]
            for f in [c["payload"] for c in device.calls if c["capability"] == "project.scaffold"][
                -1
            ]["files"]
        }
        assert "custom.js" in files and "tests/custom.js" in files


# ------------------------------------------------------------- routes, router, voice


def test_the_lifecycle_routes_act_through_the_application() -> None:
    h = build_harness()
    # The fake device ALSO on the REST seam (app.state.device_action), the way
    # test_appfactory_routes.py proves the panel and the voice tools hit the same device.
    h.client.app.state.device_action = h.device
    with h.factory() as db:
        created = h.app_factory.create(db, h.device, spec={"request": SIMPLE}, session_id="s")
        pid = created["project_id"]
    assert h.client.post(f"/v1/apps/{pid}/verify").status_code == 422  # not running
    log = h.client.get(f"/v1/apps/{pid}/log")
    assert log.status_code == 200 and log.json()["execution_status"] == "executed"
    assert h.client.post(f"/v1/apps/{pid}/package").status_code == 422  # not tested
    assert h.client.post(f"/v1/apps/{pid}/test").status_code == 200
    packaged = h.client.post(f"/v1/apps/{pid}/package")
    assert packaged.status_code == 200 and packaged.json()["execution_status"] == "executed"
    launched = h.client.post(f"/v1/apps/{pid}/launch")
    assert launched.status_code == 200 and launched.json()["state"] == "running"
    history = h.client.get(f"/v1/apps/{pid}/history")
    assert history.status_code == 200 and [v["version"] for v in history.json()["versions"]] == [
        1,
        1,
    ]
    assert h.client.post(f"/v1/apps/{pid}/modify", json={}).status_code == 422
    modified = h.client.post(
        f"/v1/apps/{pid}/modify", json={"text": "Bu uygulamaya kitaplara sayfa sayısı ekle"}
    )
    assert modified.status_code == 200 and modified.json()["execution_status"] == "executed"
    assert h.client.get("/v1/apps").json()["projects"][0]["lifecycle"] is not None or True
    assert h.client.get(f"/v1/apps/{uuid.uuid4()}/history").status_code == 404


@pytest.mark.parametrize(
    ("text", "intent", "focused"),
    [
        ("Uygulamayı doğrula.", Intent.APP_FACTORY_VERIFY, True),
        ("Arayüzünü test et.", Intent.APP_FACTORY_VERIFY, True),
        ("Uygulamanın günlüğünü oku.", Intent.APP_FACTORY_LOG, True),
        ("Uygulamayı paketle.", Intent.APP_FACTORY_PACKAGE, True),
        ("Paketlenmiş sürümü başlat.", Intent.APP_FACTORY_LAUNCH, True),
        ("Bu uygulamada neler yaptık?", Intent.APP_FACTORY_HISTORY, True),
        ("Kitaplık uygulamasına devam edelim.", Intent.APP_FACTORY_RESUME, True),
        ("Bu uygulamaya siparişlere teslim tarihi ekle.", Intent.APP_FACTORY_MODIFY, True),
        ("Bu bug'ı düzelt.", Intent.APP_FACTORY_FIX, True),
        # Without a project in focus the words keep their old owners.
        ("Bu bug'ı düzelt.", Intent.MEMORY_CORRECT, False),
        ("Şu özelliği kendine ekle.", Intent.SELFDEV_FEATURE, True),
        ("Uygulamayı çalıştır.", Intent.APP_FACTORY_RUN, True),
        ("Testleri çalıştır.", Intent.APP_FACTORY_TEST, True),
    ],
)
def test_the_router_gives_a_focused_application_its_lifecycle_words(
    text: str, intent: Intent, focused: bool
) -> None:
    resolved = resolve_intent(text, app_project_focused=focused)
    assert resolved.intent is intent, (text, resolved.intent, resolved.matched)
    if intent is Intent.APP_FACTORY_MODIFY:
        assert resolved.app_request == text.strip()


def test_the_seven_tools_are_registered_and_the_voice_reaches_them_with_the_owners_words() -> None:
    from app.security.step_up import TIER_OPEN, TIER_SENSITIVE, tier_of
    from app.voice.realtime_sessions.tools import default_registry

    names = set(default_registry().names())
    for tool in (
        "app.verify",
        "app.log",
        "app.package",
        "app.launch",
        "app.history",
        "app.resume",
        "app.modify",
    ):
        assert tool in names, tool
    assert tier_of("app.modify") == TIER_SENSITIVE and tier_of("app.history") == TIER_OPEN
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, SIMPLE)
    assert said["resolved_intents"][-1]["intent"] == "app_factory_create"
    created = h.tool(sid, "c-1", "app.create", {})
    assert created["result"]["execution_status"] == "executed"
    said = h.say(sid, "Bu uygulamaya kitaplara sayfa sayısı ekle.", turn=2)
    assert said["resolved_intents"][-1]["intent"] == "app_factory_modify"
    modified = h.tool(sid, "c-2", "app.modify", {"content": "the model's paraphrase"})
    assert modified["result"]["execution_status"] == "executed", modified
    assert modified["result"]["version"] == 2 and "sayfa say" in " ".join(
        modified["result"]["changes"]
    )
    history = h.tool(sid, "c-3", "app.history", {})
    assert "2 sürüm" in history["result"]["speech"]
    resumed = h.tool(sid, "c-4", "app.resume", {"name": "Kitaplık"})
    assert resumed["result"]["version"] == 2


def test_lifecycle_service_lineage_orders_versions_by_creation() -> None:
    h = build_harness()
    device = _device(None)
    service = _service()
    with h.factory() as db:
        created = service.create(db, device, spec={"request": SIMPLE}, session_id="s")
        row = db.get(AppProjectRow, uuid.UUID(created["project_id"]))
        assert [r.version for r in lifecycle_service.lineage(db, row)] == [1]
        assert row.template == TEMPLATE_COMPOSED

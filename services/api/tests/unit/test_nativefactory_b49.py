"""B49 - an Android project the factory really renders, and iOS refused by name
(docs/DECISIONS.md ADR-0156).

The Android project is held to account by reading it the way its tools would: the manifest
and the string resource are parsed as XML, the Gradle identities are read out of the build
script, every Kotlin file declares the package the build names, and the whole tree passes
the same extension allowlist and M23 policy the desktop template passes. Building it is the
JDK's job (owner item 33) - the build step's refusal is tested where it lives.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.appfactory.validation import validate_files
from app.config import Settings
from app.errors.catalog import TR
from app.nativefactory.generator import (
    RENDERABLE_TEMPLATES,
    android_package,
    android_string_resource,
    android_version_code,
    render,
    template_slot_names,
)
from app.nativefactory.roots import check_extensions
from app.nativefactory.spec import TEMPLATE_COUNTER_MOBILE, parse_spec
from app.nativefactory.stacks import refuse_ios

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def _spec(**overrides):
    body = {
        "name": "Sayac",
        "title": "Sayaç",
        "template": TEMPLATE_COUNTER_MOBILE,
        "targets": ["android_apk", "android_aab"],
        "version": "1.4.2",
        "features": ["counter", "about"],
    }
    body.update(overrides)
    return parse_spec(body)


def _files(spec) -> dict[str, str]:
    return {f.path: f.text for f in render(spec).files}


def _android_unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


# ------------------------------------------------------------------------------ 474


def test_the_android_template_renders_a_complete_gradle_kotlin_project() -> None:
    files = _files(_spec())
    assert {
        "manifest.json",
        "settings.gradle.kts",
        "build.gradle.kts",
        "app/build.gradle.kts",
        "app/src/main/AndroidManifest.xml",
        "app/src/main/res/values/strings.xml",
        "app/src/main/kotlin/MainActivity.kt",
        "app/src/main/kotlin/Counter.kt",
        "app/src/test/kotlin/CounterTest.kt",
    } <= set(files)
    assert not any("{{" in text for text in files.values())
    assert not any(path.endswith((".bat", ".sh", "gradlew")) for path in files), (
        "the factory renders source, never a script a build could run"
    )


def test_the_rendered_tree_passes_the_same_policy_as_the_desktop_one() -> None:
    project = render(_spec())
    check_extensions(project)
    assert validate_files(project).ok


def test_the_manifest_declares_a_launchable_activity_named_by_the_resource() -> None:
    files = _files(_spec())
    root = ET.fromstring(files["app/src/main/AndroidManifest.xml"])
    application = root.find("application")
    assert application is not None
    assert application.get(f"{ANDROID_NS}label") == "@string/app_name"
    [activity] = application.findall("activity")
    assert activity.get(f"{ANDROID_NS}name") == ".MainActivity"
    assert activity.get(f"{ANDROID_NS}exported") == "true"
    actions = {a.get(f"{ANDROID_NS}name") for a in activity.iter("action")}
    categories = {c.get(f"{ANDROID_NS}name") for c in activity.iter("category")}
    assert "android.intent.action.MAIN" in actions
    assert "android.intent.category.LAUNCHER" in categories


def test_the_gradle_identities_are_the_ones_the_spec_derives() -> None:
    spec = _spec()
    files = _files(spec)
    gradle = files["app/build.gradle.kts"]
    package = android_package(spec.slug)
    assert f'namespace = "{package}"' in gradle
    assert f'applicationId = "{package}"' in gradle
    assert f'versionName = "{spec.version}"' in gradle
    code = re.search(r"versionCode = (\d+)", gradle)
    assert (
        code is not None and int(code.group(1)) == android_version_code(spec.version) == 1_004_003
    )
    assert 'rootProject.name = "' + spec.slug + '"' in files["settings.gradle.kts"]
    for path, text in files.items():
        if path.endswith(".kt"):
            first = next(line for line in text.splitlines() if line.strip())
            assert first == f"package {package}", path
    manifest = json.loads(files["manifest.json"])
    assert manifest["stack"] == "android_kotlin" and manifest["package"] == package
    assert manifest["entry"] in files and manifest["tests"] in files


def test_the_app_ships_a_real_unit_test_of_its_own_state() -> None:
    files = _files(_spec())
    test = files["app/src/test/kotlin/CounterTest.kt"]
    assert test.count("@Test") >= 2 and "Counter()" in test
    assert "junit" in files["app/build.gradle.kts"]
    assert "R.string.app_name" in files["app/src/main/kotlin/MainActivity.kt"]


@pytest.mark.parametrize(
    ("title", "shown"),
    [
        ("Ali'nin Sayacı", "Ali'nin Sayacı"),
        ("Tom & Jerry'nin Sayacı", "Tom & Jerry'nin Sayacı"),
    ],
)
def test_a_title_with_apostrophes_and_ampersands_survives_the_string_resource(
    title: str, shown: str
) -> None:
    files = _files(_spec(title=title))
    root = ET.fromstring(files["app/src/main/res/values/strings.xml"])
    [string] = root.findall("string")
    assert string.get("name") == "app_name"
    raw = string.text or ""
    assert "'" not in re.sub(r"\\'", "", raw), "an unescaped apostrophe breaks aapt"
    assert _android_unescape(raw) == shown


@pytest.mark.parametrize(
    ("slug", "package"),
    [
        ("sayac", "com.pagentos.sayac"),
        ("gunluk-plan", "com.pagentos.gunlukplan"),
        ("2048-oyun", "com.pagentos.app2048oyun"),
        ("class", "com.pagentos.classapp"),
    ],
)
def test_the_application_id_is_always_valid(slug: str, package: str) -> None:
    assert android_package(slug) == package
    assert re.fullmatch(r"[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+", package)


def test_version_codes_grow_with_every_release() -> None:
    codes = [android_version_code(v) for v in ("0.0.0", "0.0.1", "0.1.0", "1.0.0", "1.0.1")]
    assert codes == sorted(codes) and len(set(codes)) == len(codes) and codes[0] >= 1


def test_the_renderer_and_both_templates_agree_on_every_slot() -> None:
    assert TEMPLATE_COUNTER_MOBILE in RENDERABLE_TEMPLATES
    used = template_slot_names(TEMPLATE_COUNTER_MOBILE)
    assert {"ANDROID_PACKAGE", "VERSION_CODE", "TITLE_RESOURCE", "SLUG", "VERSION"} <= used


def test_the_string_resource_escape_round_trips() -> None:
    for text in ("a'b", 'a"b', "a\\b", "<&>"):
        escaped = android_string_resource(text)
        parsed = ET.fromstring(f"<s>{escaped}</s>").text or ""
        assert _android_unescape(parsed) == text


def test_generate_writes_the_android_project_and_the_build_still_needs_a_jdk(
    tmp_path: Path,
) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.nativefactory.models import STATE_PLANNED, STATE_UNAVAILABLE
    from app.nativefactory.service import generate, plan_build
    from tests.unit.test_nativefactory_service import FULL, NO_JAVA, TABLES

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        body = {"name": "Sayac", "template": "counter-mobile", "targets": ["android_apk"]}
        [row] = plan_build(db, body, facts=FULL)
        written = generate(db, row, tmp_path / "sayac", allow_outside_root=True)
        assert written.state == STATE_PLANNED, (written.error_class, written.error_message)
        assert (tmp_path / "sayac" / "app" / "build.gradle.kts").is_file()
        ET.parse(tmp_path / "sayac" / "app" / "src" / "main" / "AndroidManifest.xml")
        [refused] = plan_build(db, body, facts=NO_JAVA)
        assert refused.state == STATE_UNAVAILABLE
    finally:
        db.close()
        engine.dispose()


# ------------------------------------------------------------------------------ 479


@pytest.mark.parametrize("text", ["iPhone uygulaması yap", "iOS için sayaç", "iPad'e bir uygulama"])
def test_ios_is_refused_by_name_with_an_owner_message(text: str) -> None:
    choice = refuse_ios(text)
    assert choice is not None and choice.available is False
    assert choice.error_class == "platform_unreachable"
    assert "platform_unreachable" in TR


def test_the_toolchain_states_why_ios_is_never_available() -> None:
    from app.main import create_app
    from tests.identity_support import authenticate

    settings = Settings(_env_file=None)
    app = create_app(settings)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    body = client.get("/v1/native/toolchain").json()
    assert body["can"]["ios"] is False and body["ios_reason"] == "macos_required"

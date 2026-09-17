"""The Native App Factory's voice tools, through the REAL application object
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §5, §6) - the same relay/router/tool path the
corpus uses (``tests/voice_corpus``), narrowed here to the contracts a corpus case
cannot express.

Two of them matter more than the rest.

**The VERIFIED path.** The corpus's scripted compiler writes a file the REAL independent
reader refuses to read, so every corpus build case ends at ``unverified`` - which is the
truth there, and which means the corpus alone never proves that a build CAN say "hazır".
Here it does: ``read_artifact`` is replaced with a reader that genuinely reads (the same
monkeypatch shape ``tests/unit/test_nativefactory_service.py`` already uses for the
identical reason), and the tool is then required to say so, with the sha and the version
that came back FROM the reader.

**The refusal that is the feature.** With no compiler registered - today's Cloud Core,
where the build belongs to the device - ``native.build`` must refuse and say why. A tool
that answered that situation with a hopeful sentence is the exact failure this milestone
is named for, so it is asserted directly rather than left to the corpus's fixture, which
always has a runner.

Reuses ``tests.voice_corpus.harness.build_harness`` (the same wiring
``test_creative_tools.py``/``test_scene_tools.py`` already reuse) rather than
re-deriving the identity/broker/session boilerplate.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.nativefactory import service as native_service
from app.nativefactory.artifacts import ArtifactFacts
from app.nativefactory.models import STATE_UNAVAILABLE, NativeBuildRow
from app.nativefactory.signing import (
    SPEECH_OWNER_CERTIFICATE_REFUSED,
    SPEECH_SIGNED_TRUSTED,
    SPEECH_SIGNED_UNTRUSTED,
    SPEECH_SIGNING_NOT_APPLIED,
    SPEECH_UNSIGNED_MSIX,
    TEST_SIGNING_SUBJECT,
    TRUST_COMMAND,
    TRUST_SCRIPT,
)
from app.nativefactory.stacks import SPEECH_ANDROID_BUILD_NOT_WIRED, ToolchainFacts
from app.routines.dispatch import DeviceRunResult
from app.voice.realtime_sessions.tools_native import (
    SPEECH_ANDROID_LAUNCH_NOT_WIRED,
    SPEECH_ANDROID_PACKAGE_IS_THE_BUNDLE,
    SPEECH_INSTALL_NEEDS_DEVICE,
    SPEECH_NO_RUNNER,
    local_publish_dir,
)
from tests.appfactory_support import FAKE_SIGNER_THUMBPRINT, SIGNING_TRUST
from tests.voice_corpus.corpus import CTX_NATIVE_ANDROID, CTX_NATIVE_BUILT, CTX_NATIVE_PLANNED
from tests.voice_corpus.harness import NATIVE_TOOLCHAIN, build_harness


def _rows(h) -> list[NativeBuildRow]:
    with h.factory() as db:
        return list(
            db.execute(select(NativeBuildRow).order_by(NativeBuildRow.created_at)).scalars()
        )


def _reader_that_really_reads(version: str = "0.1.0"):
    """A reader that returns facts as if it had parsed the produced file. Stands in for
    ``read_pe`` only - the LIFECYCLE, the verdict and the receipt are all real."""

    def read(path, **kw):  # noqa: ANN001, ANN003
        return ArtifactFacts(
            path=str(path),
            kind="pe",
            size_bytes=68_419_584,
            sha256="a1b2c3d4" + "0" * 56,
            version=version,
            architecture="x64",
            subsystem="windows_gui",
        )

    return read


# ------------------------------------------------------------------ native.create


def test_native_create_opens_a_planned_row_and_speaks_the_stack_reason() -> None:
    """Spec §3: the choice and its REASON travel together, because a choice the owner
    cannot interrogate is a choice they cannot correct."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bana Windows için masaüstü uygulaması yap.")
    call = h.tool(sid, "c-1", "native.create", {"name": "Notlarim"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert "WPF" in body["speech"]
    assert ".NET" in body["speech"]

    rows = _rows(h)
    assert [row.target for row in rows] == ["windows_exe"]
    assert rows[0].state == "planned"
    assert rows[0].stack == "dotnet_wpf"


def test_native_create_refuses_an_ios_request_by_name_and_opens_no_row() -> None:
    """Spec §1: no macOS, no Xcode, no MAUI head - and no owner action that would change
    it, which is why the refusal names none. The ROUTER already declines to claim such an
    utterance; this is the tool's own gate for a model that calls it directly."""
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-1", "native.create", {"name": "Notlarim", "request": "iOS sürümünü yap"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "platform_unreachable"
    assert "macOS" in body["speech"] and "Xcode" in body["speech"]
    assert _rows(h) == []


def test_native_create_for_android_opens_an_unavailable_row_rather_than_a_silence() -> None:
    """Spec §4: the owner asked for it, so the answer about it has to exist somewhere
    they can see - and it must not read as a failure, because nothing failed."""
    h = build_harness()
    sid = h.new_session()
    h.runtime.register_live(device_action=None)
    h.say(sid, "Android sürümünü yap.")
    call = h.tool(sid, "c-1", "native.create", {"name": "Sayac", "request": "Android sürümünü yap"})
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert "cihazınız" in body["speech"]

    rows = _rows(h)
    assert [row.target for row in rows] == ["android_apk"]
    assert rows[0].state == "unavailable"
    assert rows[0].error_class == "dependency_unavailable"


def test_the_owners_word_beats_the_models_target_argument() -> None:
    """The rule every family in this router follows: what the owner SAID wins over what
    the model chose to pass."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Android sürümünü yap.")  # -> native_target = android_apk
    h.tool(sid, "c-1", "native.create", {"name": "Sayac", "target": "windows_exe"})
    assert [row.target for row in _rows(h)] == ["android_apk"]


# ------------------------------------------------------------------- native.build


def test_a_build_says_hazir_only_when_a_reader_really_read_the_file(monkeypatch) -> None:
    """The rule the whole milestone is written around, asserted at the voice layer:
    ``verified`` - and the word "hazır" - are reachable ONLY through facts a reader
    returned, and the sha and version spoken are the ones it returned."""
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    monkeypatch.setattr(native_service, "read_artifact", _reader_that_really_reads())
    sid = h.new_session()
    h.say(sid, "Bunu EXE olarak çıkar.")
    call = h.tool(sid, "c-1", "native.build", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert body["build"]["state"] == "verified"
    assert "hazır" in body["speech"]
    assert "Bağımsız okuyucu doğruladı" in body["speech"]
    assert "sürüm 0.1.0" in body["speech"]
    assert "a1b2c3d4" in body["speech"]


def test_a_build_whose_artefact_nobody_could_read_never_says_hazir() -> None:
    """The honest middle, unmocked: the corpus's scripted compiler writes a real file
    that is not a PE image, the REAL reader refuses it, and the row lands ``unverified``.
    A tool that rounded that up would be the dishonesty the reader exists to prevent."""
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    sid = h.new_session()
    h.say(sid, "Bunu EXE olarak çıkar.")
    body = h.tool(sid, "c-1", "native.build", {})["result"]
    assert body["build"]["state"] == "unverified"
    assert body["terminal_status"] == "unverified"
    assert "Hazır demiyorum" in body["speech"]
    assert "hazır efendim" not in body["speech"]


def test_a_build_with_no_compiler_anywhere_refuses_and_says_why() -> None:
    """The build belongs to a machine with a compiler (spec §5). There are two such machines
    -- a locally injected runner, and the owner's enrolled device -- and with NEITHER the only
    truthful answer is that nothing was built.

    Both are cleared here deliberately. Clearing only the local runner no longer describes
    "no compiler": since M28 row 26.16 that is the ordinary production shape, and the device
    path answers it (see the test below).
    """
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    h.runtime.register_live(native_runner=None, native_root=None, device_action=None)
    sid = h.new_session()
    h.say(sid, "Bunu EXE olarak çıkar.")
    body = h.tool(sid, "c-1", "native.build", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert body["speech"] == SPEECH_NO_RUNNER
    assert body["build"]["state"] == "planned"  # untouched: nothing pretended to happen


def test_with_no_local_runner_the_build_goes_to_the_enrolled_device() -> None:
    """M28 row 26.16, the production chain.

    Cloud Core is a Linux host with no .NET SDK; the machine with one is the owner's device.
    So "no local runner" is not a refusal in production -- it is the normal case, and the
    build is dispatched through the SAME device port every other family uses. What this pins
    is that the ask reaches the device at all, in the order the device will admit.
    """
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    h.runtime.register_live(native_runner=None, native_root=None)
    sid = h.new_session()
    h.say(sid, "Bunu EXE olarak çıkar.")

    body = h.tool(sid, "c-1", "native.build", {})["result"]

    assert body["execution_status"] != "refused", body.get("speech")
    assert body["built_on"] == "device"
    # The device was actually asked, in the one order it admits (DEVICE_PROTOCOL §6n).
    asked = h.device.capabilities_called()
    assert asked[:2] == ["project.scaffold", "project.run"]
    assert "file.inspect" in asked
    # And the row reaches `verified` -- EARNED, not inferred from the path taken. The device
    # reported the PE's own version and Cloud Core ran the same `validate_against_spec` the
    # lab path runs. A device that reported no identity would leave this `unverified`; the
    # matrix for that lives in test_nativefactory_device_build.py.
    assert body["build"]["state"] == "verified"


def test_an_android_row_planned_for_this_machine_says_the_device_builds_it() -> None:
    """A row opened against THIS machine's facts (the seeded context) is refused in the one
    sentence that is true whatever this machine has: its build step is dotnet, and Android
    is built on the enrolled device (ADR-0161)."""
    h = build_harness()
    h.seed(CTX_NATIVE_ANDROID)
    sid = h.new_session()
    h.say(sid, "APK üret.")
    body = h.tool(sid, "c-1", "native.build", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert body["speech"] == SPEECH_ANDROID_BUILD_NOT_WIRED
    assert "Java yok" not in body["speech"]


class _RunnerThatMustNotRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv, cwd, *, timeout_s):  # noqa: ANN001
        self.calls.append(list(argv))
        raise AssertionError(f"the local runner is dotnet-only, yet it got {argv}")


class _AndroidDevice:
    """The enrolled device, answering the Android chain the way the real companion does."""

    ROOT = r"C:\Users\o\Documents\PagentOS Projects\native\sayac"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, *, capability, payload, idempotency_key, timeout_s):  # noqa: ANN001
        self.calls.append((capability, payload))
        if capability == "project.scaffold":
            return DeviceRunResult(True, result={"root_path": self.ROOT})
        if capability == "project.run":
            return DeviceRunResult(True, result={"exit_code": 0, "runtime": "gradle"})
        if capability == "project.test":
            return DeviceRunResult(
                True,
                result={"exit_code": 0, "passed": 2, "failed": 0, "counts_parsed": True},
            )
        if capability == "file.inspect":
            return DeviceRunResult(
                True,
                result={
                    "file": {"size": 800957, "sha256": "b" * 64},
                    "kind": "unknown",
                    "android": {
                        "format": "apk",
                        "package": "com.pagentos.sayac",
                        "version_code": 1004003,
                        "version_name": "1.4.2",
                        "min_sdk": 23,
                        "target_sdk": 33,
                        "signed": True,
                        "signature_schemes": ["v2+"],
                    },
                },
            )
        return DeviceRunResult(False, "capability_missing", capability)


def test_an_android_app_is_planned_and_built_on_the_device_never_by_the_local_runner() -> None:
    """ADR-0161. The local runner (dotnet) is registered and a JDK is on this machine - the
    two facts that, on 2026-09-16, sent app/build.gradle.kts to `dotnet build`. Now the row
    is planned for the device and built there with the Gradle shapes, and the runner is
    never called."""
    h = build_harness()
    runner = _RunnerThatMustNotRun()
    device = _AndroidDevice()
    with_jdk = ToolchainFacts(
        **{**NATIVE_TOOLCHAIN.as_dict(), "java": r"E:\jdk\bin\java.exe", "java_home": r"E:\jdk"}
    )
    h.runtime.register_live(native_runner=runner, native_toolchain=with_jdk, device_action=device)
    sid = h.new_session()
    h.say(sid, "Bana sayaç uygulaması yap, APK olsun.")
    created = h.tool(
        sid,
        "c-1",
        "native.create",
        {"targets": ["android_apk"], "name": "Sayac", "version": "1.4.2"},
    )["result"]
    rows = _rows(h)
    assert [r.state for r in rows] == ["planned"], created.get("speech")
    assert "cihazınız" in created["speech"]

    h.say(sid, "APK üret.")
    built = h.tool(sid, "c-2", "native.build", {})["result"]
    assert built["execution_status"] == "executed", built.get("speech")
    assert built["built_on"] == "device"
    assert built["build"]["state"] == "verified", built.get("speech")
    assert runner.calls == []
    scaffold = next(p for c, p in device.calls if c == "project.scaffold")
    gradle_build = "gradle --no-daemon --console=plain assembleDebug"
    assert scaffold["manifest"]["run"]["build"] == gradle_build
    assert [p.get("command_key") for c, p in device.calls if c == "project.run"] == ["build"]


def test_without_a_device_an_android_build_is_refused_by_name() -> None:
    h = build_harness()
    runner = _RunnerThatMustNotRun()
    h.runtime.register_live(native_runner=runner, device_action=None)
    sid = h.new_session()
    h.say(sid, "Bana sayaç uygulaması yap, APK olsun.")
    h.tool(sid, "c-1", "native.create", {"targets": ["android_apk"], "name": "Sayac"})
    rows = _rows(h)
    assert rows[0].state == STATE_UNAVAILABLE
    assert rows[0].error_message == SPEECH_ANDROID_BUILD_NOT_WIRED
    h.say(sid, "APK üret.")
    built = h.tool(sid, "c-2", "native.build", {})["result"]
    assert built["execution_status"] == "refused"
    assert runner.calls == []


def test_android_package_says_the_bundle_is_the_package() -> None:
    h = build_harness()
    h.seed(CTX_NATIVE_ANDROID)
    sid = h.new_session()
    h.say(sid, "Kurulum dosyasını oluştur.")
    packaged = h.tool(sid, "c-1", "native.package", {})["result"]
    assert packaged["execution_status"] == "refused"
    assert packaged["speech"] == SPEECH_ANDROID_PACKAGE_IS_THE_BUNDLE


# ------------------------------------------------ native.check / fix / rebuild


def test_check_reads_the_row_back_and_never_claims_more_than_it_says() -> None:
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Çalışıyor mu kontrol et.")
    body = h.tool(sid, "c-1", "native.check", {})["result"]
    assert body["verified"] is False
    assert body["state"] == "unverified"
    assert "Hazır demiyorum" in body["speech"]
    # A QUERY: it changed nothing.
    assert [row.state for row in _rows(h)] == ["unverified"]


def test_fix_with_nothing_broken_says_so_rather_than_pretending_a_repair() -> None:
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    sid = h.new_session()
    h.say(sid, "Hata varsa düzelt.")
    body = h.tool(sid, "c-1", "native.fix", {})["result"]
    assert body["execution_status"] == "noop"
    assert body["fixed"] is False
    assert "Düzeltilecek bir hata görünmüyor" in body["speech"]


def test_fix_with_something_broken_and_no_device_reads_the_error_and_says_so() -> None:
    """Spec §9: the coding-model seam is inert here. With no device to rebuild on, the
    truthful answer is the ERROR ITSELF plus an explicit statement that the fix was not
    written."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    h.runtime.register_live(device_action=None)
    sid = h.new_session()
    h.say(sid, "Hata varsa düzelt.")
    body = h.tool(sid, "c-1", "native.fix", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["fixed"] is False
    assert "düzeltmeyi kendi başıma yazamıyorum" in body["speech"]


def test_fix_with_a_device_regenerates_and_rebuilds_there_and_reports_the_verdict() -> None:
    """B33 req 470: the one repair this factory can honestly perform is deterministic - the
    source is re-rendered from the spec and the whole build runs again ON THE DEVICE; the
    row's new state is the device's verdict, and "düzelttim" is said only for verified."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Hata varsa düzelt.")
    body = h.tool(sid, "c-1", "native.fix", {})["result"]
    assert body["execution_status"] == "executed", body.get("speech")
    assert body["fixed"] is True
    assert body["build"]["state"] == "verified"
    assert body["speech"].startswith("Düzelttim efendim")
    assert "yeniden derledim" in body["speech"]
    assert h.device.capabilities_called()[:2] == ["project.scaffold", "project.run"]
    assert body["observed_after"]["server"]["was"] == "unverified"
    assert body["observed_after"]["server"]["attempt"] == 2


def test_rebuild_opens_a_new_row_at_the_next_version_and_leaves_the_old_verdict_alone(
    monkeypatch,
) -> None:
    """A rebuild that overwrote the previous row would destroy the evidence about the
    artefact the owner may still have installed."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    before = _rows(h)
    assert [row.version for row in before] == ["0.1.0"]
    monkeypatch.setattr(native_service, "read_artifact", _reader_that_really_reads("0.1.1"))
    sid = h.new_session()
    h.say(sid, "Yeni sürümü build et.")
    body = h.tool(sid, "c-1", "native.rebuild", {})["result"]
    assert body["build"]["version"] == "0.1.1"
    assert body["build"]["state"] == "verified"

    after = _rows(h)
    assert len(after) == 2
    assert sorted(row.version for row in after) == ["0.1.0", "0.1.1"]
    assert after[0].state == "unverified"  # the old verdict, untouched


# ---------------------------------------------------- native.package / install


def test_a_device_path_is_never_a_local_publish_folder(tmp_path) -> None:
    """2026-09-17: on the Linux Cloud Core a device's Windows path is ONE relative file
    name whose parent is ``.`` - and ``.`` was zipped into itself until the runner's disk
    was full. Only a path that is absolute here, with a folder that exists, is local."""
    from pathlib import PurePosixPath

    device_path = "C:\\Users\\owner\\publish\\notlarim.exe"
    # What the Linux Cloud Core sees: not absolute, and its parent is the working directory.
    assert not PurePosixPath(device_path).is_absolute()
    assert str(PurePosixPath(device_path).parent) == "."
    # So the same shape - a bare relative name - is never local, on any platform.
    assert local_publish_dir("notlarim.exe") is None
    assert local_publish_dir(PurePosixPath(device_path).name) is None
    assert local_publish_dir(None) is None
    assert local_publish_dir(str(tmp_path / "missing" / "notlarim.exe")) is None
    exe = tmp_path / "publish" / "notlarim.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    assert local_publish_dir(str(exe)) == exe.parent


def test_package_makes_a_real_portable_package_and_says_what_it_did_not_make() -> None:
    """ "Kurulum dosyasını oluştur." must not quietly mean "a zip": the receipt names the
    package it really wrote AND states that the signed MSIX is the device's (spec §1)."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Kurulum dosyasını oluştur.")
    body = h.tool(sid, "c-1", "native.package", {})["result"]
    assert body["execution_status"] == "executed"
    assert body["package_path"].endswith("notlarim-0.1.0.zip")
    assert "MSIX kurulumu cihazda üretilir" in body["speech"]

    from pathlib import Path
    from zipfile import ZipFile

    written = Path(body["package_path"])
    assert written.is_file()
    with ZipFile(written) as bundle:  # a real zip, opened by the standard library
        assert bundle.namelist()


def test_fix_whose_device_rebuild_still_fails_says_so_in_the_compilers_words() -> None:
    """B33 req 470, the other half: a failure that survives the regenerate-and-rebuild is
    reported as the DEVICE's own error and "fixed" stays False - never a repair claimed."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    h.device.results["project.test"] = DeviceRunResult(
        False, "tests_failed", "Failed!  - Failed: 1, Passed: 2 (NoteStore.Add)"
    )
    sid = h.new_session()
    h.say(sid, "Hata varsa düzelt.")
    body = h.tool(sid, "c-1", "native.fix", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["fixed"] is False
    assert body["speech"].startswith("Düzeltemedim efendim")
    assert "NoteStore.Add" in body["speech"]
    assert body["build"]["state"] == "failed"
    assert "project.scaffold" in h.device.capabilities_called()


def test_install_refuses_with_no_device_because_the_package_installs_where_the_owner_is() -> None:
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    h.runtime.register_live(device_action=None)
    sid = h.new_session()
    body = h.tool(sid, "c-1", "native.install", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert body["speech"] == SPEECH_INSTALL_NEEDS_DEVICE


def test_install_then_uninstall_go_through_the_device_and_a_second_removal_is_refused() -> None:
    """B33 req 468/469: project.install writes the Start Menu shortcut and the receipt
    carries what the device OBSERVED; project.uninstall removes it and keeps the build;
    removing what this system never installed is refused by name (spec §6's negative)."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    installed = h.tool(sid, "c-1", "native.install", {})["result"]
    assert installed["execution_status"] == "executed", installed.get("speech")
    observed = installed["observed_after"]["server"]
    assert observed["installed"] is True
    assert observed["method"] == "start_menu_shortcut"
    assert installed["shortcut"].endswith("PagentOS\\Notlarim.lnk")
    assert observed["observed"] == {"shortcut_exists": True, "exe_exists": True}
    assert "Başlat menüsünde" in installed["speech"]
    assert "project.install" in h.device.capabilities_called()

    removed = h.tool(sid, "c-2", "native.uninstall", {})["result"]
    assert removed["execution_status"] == "executed", removed.get("speech")
    assert removed["observed_after"]["server"]["uninstalled"] is True
    assert removed["observed_after"]["server"]["build_kept"] is True
    assert "derleme klasörü yerinde" in removed["speech"]

    again = h.tool(sid, "c-3", "native.uninstall", {})["result"]
    assert again["execution_status"] == "refused"
    assert again["error_class"] == "not_found"
    assert "kurulmamış" in again["speech"]


def test_launch_on_windows_goes_through_the_device_and_reads_the_window_back() -> None:
    """B33 req 462/463: app.launch with the built executable, then one ui.inspect. The
    harness's device shows a Notepad tree, so the template's controls are NOT found and the
    receipt says the window came up but the UI was not read - never "verified"."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Masaüstü uygulamasını aç.")
    body = h.tool(sid, "c-1", "native.launch", {})["result"]
    assert body["execution_status"] == "executed", body.get("speech")
    assert body["terminal_status"] == "unverified"
    assert body["ui_verified"] is False
    assert body["window_id"]
    assert "açıldı" in body["speech"] and "tam okuyamadım" in body["speech"]
    called = h.device.capabilities_called()
    assert "app.launch" in called and "ui.inspect" in called


def test_verify_with_a_window_that_lacks_the_template_controls_names_the_failed_step() -> None:
    """B33 req 463-466: the verification is the 26.15 flow; with the wrong window (the
    harness's Notepad tree) it stops at the first read-back and NAMES it."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Uygulamayı doğrula.")
    body = h.tool(sid, "c-1", "native.verify", {})["result"]
    assert body["execution_status"] == "executed", body.get("speech")
    assert body["terminal_status"] == "unverified"
    assert body["verification"]["verified"] is False
    assert body["verification"]["failed_step"] == "ui.inspect"
    assert "doğrulanamadı" in body["speech"] and "ui.inspect" in body["speech"]


def test_log_reads_the_apps_own_log_through_the_device() -> None:
    """B33 req 466: file.read of data\\app.log beside the executable; the tail lands on
    the row."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Uygulamanın günlüğünü oku.")
    body = h.tool(sid, "c-1", "native.log", {})["result"]
    assert body["execution_status"] == "executed", body.get("speech")
    assert body["observed_after"]["server"]["path"].endswith("\\data\\app.log")
    assert "günlüğü" in body["speech"]
    assert "file.read" in h.device.capabilities_called()


def test_launch_on_android_says_opening_an_apk_is_not_wired() -> None:
    """Two refusals, and they are different: an APK is not opened by this chain at all
    (ADR-0161), so that reason wins over "the launcher lives on the device"."""
    h = build_harness()
    h.seed(CTX_NATIVE_ANDROID)
    sid = h.new_session()
    h.say(sid, "Uygulamayı emülatörde aç.")
    body = h.tool(sid, "c-1", "native.launch", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["speech"] == SPEECH_ANDROID_LAUNCH_NOT_WIRED
    assert "owner_action" not in body


# -------------------------------------------------------------- no build at all


@pytest.mark.parametrize("tool", ["native.build", "native.package", "native.check", "native.fix"])
def test_every_tool_asks_rather_than_guesses_when_nothing_has_been_made(tool) -> None:
    """ADR-0077's contract, extended to this family: a question is not a success and not
    a failure. It matters more here than anywhere else - a build tool that answered a
    question with a receipt would be claiming something was built."""
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-1", tool, {})
    assert call["status"] == "needs_clarification", call
    assert "Henüz derlenmiş bir uygulama yok" in call["result"]["speech"]


def test_a_build_id_the_caller_invented_is_not_silently_treated_as_the_latest() -> None:
    """A wrong id must not resolve to "whatever was most recent" without the target
    matching - the row the owner meant is the one on the stack, and spec §7 resolves
    through ids, never through a hopeful fallback to something else's build."""
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    sid = h.new_session()
    h.say(sid, "Çalışıyor mu kontrol et.")
    body = h.tool(sid, "c-1", "native.check", {"build_id": str(uuid.uuid4())})["result"]
    # There IS exactly one row, and it is the one the owner is talking about.
    assert body["build"]["build_id"] == str(_rows(h)[0].id)


# ------------------------------------------------- production's own facts (row 26.16)

#: Production Cloud Core as it really is: a Linux host with no .NET, no Windows Kits, no
#: Java and no Android SDK. The corpus harness injects the owner's PC as "this machine"
#: instead - which is exactly why the planning defect below never showed in any suite.
LINUX_CLOUD_CORE = ToolchainFacts(
    dotnet=None,
    dotnet_sdk=None,
    makeappx=None,
    signtool=None,
    java=None,
    java_home=None,
    android_sdk=None,
    aapt2=None,
    macos=False,
)


def _production_shaped():
    h = build_harness()
    h.runtime.register_live(native_runner=None, native_root=None, native_toolchain=LINUX_CLOUD_CORE)
    return h


def test_production_plans_a_windows_app_for_the_device_and_builds_it_there() -> None:
    """M28 row 26.16, from the owner's sentence to a verified row, on production's facts.

    Until 2026-09-11 native.create measured THIS machine to decide what was reachable. On
    the Linux Cloud Core that opened the Windows row as `unavailable` (".NET SDK bu makinede
    yok") and native.build refused it before the device path was reached - so the device
    dispatch proven above could never run in production. Every earlier test either seeded
    an already-planned row or had the owner's PC injected as this machine.
    """
    h = _production_shaped()
    sid = h.new_session()
    h.say(sid, "Bana Windows için masaüstü not uygulaması yap.")

    created = h.tool(sid, "c-1", "native.create", {"targets": ["windows_exe"], "name": "Notlarim"})[
        "result"
    ]

    assert created["execution_status"] == "executed", created.get("speech")
    assert [b["state"] for b in created["builds"]] == ["planned"]
    # The owner hears who will build it - and not a verdict about a machine that won't.
    assert "cihaz" in created["speech"]
    assert ".NET SDK bu makinede yok" not in created["speech"]

    built = h.tool(sid, "c-2", "native.build", {"build_id": created["builds"][0]["build_id"]})[
        "result"
    ]

    assert built["built_on"] == "device", built.get("speech")
    assert built["build"]["state"] == "verified"
    assert h.device.capabilities_called()[:2] == ["project.scaffold", "project.run"]


def test_a_packaging_target_is_built_on_the_device_and_packaged_there_self_signed() -> None:
    """B33 req 456/457/473: the device packs the MSIX after the EXE is read back, and - by
    the owner's decision of 2026-09-16 - signs it with its self-signed identity. The manifest
    names that identity as Publisher, and the row carries what the DEVICE read back."""
    h = _production_shaped()
    sid = h.new_session()
    created = h.tool(
        sid,
        "c-1",
        "native.create",
        {"targets": ["windows_exe", "windows_msix"], "name": "Notlarim"},
    )["result"]
    states = {b["target"]: b["state"] for b in created["builds"]}
    assert states == {"windows_exe": "planned", "windows_msix": "planned"}
    msix = next(b for b in created["builds"] if b["target"] == "windows_msix")

    built = h.tool(sid, "c-2", "native.build", {"build_id": msix["build_id"]})["result"]

    assert built["execution_status"] == "executed", built.get("speech")
    assert built["build"]["state"] == "verified"
    called = h.device.capabilities_called()
    assert called[-1] == "project.package"
    assert h.device.payload_for("project.package")["kind"] == "msix"
    assert h.device.payload_for("project.package")["signing_mode"] == "test_certificate"
    scaffolded = {f["path"]: f["text"] for f in h.device.payload_for("project.scaffold")["files"]}
    assert f'Publisher="{TEST_SIGNING_SUBJECT}"' in scaffolded["staging/AppxManifest.xml"]
    with h.factory() as db:
        row = db.get(NativeBuildRow, uuid.UUID(msix["build_id"]))
        assert row.artifact_path.endswith(".msix")
        package = row.artifact_json["package"]
        assert package["kind"] == "msix"
        assert package["signed"] is True
        assert package["signer_thumbprint"] == FAKE_SIGNER_THUMBPRINT
        assert package["trusted"] is False
        assert package["executable"].endswith(".exe")


def _built_msix(h, sid):  # noqa: ANN001, ANN202
    created = h.tool(
        sid, "c-1", "native.create", {"targets": ["windows_msix"], "name": "Notlarim"}
    )["result"]
    build_id = created["builds"][0]["build_id"]
    built = h.tool(sid, "c-2", "native.build", {"build_id": build_id})["result"]
    assert built["build"]["state"] == "verified", built.get("speech")
    return build_id


def test_an_untrusted_signer_refuses_the_msix_install_and_names_the_owner_s_one_step() -> None:
    """B33 req 473: the machine does not trust the device's certificate until the owner runs
    the elevated trust script once. The device refuses; the owner hears the script's name and
    the receipt carries the exact command - and nothing claims an install."""
    h = _production_shaped()
    sid = h.new_session()
    build_id = _built_msix(h, sid)

    body = h.tool(sid, "c-3", "native.install", {"build_id": build_id})["result"]

    assert body["execution_status"] == "refused", body.get("speech")
    assert body["error_class"] == "signing_cert_untrusted"
    assert "trust-native-signing-cert.ps1" in body["speech"]
    assert "yönetici" in body["speech"]
    assert "kuruldu efendim" not in body["speech"]
    assert body["owner_action"] == {"script": TRUST_SCRIPT, "command": TRUST_COMMAND}
    assert h.device.payload_for("project.install")["kind"] == "msix"


def test_after_the_trust_step_the_signed_msix_installs_and_uninstalls_as_a_package() -> None:
    h = _production_shaped()
    SIGNING_TRUST["trusted"] = True
    try:
        sid = h.new_session()
        build_id = _built_msix(h, sid)
        package = h.tool(sid, "c-3", "native.package", {"build_id": build_id})["result"]
        installed = h.tool(sid, "c-4", "native.install", {"build_id": build_id})["result"]
        removed = h.tool(sid, "c-5", "native.uninstall", {"build_id": build_id})["result"]
    finally:
        SIGNING_TRUST["trusted"] = False

    assert package["execution_status"] == "executed", package.get("speech")
    assert SPEECH_SIGNED_TRUSTED in package["speech"]
    assert "owner_action" not in package

    assert installed["execution_status"] == "executed", installed.get("speech")
    server = installed["observed_after"]["server"]
    assert server["method"] == "msix"
    assert server["observed"] == {"package_registered": True}
    assert installed["package_full_name"].startswith("PagentOS.")
    assert "kendinden imzalı MSIX" in installed["speech"]
    assert "Başlat menüsünde" not in installed["speech"]

    assert removed["execution_status"] == "executed", removed.get("speech")
    assert "paket kaydı gitti" in removed["speech"]


def test_packaging_an_msix_on_the_device_speaks_the_signature_the_device_read_back() -> None:
    h = _production_shaped()
    sid = h.new_session()
    build_id = _built_msix(h, sid)

    body = h.tool(sid, "c-3", "native.package", {"build_id": build_id})["result"]

    assert body["execution_status"] == "executed", body.get("speech")
    assert SPEECH_SIGNED_UNTRUSTED in body["speech"]
    server = body["observed_after"]["server"]
    assert server["signed"] is True
    assert server["trusted"] is False
    assert server["signer_thumbprint"] == FAKE_SIGNER_THUMBPRINT
    assert body["owner_action"] == {"script": TRUST_SCRIPT, "command": TRUST_COMMAND}
    assert h.device.payload_for("project.package")["signing_mode"] == "test_certificate"


def test_a_device_that_answers_unsigned_is_never_described_as_signed() -> None:
    """A device older than B33 ignores signing_mode. The owner hears 'imzalanmadı', the row
    says unsigned, and the install is refused before the device is asked."""
    h = _production_shaped()
    unsigned_answer = DeviceRunResult(
        True,
        result={
            "kind": "msix",
            "path": "C:\\x\\dist\\notlarim.msix",
            "name": "notlarim.msix",
            "bytes": 10,
            "sha256": "e" * 64,
            "signed": False,
            "observed": {"exists": True, "bytes": 10},
        },
    )
    h.device.results["project.package"] = unsigned_answer
    sid = h.new_session()
    build_id = _built_msix(h, sid)
    package = h.tool(sid, "c-3", "native.package", {"build_id": build_id})["result"]
    installed = h.tool(sid, "c-4", "native.install", {"build_id": build_id})["result"]

    assert SPEECH_SIGNING_NOT_APPLIED in package["speech"]
    assert package["observed_after"]["server"]["signed"] is False
    assert installed["execution_status"] == "refused"
    assert installed["error_class"] == "package_unsigned"
    assert "project.install" not in h.device.capabilities_called()


def test_the_unsigned_opt_out_and_the_owner_certificate_refusal_are_spoken_as_themselves() -> None:
    from types import SimpleNamespace

    for mode, error_class, speech in (
        ("unsigned", "package_unsigned", SPEECH_UNSIGNED_MSIX),
        ("owner_certificate", "signing_mode_refused", SPEECH_OWNER_CERTIFICATE_REFUSED),
    ):
        h = _production_shaped()
        h.runtime.register_live(settings=SimpleNamespace(native_signing_mode=mode))
        sid = h.new_session()
        build_id = _built_msix(h, sid)
        assert h.device.payload_for("project.package")["signing_mode"] == "unsigned"
        body = h.tool(sid, "c-3", "native.install", {"build_id": build_id})["result"]
        assert body["execution_status"] == "refused", mode
        assert body["error_class"] == error_class
        assert body["speech"] == speech
        assert "project.install" not in h.device.capabilities_called()


def test_an_android_target_is_planned_for_the_device_and_asked_in_gradle_shapes() -> None:
    """ADR-0161: the device path builds Android too. On production's facts the row is
    planned, the device is asked with the three Gradle shapes, and - because this fake
    device's read-back carries no android block - the row is never called verified."""
    h = _production_shaped()
    # An agent older than ADR-0161: it reports the file, not the package's identity.
    h.device.results["file.inspect"] = DeviceRunResult(
        True, result={"file": {"size": 800957, "sha256": "d" * 64}, "kind": "unknown"}
    )
    sid = h.new_session()
    created = h.tool(sid, "c-1", "native.create", {"targets": ["android_apk"], "name": "Notlarim"})[
        "result"
    ]
    assert [b["state"] for b in created["builds"]] == ["planned"]
    built = h.tool(sid, "c-2", "native.build", {"build_id": created["builds"][0]["build_id"]})[
        "result"
    ]
    assert built.get("built_on") == "device", built
    manifest = h.device.payload_for("project.scaffold")["manifest"]
    assert set(manifest["run"].values()) == {
        "gradle --no-daemon --console=plain assembleDebug",
        "gradle --no-daemon --console=plain bundleRelease",
    }
    assert "dotnet" not in str(manifest)
    assert built["build"]["state"] != "verified"


def test_the_lab_still_plans_against_the_machine_it_runs_on() -> None:
    """With a local runner and a local compiler the build is HERE, so this machine's facts
    are the right ones and the reason names the SDK it measured."""
    h = build_harness()
    sid = h.new_session()
    created = h.tool(sid, "c-1", "native.create", {"targets": ["windows_exe"], "name": "Notlarim"})[
        "result"
    ]
    assert [b["state"] for b in created["builds"]] == ["planned"]
    assert ".NET 10.0.400" in created["speech"]
    assert "cihaz" not in created["speech"]

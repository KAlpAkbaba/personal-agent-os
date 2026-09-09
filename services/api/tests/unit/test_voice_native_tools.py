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
from app.nativefactory.models import NativeBuildRow
from app.voice.realtime_sessions.tools_native import (
    SPEECH_ANDROID_NEEDS_JDK,
    SPEECH_INSTALL_NEEDS_DEVICE,
    SPEECH_NO_RUNNER,
)
from tests.voice_corpus.corpus import CTX_NATIVE_ANDROID, CTX_NATIVE_BUILT, CTX_NATIVE_PLANNED
from tests.voice_corpus.harness import build_harness


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
    h.say(sid, "Android sürümünü yap.")
    call = h.tool(sid, "c-1", "native.create", {"name": "Sayac", "request": "Android sürümünü yap"})
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert "33" in body["speech"]

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


def test_a_build_with_no_compiler_registered_refuses_and_says_why() -> None:
    """Today's Cloud Core: the build belongs to the device (spec §5), so with no runner
    injected the only truthful answer is that nothing was built."""
    h = build_harness()
    h.seed(CTX_NATIVE_PLANNED)
    h.runtime.register_live(native_runner=None, native_root=None)
    sid = h.new_session()
    h.say(sid, "Bunu EXE olarak çıkar.")
    body = h.tool(sid, "c-1", "native.build", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert body["speech"] == SPEECH_NO_RUNNER
    assert body["build"]["state"] == "planned"  # untouched: nothing pretended to happen


def test_an_apk_build_names_the_two_facts_separately_and_the_owner_item() -> None:
    """Spec §1: the SDK being present is exactly what makes this blocker confusing, so
    the refusal states both facts rather than "Android is not available"."""
    h = build_harness()
    h.seed(CTX_NATIVE_ANDROID)
    sid = h.new_session()
    h.say(sid, "APK üret.")
    body = h.tool(sid, "c-1", "native.build", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert "SDK" in body["speech"] and "Java" in body["speech"] and "33" in body["speech"]


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


def test_fix_with_something_broken_reads_the_error_and_says_it_did_not_fix_it() -> None:
    """Spec §9: the coding-model seam is inert here. The truthful answer is the ERROR
    ITSELF plus an explicit statement that the fix was not written."""
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    h.say(sid, "Hata varsa düzelt.")
    body = h.tool(sid, "c-1", "native.fix", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["fixed"] is False
    assert "düzeltmeyi kendi başıma yazamıyorum" in body["speech"]


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


def test_install_refuses_because_the_package_installs_where_the_owner_is() -> None:
    h = build_harness()
    h.seed(CTX_NATIVE_BUILT)
    sid = h.new_session()
    body = h.tool(sid, "c-1", "native.install", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert body["speech"] == SPEECH_INSTALL_NEEDS_DEVICE


def test_launch_on_android_names_the_owner_item_rather_than_the_missing_device() -> None:
    """Two refusals, and they are different: Android cannot be reached AT ALL until a
    JDK exists, so that reason wins over "the launcher lives on the device"."""
    h = build_harness()
    h.seed(CTX_NATIVE_ANDROID)
    sid = h.new_session()
    h.say(sid, "Uygulamayı emülatörde aç.")
    body = h.tool(sid, "c-1", "native.launch", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["speech"] == SPEECH_ANDROID_NEEDS_JDK
    assert body["owner_action"] == "33"


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

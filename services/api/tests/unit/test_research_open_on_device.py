"""``research.open`` with a device: the report that is opened is THAT research's report,
and a failed open says why (office event, 2026-09-29).

The owner ran a research from the office PC; when it was ready the assistant answered
"... odağa aldım efendim; cihazda açamadım." and nothing on the device had been asked to
do anything. Two defects, both on the cloud side of the path, both invisible to the one
``research.open`` test that existed (it runs with no device at all):

* ``research_open`` handed the report's id to ``artifact_open`` as an ``artifact_id``
  argument that ``artifact_open`` never reads - it resolves its target from the
  ARTIFACT focus stack (``app.operator.focus``), which a research never moves. With
  nothing in that stack the open was a clarification question that ``research_open``
  reported as "cihazda açamadım"; with a factory artifact in it, the WRONG file opened.
* every failure - no device advertising ``file.fetch``, a refused origin, a hash
  mismatch, a question - was spoken as the same three words, and logged nowhere.

Through the real application object, the real relay and the real tools; the device is
``tests.alarms_support.FakeDeviceAction`` registered the way ``create_app`` registers the
real one (``RealtimeVoiceRuntime.register_live``).
"""

# ruff: noqa: F811 - the fixture is imported from the suite that owns it
from __future__ import annotations

import uuid

from app.artifacts import factory as artifact_factory
from app.artifacts import render_store
from app.artifacts import service as artifact_service
from app.artifacts.spec import ArtifactSpec
from app.object_store import InMemoryObjectStore
from app.operator import focus as operator_focus
from app.operator.models import FOCUS_KIND_ARTIFACT, ObjectFocusRow
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import FakeDeviceAction
from tests.artifacts_support import artifact_capability_results
from tests.unit.test_voice_research_followup import (
    _complete_a_research,
    _create,
    _enroll_online_device,
    _say,
    _tool,
    wired,  # noqa: F401 - the fixture
)

REPORT_BODY = "# Rapor\n\nBirinci bulgu: bağlam penceresi büyüdü.\n"


def _with_device(runtime, artifacts, device: FakeDeviceAction) -> None:
    """What production has and the ``wired`` fixture leaves out: the artifact focus
    table, an object store for renders, and the device port on ``ToolContext.live``."""
    ObjectFocusRow.__table__.create(runtime.engine, checkfirst=True)
    artifacts._store = InMemoryObjectStore()
    runtime.register_live(device_action=device)


def _give_the_report_a_render(runtime, artifacts, artifact_id: str) -> None:
    """What ``research_compose`` + ``research_render`` leave behind for a real run."""
    with runtime.session() as db:
        aid = uuid.UUID(artifact_id)
        version = artifact_service.add_artifact_version(
            db,
            artifact_id=aid,
            canonical_body=REPORT_BODY,
            content_hash="0" * 64,
            source_manifest=[],
        )
        artifact = artifact_service.get_artifact(db, aid)
        render_store.ensure_renders(
            db, artifacts.store, version=version, title=artifact.title, formats=("html",)
        )
        db.commit()


def _a_research_with_a_report(client, runtime, artifacts, *, topic: str) -> tuple[str, str]:
    task_id, artifact_id = _complete_a_research(client, runtime, artifacts, topic=topic)
    _give_the_report_a_render(runtime, artifacts, artifact_id)
    return task_id, artifact_id


def _open_the_research(client) -> dict:
    sid = _create(client)
    _say(client, sid, "Son araştırmayı aç.")
    call = _tool(client, sid, "open-1", "research.open", {})
    assert call["status"] == "succeeded", call
    return call["result"]


def test_research_open_opens_that_researchs_report_with_no_artifact_in_focus(wired) -> None:
    """The office event's own shape: a research, no factory artifact ever made."""
    client, runtime, _sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    device = FakeDeviceAction(results=artifact_capability_results())
    _with_device(runtime, artifacts, device)
    _task, artifact_id = _a_research_with_a_report(client, runtime, artifacts, topic="Ofis konusu")

    body = _open_the_research(client)

    assert device.capabilities_called() == ["file.fetch"], body
    assert body["open_receipt"]["artifact_id"] == artifact_id
    assert body["opened"] is True
    assert "açtım" in body["speech"]
    assert "açamadım" not in body["speech"]


def test_research_open_never_opens_the_artifact_that_happens_to_be_in_focus(wired) -> None:
    """A budget table the owner made last week is still the artifact focus. "Son
    araştırmayı aç" must not open it."""
    client, runtime, _sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    device = FakeDeviceAction(results=artifact_capability_results())
    _with_device(runtime, artifacts, device)
    with runtime.session() as db:
        spec = ArtifactSpec.model_validate(
            {
                "kind": "document",
                "title": "Başka belge",
                "sections": [{"heading": "Not", "level": 1, "paragraphs": ["Başka bir şey."]}],
            }
        )
        other = artifact_factory.create(db, artifacts.store, spec=spec)
        operator_focus.set_focus(
            db, FOCUS_KIND_ARTIFACT, str(other.artifact_id), label=spec.title, source="test"
        )
        db.commit()
        other_id = str(other.artifact_id)
    _task, artifact_id = _a_research_with_a_report(client, runtime, artifacts, topic="Ofis konusu")

    body = _open_the_research(client)

    assert body["open_receipt"]["artifact_id"] == artifact_id
    assert body["open_receipt"]["artifact_id"] != other_id
    payload = device.payload_for("file.fetch")
    assert payload is not None
    assert payload["name"].endswith(".html")
    assert "Başka belge" not in payload["name"]


def test_a_failed_open_says_why_and_is_logged(wired, capsys) -> None:
    """No online device advertises ``file.fetch`` - the office PC, installed without the
    operator family. The owner hears THAT, not three words that fit every failure."""
    client, runtime, _sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    device = FakeDeviceAction(
        results={
            "file.fetch": DeviceRunResult(
                False,
                "no_capable_device",
                "'file.fetch' yeteneğine sahip çevrimiçi bir cihaz bulunamadı.",
            )
        }
    )
    _with_device(runtime, artifacts, device)
    _task, artifact_id = _a_research_with_a_report(client, runtime, artifacts, topic="Ofis konusu")

    body = _open_the_research(client)

    assert body["opened"] is False
    assert body["open_error_class"] == "capability_missing"
    assert body["open_receipt"]["artifact_id"] == artifact_id
    assert "cihazda açamadım" in body["speech"]
    assert "dosya getiremiyor" in body["speech"], body["speech"]
    # The summary is still spoken: the research was found and focused.
    assert body["topic"] in body["speech"]
    logged = capsys.readouterr()
    assert "research_open_device_open_failed" in (logged.out + logged.err)
    assert "capability_missing" in (logged.out + logged.err)

"""Executive Autonomy's REST surface (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §6), through
the REAL application object (``tests.voice_corpus.harness.build_harness``) — the same
wiring the voice tools and the corpus run on. ``Client.connect`` is faked (mirrors
``tests.voice_corpus.harness.Harness._complete_research``'s own pattern for the
identical need) so these stay fast unit tests; the real Temporal round trip is the
voice corpus's and the workflow suite's job, not this file's.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.executive import activities
from app.object_store import InMemoryObjectStore
from tests.voice_corpus.harness import Harness, build_harness


@pytest.fixture()
def h(monkeypatch) -> Harness:
    """A fresh harness, with ``app.executive.activities``' own DB seam (module
    docstring of activities.py: it builds a fresh context from global ``Settings``,
    correct for a real deployment's one real Postgres) redirected at THIS harness's
    in-memory SQLite engine — the same seam
    ``tests.unit.test_executive_activities._patch_seams`` establishes, needed here
    because the cancel ROUTE calls ``activities.cancel_run_and_compensate`` directly,
    not only a Temporal followup ever would."""
    harness = build_harness()
    store = InMemoryObjectStore()
    monkeypatch.setattr(
        activities, "build_artifact_context", lambda _settings: (harness.factory, store)
    )
    return harness


def _fake_temporal_client():
    fake_handle = AsyncMock()
    fake_handle.signal = AsyncMock(return_value=None)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    fake_client.get_workflow_handle = lambda *_a, **_k: fake_handle
    return fake_client


def _start_run(h: Harness, directive: str) -> str:
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post("/v1/executive/runs", json={"directive": directive})
    assert resp.status_code == 200, resp.text
    # The row names the run `run_id` — the bus's own word for the same fact, and the one
    # the Cockpit panel reads. It said `id` here and `run_id` on the detail route until
    # the two halves were read against each other.
    return resp.json()["run_id"]


_RESEARCH_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)


def test_list_is_empty_before_any_run(h: Harness) -> None:
    resp = h.client.get("/v1/executive/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_start_creates_a_running_run(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.get(f"/v1/executive/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running"
    assert body["total"] == 5
    assert len(body["steps"]) == 5


def test_start_with_no_directive_is_422(h: Harness) -> None:
    resp = h.client.post("/v1/executive/runs", json={"directive": ""})
    assert resp.status_code == 422


def test_start_with_an_unrecognised_directive_is_422_with_the_service_speech(h: Harness) -> None:
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post("/v1/executive/runs", json={"directive": "Bana yardım eder misin?"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "clarification_needed"


def test_get_unknown_run_is_404(h: Harness) -> None:
    resp = h.client.get("/v1/executive/runs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_list_returns_the_started_run(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.get("/v1/executive/runs")
    assert [r["run_id"] for r in resp.json()["runs"]] == [run_id]


def test_explain_names_the_current_step(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.get(f"/v1/executive/runs/{run_id}/explain")
    assert resp.status_code == 200
    assert resp.json()["speech"]


def test_pause_then_resume_round_trips(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        pause_resp = h.client.post(f"/v1/executive/runs/{run_id}/pause")
        assert pause_resp.status_code == 200, pause_resp.text
        status_resp = h.client.get(f"/v1/executive/runs/{run_id}")
        assert status_resp.json()["state"] == "paused"

        resume_resp = h.client.post(f"/v1/executive/runs/{run_id}/resume")
        assert resume_resp.status_code == 200, resume_resp.text
    status_resp = h.client.get(f"/v1/executive/runs/{run_id}")
    assert status_resp.json()["state"] == "running"


def test_resume_without_pause_is_422(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post(f"/v1/executive/runs/{run_id}/resume")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_state"


def test_cancel_marks_the_run_cancelled(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post(f"/v1/executive/runs/{run_id}/cancel")
    assert resp.status_code == 200, resp.text
    status_resp = h.client.get(f"/v1/executive/runs/{run_id}")
    assert status_resp.json()["state"] == "cancelled"
    steps = status_resp.json()["steps"]
    assert all(s["state"] == "cancelled" for s in steps)


def test_cancel_twice_the_second_is_422(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        h.client.post(f"/v1/executive/runs/{run_id}/cancel")
        second = h.client.post(f"/v1/executive/runs/{run_id}/cancel")
    assert second.status_code == 422


def test_retry_requires_a_step_id(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.post(f"/v1/executive/runs/{run_id}/retry", json={})
    assert resp.status_code == 422


def test_retry_on_a_pending_step_is_422(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.post(f"/v1/executive/runs/{run_id}/retry", json={"step_id": "s2"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_state"


def test_amend_requires_a_step(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    resp = h.client.post(f"/v1/executive/runs/{run_id}/amend", json={})
    assert resp.status_code == 422


def test_amend_adds_a_step(h: Harness) -> None:
    run_id = _start_run(h, _RESEARCH_DIRECTIVE)
    new_step = {
        "id": "s6",
        "kind": "artifacts.create",
        "inputs": {"kind": "presentation", "source": "s2.text"},
        "precondition": {"check": "step_done", "arg": "s2"},
        "postcondition": {"evidence": "artifact_id"},
        "timeout_s": 120,
        "retry": {"max_attempts": 1, "backoff_s": 1.0, "only_on": []},
        "risk_class": "mutate_local",
        "compensation": "delete_render",
    }
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post(f"/v1/executive/runs/{run_id}/amend", json={"step": new_step})
    assert resp.status_code == 200, resp.text
    status_resp = h.client.get(f"/v1/executive/runs/{run_id}")
    assert status_resp.json()["total"] == 6


def test_two_active_runs_is_the_bound(h: Harness) -> None:
    _start_run(h, _RESEARCH_DIRECTIVE)
    _start_run(h, "Bu klasördeki teklifleri karşılaştır, Excel oluştur ve yönetici özeti hazırla.")
    with patch(
        "app.executive.routes.Client.connect", AsyncMock(return_value=_fake_temporal_client())
    ):
        resp = h.client.post(
            "/v1/executive/runs",
            json={
                "directive": (
                    "Bu mail zincirini analiz et, ilgili dosyaları bul ve cevap taslağı hazırla."
                )
            },
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "too_many_active_runs"

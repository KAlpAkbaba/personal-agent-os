"""Latest News Mode's REST surface (docs/M26_LATEST_NEWS_MODE_SPEC.md §1, §2, §5, §6),
through the REAL application object (``tests.voice_corpus.harness.build_harness``) — the
same wiring the voice tools and the corpus run on, the same discipline
``test_executive_routes.py`` already establishes for its own family. ``Client.connect``
is faked for ``/v1/news/summarize`` (mirrors ``test_executive_routes.py``'s own pattern
for the identical need) so this stays a fast unit test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.news.classification import VideoCandidate
from tests.voice_corpus.harness import Harness, build_harness

REAL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"


def _channel_id(tag: str) -> str:
    """A syntactically valid ("UC" + 22 chars) fixture channel id, distinct per test —
    never a real claim, just a shape ``resolve_channel_identity`` accepts."""
    return ("UC" + tag).ljust(24, "0")[:24]


@pytest.fixture()
def h() -> Harness:
    harness = build_harness()
    # app.news.routes reads request.app.state.device_action, exactly like
    # app.artifacts.routes/app.creative3d.routes already do — but create_app() sets
    # that to a REAL BrokerDeviceAction pointed at Settings.database_url (production
    # shape), not the harness's own shared in-memory engine/fake device. Every other
    # family's own REST-route tests reach the device through ctx.live (the voice
    # tools) instead for this exact reason; this override is scoped to this file only
    # so `/v1/news/open|close` can be proven through the SAME fake device the corpus
    # and the voice-tool tests already use.
    harness.client.app.state.device_action = harness.device
    return harness


def _fake_temporal_client():
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    return fake_client


class TestSourcesCrud:
    def test_create_with_no_channel_input_stays_needs_identity(self, h: Harness) -> None:
        resp = h.client.post(
            "/v1/news/sources",
            json={"news_source_id": "show-ana-haber", "display_name": "Show Ana Haber"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["identity_status"] == "needs_identity"
        assert body["channel_id"] is None

    def test_create_with_an_authoritative_url_resolves(self, h: Harness) -> None:
        resp = h.client.post(
            "/v1/news/sources",
            json={
                "news_source_id": "nasa",
                "display_name": "NASA",
                "channel_input": f"https://www.youtube.com/channel/{REAL_ID}",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["identity_status"] == "resolved"
        assert body["channel_id"] == REAL_ID

    def test_duplicate_is_409(self, h: Harness) -> None:
        h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X"})
        resp = h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X2"})
        assert resp.status_code == 409

    def test_list_and_get(self, h: Harness) -> None:
        h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X"})
        listed = h.client.get("/v1/news/sources").json()["sources"]
        assert [s["news_source_id"] for s in listed] == ["x"]
        got = h.client.get("/v1/news/sources/x")
        assert got.status_code == 200
        assert got.json()["display_name"] == "X"

    def test_get_missing_is_404(self, h: Harness) -> None:
        assert h.client.get("/v1/news/sources/nope").status_code == 404

    def test_update_priority(self, h: Harness) -> None:
        h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X"})
        resp = h.client.patch("/v1/news/sources/x", json={"priority": 3})
        assert resp.status_code == 200
        assert resp.json()["priority"] == 3

    def test_delete_then_404(self, h: Harness) -> None:
        h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X"})
        assert h.client.delete("/v1/news/sources/x").status_code == 204
        assert h.client.get("/v1/news/sources/x").status_code == 404

    def test_owner_gate_requires_a_session(self) -> None:
        harness = build_harness()
        harness.client.headers.pop("Authorization", None)
        resp = harness.client.get("/v1/news/sources")
        assert resp.status_code in (401, 403)


class TestResolveRoute:
    def _seed_resolved_source(self, h: Harness, channel_id: str) -> None:
        h.client.post(
            "/v1/news/sources",
            json={
                "news_source_id": "nasa",
                "display_name": "NASA",
                "channel_input": f"https://www.youtube.com/channel/{channel_id}",
            },
        )
        candidate = VideoCandidate(
            video_id="v1",
            title="Ana Haber Bülteni",
            published_at=datetime(2026, 9, 8, tzinfo=UTC),
            channel_id=channel_id,
            url="https://www.youtube.com/watch?v=v1",
        )
        h.news_provider.channels[channel_id] = [candidate]

    def test_resolve_answers_from_the_fixture_provider(self, h: Harness) -> None:
        self._seed_resolved_source(h, _channel_id("resolve"))
        resp = h.client.post("/v1/news/resolve", json={"news_source_id": "nasa"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["selected"]["video_id"] == "v1"
        assert body["answered_by"] == "fixture"

    def test_resolve_unresolved_identity_is_409(self, h: Harness) -> None:
        h.client.post("/v1/news/sources", json={"news_source_id": "x", "display_name": "X"})
        resp = h.client.post("/v1/news/resolve", json={"news_source_id": "x"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_class"] == "identity_unresolved"

    def test_resolve_missing_source_is_404(self, h: Harness) -> None:
        resp = h.client.post("/v1/news/resolve", json={"news_source_id": "nope"})
        assert resp.status_code == 404


class TestOpenAndCloseRoutes:
    def _seed_resolved_source(self, h: Harness, channel_id: str) -> None:
        h.client.post(
            "/v1/news/sources",
            json={
                "news_source_id": "nasa",
                "display_name": "NASA",
                "channel_input": f"https://www.youtube.com/channel/{channel_id}",
            },
        )
        candidate = VideoCandidate(
            video_id="v1",
            title="Ana Haber Bülteni",
            published_at=datetime(2026, 9, 8, tzinfo=UTC),
            channel_id=channel_id,
            url="https://www.youtube.com/watch?v=v1",
        )
        h.news_provider.channels[channel_id] = [candidate]

    def test_open_plays_the_resolved_video_on_the_news_profile(self, h: Harness) -> None:
        self._seed_resolved_source(h, _channel_id("open"))
        resp = h.client.post("/v1/news/open", json={"news_source_id": "nasa"})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["video_id"] == "v1"
        open_call = next(c for c in h.device.calls if c["capability"] == "browser.session_open")
        assert open_call["payload"]["profile"] == "news"
        assert open_call["payload"]["session_kind"] == "media"

    def test_open_with_no_source_named_and_none_default_is_409(self, h: Harness) -> None:
        resp = h.client.post("/v1/news/open", json={})
        assert resp.status_code == 409

    def test_get_playback_status(self, h: Harness) -> None:
        self._seed_resolved_source(h, _channel_id("status"))
        open_resp = h.client.post("/v1/news/open", json={"news_source_id": "nasa"})
        context_id = open_resp.json()["context_id"]
        status = h.client.get(f"/v1/news/open/{context_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "playing"

    def test_close_stops_the_named_context(self, h: Harness) -> None:
        self._seed_resolved_source(h, _channel_id("close"))
        open_resp = h.client.post("/v1/news/open", json={"news_source_id": "nasa"})
        context_id = open_resp.json()["context_id"]
        close_resp = h.client.post(f"/v1/news/open/{context_id}/close")
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "closed"
        stop_call = next(c for c in h.device.calls if c["capability"] == "browser.media_stop")
        assert stop_call["payload"]["session_id"] == f"news-{context_id}"


class TestSummarizeRoute:
    def test_summarize_creates_a_research_task_never_a_playback_context(self, h: Harness) -> None:
        tasks_before = h.research_task_ids()
        with patch(
            "temporalio.client.Client.connect", AsyncMock(return_value=_fake_temporal_client())
        ):
            resp = h.client.post("/v1/news/summarize", json={})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "planned"
        assert h.research_task_ids() != tasks_before
        # Never touches the browser at all.
        assert all(
            c["capability"] not in ("browser.session_open", "browser.media_play")
            for c in h.device.calls
        )

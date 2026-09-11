"""A workflow that never started leaves no work "still to come" behind (Phase 8, 2026-09-11).

research.start, news.summarize and executive.start all write their durable row FIRST and
start the Temporal workflow afterwards, in a follow-up. When Temporal did not answer, each
told the owner (or, for executive, only the log) and left the row in its opening state for
ever: a research/news task CREATED, an executive run `running` with no workflow - which
also counted against the two-run bound. test_voice_research_start and the route tests hold
research and the REST paths; this module holds the two voice paths that had no test at all,
through the real application object and the real tool relay.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.artifacts.models import TASK_STATUS_FAILED_TERMINAL, Task
from app.executive.models import STATE_FAILED, ExecutiveRunRow
from tests.voice_corpus.corpus import CTX_NEWS_SOURCE_CONFIGURED
from tests.voice_corpus.harness import build_harness

_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)


def _temporal_down():
    return patch("app.research.service.Client.connect", AsyncMock(side_effect=OSError("down")))


def test_news_summarize_closes_its_task_when_the_workflow_cannot_start() -> None:
    h = build_harness()
    h.seed(CTX_NEWS_SOURCE_CONFIGURED)
    sid = h.new_session()

    with _temporal_down():
        body = h.tool(sid, "n1", "news.summarize", {})

    task_id = uuid.UUID(body["result"]["task_id"])
    with h.factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        assert task.status == TASK_STATUS_FAILED_TERMINAL
        assert task.error_class == "workflow_start_failed"


def test_executive_start_closes_its_run_when_the_workflow_cannot_start() -> None:
    h = build_harness()
    sid = h.new_session()

    with _temporal_down():
        body = h.tool(sid, "e1", "executive.start", {"directive": _DIRECTIVE})

    run_id = uuid.UUID(body["result"]["run_id"])
    with h.factory() as db:
        run = db.get(ExecutiveRunRow, run_id)
        assert run is not None
        assert run.state == STATE_FAILED
        assert run.error_class == "workflow_start_failed"
        assert run.workflow_id is None

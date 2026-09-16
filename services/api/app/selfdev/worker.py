"""The self-development worker (B35 req 615): the process on the development machine
that turns queued defects into candidates.

The cloud VM holds the queue and has no repository; the development machine holds the
repository and the model key. So the worker POLLS: claim one defect through the owner's
session, run the engine on it, post the record back. A claim it cannot finish expires on
the server and is queued again. It never pushes, merges or promotes: the engine stops at
the policy boundary and the worker stops with it.

``python -m app.selfdev worker --api http://... --repo ...`` runs :func:`run_forever`;
:class:`ScriptedQueueClient` lets a test drive :func:`run_once` with no network.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.selfdev.model import DefectSpec

CLAIM_PATH = "/v1/selfdev/worker/claim"


class QueueClient(Protocol):
    def claim(self, worker_id: str) -> dict[str, Any]: ...
    def start(self, defect_id: str, run_id: str) -> dict[str, Any]: ...
    def finish(self, defect_id: str, record: dict[str, Any]) -> dict[str, Any]: ...


class Engine(Protocol):
    def run(self, defect: DefectSpec, *, base_sha: str, targeted_tests: list[str]) -> Any: ...


@dataclass(slots=True)
class HttpQueueClient:
    """The real client: the queue routes over HTTP with the owner's bearer token. The
    token is READ from the environment by the CLI and never logged or written."""

    base_url: str
    token: str
    timeout_s: float = 60.0

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"{path} answered {exc.code}: {detail}") from exc

    def claim(self, worker_id: str) -> dict[str, Any]:
        return self._post(CLAIM_PATH, {"worker_id": worker_id})

    def start(self, defect_id: str, run_id: str) -> dict[str, Any]:
        return self._post(f"/v1/selfdev/defects/{defect_id}/start", {"run_id": run_id})

    def finish(self, defect_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/v1/selfdev/defects/{defect_id}/finish", {"record": record})


@dataclass(slots=True)
class ScriptedQueueClient:
    """What a test hands :func:`run_once`: the claims it will answer, and what it saw."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    started: list[tuple[str, str]] = field(default_factory=list)
    finished: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def claim(self, worker_id: str) -> dict[str, Any]:
        return self.claims.pop(0) if self.claims else {"defect": None, "reason": "queue_empty"}

    def start(self, defect_id: str, run_id: str) -> dict[str, Any]:
        self.started.append((defect_id, run_id))
        return {"defect": {"defect_id": defect_id, "state": "running"}}

    def finish(self, defect_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self.finished.append((defect_id, record))
        return {"defect": {"defect_id": defect_id}}


def defect_spec_of(entry: dict[str, Any]) -> DefectSpec:
    """The queue row -> the engine's spec. The defect id the engine slugs is the row's
    id, so the run folder and the branch name lead back to the row."""
    scope = tuple(str(p) for p in (entry.get("scope") or ()) if str(p).strip())
    failing = entry.get("failing_test")
    return DefectSpec(
        defect_id=str(entry["defect_id"]),
        title=str(entry.get("title") or entry["defect_id"]),
        evidence=str(entry.get("evidence") or ""),
        scope=scope or ("services/api/app/", "services/api/tests/"),
        failing_test=str(failing) if isinstance(failing, str) and failing else None,
    )


def record_dict(record: Any) -> dict[str, Any]:
    return asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)


def run_once(
    client: QueueClient,
    engine: Engine,
    *,
    worker_id: str,
    base_sha: Callable[[], str],
) -> dict[str, Any] | None:
    """Claim -> start -> run -> finish, once. Returns the record posted, or None when the
    server refused the claim (the reason is the server's, printed by the caller)."""
    answer = client.claim(worker_id)
    entry = answer.get("defect")
    if not entry:
        return None
    defect = defect_spec_of(entry)
    targeted = [defect.failing_test] if defect.failing_test else []
    run_id_hint = f"pending-{entry['defect_id'][:8]}"
    client.start(entry["defect_id"], run_id_hint)
    try:
        record = record_dict(engine.run(defect, base_sha=base_sha(), targeted_tests=targeted))
    except Exception as exc:  # noqa: BLE001 - the queue must hear that the run died
        record = {
            "run_id": run_id_hint,
            "status": "FAILED",
            "reason": f"worker: {type(exc).__name__}: {exc}"[:500],
        }
    client.finish(entry["defect_id"], record)
    return record


def run_forever(
    client: QueueClient,
    engine: Engine,
    *,
    worker_id: str,
    base_sha: Callable[[], str],
    poll_s: float = 30.0,
    max_runs: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    runs = 0
    while max_runs is None or runs < max_runs:
        record = run_once(client, engine, worker_id=worker_id, base_sha=base_sha)
        if record is None:
            sleep(poll_s)
            continue
        runs += 1
        log(json.dumps({"run_id": record.get("run_id"), "status": record.get("status")}))
    return runs

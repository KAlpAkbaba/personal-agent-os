"""The browser task loop in the cloud, against the real world (card cloud-task-loop-evidence).

Everything here is real except the place: the api (``uvicorn app.main:app``), the api's own
Temporal worker (``python -m app.worker``) and the cloud companion
(``python -m browser_agent.cloud``: enroll, then a headless Chromium behind the broker) are
started by THIS test as its own children, on the dev stack's PostgreSQL and Temporal. The
planner is the real model planner with the real key; the sites are real public sites. The
companion enrolls with a token the test's owner session minted (ADR-0219's path) and must
show up in the registry as platform ``cloud``, alias ``bulut`` - a companion started by hand
is not evidence.

Opt-in: ``PAGENTOS_TEST_CLOUD_COMPANION=process`` (or ``container``: the cloud-browser image,
``PAGENTOS_TEST_CLOUD_IMAGE``). Without it the module's tasks skip, because the gate runs
every ``-m integration`` test and this one spends real model money and reaches real sites on
every run. Once opted in nothing skips: a companion that cannot start, or a missing model
key, FAILS and says why (a NOT_RUN is not evidence).

The tasks and the evidence writer are ``scripts/cloud/cloud-task-loop-evidence.py``'s,
imported - one schema, not two copies. ``PAGENTOS_EVIDENCE_OUT`` names where the evidence
files go (``docs/evidence`` for the card's run); unset, they go to the test's tmp folder.

Run (owner's machine; the key comes from the DPAPI store, never from a file):
    scripts/secret-store.ps1 -WorkingDirectory services/api -Run "uv run pytest
        tests/integration/test_webtask_cloud_companion.py -q -m integration"
(one line) with PAGENTOS_TEST_CLOUD_COMPANION=process in the environment, under a test-slot
ONAY.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from typing import Any

import pytest
from sqlalchemy import select

from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.execution import allowlist_store
from app.webtask import service
from app.webtask.gate import NOT_ON_LIST_TR
from app.webtask.models import WebTaskRow
from app.webtask.types import STATUS_RUNNING, STATUS_WAITING_OWNER
from tests.integration import procs

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
BROWSER_ROOT = REPO_ROOT / "services" / "browser"
SCRIPT = REPO_ROOT / "scripts" / "cloud" / "cloud-task-loop-evidence.py"

MODE_ENV = "PAGENTOS_TEST_CLOUD_COMPANION"
MODES = ("process", "container")
IMAGE_ENV = "PAGENTOS_TEST_CLOUD_IMAGE"
DEFAULT_IMAGE = "pagentos-cloud-browser:local"
BROWSER_PYTHON_ENV = "PAGENTOS_TEST_BROWSER_PYTHON"
EVIDENCE_ENV = "PAGENTOS_EVIDENCE_OUT"

#: Hang guards, not assertions: what is asserted is the state that follows.
START_TIMEOUT_S = 90.0
REGISTRY_TIMEOUT_S = 180.0


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("cloud_task_loop_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ev = _load_script()


# ------------------------------------------------------------------ the stack


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"(no log at {path})"
    return "\n".join(text.splitlines()[-lines:])


def _browser_python() -> Path:
    named = os.environ.get(BROWSER_PYTHON_ENV, "").strip()
    if named:
        return Path(named)
    for candidate in (
        BROWSER_ROOT / ".venv" / "Scripts" / "python.exe",
        BROWSER_ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    pytest.fail(
        f"the cloud companion cannot start: no python for services/browser ({BROWSER_ROOT}/.venv "
        f"is missing; run `uv sync` there or set {BROWSER_PYTHON_ENV})"
    )


@dataclass
class Stack:
    mode: str
    base_url: str
    credential: str = field(repr=False)
    client: Any = field(repr=False)
    device_id: str
    tmp: Path
    worker_log: Path
    api_log: Path
    companion_log: Path
    registry_lines: list[str]
    factory: Any
    task_ids: list[str] = field(default_factory=list)
    added_sites: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)

    def new_session(self, label: str) -> Any:
        token = ev.open_session(self.base_url, self.credential, label=label)
        self.client = ev.ApiClient(self.base_url, token)
        return self.client

    def worker_offset(self) -> int:
        return self.worker_log.stat().st_size if self.worker_log.exists() else 0

    def model_usage(self, start: int) -> list[dict[str, Any]]:
        """The planner's own log lines (``webtask_planner_answered``) written since ``start``."""
        with self.worker_log.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read().decode("utf-8", errors="replace")
        return ev.usage_from_log_lines(chunk.splitlines())


def _wait_health(base_url: str, proc: subprocess.Popen[bytes], log: Path) -> None:
    import httpx

    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"the api exited ({proc.returncode}) before it answered:\n{_tail(log)}")
        try:
            if httpx.get(f"{base_url}/v1/system/health", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    pytest.fail(f"the api did not answer in {START_TIMEOUT_S:.0f}s:\n{_tail(log)}")


def _start_companion_process(
    base_url: str, state: Path, data: Path, log: Path
) -> subprocess.Popen[bytes]:
    python = _browser_python()
    env = os.environ.copy()
    for key in [k for k in env if k.startswith("PAGENTOS_")]:
        env.pop(key)  # no model key, no owner secret: the companion needs none of them
    env.update(
        {
            "PAGENTOS_CLOUD_BROKER_URL": base_url,
            "PAGENTOS_CLOUD_STATE_DIR": str(state),
            "PAGENTOS_CLOUD_DATA_DIR": str(data),
            "PAGENTOS_CLOUD_ENROLLMENT_TOKEN_FILE": str(state / "enroll.token"),
            "PYTHONPATH": str(BROWSER_ROOT),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    handle = log.open("wb")
    try:
        return procs.spawn(
            [str(python), "-m", "browser_agent.cloud"],
            cwd=str(BROWSER_ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        pytest.fail(f"the cloud companion cannot start as a child process: {exc}")


def _docker() -> str:
    found = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if not Path(found).is_file() and not shutil.which(found):
        pytest.fail("container mode: docker is not reachable from this test")
    return found


def _start_companion_container(port: int, state: Path, data: Path, log: Path) -> str:
    image = os.environ.get(IMAGE_ENV, DEFAULT_IMAGE)
    name = f"pagentos-itest-cloud-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(  # noqa: S603 - fixed argv, the test's own values
        [
            _docker(),
            "run",
            "-d",
            "--name",
            name,
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            f"PAGENTOS_CLOUD_BROKER_URL=http://host.docker.internal:{port}",
            "-v",
            f"{state}:/var/lib/pagentos-cloud/state",
            "-v",
            f"{data}:/var/lib/pagentos-cloud/data",
            image,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    log.write_text(run.stdout + run.stderr, encoding="utf-8")
    if run.returncode != 0:
        pytest.fail(f"the cloud companion container did not start ({image}):\n{run.stderr}")
    return name


def _stop_container(name: str, log: Path) -> None:
    docker = _docker()
    logs = subprocess.run(  # noqa: S603
        [docker, "logs", name], capture_output=True, text=True, timeout=60
    )
    log.write_text(logs.stdout + logs.stderr, encoding="utf-8")
    subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=60)  # noqa: S603


def _wait_for_cloud_device(
    client: Any, state: Path, alive: Any, log: Path
) -> tuple[str, dict[str, Any]]:
    """The companion's own device id (its identity file), online, platform cloud, alias bulut."""
    deadline = time.monotonic() + REGISTRY_TIMEOUT_S
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        problem = alive()
        if problem:
            pytest.fail(f"the cloud companion stopped: {problem}\n{_tail(log)}")
        identity = state / "identity.json"
        if identity.is_file():
            device_id = str(json.loads(identity.read_text(encoding="utf-8"))["device_id"])
            last = client.devices()
            for device in last:
                if (
                    device.get("device_id") == device_id
                    and device.get("platform") == "cloud"
                    and "bulut" in (device.get("aliases") or [])
                    and device.get("status") == "online"
                ):
                    return device_id, device
        time.sleep(1.0)
    pytest.fail(
        f"the companion never stood in the registry as cloud/bulut/online in "
        f"{REGISTRY_TIMEOUT_S:.0f}s; registry: {last}\n{_tail(log)}"
    )


def _cleanup(stack: Stack) -> dict[str, int]:
    """Our tasks closed, our allow-list rows gone, our device revoked; the counts."""
    client = stack.new_session("cloud-task-loop-evidence-cleanup")
    for task_id in stack.task_ids:
        ev.close_task(client, task_id)
    for site in list(stack.added_sites):
        client.allowlist_remove(site)
        stack.added_sites.remove(site)
    try:
        client.revoke_device(stack.device_id)
    except ev.ApiError:
        pass
    with stack.factory() as db:
        active = (
            db.execute(
                select(WebTaskRow.id).where(
                    WebTaskRow.status.in_((STATUS_RUNNING, STATUS_WAITING_OWNER))
                )
            )
            .scalars()
            .all()
        )
    allowlist_store.bind(stack.factory)
    owner_sites = [
        e.site for e in allowlist_store.entries() if e.source != allowlist_store.SOURCE_SEED
    ]
    return {
        "web_tasks_active_after": len(active),
        "owner_allow_list_sites_after": len(owner_sites),
        "test_sites_left_on_list": sum(1 for s in owner_sites if s == ev.FORM_SITE),
    }


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    mode = os.environ.get(MODE_ENV, "").strip().lower()
    if not mode:
        pytest.skip(
            f"opt-in live run (real model, real sites): set {MODE_ENV}=process|container "
            "under a test-slot ONAY"
        )
    if mode not in MODES:
        pytest.fail(f"{MODE_ENV}={mode!r}; expected one of {MODES}")
    settings = Settings()
    if not settings.anthropic_api_key:
        pytest.fail(
            "no model key (PAGENTOS_ANTHROPIC_API_KEY): the real planner cannot run. Start the "
            "test under scripts/secret-store.ps1 -Run so the DPAPI store injects it."
        )
    tmp = tmp_path_factory.mktemp("cloud-task-loop")
    identity_root = tmp / "identity"
    state, data = tmp / "cloud-state", tmp / "cloud-data"
    for folder in (identity_root, state, data):
        folder.mkdir()
    port = _free_port()
    host = "0.0.0.0" if mode == "container" else "127.0.0.1"  # noqa: S104 - the container dials in
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PAGENTOS_IDENTITY_ROOT_DIR": str(identity_root),
            # A queue of our own: a dev worker on the default queue must not run these.
            "PAGENTOS_TEMPORAL_TASK_QUEUE": f"pagentos-itest-cloud-{uuid.uuid4().hex[:8]}",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    api_log, worker_log, companion_log = tmp / "api.log", tmp / "worker.log", tmp / "companion.log"
    api_out, worker_out = api_log.open("wb"), worker_log.open("wb")
    api = procs.spawn(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)],
        cwd=str(API_ROOT),
        env=env,
        stdout=api_out,
        stderr=subprocess.STDOUT,
    )
    worker = procs.spawn(
        [sys.executable, "-m", "app.worker"],
        cwd=str(API_ROOT),
        env=env,
        stdout=worker_out,
        stderr=subprocess.STDOUT,
    )
    companion: subprocess.Popen[bytes] | None = None
    container = ""
    factory, _store = build_artifact_context(settings)
    built: Stack | None = None
    try:
        _wait_health(base_url, api, api_log)
        credential = ev.bootstrap_owner(base_url)
        client = ev.ApiClient(base_url, ev.open_session(base_url, credential, label="itest-cloud"))
        (state / "enroll.token").write_text(client.enrollment_token(), encoding="utf-8")
        if mode == "process":
            companion = _start_companion_process(base_url, state, data, companion_log)

            def alive() -> str:
                code = companion.poll() if companion else None
                return "" if code is None else f"exit code {code}"
        else:
            container = _start_companion_container(port, state, data, companion_log)

            def alive() -> str:
                probe = subprocess.run(  # noqa: S603
                    [_docker(), "inspect", "-f", "{{.State.Running}}", container],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return "" if probe.stdout.strip() == "true" else "the container is not running"

        device_id, device = _wait_for_cloud_device(client, state, alive, companion_log)
        if worker.poll() is not None:
            pytest.fail(f"the api worker exited ({worker.returncode}):\n{_tail(worker_log)}")
        registry_lines = [
            json.dumps(
                {k: device.get(k) for k in ("device_id", "name", "platform", "aliases", "status")},
                ensure_ascii=False,
            ),
            *[
                line
                for line in api_log.read_text(encoding="utf-8", errors="replace").splitlines()
                if "broker_device_enrolled" in line or "cloud" in line and "hello" in line
            ][:5],
        ]
        built = Stack(
            mode=mode,
            base_url=base_url,
            credential=credential,
            client=client,
            device_id=device_id,
            tmp=tmp,
            worker_log=worker_log,
            api_log=api_log,
            companion_log=companion_log,
            registry_lines=registry_lines,
            factory=factory,
        )
        yield built
    finally:
        cleanup: dict[str, int] = {}
        if built is not None:
            try:
                cleanup = _cleanup(built)
            finally:
                out = Path(os.environ.get(EVIDENCE_ENV) or (tmp / "evidence"))
                doc = ev.evidence_document(
                    host_kind=(
                        f"dev-stack ({mode} companion, owner's PC; capable planner "
                        f"{settings.executive_planner_model})"
                    ),
                    records=built.records,
                    companion={"mode": mode, "registry": built.registry_lines},
                    cleanup=cleanup,
                )
                paths = ev.write_evidence(doc, out)
                print(f"\ncloud-task-loop evidence: {', '.join(str(p) for p in paths)}")
                print(json.dumps({"cleanup": cleanup}, ensure_ascii=False))
        if container:
            _stop_container(container, companion_log)
        procs.stop(companion)
        procs.stop(worker)
        procs.stop(api)
        api_out.close()
        worker_out.close()


# ------------------------------------------------------------------ helpers


def _row(stack: Stack, task_id: str) -> WebTaskRow:
    with stack.factory() as db:
        return service.get_task(db, uuid.UUID(task_id))


class Watcher:
    """Reads the row while the task runs and keeps what it saw of the page: an ended task's
    observation is scrubbed (page text is not kept), so the last page, and the player's
    clock for T4, are read while they are there."""

    def __init__(self, stack: Stack) -> None:
        self._stack = stack
        self.last_observation: dict[str, Any] | None = None
        self.player: list[tuple[float, int]] = []

    def __call__(self, task_id: str) -> dict[str, Any]:
        row = _row(self._stack, task_id)
        observation = (row.state_json or {}).get("observation")
        if isinstance(observation, dict) and observation.get("url"):
            self.last_observation = observation
            seconds = ev.player_seconds(observation)
            if seconds is not None and (not self.player or self.player[-1][1] != seconds):
                self.player.append((time.monotonic(), seconds))
        return service.task_dict(row)

    def playback(self) -> dict[str, Any]:
        """Did the clock move? Two readings that differ, or - the task usually ends on the
        first observation of the playing video - one reading above 0:00 on a watch address
        that starts the video at 0:00 (no ``t=``/``start=`` offset): it played that long."""
        readings = [s for _, s in self.player]
        url = str((self.last_observation or {}).get("url") or "")
        query = dict(parse_qsl(urlsplit(url).query))
        from_zero = "/watch" in url and not ({"t", "start", "time_continue"} & set(query))
        if len(readings) >= 2:
            advanced: bool | None = readings[-1] > readings[0]
            basis = "two readings"
        elif readings and from_zero:
            advanced = readings[0] > 0
            basis = "one reading against the 0:00 start of a watch address with no offset"
        else:
            advanced, basis = None, "not measurable"
        return {
            "method": "player clock in the task's own observations, read while it ran",
            "readings_s": readings,
            "basis": basis,
            "advanced": advanced,
        }


def _record(
    stack: Stack, spec: Any, task_id: str, usage_from: int, watcher: Watcher, **extra: Any
) -> dict[str, Any]:
    row = _row(stack, task_id)
    state = row.state_json or {}
    record = ev.task_record(
        spec=spec,
        task=service.task_dict(row),
        planner_calls=int(state.get("planner_calls") or 0),
        planner_model_calls=int(state.get("planner_model_calls") or 0),
        usage=stack.model_usage(usage_from),
        observation=state.get("observation") or watcher.last_observation,
        **extra,
    )
    stack.records.append(record)
    # ASCII: under secret-store.ps1 -Run the console is cp1252 and a Turkish letter in a
    # print raised UnicodeEncodeError in the middle of T1 (2026-10-07).
    print("\n" + json.dumps(record, ensure_ascii=True))
    return record


def _start(stack: Stack, spec: Any) -> str:
    started = stack.client.start_task(spec)
    task_id = str(started["task_id"])
    stack.task_ids.append(task_id)
    assert started["target"] == "cloud", started
    assert started["device_id"] == stack.device_id, started
    assert started["attended"] is False, started
    return task_id


# ------------------------------------------------------------------ the tasks


def test_the_companion_is_this_tests_own_child_and_stands_as_cloud_bulut(stack: Stack) -> None:
    assert stack.registry_lines, "no registry line"
    first = json.loads(stack.registry_lines[0])
    assert first["platform"] == "cloud" and "bulut" in first["aliases"], first
    assert first["status"] == "online" and first["device_id"] == stack.device_id, first
    print("\n" + "\n".join(stack.registry_lines))


def test_t1_finds_and_summarises_a_news_story_and_goes_on_after_the_owner_leaves(
    stack: Stack,
) -> None:
    """(a) + (d) + (e): the owner's session is closed right after the start; the task goes
    on unattended, and only 'done' on a real news page counts as T1's success."""
    spec = ev.TASKS["T1"]
    usage_from = stack.worker_offset()
    task_id = _start(stack, spec)
    # The owner leaves: his session is closed and he is on no device.
    stack.client.close_session()
    with pytest.raises(ev.ApiError) as refused:
        stack.client.get_task(task_id)
    assert refused.value.status == 401, refused.value
    at_leave = _row(stack, task_id).round_index

    watcher = Watcher(stack)
    try:
        final = ev.wait_until_settled(watcher, task_id)
        row = _row(stack, task_id)
        record = _record(stack, spec, task_id, usage_from, watcher, owner_left_at_round=at_leave)
    finally:
        # The owner comes back whatever T1 did: the later tasks need a session.
        stack.new_session("itest-cloud-after-t1")
        ev.close_task(stack.client, task_id)
    assert row.attended is False
    assert row.round_index > at_leave, "the task stopped when the owner left"
    assert row.failure not in ("abandoned", "owner_absent"), row.failure

    assert record["planner_calls"] >= 1, record
    if record["outcome"] == ev.OUTCOME_ASK_OWNER:
        assert record["bot_wall"], f"T1 asked the owner for something other than a wall: {record}"
        return  # honest, written into the evidence; NOT T1's success
    assert final["status"] == "done", f"T1 did not finish: {record}"
    assert ev.site_of(record["last_observation_url"]) == ev.site_of(f"https://{ev.NEWS_HOST}/"), (
        record
    )
    assert ev.is_article_url(record["last_observation_url"]), record


def test_t2_fills_a_listed_form_and_never_submits_it(stack: Stack) -> None:
    spec = ev.TASKS["T2"]
    stack.client.allowlist_add(ev.FORM_SITE)
    stack.added_sites.append(ev.FORM_SITE)
    usage_from = stack.worker_offset()
    task_id = _start(stack, spec)
    watcher = Watcher(stack)
    final = ev.wait_until_settled(watcher, task_id)
    record = _record(stack, spec, task_id, usage_from, watcher, allow_listed=True)
    ev.close_task(stack.client, task_id)

    if record["outcome"] == ev.OUTCOME_ASK_OWNER and record["bot_wall"]:
        pytest.fail(f"T2's own test form behind a wall: {record}")
    assert final["status"] == "done", record
    filled = [r for r in final["rounds"] if r["action"] == "fill" and r["outcome"] == "acted"]
    verified = [r["element"] for r in filled if r["verified"]]
    for name in ev.FORM_FIELDS:
        assert any(name.lower() in e.lower() for e in verified), (name, final["rounds"])
    assert all((r["value_chars"] or 0) > 0 for r in filled), filled
    submitted = [
        r
        for r in final["rounds"]
        if r["action"] == "click" and r["outcome"] == "acted" and "submit" in r["element"].lower()
    ]
    assert submitted == [], submitted
    assert record["submit_seen"], "the submit control was never observed"
    assert "/forms/post" in record["last_observation_url"], record


def test_t2_off_the_list_the_gate_refuses_in_turkish(stack: Stack) -> None:
    spec = ev.TASKS["T2"]
    # Runnable on its own stack too (-k t2_off): the site may never have been added here.
    stack.client.allowlist_remove(ev.FORM_SITE)
    if ev.FORM_SITE in stack.added_sites:
        stack.added_sites.remove(ev.FORM_SITE)
    usage_from = stack.worker_offset()
    task_id = _start(stack, spec)
    watcher = Watcher(stack)
    final = ev.wait_until_settled(watcher, task_id)
    record = _record(stack, spec, task_id, usage_from, watcher, allow_listed=False)
    ev.close_task(stack.client, task_id)

    assert record["allow_listed"] is False and record["planner_calls"] >= 1, record
    refusals = [r for r in final["rounds"] if r["detail"] == "not_on_owner_allow_list"]
    assert refusals, final["rounds"]
    assert not [r for r in final["rounds"] if r["action"] == "fill" and r["outcome"] == "acted"]
    # The refusal is the task's last word: only the owner can lift it (Onay Merkezi).
    assert final["status"] == "failed", final
    assert final["failure"] == "not_on_owner_allow_list", final
    assert final["message"] == NOT_ON_LIST_TR, final
    assert "yazamam" in NOT_ON_LIST_TR and "Onay Merkezi" in NOT_ON_LIST_TR


def test_t4_youtube_plays_or_meets_a_bot_wall(stack: Stack) -> None:
    spec = ev.TASKS["T4"]
    usage_from = stack.worker_offset()
    task_id = _start(stack, spec)
    watcher = Watcher(stack)
    final = ev.wait_until_settled(watcher, task_id, poll_s=1.0)
    # The task's session is closed when it ends (and the video with it): the clock is the
    # one its own observations showed while it ran.
    playback = watcher.playback()
    record = _record(stack, spec, task_id, usage_from, watcher, playback=playback)
    ev.close_task(stack.client, task_id)

    if record["outcome"] == ev.OUTCOME_ASK_OWNER:
        assert record["bot_wall"], f"T4 asked the owner for something other than a wall: {record}"
        return
    assert final["status"] == "done", record
    assert "youtube.com" in record["last_observation_url"], record
    assert playback.get("advanced") is True, f"currentTime did not advance: {playback}"


# ------------------------------------------------------------------ the real app mounts it


def test_the_real_app_mounts_the_web_task_routes() -> None:
    """The unit tests mount the router on a bare FastAPI; the real app must mount it too.

    Before ``ROUTERS = [router]`` in ``app/webtask/routes.py`` the real ``create_app()`` had 0
    ``web-tasks`` paths and POST /v1/web-tasks answered 404 to every task of this module.
    """
    from app.main import create_app

    # The OpenAPI table, not ``app.routes``: an included router sits there as one entry
    # without a path, so a walk of ``app.routes`` sees 0 either way.
    paths = {
        f"{method.upper()} {path}"
        for path, item in create_app().openapi()["paths"].items()
        for method in item
        if "web-tasks" in path
    }
    assert "POST /v1/web-tasks" in paths, sorted(paths)
    assert len(paths) > 1, sorted(paths)


# ------------------------------------------------------------------ the script's refusal


@pytest.mark.parametrize(
    "url",
    [
        "https://prod.example.org",
        "http://pagentos-prod:8001",
        "https://pagentos-core.tail0e6789.ts.net",
        "http://100.90.158.26:8001",
    ],
)
def test_the_evidence_script_refuses_production(url: str, tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("x" * 40, encoding="utf-8")
    run = subprocess.run(  # noqa: S603 - the script under test, fixed argv
        [sys.executable, str(SCRIPT), "--api-url", url, "--token-file", str(token)],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    assert run.returncode == 2, (run.returncode, run.stdout, run.stderr)
    assert "üretim" in run.stderr, run.stderr


def test_the_evidence_files_hold_no_secret_and_no_owner_data(tmp_path: Path) -> None:
    """The writer refuses a record carrying a token-shaped or mail-shaped value."""
    record = ev.task_record(
        spec=ev.TASKS["T1"],
        task={"status": "done", "rounds": [], "message": "özet", "round_index": 1},
        planner_calls=1,
        planner_model_calls=1,
        usage=[],
        observation={"url": "https://www.trthaber.com/haber/x.html", "page_kind": "ok"},
    )
    doc = ev.evidence_document(host_kind="unit", records=[record], companion={}, cleanup={})
    json_path, md_path = ev.write_evidence(doc, tmp_path)
    for path in (json_path, md_path):
        assert ev.find_secrets(path.read_text(encoding="utf-8")) == []
    bad = dict(record, message="anahtar sk-ant-api03-" + "A" * 40)
    with pytest.raises(ValueError):
        ev.write_evidence(
            ev.evidence_document(host_kind="unit", records=[bad], companion={}, cleanup={}),
            tmp_path / "bad",
        )

"""The search-engine probe (ADR-0248 'At merge (the lead)'): measure, never bypass.

No network and no browser. Recorded pages go through the REAL ``run_search`` (its ``fetch``
seam answers with the fixture, page kind from the real ``classify_page``) and then through
``row_from_ack``, so the probe reads what the worker really emits. The driver runs against a
scripted worker link with an injected clock and sleeper. The host script runs under bash
with a fake ``docker`` first on PATH that records its arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from bs4 import BeautifulSoup

from browser_agent import search_engines
from browser_agent.cloud import engine_probe
from browser_agent.cloud import policy as cloud_policy
from browser_agent.errors import BrowserError
from browser_agent.page_kind import classify_page

REPO = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).parent.parent / "fixtures" / "serp"
FRAGMENT = REPO / "infra" / "docker" / "cloud-browser" / "compose.fragment.yml"
SCRIPT = REPO / "infra" / "docker" / "cloud-browser" / "measure-search-engines.sh"


# ------------------------------------------------------------------ (1) the plan


def test_the_engines_are_the_workers_own_and_the_cap_is_twelve() -> None:
    assert engine_probe.ENGINES is search_engines.ENGINES
    # If an engine is added to the worker, the default plan grows past the cap and this
    # test says so: the cap is a decision, not a product of the engine list.
    assert len(search_engines.ENGINES) * 3 == 12
    assert engine_probe.MAX_REQUESTS == 12
    assert len(engine_probe.QUERIES) == 3
    assert engine_probe.MIN_GAP_S == 20
    assert engine_probe.REQUEST_TIMEOUT_S == 45
    assert engine_probe.RUN_DEADLINE_S == 15 * 60


def test_the_default_plan_is_every_engine_once_per_query_never_the_same_engine_twice_in_a_row() -> (
    None
):
    planned = engine_probe.plan()
    assert len(planned) == 12
    assert sorted(planned) == sorted(
        (engine, index) for engine in search_engines.ENGINES for index in range(3)
    )
    assert len(set(planned)) == len(planned)
    for first, second in zip(planned, planned[1:], strict=False):
        assert first[0] != second[0]


def test_a_matrix_of_thirteen_is_refused() -> None:
    with pytest.raises(ValueError, match="12"):
        engine_probe.plan(("bing",), tuple(f"q{i}" for i in range(13)))
    with pytest.raises(ValueError):
        engine_probe.plan(search_engines.ENGINES, ("a", "b", "c", "d"))


def test_an_unknown_engine_is_refused() -> None:
    with pytest.raises(ValueError, match="yandex"):
        engine_probe.plan(("bing", "yandex"))
    with pytest.raises(ValueError):
        engine_probe.plan(("auto",))


# ------------------------------------------------------------------ (2) recorded pages


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _page_kind(html: str) -> str:
    """What the worker's fetch reads off a landed page, from the fixture's DOM."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    return classify_page(
        title=soup.title.get_text() if soup.title else "",
        heading_text=heading.get_text() if heading else "",
        body_text=soup.body.get_text(" ") if soup.body else "",
        has_password_field=soup.select_one("input[type=password]") is not None,
        http_status=200,
    ).page_kind


def _worker_ack(engine: str, fixture: str) -> dict[str, Any]:
    """The worker's own envelope for one named-engine search answered by ``fixture``."""
    html = _fixture(fixture)

    async def fetch(provider: str, url: str) -> tuple[str, str, int, str]:
        return html, _page_kind(html), 200, url

    try:
        outcome = asyncio.run(
            search_engines.run_search("probe", engine, fetch=fetch, max_results=10)
        )
    except BrowserError as exc:
        return {
            "type": "result",
            "ok": False,
            "error": {
                "class": str(exc.error_class),
                "message": str(exc),
                "retryable": exc.retryable,
                "evidence": exc.evidence,
            },
        }
    return {"type": "result", "ok": True, "result": outcome.as_dict()}


@pytest.mark.parametrize(
    ("engine", "fixture", "count"),
    [
        ("bing", "bing.html", 2),
        ("brave", "brave.html", 2),
        ("duckduckgo", "duckduckgo.html", 2),
        ("google", "google.html", 3),
    ],
)
def test_a_results_page_is_answered_with_its_count(engine: str, fixture: str, count: int) -> None:
    row = engine_probe.row_from_ack(engine, 0, _worker_ack(engine, fixture), 1234)
    assert row["outcome"] == "ok"
    assert row["results"] == count
    assert row["wall"] is None
    assert row["engine"] == engine
    assert row["query_index"] == 0
    assert row["elapsed_ms"] == 1234


@pytest.mark.parametrize(
    ("engine", "fixture", "wall"),
    [("google", "google-sorry.html", "captcha"), ("google", "google-consent.html", "consent")],
)
def test_googles_interstitials_are_walls(engine: str, fixture: str, wall: str) -> None:
    row = engine_probe.row_from_ack(engine, 1, _worker_ack(engine, fixture), 10)
    assert row["wall"] == wall
    assert row["outcome"] == wall
    assert row["results"] == 0
    assert row["error_class"] == "provider_rate_limited"


@pytest.mark.parametrize(
    ("engine", "fixture"),
    [
        ("duckduckgo", "duckduckgo-captcha.html"),
        ("bing", "bing-wall.html"),
        ("brave", "brave-wall.html"),
    ],
)
def test_a_wall_page_is_a_wall_never_answered_and_never_empty(engine: str, fixture: str) -> None:
    row = engine_probe.row_from_ack(engine, 0, _worker_ack(engine, fixture), 10)
    assert row["wall"] in {"captcha", "consent", "blocked"}
    assert row["outcome"] == row["wall"]
    assert row["outcome"] not in {"ok", "empty"}
    assert row["results"] == 0


def test_an_empty_results_page_is_empty_and_no_wall() -> None:
    html = (
        "<html><head><title>nothing - Bing</title></head><body><ol id='b_results'>"
        "<li class='b_no'>There are no results for this question. Check your spelling.</li>"
        "</ol></body></html>"
    )

    async def fetch(provider: str, url: str) -> tuple[str, str, int, str]:
        return html, _page_kind(html), 200, url

    outcome = asyncio.run(search_engines.run_search("probe", "bing", fetch=fetch))
    ack = {"type": "result", "ok": True, "result": outcome.as_dict()}
    row = engine_probe.row_from_ack("bing", 2, ack, 5)
    assert row["outcome"] == "empty"
    assert row["wall"] is None
    assert row["results"] == 0


# ------------------------------------------------------------------ (3) error shapes


def test_the_rate_limited_error_reads_into_the_same_row_as_a_returned_wall() -> None:
    error_ack = {
        "type": "result",
        "ok": False,
        "error": {
            "class": "provider_rate_limited",
            "message": "every provider (duckduckgo) ended in captcha",
            "retryable": True,
            "evidence": {
                "requested_provider": "duckduckgo",
                "attempts": [{"provider": "duckduckgo", "outcome": "captcha", "detail": ""}],
            },
        },
    }
    returned_ack = {
        "type": "result",
        "ok": True,
        "result": {
            "result_count": 0,
            "results": [],
            "attempts": [{"provider": "duckduckgo", "outcome": "captcha", "detail": ""}],
        },
    }
    from_error = engine_probe.row_from_ack("duckduckgo", 0, error_ack, 7)
    from_result = engine_probe.row_from_ack("duckduckgo", 0, returned_ack, 7)
    assert from_error["outcome"] == from_result["outcome"] == "captcha"
    assert from_error["wall"] == from_result["wall"] == "captcha"
    assert from_error["error_class"] == "provider_rate_limited"
    assert from_result["error_class"] is None


def test_an_unknown_error_class_is_kept_never_dropped() -> None:
    ack = {"type": "result", "ok": False, "error": {"class": "brand_new_class", "message": "x"}}
    row = engine_probe.row_from_ack("brave", 1, ack, 3)
    assert row["error_class"] == "brand_new_class"
    assert row["outcome"] == "error"
    assert row["results"] == 0


# ------------------------------------------------------------------ (4) the driver


def _ok_ack(engine: str, count: int = 3) -> dict[str, Any]:
    return {
        "type": "result",
        "ok": True,
        "result": {
            "result_count": count,
            "results": [
                {"rank": i + 1, "url": f"https://leak.example/{i}", "title": f"LEAKED TITLE {i}"}
                for i in range(count)
            ],
            "attempts": [{"provider": engine, "outcome": "ok", "detail": f"{count} results"}],
        },
    }


def _captcha_ack(engine: str) -> dict[str, Any]:
    return {
        "type": "result",
        "ok": False,
        "error": {
            "class": "provider_rate_limited",
            "message": "wall",
            "evidence": {"attempts": [{"provider": engine, "outcome": "captcha", "detail": ""}]},
        },
    }


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class ScriptedLink:
    """The WorkerLink shape: start / exec / stop; answers from a script, records every call."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        answer: Any = None,
        search_takes_s: float = 2.0,
        raise_on_search: int | None = None,
    ) -> None:
        self.clock = clock
        self.answer = answer or (lambda engine, query: _ok_ack(engine))
        self.search_takes_s = search_takes_s
        self.raise_on_search = raise_on_search
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.starts = 0
        self.stops = 0

    async def start(self) -> dict[str, Any]:
        self.starts += 1
        return {
            "type": "hello",
            "worker_version": "0.5.0",
            "browser": {"channel": "chromium", "version": "140.0.7339.16"},
        }

    async def exec(
        self, request_id: str, capability: str, payload: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]:
        self.calls.append((capability, payload, self.clock.now))
        if capability != "browser.search":
            return {"type": "result", "ok": True, "result": {}}
        searches = sum(1 for c in self.calls if c[0] == "browser.search")
        if self.raise_on_search is not None and searches == self.raise_on_search:
            raise RuntimeError("worker link broke")
        assert timeout_ms == engine_probe.REQUEST_TIMEOUT_S * 1000
        self.clock.now += self.search_takes_s
        return self.answer(payload["engine"], payload["query"])

    def cancel(self, request_id: str) -> None:  # pragma: no cover - not reached
        pass

    async def stop(self) -> None:
        self.stops += 1

    def of(self, capability: str) -> list[dict[str, Any]]:
        return [payload for cap, payload, _ in self.calls if cap == capability]


def _drive(link: ScriptedLink, clock: FakeClock, **kwargs: Any) -> list[dict[str, Any]]:
    rows, _meta = asyncio.run(
        engine_probe.run_probe(link, clock=clock, sleep=clock.sleep, **kwargs)
    )
    return rows


def test_the_driver_opens_once_searches_at_most_twelve_times_and_closes_once() -> None:
    clock = FakeClock()
    link = ScriptedLink(clock)
    rows = _drive(link, clock)
    assert len(link.of("browser.session_open")) == 1
    assert len(link.of("browser.session_close")) == 1
    searches = link.of("browser.search")
    assert len(searches) == 12
    assert link.calls[0][0] == "browser.session_open"
    assert link.calls[-1][0] == "browser.session_close"
    assert link.starts == 1 and link.stops == 1
    assert len(rows) == 12
    assert all(row["outcome"] == "ok" and row["results"] == 3 for row in rows)
    for payload in searches:
        assert payload["engine"] in search_engines.ENGINES
        assert payload["interstitial"] == "fallback"
        assert payload["query"] in engine_probe.QUERIES
        assert payload["session_id"] == link.of("browser.session_open")[0]["session_id"]


def test_after_a_wall_the_engine_is_not_asked_again_and_nothing_is_repeated() -> None:
    clock = FakeClock()

    def answer(engine: str, query: str) -> dict[str, Any]:
        return _captcha_ack(engine) if engine == "duckduckgo" else _ok_ack(engine)

    link = ScriptedLink(clock, answer=answer)
    rows = _drive(link, clock)
    searches = link.of("browser.search")
    ddg = [p for p in searches if p["engine"] == "duckduckgo"]
    assert len(ddg) == 1
    assert ddg[0]["query"] == engine_probe.QUERIES[0]
    assert len(searches) == 10
    pairs = [(p["engine"], p["query"]) for p in searches]
    assert len(pairs) == len(set(pairs))
    ddg_rows = [r for r in rows if r["engine"] == "duckduckgo"]
    assert [r["outcome"] for r in ddg_rows] == [
        "captcha",
        "skipped_after_wall",
        "skipped_after_wall",
    ]
    assert len(rows) == 12
    assert all(p["interstitial"] != "handoff" for p in searches)


def test_a_failure_is_never_retried() -> None:
    clock = FakeClock()

    def answer(engine: str, query: str) -> dict[str, Any]:
        if engine == "brave":
            return {"type": "result", "ok": False, "error": {"class": "timeout", "message": "t"}}
        return _ok_ack(engine)

    link = ScriptedLink(clock, answer=answer)
    rows = _drive(link, clock)
    pairs = [(p["engine"], p["query"]) for p in link.of("browser.search")]
    assert len(pairs) == len(set(pairs))
    assert [r["outcome"] for r in rows if r["engine"] == "brave"] == [
        "error",
        "skipped_after_wall",
        "skipped_after_wall",
    ]


def test_two_requests_to_one_engine_are_at_least_twenty_seconds_apart() -> None:
    clock = FakeClock()
    link = ScriptedLink(clock, search_takes_s=1.0)
    _drive(link, clock)
    sent: dict[str, list[float]] = {}
    for capability, payload, at in link.calls:
        if capability == "browser.search":
            sent.setdefault(payload["engine"], []).append(at)
    for times in sent.values():
        for earlier, later in zip(times, times[1:], strict=False):
            assert later - earlier >= engine_probe.MIN_GAP_S
    # 4 engines x 1 s each: the sleeper was asked for the rest of the gap, never a guess
    assert clock.slept and all(s > 0 for s in clock.slept)
    assert max(clock.slept) <= engine_probe.MIN_GAP_S

    # one engine alone: every request after the first waits for the whole gap
    clock = FakeClock()
    link = ScriptedLink(clock, search_takes_s=0.0)
    _drive(link, clock, engines=("bing",))
    assert clock.slept == [engine_probe.MIN_GAP_S, engine_probe.MIN_GAP_S]


def test_the_whole_run_deadline_stops_the_run() -> None:
    clock = FakeClock()
    link = ScriptedLink(clock, search_takes_s=300.0)
    rows = _drive(link, clock)
    assert len(link.of("browser.search")) == 3
    assert [r["outcome"] for r in rows[3:]] == ["not_run_deadline"] * 9
    assert len(link.of("browser.session_close")) == 1
    assert link.stops == 1


def test_close_and_stop_are_called_when_a_search_raises() -> None:
    clock = FakeClock()
    link = ScriptedLink(clock, raise_on_search=2)
    with pytest.raises(RuntimeError, match="worker link broke"):
        _drive(link, clock)
    assert len(link.of("browser.session_close")) == 1
    assert link.stops == 1
    assert len(link.of("browser.search")) == 2


# ------------------------------------------------------------------ (5) the table


def _meta() -> dict[str, Any]:
    return {
        "date_utc": "2026-10-03T00:00:00Z",
        "image_digest": "sha256:" + "a" * 64,
        "worker_version": "0.5.0",
        "chromium_version": "140.0.7339.16",
        "address": engine_probe.ADDRESS_NOTE,
    }


def test_the_table_has_one_row_per_request_a_summary_per_engine_and_no_leak() -> None:
    clock = FakeClock()

    def answer(engine: str, query: str) -> dict[str, Any]:
        return _captcha_ack(engine) if engine == "duckduckgo" else _ok_ack(engine)

    link = ScriptedLink(clock, answer=answer)
    rows, meta = asyncio.run(engine_probe.run_probe(link, clock=clock, sleep=clock.sleep))
    meta = {**meta, "image_digest": "sha256:" + "b" * 64}
    table = engine_probe.render_table(rows, meta)
    body_lines = [
        line for line in table.splitlines() if line.startswith("| ") and "---" not in line
    ]
    assert len(body_lines) == 12 + 1  # header + one per planned request
    assert "bing: answered 3 of 3" in table
    assert "duckduckgo: wall: captcha at query 1" in table
    assert "sha256:" + "b" * 64 in table
    assert "0.5.0" in table and "140.0.7339.16" in table
    assert engine_probe.ADDRESS_NOTE in table
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", table)
    assert "LEAKED TITLE" not in table and "leak.example" not in table
    assert re.search(r"date_utc: \d{4}-\d{2}-\d{2}T", table)


def test_to_json_round_trips_and_carries_no_titles() -> None:
    rows = [
        engine_probe.row_from_ack("bing", 0, _ok_ack("bing", 4), 900),
        engine_probe.skipped_row("bing", 1, "skipped_after_wall"),
    ]
    text = engine_probe.to_json(rows, _meta())
    data = json.loads(text)
    assert data["rows"] == rows
    assert data["meta"] == _meta()
    assert "LEAKED TITLE" not in text and "leak.example" not in text


# ------------------------------------------------------------------ (6) the window


def test_the_session_open_the_driver_sends_is_headless_chromium_after_the_clamp() -> None:
    clock = FakeClock()
    link = ScriptedLink(clock)
    _drive(link, clock, engines=("bing",), queries=("x",))
    sent = link.of("browser.session_open")[0]
    assert sent["profile"] == "research"
    clamped = cloud_policy.clamp_command("browser.session_open", sent)
    assert clamped["channel"] == "chromium"
    assert clamped["policy"]["visible"] is False
    assert sent == clamped  # the driver sends it already clamped


# ------------------------------------------------------------------ (7) the host script


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin"
        if (git_bash / "bash.exe").is_file():
            return str(git_bash / "bash.exe")
        return None
    return shutil.which("bash")


FAKE_DOCKER = """#!/usr/bin/env bash
{ printf 'CALL'; for a in "$@"; do printf '\\x1f%s' "$a"; done; printf '\\n'; } \\
  >> "$FAKE_DOCKER_LOG"
if [ "$1" = image ] && [ "$2" = inspect ]; then echo "sha256:feedface"; exit 0; fi
if [ "$1" = run ]; then
  for a in "$@"; do
    if [ "$a" = "-c" ]; then exit "${FAKE_MODULE_EXIT:-0}"; fi
  done
  echo "| engine | query | outcome |"
  exit 0
fi
exit 0
"""


def _run_script(
    tmp_path: Path, *, mem_kib: int, module_exit: int = 0
) -> tuple[Any, list[list[str]]]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is absent")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_bytes(FAKE_DOCKER.encode("utf-8"))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    meminfo = tmp_path / "meminfo"
    meminfo.write_bytes(
        f"MemTotal:       8000000 kB\nMemAvailable:   {mem_kib} kB\n".encode("ascii")
    )
    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_DOCKER_LOG": log.as_posix(),
        "FAKE_MODULE_EXIT": str(module_exit),
        "PAGENTOS_PROBE_MEMINFO": meminfo.as_posix(),
    }
    done = subprocess.run(
        [bash, SCRIPT.as_posix()],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = []
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            parts = line.split("\x1f")
            assert parts[0] == "CALL"
            calls.append(parts[1:])
    return done, calls


def _fragment_service() -> dict[str, Any]:
    return yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))["services"]["cloud-browser"]


def _flag(args: list[str], name: str) -> list[str]:
    values = []
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            values.append(args[i + 1])
        elif arg.startswith(name + "="):
            values.append(arg.split("=", 1)[1])
    return values


FORBIDDEN_VERBS = {"exec", "stop", "restart", "build", "compose", "rm", "kill", "start", "create"}


def _assert_safe(calls: list[list[str]]) -> None:
    for call in calls:
        assert call and call[0] not in FORBIDDEN_VERBS, call
        assert "pagentos-prod-cloud-browser" not in call, call
        joined = " ".join(call)
        assert "/mnt/pagentos-data" not in joined
        assert "PAGENTOS_CLOUD_BROKER_URL" not in joined
        assert "ENROLLMENT_TOKEN" not in joined
        assert "enroll.token" not in joined


def test_the_script_runs_a_throwaway_container_with_the_fragments_limits(tmp_path: Path) -> None:
    done, calls = _run_script(tmp_path, mem_kib=6 * 1024 * 1024)
    assert done.returncode == 0, done.stderr
    assert "| engine | query | outcome |" in done.stdout
    _assert_safe(calls)
    runs = [c for c in calls if c[0] == "run"]
    assert len(runs) == 2  # the module check, then the probe
    service = _fragment_service()
    for run in runs:
        assert run[1] == "--rm"
        assert _flag(run, "--memory") == [str(service["mem_limit"])]
        assert _flag(run, "--memory-swap") == [str(service["memswap_limit"])]
        assert _flag(run, "--shm-size") == [str(service["shm_size"])]
        assert _flag(run, "--pids-limit") == [str(service["pids_limit"])]
        assert _flag(run, "--cap-drop") == list(service["cap_drop"])
        assert _flag(run, "--security-opt") == list(service["security_opt"])
        assert not _flag(run, "-v") and not _flag(run, "--volume") and not _flag(run, "--mount")
        assert "pagentos/cloud-browser:local" in run
        assert any(a.startswith("/tmp") for a in _flag(run, "--tmpfs"))
    probe = runs[1]
    after_image = probe[probe.index("pagentos/cloud-browser:local") + 1 :]
    assert after_image[:2] == ["-m", "browser_agent.cloud.engine_probe"]
    assert _flag(probe, "--entrypoint") == ["python"]
    assert "PAGENTOS_PROBE_IMAGE_DIGEST=sha256:feedface" in _flag(probe, "-e")


def test_the_script_exits_three_when_the_image_has_no_probe(tmp_path: Path) -> None:
    done, calls = _run_script(tmp_path, mem_kib=6 * 1024 * 1024, module_exit=3)
    assert done.returncode == 3
    lines = [line for line in done.stdout.splitlines() + done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and "engine_probe" in lines[0]
    _assert_safe(calls)
    assert len([c for c in calls if c[0] == "run"]) == 1


def test_the_script_refuses_below_the_memory_floor(tmp_path: Path) -> None:
    done, calls = _run_script(tmp_path, mem_kib=2 * 1024 * 1024)
    assert done.returncode not in (0, 3)
    assert "memory" in (done.stdout + done.stderr).lower()
    assert not [c for c in calls if c[0] == "run"]

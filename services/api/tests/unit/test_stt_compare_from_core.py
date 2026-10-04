"""Unit tests: ``scripts/voice/stt-compare.ps1 -FromCore`` under Windows PowerShell 5.1
(measure-compare-from-core; team/plans/measure-compare-from-core-adr.md).

The REAL script runs against a loopback HTTP server started here whose handler FORWARDS every
request to the real application object through ``TestClient`` - no response is written by
hand, so the script and the API cannot drift apart. The recordings are uploaded through the
real PUT. No real engine can be called: every run passes ``-Engines chrome-web-speech``, the
engine keys are removed from the child's environment and its secret store is an empty folder.

The script runs from a byte copy of the repository's ``scripts`` tree (so "no .wav inside the
repository" is checkable), with ``-Python`` pointing at a wrapper that marks that the
measuring child was started and then runs this interpreter on the code under test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app as app_package
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.object_store import InMemoryObjectStore
from tests.identity_support import install_identity, issue_token
from tests.unit.test_measurement_recordings import b64, wav

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "voice" / "stt-compare.ps1"
#: The ``services/api`` the code under test lives in (the worktree's, never the venv's pin).
API_DIR = Path(app_package.__file__).resolve().parent.parent
CHROME = "chrome-web-speech"
OFIS_HEARD = "Ofisü bilgisayarında hesap makinesini açın."
NO_RECORDING_LINE = "Cloud Core'da ölçülecek kayıt yok"
ENGINE_KEYS = (
    "PAGENTOS_VOICE_OPENAI_API_KEY",
    "PAGENTOS_VOICE_SONIOX_API_KEY",
    "PAGENTOS_VOICE_AZURE_SPEECH_KEY",
    "PAGENTOS_VOICE_AZURE_SPEECH_REGION",
)
RUN_DEADLINE_S = 240


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


pytestmark = pytest.mark.skipif(
    _powershell() is None, reason="Windows PowerShell is not on this machine"
)


# ------------------------------------------------------------- the loopback Core


class _ForwardingServer(HTTPServer):
    client: TestClient
    #: Return different bytes for the first audio download (case c).
    tamper_audio: bool = False
    paths: list[str]


class _Forward(BaseHTTPRequestHandler):
    server: _ForwardingServer

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        self.server.paths.append(f"{self.command} {self.path}")
        response = self.server.client.request(
            self.command, self.path, content=body, headers=headers
        )
        data = response.content
        if self.server.tamper_audio and self.path.endswith("/audio"):
            self.server.tamper_audio = False
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        self.send_response(response.status_code)
        for key, value in response.headers.items():
            if key.lower() not in {"content-length", "transfer-encoding", "connection"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_PUT = do_POST = do_DELETE = _forward

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base's name
        return


@dataclass
class Core:
    url: str
    token: str
    owner: TestClient
    server: _ForwardingServer


@pytest.fixture()
def core() -> Iterator[Core]:
    settings = Settings(_env_file=None)
    application = create_app(settings)
    runtime = install_identity(application, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._store = InMemoryObjectStore()
    application.state.artifacts = artifacts
    token = issue_token(runtime)
    owner = TestClient(application, raise_server_exceptions=False)
    owner.headers["Authorization"] = f"Bearer {token}"
    server = _ForwardingServer(("127.0.0.1", 0), _Forward)
    server.client = TestClient(application, raise_server_exceptions=False)
    server.paths = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Core(f"http://127.0.0.1:{server.server_port}", token, owner, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _upload(core: Core, place: str, index: int, *, fill: int, transcript: str | None) -> None:
    response = core.owner.put(
        f"/v1/voice/measurement/recordings/{place}/{index}",
        json={
            "audio_wav_base64": b64(wav(0.5, fill=fill)),
            "browser_transcript": transcript,
            "browser_engine": "Chrome webkitSpeechRecognition" if transcript is not None else None,
        },
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------- the run


@dataclass
class Run:
    code: int
    out: str
    err: str
    evidence: Path
    python_started: bool

    def reports(self) -> list[Path]:
        return sorted(self.evidence.glob("*.json"))


def _repo_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    (copy / "scripts" / "voice").mkdir(parents=True)
    shutil.copy2(SCRIPT, copy / "scripts" / "voice" / SCRIPT.name)
    shutil.copytree(REPO / "scripts" / "lib", copy / "scripts" / "lib")
    (copy / "services" / "api").mkdir(parents=True)
    assert (copy / "scripts" / "voice" / SCRIPT.name).read_bytes() == SCRIPT.read_bytes()
    return copy


def _run(tmp_path: Path, core: Core | None, *arguments: str, mode: str = "file") -> Run:
    shell = _powershell()
    assert shell is not None
    copy = _repo_copy(tmp_path)
    script = copy / "scripts" / "voice" / SCRIPT.name
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    secrets_home = tmp_path / "localappdata"  # an empty secret store: no engine key exists
    secrets_home.mkdir()
    marker = tmp_path / "python-started.txt"
    wrapper = tmp_path / "python.cmd"
    lines = [
        "@echo off",
        f'echo started>"{marker}"',
        f'"{sys.executable}" %*',
        "exit /b %ERRORLEVEL%",
    ]
    wrapper.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", newline="")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in ENGINE_KEYS and not key.upper().startswith("PAGENTOS_OWNER")
    }
    env.update(
        {
            "LOCALAPPDATA": str(secrets_home),
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONPATH": str(API_DIR),
        }
    )
    common = ["-EvidenceDir", str(evidence), "-Engines", CHROME, "-Python", str(wrapper)]
    if core is not None:
        env["PAGENTOS_OWNER_SESSION_TOKEN"] = core.token
        common = ["-CoreUrl", core.url, *common]
    every = [*arguments, *common]
    if mode == "file":
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        command += ["-File", str(script), *every]
    else:
        quoted = " ".join(
            a if a.startswith("-") else "'" + a.replace("'", "''") + "'" for a in every
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        command += ["-Command", f"& '{script}' {quoted}; exit $LASTEXITCODE"]
    child = subprocess.Popen(  # noqa: S603 - a fixed interpreter and a copy of this repo's script
        command,
        cwd=copy,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = child.communicate(timeout=RUN_DEADLINE_S)
    except subprocess.TimeoutExpired:
        # the child is killed together with what it started (the python under the wrapper)
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run(  # noqa: S603
            [str(taskkill), "/T", "/F", "/PID", str(child.pid)], check=False, timeout=60
        )
        child.communicate(timeout=60)
        pytest.fail(f"stt-compare.ps1 did not finish in {RUN_DEADLINE_S} s")
    run = Run(
        child.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
        evidence,
        marker.exists(),
    )
    # (d) success or failure: no folder of the script's in the temp directory, no .wav in
    # the repository copy
    assert [p.name for p in temp.iterdir() if p.name.startswith("pagentos-stt-compare-")] == []
    assert list(copy.rglob("*.wav")) == []
    if core is not None:
        # (e) the token is never printed and never in the report
        written = "".join(p.read_text(encoding="utf-8") for p in evidence.iterdir())
        assert core.token not in run.out + run.err + written
    return run


# ------------------------------------------------------------------- the cases


@pytest.mark.parametrize("mode", ["file", "command"])
def test_two_recordings_on_the_core_become_a_report_with_the_chrome_row(
    tmp_path: Path, core: Core, mode: str
) -> None:
    _upload(core, "ev", 1, fill=3, transcript=OFIS_HEARD)
    _upload(core, "ev", 2, fill=9, transcript=None)
    run = _run(tmp_path, core, "-FromCore", mode=mode)
    assert run.code == 0, run.out + run.err
    assert run.python_started
    [report_path] = run.reports()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.1"
    assert report["recordings"]["usable"] == 2
    [chrome] = [row for row in report["engines"] if row["label"] == CHROME]
    assert (chrome["status"], chrome["source"]) == ("RAN", "recorded_live")
    assert (chrome["files_ran"], chrome["files_failed"]) == (1, 1)
    assert chrome["errors"] == [{"id": "ev-02.wav", "error_class": "no ready transcript"}]
    # nothing was sent to any engine by this run
    assert report["audio_sent_to"] == []
    assert [entry["engine"] for entry in report["heard_live_by"]] == [CHROME]
    assert [item["id"] for item in report["items"]] == ["ev-01.wav", "ev-02.wav"]
    assert report_path.with_suffix(".md").is_file()
    # both audio files were downloaded, under the owner session
    audio_reads = [p for p in core.server.paths if p.endswith("/audio")]
    assert audio_reads == [
        "GET /v1/voice/measurement/recordings/ev/1/audio",
        "GET /v1/voice/measurement/recordings/ev/2/audio",
    ]


def test_no_recording_on_the_core_is_one_turkish_line_and_no_report(
    tmp_path: Path, core: Core
) -> None:
    run = _run(tmp_path, core, "-FromCore")
    assert run.code == 0, run.out + run.err
    assert NO_RECORDING_LINE in run.out
    assert list(run.evidence.iterdir()) == []
    assert not run.python_started


def test_the_place_filter_reads_only_that_place(tmp_path: Path, core: Core) -> None:
    _upload(core, "ofis", 3, fill=5, transcript="")
    run = _run(tmp_path, core, "-FromCore", "-Place", "ev")
    assert run.code == 0, run.out + run.err
    assert NO_RECORDING_LINE in run.out
    assert not run.python_started
    assert "GET /v1/voice/measurement/manifest?place=ev" in core.server.paths


def test_a_download_whose_sha256_differs_stops_before_any_engine(
    tmp_path: Path, core: Core
) -> None:
    _upload(core, "ev", 1, fill=3, transcript=OFIS_HEARD)
    _upload(core, "ofis", 1, fill=4, transcript=OFIS_HEARD)
    core.server.tamper_audio = True
    run = _run(tmp_path, core, "-FromCore")
    assert run.code != 0
    assert "sha256" in run.out
    assert run.reports() == []
    assert list(run.evidence.iterdir()) == []
    assert not run.python_started


def test_from_core_together_with_folder_is_refused(tmp_path: Path, core: Core) -> None:
    folder = tmp_path / "kayitlar"
    folder.mkdir()
    run = _run(tmp_path, core, "-FromCore", "-Folder", str(folder))
    assert run.code != 0
    assert not run.python_started
    assert list(folder.iterdir()) == []
    assert core.server.paths == []


def test_folder_alone_still_writes_the_template_when_there_is_no_manifest(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "kayitlar"
    folder.mkdir()
    run = _run(tmp_path, None, "-Folder", str(folder))
    assert run.code == 0, run.out + run.err
    assert run.python_started
    assert (folder / "manifest.template.json").is_file()
    assert not (folder / "manifest.json").exists()
    assert run.reports() == []


def test_the_script_merges_no_native_stderr_into_the_pipeline() -> None:
    assert "2>&1" not in SCRIPT.read_text(encoding="utf-8-sig")

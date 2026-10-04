"""Unit tests: the REAL ``scripts/voice/tts-measure.ps1`` under Windows PowerShell 5.1
(tts-freya-measure; team/plans/tts-freya-measure-adr.md).

docker is replaced by a fake executable (``-Docker``, the script's one overridable path): a
``.cmd`` that runs ``fake_docker.py`` written here. The fake logs every call, answers ``build``,
``image inspect``, ``volume create`` and ``rm -f``, and for the synthesis ``run`` reads the JSON
lines on stdin, writes a tiny WAV per sentence into the ``/out`` mount and prints canned JSON
lines. ``FAKE_DOCKER_MODE`` turns it into a weight-hash mismatch or a container that hangs.

The script runs from a byte copy of the repository's ``scripts`` and ``tools/tts-measure``
(so "no .wav inside the repository" is checkable) with ``-Python`` pointing at this
interpreter on the code under test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import app as app_package

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "voice" / "tts-measure.ps1"
TOOL = REPO / "tools" / "tts-measure"
API_DIR = Path(app_package.__file__).resolve().parent.parent
RUN_DEADLINE_S = 240

FAKE_DOCKER = r'''
import json, os, re, struct, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
state = os.environ["FAKE_DOCKER_STATE"]
if not args:
    sys.exit(2)
if args[0] == "version":
    print("fake 1.0"); sys.exit(0)
if args[:2] == ["image", "inspect"]:
    sys.exit(0 if os.path.exists(state) else 1)
if args[0] == "build":
    open(state, "w").close(); print("built"); sys.exit(0)
if args[0] in ("rm", "volume", "image"):
    sys.exit(0)
if args[0] != "run":
    sys.exit(2)
if "selfcheck" in args:
    print("IMPORT_OK 2.11.0+cpu False 4 AudioVAEV2"); sys.exit(0)
if "fill" in args:
    print(json.dumps({"event": "fill", "ok": True})); sys.exit(0)
if mode == "hang":
    time.sleep(600); sys.exit(0)
if mode == "mismatch":
    print(json.dumps({"event": "error", "error": "weight_hash_mismatch", "files": ["model.safetensors"]}))
    sys.exit(3)
out = None
for i, a in enumerate(args):
    if a == "-v" and args[i + 1].endswith(":/out"):
        out = args[i + 1][: -len(":/out")]
print(json.dumps({"event": "load", "load_ms": 4321.0, "peak_rss_mb": 1500.0, "cpu": "Fake CPU", "threads": 4}))
for raw in sys.stdin.buffer.read().decode("utf-8").splitlines():
    row = json.loads(raw)
    data = b"\x00\x00" * 480
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16) + b"data" + struct.pack("<I", len(data))
    with open(os.path.join(out, "%02d.wav" % row["index"]), "wb") as f:
        f.write(header + data)
    print(json.dumps({"index": row["index"], "chars": len(row["text"]), "audio_ms": 1000.0, "synth_ms": 800.0, "first_audio_ms": 800.0, "streamed": False, "peak_rss_mb": 1600.0, "retries": 0}))
sys.exit(0)
'''


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


@dataclass
class Run:
    code: int
    out: str
    err: str
    evidence: Path
    localappdata: Path
    calls: list[list[str]]


def _repo_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    (copy / "scripts" / "voice").mkdir(parents=True)
    shutil.copy2(SCRIPT, copy / "scripts" / "voice" / SCRIPT.name)
    shutil.copytree(TOOL, copy / "tools" / "tts-measure")
    (copy / "services" / "api").mkdir(parents=True)
    (copy / "docs" / "evidence").mkdir(parents=True)
    assert (copy / "scripts" / "voice" / SCRIPT.name).read_bytes() == SCRIPT.read_bytes()
    return copy


def _run(
    tmp_path: Path, *arguments: str, mode: str = "file", fake: str = "ok", host: str = "MAIL"
) -> Run:
    shell = _powershell()
    assert shell is not None
    copy = _repo_copy(tmp_path)
    script = copy / "scripts" / "voice" / SCRIPT.name
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    localappdata = tmp_path / "localappdata"
    localappdata.mkdir()
    (tmp_path / "fake_docker.py").write_text(FAKE_DOCKER, encoding="utf-8")
    log = tmp_path / "docker-calls.jsonl"
    docker = tmp_path / "docker.cmd"
    docker.write_text(
        "\r\n".join(
            ["@echo off", f'"{sys.executable}" "{tmp_path / "fake_docker.py"}" %*', "exit /b %ERRORLEVEL%"]
        )
        + "\r\n",
        encoding="ascii",
        newline="",
    )
    env = dict(os.environ)
    env.update(
        {
            "LOCALAPPDATA": str(localappdata),
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONPATH": str(API_DIR),
            "COMPUTERNAME": host,
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_MODE": fake,
            "FAKE_DOCKER_STATE": str(tmp_path / "image-built"),
        }
    )
    common = [
        "-EvidenceDir", str(evidence),
        "-Docker", str(docker),
        "-Python", sys.executable,
        "-MinFreeGB", "0",
    ]
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
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run(  # noqa: S603
            [str(taskkill), "/T", "/F", "/PID", str(child.pid)], check=False, timeout=60
        )
        child.communicate(timeout=60)
        pytest.fail(f"tts-measure.ps1 did not finish in {RUN_DEADLINE_S} s")
    calls = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    # success or failure: no .wav in the repository copy, no work folder left in temp
    assert list(copy.rglob("*.wav")) == []
    assert [p.name for p in temp.iterdir() if p.name.startswith("pagentos-tts-measure-")] == []
    return Run(
        child.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
        evidence,
        localappdata,
        calls,
    )


def _assert_green(run: Run, label: str) -> None:
    assert run.code == 0, run.out + run.err
    report = json.loads((run.evidence / "tts-freya-measure.json").read_text(encoding="utf-8"))
    text = (run.evidence / "tts-freya-measure.md").read_text(encoding="utf-8")
    [machine] = [m for m in report["machines"] if m["label"] == label]
    assert machine["sentences_ok"] == 20
    assert machine["rtf_pooled"] == 0.8
    assert machine["load_ms"] == 4321.0
    wavs = run.localappdata / "PagentOS" / "tts-measure" / label
    assert sorted(p.name for p in wavs.glob("*.wav")) == [f"{i:02d}.wav" for i in range(1, 21)]
    assert str(wavs / "05.wav") in text
    synth = [c for c in run.calls if c[0] == "run" and "synth" in c]
    assert len(synth) == 1
    assert synth[0][synth[0].index("--network") + 1] == "none"


def test_script_green_with_file(tmp_path: Path) -> None:
    run = _run(tmp_path, "-Label", "ev-pc")
    _assert_green(run, "ev-pc")
    assert any(c[0] == "build" for c in run.calls)


def test_script_green_with_command_ampersand(tmp_path: Path) -> None:
    run = _run(tmp_path, "-Label", "cpx32-bicimi", "-Cpus", "4", "-Threads", "4", mode="command")
    _assert_green(run, "cpx32-bicimi")
    synth = [c for c in run.calls if c[0] == "run" and "synth" in c][0]
    assert synth[synth.index("--cpus") + 1] == "4"
    assert synth[synth.index("--threads") + 1] == "4"


def test_script_weight_hash_mismatch_exits_nonzero_without_evidence(tmp_path: Path) -> None:
    run = _run(tmp_path, "-Label", "ev-pc", fake="mismatch")
    assert run.code != 0
    assert list(run.evidence.iterdir()) == []
    assert "weight_hash_mismatch" in run.out + run.err


def test_script_hanging_container_is_killed_by_deadline_and_removed(tmp_path: Path) -> None:
    run = _run(tmp_path, "-Label", "ev-pc", "-TimeoutSec", "5", fake="hang")
    assert run.code != 0
    assert list(run.evidence.iterdir()) == []
    synth = [c for c in run.calls if c[0] == "run" and "synth" in c][0]
    name = synth[synth.index("--name") + 1]
    assert ["rm", "-f", name] in run.calls


def test_script_refuses_a_host_outside_the_allow_list(tmp_path: Path) -> None:
    run = _run(tmp_path, "-Label", "ev-pc", host="OFIS-PC")
    assert run.code != 0
    assert run.calls == []
    assert list(run.evidence.iterdir()) == []


def test_script_text_has_no_stderr_merge() -> None:
    assert "2>&1" not in SCRIPT.read_text(encoding="utf-8")

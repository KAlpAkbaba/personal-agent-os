# ruff: noqa: E501 - the fake docker below is one literal program
"""Unit tests: the REAL ``scripts/voice/speaker-compare.ps1`` under Windows PowerShell 5.1
(speaker-engine-measure; team/plans/speaker-engine-measure-adr.md).

docker is replaced by a fake executable (``-Docker``): a ``.cmd`` running ``fake_docker.py``
written here. The fake logs every call and, for each measurement ``run``, the names of the
files in the ``/in`` mount at that moment - so "the guest's recording was never mounted" is
read from what the container would have seen. It answers ``measure`` with canned JSON lines
and writes a tiny WAV for ``splice``. ``FAKE_DOCKER_MODE=mismatch`` turns it into a weight-hash
mismatch.

The script runs from a byte copy of ``scripts`` and ``tools/speaker-measure`` (so "no .wav
inside the repository" is checkable) with ``-Python`` pointing at this interpreter on the code
under test. The ``-FromCore`` cases reuse the loopback Core of test_stt_compare_from_core.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import app as app_package
from tests.unit.test_measurement_recordings import wav
from tests.unit.test_stt_compare_from_core import Core, _upload, core  # noqa: F401 - the fixture

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "voice" / "speaker-compare.ps1"
TOOL = REPO / "tools" / "speaker-measure"
API_DIR = Path(app_package.__file__).resolve().parent.parent
RUN_DEADLINE_S = 240
NO_CONSENT = "rızasız: ölçülmedi"

FAKE_DOCKER = r"""
import json, os, struct, sys
args = sys.argv[1:]
mounts = {}
for i, a in enumerate(args):
    if a == "-v" and ":/" in args[i + 1]:
        source, target = args[i + 1].split(":/", 1)
        mounts["/" + target.split(":")[0]] = source
entry = {"args": args}
if args and args[0] == "run" and "/in" in mounts and os.path.isdir(mounts["/in"]):
    entry["in"] = sorted(os.listdir(mounts["/in"]))
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(entry) + "\n")
mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
state = os.environ["FAKE_DOCKER_STATE"]
if not args:
    sys.exit(2)
if args[:2] == ["image", "inspect"]:
    if "--format" in args:
        print("sha256:fakeimageid"); sys.exit(0)
    sys.exit(0 if os.path.exists(state) else 1)
if args[0] == "build":
    open(state, "w").close(); print("built"); sys.exit(0)
if args[0] in ("rm", "volume"):
    sys.exit(0)
if args[0] != "run":
    sys.exit(2)
if "selfcheck" in args:
    print("SELFCHECK_OK 1.13.8 2.5.3"); sys.exit(0)
if "fill" in args:
    print(json.dumps({"kind": "fill", "ok": True})); sys.exit(0)
if "splice" in args:
    data = b"\x00\x00" * 16000
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(data))
    with open(os.path.join(mounts["/out"], "eklenti.wav"), "wb") as f:
        f.write(header + data)
    print(json.dumps({"kind": "splice", "segments": 4, "audio_ms": 1000.0})); sys.exit(0)
if mode == "mismatch":
    print(json.dumps({"kind": "error", "error": "weight_hash_mismatch", "files": ["campplus-zh-en-advanced"]}))
    sys.exit(3)
model = args[args.index("--model") + 1]
print(json.dumps({"kind": "load", "load_ms": {model: 900.0, "pyannote-seg-3-0": 300.0}, "peak_rss_mb": 410.0, "cpu": "Fake CPU", "threads": 4}))
canned = {"same": [0.9, 0.8, 0.7], "same_long": [0.85], "diff": [0.2, 0.5, 0.1, 0.3]}
for raw in sys.stdin.buffer.read().decode("utf-8").splitlines():
    job = json.loads(raw)
    if job["kind"] == "embed":
        print(json.dumps({"kind": "embed", "model": model, "label": job["label"], "chunks": 1, "audio_ms": 3000.0, "wall_ms": 300.0, "peak_rss_mb": 420.0}))
    elif job["kind"] == "score":
        print(json.dumps({"kind": "score", "model": model, "name": job["name"], "n": len(canned[job["name"]]), "scores": canned[job["name"]]}))
    elif job["kind"] == "diarize":
        print(json.dumps({"kind": "diarize", "model": model, "label": job["label"], "num_speakers": job["num_speakers"], "segments": [{"start_s": 0.0, "end_s": 2.0, "speaker": 0}, {"start_s": 2.5, "end_s": 5.0, "speaker": 1}], "speakers_found": 2, "audio_ms": 6000.0, "wall_ms": 600.0, "peak_rss_mb": 450.0}))
sys.exit(0)
"""


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
    calls: list[dict[str, Any]]

    def runs(self, word: str) -> list[list[str]]:
        return [c["args"] for c in self.calls if c["args"][0] == "run" and word in c["args"]]

    def mounted(self) -> list[str]:
        return [name for c in self.calls for name in c.get("in", [])]


def _folder(tmp_path: Path, *, consent: bool, sentences: bool = True) -> Path:
    folder = tmp_path / "kayitlar"
    folder.mkdir()
    if sentences:
        for n in (1, 2, 3):
            (folder / f"ev-{n:02d}.wav").write_bytes(wav(1.0, fill=n))
    (folder / "owner.wav").write_bytes(wav(10.0, fill=5))
    (folder / "guest.wav").write_bytes(wav(12.0, fill=9))
    (folder / "conversation.wav").write_bytes(wav(6.0, fill=11))
    if consent:
        (folder / "consent.json").write_text(
            json.dumps({"guest_consent": True, "consent_date": "2026-10-07"}), encoding="utf-8"
        )
    return folder


def _repo_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    (copy / "scripts" / "voice").mkdir(parents=True)
    shutil.copy2(SCRIPT, copy / "scripts" / "voice" / SCRIPT.name)
    shutil.copytree(REPO / "scripts" / "lib", copy / "scripts" / "lib")
    shutil.copytree(TOOL, copy / "tools" / "speaker-measure")
    (copy / "services" / "api").mkdir(parents=True)
    (copy / "docs" / "evidence").mkdir(parents=True)
    assert (copy / "scripts" / "voice" / SCRIPT.name).read_bytes() == SCRIPT.read_bytes()
    return copy


def _run(
    tmp_path: Path,
    *arguments: str,
    mode: str = "file",
    fake: str = "ok",
    core: Core | None = None,  # noqa: F811 - the fixture's name
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
            [
                "@echo off",
                f'"{sys.executable}" "{tmp_path / "fake_docker.py"}" %*',
                "exit /b %ERRORLEVEL%",
            ]
        )
        + "\r\n",
        encoding="ascii",
        newline="",
    )
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAGENTOS_OWNER")}
    env.update(
        {
            "LOCALAPPDATA": str(localappdata),
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONPATH": str(API_DIR),
            "COMPUTERNAME": "MAIL",
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_MODE": fake,
            "FAKE_DOCKER_STATE": str(tmp_path / "image-built"),
        }
    )
    common = [
        "-EvidenceDir",
        str(evidence),
        "-Docker",
        str(docker),
        "-Python",
        sys.executable,
        "-MinFreeGB",
        "0",
    ]
    if core is not None:
        env["PAGENTOS_OWNER_SESSION_TOKEN"] = core.token
        common = ["-CoreUrl", core.url, *common]
    every = [*arguments, *common]
    command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if mode == "file":
        command += ["-File", str(script), *every]
    else:
        quoted = " ".join(
            a if a.startswith("-") else "'" + a.replace("'", "''") + "'" for a in every
        )
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
        pytest.fail(f"speaker-compare.ps1 did not finish in {RUN_DEADLINE_S} s")
    calls = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    # success or failure: no .wav in the repository copy, no work folder left in temp
    assert list(copy.rglob("*.wav")) == []
    assert [p.name for p in temp.iterdir() if p.name.startswith("pagentos-speaker-")] == []
    run = Run(
        child.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
        evidence,
        localappdata,
        calls,
    )
    if core is not None:
        written = "".join(p.read_text(encoding="utf-8") for p in evidence.iterdir())
        assert core.token not in run.out + run.err + written
    return run


def _measurement_runs(run: Run) -> list[list[str]]:
    return run.runs("measure") + run.runs("splice")


def _assert_offline(run: Run) -> None:
    measured = _measurement_runs(run)
    assert measured
    for args in measured:
        assert args[args.index("--network") + 1] == "none", args
        assert "--read-only" in args, args
        assert args[args.index("--user") + 1] == "10001", args
        [image] = [a for a in args if a.startswith("pagentos-speaker-measure:")]
        assert len(image.split(":", 1)[1]) == 12


@pytest.mark.parametrize("mode", ["file", "command"])
def test_folder_with_consent_measures_and_writes_evidence_outside_the_repo(
    tmp_path: Path, mode: str
) -> None:
    folder = _folder(tmp_path, consent=True)
    run = _run(tmp_path, "-Label", "ev-pc", "-Folder", str(folder), mode=mode)
    assert run.code == 0, run.out + run.err
    report = json.loads((run.evidence / "speaker-measure.json").read_text(encoding="utf-8"))
    text = (run.evidence / "speaker-measure.md").read_text(encoding="utf-8")
    [shape] = report["shapes"]
    assert (shape["label"], shape["consent"]) == ("ev-pc", True)
    assert [m["status"] for m in shape["models"]] == ["RAN", "RAN", "RAN"]
    assert shape["models"][0]["eer_pct"] is not None
    assert shape["inputs"]["sahip_cumle"] == 3
    assert NO_CONSENT not in text
    listen = run.localappdata / "PagentOS" / "speaker-measure" / "ev-pc"
    assert (listen / "eklenti.wav").is_file()
    assert (listen / "konusma.wav").is_file()
    assert "konuşmacı" in (listen / "eklenti-zaman.txt").read_text(encoding="utf-8")
    assert shape["listen_dir"] == str(listen)
    _assert_offline(run)
    assert len(run.runs("measure")) == 3  # one container per embedding model
    assert "konuk.wav" in run.mounted()
    assert "consent.json" not in run.mounted()


def test_folder_without_consent_never_mounts_the_guest(tmp_path: Path) -> None:
    folder = _folder(tmp_path, consent=False)
    run = _run(tmp_path, "-Label", "ev-pc", "-Folder", str(folder))
    assert run.code == 0, run.out + run.err
    text = (run.evidence / "speaker-measure.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("campplus-zh-en-advanced |")]
    assert rows and NO_CONSENT in rows[0]
    report = json.loads((run.evidence / "speaker-measure.json").read_text(encoding="utf-8"))
    assert report["shapes"][0]["consent"] is False
    assert report["shapes"][0]["inputs"]["konuk_s"] is None
    mounted = run.mounted()
    assert mounted and "sahip-01.wav" in mounted
    assert not any(name in mounted for name in ("konuk.wav", "konusma.wav", "guest.wav"))
    assert not any("guest" in arg or "konuk" in arg for c in run.calls for arg in c["args"])
    assert run.runs("splice") == []


def test_a_weight_hash_mismatch_stops_with_no_evidence(tmp_path: Path) -> None:
    folder = _folder(tmp_path, consent=True)
    run = _run(tmp_path, "-Label", "ev-pc", "-Folder", str(folder), fake="mismatch")
    assert run.code != 0
    assert list(run.evidence.iterdir()) == []
    assert "weight_hash_mismatch" in run.out + run.err


def test_a_folder_holding_another_file_is_refused_before_docker(tmp_path: Path) -> None:
    folder = _folder(tmp_path, consent=True)
    (folder / "ayse.wav").write_bytes(wav(1.0))
    run = _run(tmp_path, "-Label", "ev-pc", "-Folder", str(folder))
    assert run.code == 2
    assert run.calls == []
    assert list(run.evidence.iterdir()) == []


def test_from_core_downloads_checks_and_removes_the_recordings(
    tmp_path: Path,
    core: Core,  # noqa: F811 - the fixture
) -> None:
    _upload(core, "ev", 1, fill=3, transcript=None)
    _upload(core, "ev", 2, fill=9, transcript=None)
    folder = _folder(tmp_path, consent=True, sentences=False)
    run = _run(tmp_path, "-Label", "ev-pc", "-FromCore", "-Folder", str(folder), core=core)
    assert run.code == 0, run.out + run.err
    assert {"sahip-01.wav", "sahip-02.wav"} <= set(run.mounted())
    report = json.loads((run.evidence / "speaker-measure.json").read_text(encoding="utf-8"))
    assert report["shapes"][0]["inputs"]["sahip_cumle"] == 2
    audio_reads = [p for p in core.server.paths if p.endswith("/audio")]
    assert audio_reads == [
        "GET /v1/voice/measurement/recordings/ev/1/audio",
        "GET /v1/voice/measurement/recordings/ev/2/audio",
    ]


def test_from_core_a_tampered_download_stops_before_any_container(
    tmp_path: Path,
    core: Core,  # noqa: F811 - the fixture
) -> None:
    _upload(core, "ev", 1, fill=3, transcript=None)
    _upload(core, "ev", 2, fill=9, transcript=None)
    core.server.tamper_audio = True
    folder = _folder(tmp_path, consent=True, sentences=False)
    run = _run(tmp_path, "-Label", "ev-pc", "-FromCore", "-Folder", str(folder), core=core)
    assert run.code != 0
    assert "sha256" in run.out + run.err
    assert [c for c in run.calls if c["args"][0] == "run"] == []
    assert list(run.evidence.iterdir()) == []


def test_the_script_merges_no_native_stderr_into_the_pipeline() -> None:
    assert "2>&1" not in SCRIPT.read_text(encoding="utf-8-sig")

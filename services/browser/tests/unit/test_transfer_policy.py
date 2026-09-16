"""B31 req 180/181: bounded, authorised transfers - and no flag without an operation.

NO BROWSER IS LAUNCHED HERE. The worker is built in-process and its session is a fake
that writes (download) or receives (upload) a real file under the worker's data dir, the
way ``tests/unit/test_media_ops.py`` drives the media operations.

- 181 ("yalan duyuru"): every capability FLAG that names an operation has a handler in
  the worker's dispatch table. Until this batch ``uploads=True`` was advertised on every
  backend and ``browser.upload`` did not exist.
- 180: an ``authorization_ref`` has a shape; a file over the cap is refused (and a
  download over the cap is deleted); a download cannot leave ``file_io_root``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from browser_agent import policy
from browser_agent.backends import ManagedBackend
from browser_agent.capabilities import CAPABILITY_FLAGS
from browser_agent.detect import BrowserInfo
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.session import BrowserSession, DownloadResult
from browser_agent.worker import (
    _HANDLERS,
    AUTHORIZATION_REF_RE,
    MAX_TRANSFER_BYTES,
    SessionState,
    Worker,
    build_arg_parser,
    enforce_transfer_size,
    require_transfer_authorization,
    transfer_cap,
)

#: The flags that promise an OPERATION (the others describe the session: authenticated,
#: extensions, existing tabs, multiple windows, headless).
OPERATION_FLAGS = {"downloads": "browser.download", "uploads": "browser.upload"}

GOOD_REF = "approval:2026-09-14:0001"


# --------------------------------------------------------------------------- fakes


class _FakeBackend:
    main_pid = 4242
    last_launch_kind = "dedicated"
    launch_lock_name = None
    job_object_assigned = False
    persistent = False

    def capabilities(self):
        return ManagedBackend().capabilities()


class _FakeBrowserSession:
    """Answers download/upload the way the real one would have, minus the browser."""

    def __init__(self, *, download_bytes: int = 16) -> None:
        self.backend = _FakeBackend()
        self.download_bytes = download_bytes
        self.uploaded: list[tuple[Any, Path]] = []

    async def download(self, target, *, save_dir=None, timeout_ms=15_000) -> DownloadResult:
        directory = Path(save_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "sample.bin"
        path.write_bytes(b"x" * self.download_bytes)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return DownloadResult(path=path, sha256=digest, suggested_filename="sample.bin")

    async def upload(self, target, file_path, *, timeout_ms=5_000, frame=None) -> None:
        self.uploaded.append((target, Path(file_path)))


def _make_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Worker:
    argv = ["--data-dir", str(tmp_path / "data"), "--channel", "chrome", "--headless"]
    worker = Worker(build_arg_parser().parse_args(argv))

    async def fake_detect(_channel):
        return BrowserInfo(
            channel="chrome", available=True, version="999.0.0.0", executable_path="/fake"
        )

    monkeypatch.setattr("browser_agent.worker.detect_browser", fake_detect)
    return worker


def _state(
    worker: Worker, session: _FakeBrowserSession, *, allowed: frozenset[policy.RiskClass]
) -> SessionState:
    state = SessionState(
        session_id="s1",
        browser_session=session,  # type: ignore[arg-type]
        backend=session.backend,  # type: ignore[arg-type]
        policy_allowed=allowed,
        visible=True,
        channel="chrome",
        browser_version="999.0.0.0",
        profile="research",
        session_uid="uid-1",
        profile_dir=None,
        session_kind="research",
        last_used=0.0,
    )
    worker._sessions["s1"] = state
    return state


ALL = frozenset(policy.RiskClass)
READ_ONLY = frozenset({policy.RiskClass.READ, policy.RiskClass.NAVIGATE})


# ----------------------------------------------------------- 181: no flag without an op


def test_every_capability_flag_that_names_an_operation_has_a_handler() -> None:
    for flag, operation in OPERATION_FLAGS.items():
        assert flag in CAPABILITY_FLAGS, flag
        assert operation in _HANDLERS, f"{flag}=True is advertised with no {operation} operation"


def test_the_backends_advertise_only_what_the_worker_can_do() -> None:
    caps = ManagedBackend().capabilities().as_dict()
    for flag, operation in OPERATION_FLAGS.items():
        if caps.get(flag):
            assert operation in _HANDLERS, flag


# ----------------------------------------------------------------- 180: the gate


@pytest.mark.parametrize(
    ("ref", "ok"),
    [
        (GOOD_REF, True),
        ("owner-approved-1234", True),
        ("a" * 8, True),
        ("a" * 128, True),
        ("", False),
        ("x", False),
        ("short-1", False),
        ("a" * 129, False),
        ("has space 1234", False),
        ("-leading-dash", False),
        (None, False),
        (1234567890, False),
    ],
)
def test_the_authorisation_reference_has_a_shape(ref: object, ok: bool) -> None:
    class _State:
        policy_allowed = ALL

    if ok:
        assert require_transfer_authorization(_State(), {"authorization_ref": ref}, op="t") == ref
        assert AUTHORIZATION_REF_RE.match(str(ref))
    else:
        with pytest.raises(BrowserError) as exc_info:
            require_transfer_authorization(_State(), {"authorization_ref": ref}, op="t")
        assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
        assert exc_info.value.evidence["authorization_ref_well_formed"] is False


def test_the_gate_needs_high_impact_whatever_the_reference() -> None:
    class _State:
        policy_allowed = READ_ONLY

    with pytest.raises(BrowserError) as exc_info:
        require_transfer_authorization(_State(), {"authorization_ref": GOOD_REF}, op="t")
    assert exc_info.value.evidence["high_impact_allowed"] is False
    assert exc_info.value.evidence["authorization_ref_well_formed"] is True


def test_the_cap_can_be_lowered_never_raised() -> None:
    assert transfer_cap({}) == MAX_TRANSFER_BYTES
    assert transfer_cap({"max_bytes": 1024}) == 1024
    assert transfer_cap({"max_bytes": MAX_TRANSFER_BYTES * 10}) == MAX_TRANSFER_BYTES
    for bad in (0, -1, "big", "1.5"):
        with pytest.raises(BrowserError):
            transfer_cap({"max_bytes": bad})


def test_an_oversized_download_is_deleted_and_refused(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"y" * 2048)
    with pytest.raises(BrowserError) as exc_info:
        enforce_transfer_size(big, cap=1024, op="browser.download")
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert not big.exists(), "an oversized download must not stay behind"
    small = tmp_path / "small.bin"
    small.write_bytes(b"z" * 10)
    assert enforce_transfer_size(small, cap=1024, op="browser.download") == 10
    assert small.exists()


def test_an_oversized_upload_is_refused_and_kept(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"y" * 2048)
    with pytest.raises(BrowserError):
        enforce_transfer_size(big, cap=1024, op="browser.upload", delete=False)
    assert big.exists(), "the owner's own file is never deleted"


# ------------------------------------------------------- the two ops, in the worker


async def test_download_through_the_worker_is_capped_and_lands_in_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path, monkeypatch)
    session = _FakeBrowserSession(download_bytes=64)
    _state(worker, session, allowed=ALL)
    result = await worker._execute(
        "browser.download",
        {
            "session_id": "s1",
            "target": {"role": "link", "name": "x"},
            "authorization_ref": GOOD_REF,
        },
    )
    assert result["bytes"] == 64
    assert result["max_bytes"] == MAX_TRANSFER_BYTES
    assert result["authorization_ref"] == GOOD_REF
    assert Path(result["path"]).is_relative_to(worker._data_dir / "downloads")

    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.download",
            {
                "session_id": "s1",
                "target": {"role": "link", "name": "x"},
                "authorization_ref": GOOD_REF,
                "max_bytes": 32,
            },
        )
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert not (worker._data_dir / "downloads" / "sample.bin").exists()


async def test_download_is_refused_before_any_click_without_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path, monkeypatch)
    session = _FakeBrowserSession()
    _state(worker, session, allowed=ALL)
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.download",
            {
                "session_id": "s1",
                "target": {"role": "link", "name": "x"},
                "authorization_ref": "x",
            },
        )
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert not (worker._data_dir / "downloads").exists(), "nothing was downloaded"


async def test_upload_sends_a_file_from_uploads_only_after_the_gate_and_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path, monkeypatch)
    session = _FakeBrowserSession()
    _state(worker, session, allowed=ALL)
    uploads = worker._data_dir / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "cv.pdf").write_bytes(b"%PDF" * 10)
    result = await worker._execute(
        "browser.upload",
        {
            "session_id": "s1",
            "target": {"role": "button", "name": "Dosya seç"},
            "path": "cv.pdf",
            "authorization_ref": GOOD_REF,
        },
    )
    assert result["uploaded"] is True
    assert result["bytes"] == 40
    assert result["sha256"] == hashlib.sha256(b"%PDF" * 10).hexdigest()
    assert session.uploaded and session.uploaded[0][1] == (uploads / "cv.pdf").resolve()

    # Outside uploads/: refused, nothing sent.
    elsewhere = tmp_path / "secret.txt"
    elsewhere.write_text("x")
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.upload",
            {
                "session_id": "s1",
                "target": {"role": "button", "name": "Dosya seç"},
                "path": str(elsewhere),
                "authorization_ref": GOOD_REF,
            },
        )
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert len(session.uploaded) == 1

    # Over the cap: refused before the page sees it, the file kept.
    with pytest.raises(BrowserError):
        await worker._execute(
            "browser.upload",
            {
                "session_id": "s1",
                "target": {"role": "button", "name": "Dosya seç"},
                "path": "cv.pdf",
                "authorization_ref": GOOD_REF,
                "max_bytes": 8,
            },
        )
    assert len(session.uploaded) == 1
    assert (uploads / "cv.pdf").exists()

    # Without the gate: refused, nothing sent.
    _state(worker, session, allowed=READ_ONLY)
    with pytest.raises(BrowserError):
        await worker._execute(
            "browser.upload",
            {
                "session_id": "s1",
                "target": {"role": "button", "name": "Dosya seç"},
                "path": "cv.pdf",
                "authorization_ref": GOOD_REF,
            },
        )
    assert len(session.uploaded) == 1


def test_a_download_cannot_leave_the_file_io_root(tmp_path: Path) -> None:
    """The session-level containment (the worker sets file_io_root to its data dir)."""
    root = tmp_path / "root"
    root.mkdir()
    session = BrowserSession.__new__(BrowserSession)
    session._file_io_root = root.resolve()
    with pytest.raises(BrowserError) as exc_info:
        session._require_within_file_io_root(tmp_path / "elsewhere", op="download")
    assert exc_info.value.error_class == ErrorClass.VALIDATION_ERROR
    assert session._require_within_file_io_root(root / "downloads", op="download") == (
        root / "downloads"
    ).resolve()

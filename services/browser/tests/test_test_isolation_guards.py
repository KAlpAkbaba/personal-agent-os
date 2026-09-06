"""The owner's standing rule, guarded so it cannot regress by accident.

"Never again flood the owner's desktop with automated browser windows." The suite already
enforces it — every ManagedBackend is forced headless unless a test is explicitly marked
`live`, profiles are isolated per test, and Chromium processes descended from this pytest
run are reaped at the end. These tests exist so a future refactor that removes any of that
fails here rather than on the owner's screen.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from browser_agent import backends
from tests import conftest

CONFTEST_SOURCE = Path(conftest.__file__).read_text(encoding="utf-8")


def test_every_managed_backend_is_forced_headless_outside_live_tests() -> None:
    """Asserted against the conftest's SOURCE: the guard patches ManagedBackend at runtime,
    so the live object cannot testify about itself."""
    assert "_headless_only_outside_live" in CONFTEST_SOURCE, "the headless guard was removed"
    guard = CONFTEST_SOURCE.split("def _headless_only_outside_live", 1)
    assert len(guard) == 2
    before, body = guard[0], guard[1].split("\ndef ", 1)[0]
    assert before.rstrip().endswith("@pytest.fixture(autouse=True)"), (
        "the headless guard must stay autouse"
    )
    assert 'kwargs["headless"] = True' in body
    assert 'request.node.get_closest_marker("live")' in body, (
        "only an explicitly `live`-marked test may open a visible browser"
    )


def test_a_visible_browser_is_never_the_default_in_the_product_code() -> None:
    source = Path(backends.__file__).read_text(encoding="utf-8")
    assert "headless: bool = True," in source, (
        "ManagedBackend must default to headless in the product code, not only in tests"
    )
    assert "headless: bool = False" not in source


def test_sessions_are_isolated_and_never_the_owner_profile() -> None:
    """A test session must get its own throwaway profile directory."""
    assert "user_data_dir" in CONFTEST_SOURCE or "tmp_path" in CONFTEST_SOURCE
    # the owner's own Chrome profile must not appear anywhere in the suite's setup
    lowered = CONFTEST_SOURCE.lower()
    for forbidden in ("user data\\default", "google\\chrome\\user data", "--profile-directory"):
        assert forbidden not in lowered, f"the suite must never touch {forbidden!r}"


def test_chromium_processes_are_reaped_and_only_our_own() -> None:
    reaper = getattr(conftest, "_reap_chromium_spawned_by_this_pytest", None)
    assert reaper is not None, "the process reaper was removed"
    source = inspect.getsource(reaper)
    # scoped to descendants of THIS pytest process, so a developer's own browser survives
    assert "Is-Descendant" in source
    assert "ms-playwright" in source
    assert "Stop-Process" in source


def test_the_media_suite_never_launches_a_browser() -> None:
    """M18.3: the alarm media surface is the one that opens a VISIBLE window on
    the owner's desktop, at an hour the owner is asleep. Its unit suite must
    therefore be fake-page only — no real ManagedBackend, no launch helper —
    and the two session_open tests must monkeypatch ManagedBackend rather than
    construct a live one."""
    media_tests = Path(__file__).resolve().parent / "unit" / "test_media_ops.py"
    source = media_tests.read_text(encoding="utf-8")
    assert media_tests.exists()
    for forbidden in ("launch_dedicated", "launch_persistent_context", "async_playwright"):
        assert forbidden not in source, f"the media unit suite must never call {forbidden!r}"
    assert 'monkeypatch.setattr("browser_agent.worker.ManagedBackend", _RecordingBackend)' in source
    # ManagedBackend is imported only to assert the real-profile guard refuses a
    # path inside the owner's Chrome; it is never connected.
    assert "await ManagedBackend" not in source
    assert ".connect()" not in source


def test_the_media_script_suite_runs_node_and_only_node() -> None:
    """``test_media_scripts_in_node.py`` executes the media scripts as real
    JavaScript. The interpreter it spawns must be Playwright's bundled NODE —
    never a browser binary, and never anything discovered off PATH (this
    machine's Chrome opens a window merely for ``--version``)."""
    source = (
        Path(__file__).resolve().parent / "unit" / "test_media_scripts_in_node.py"
    ).read_text(encoding="utf-8")
    assert "compute_driver_executable" in source, "the interpreter must be playwright's node"
    # Asserted against CONSTRUCTS, not prose: the words "browser" and "launch" appear in
    # this suite's own docstring explaining that it launches none.
    for forbidden in (
        "ManagedBackend",
        "launch_dedicated",
        "launch_persistent_context",
        "async_playwright",
        '"--version"',
        "shell=True",
    ):
        assert forbidden not in source, f"the script suite must never reach for {forbidden!r}"
    # the ONE subprocess it starts is that node, given a generated harness file
    assert source.count("subprocess.run") == 1
    assert "[_node(), str(harness), str(spec_file)]" in source


@pytest.mark.parametrize("marker", ["live"])
def test_the_live_marker_is_declared_so_it_must_be_opted_into(marker: str) -> None:
    ini = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = ini.read_text(encoding="utf-8") if ini.exists() else ""
    assert marker in text or marker in CONFTEST_SOURCE, (
        "the `live` marker must be declared, so opening a real window is always deliberate"
    )

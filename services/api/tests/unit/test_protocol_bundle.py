"""The shared contract files the Cloud Core reads at run time travel with the app.

2026-09-16: the first release since B13 could not start. ``app.alarms.timing`` read
``packages/protocol/alarm-timing.json`` through a repository-relative path, the production
image is built from ``services/api`` alone, and the api died at import with
FileNotFoundError - while every test and every CI job passed, because they run from a checkout
where that path resolves. Three more modules had the same shape.

These tests hold the fix from both ends: the run-time copies equal the shared files byte for
byte, no module reaches into ``packages/`` behind the bundle's back, and - the claim that
actually failed - the app IMPORTS from a tree shaped like the image, with no ``packages/``
anywhere above it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.protocol_files import BUNDLE_DIR, BUNDLED, protocol_file

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
SHARED = REPO_ROOT / "packages" / "protocol"

#: The one reader allowed its own path: it carries an in-code copy of its markers for this very
#: layout and a test that binds that copy to the shared file (test_research_injection).
OWN_FALLBACK = {"app/research/injection.py"}


@pytest.mark.parametrize("name", BUNDLED)
def test_each_run_time_copy_is_the_shared_file_byte_for_byte(name: str) -> None:
    shared = SHARED / name
    assert shared.is_file(), f"{name} is bundled but packages/protocol has no such file"
    assert protocol_file(name).read_bytes() == shared.read_bytes(), (
        f"app/protocol_bundle/{name} differs from packages/protocol/{name}; "
        "run `python scripts/sync-protocol-bundle.py`"
    )


def test_the_bundle_holds_exactly_the_bundled_files() -> None:
    assert sorted(p.name for p in BUNDLE_DIR.glob("*.json")) == sorted(BUNDLED)


def test_an_unbundled_name_is_an_error_not_a_guess() -> None:
    with pytest.raises(KeyError):
        protocol_file("device-protocol.json")


def test_no_app_module_reaches_into_packages_behind_the_bundle() -> None:
    pattern = re.compile(
        r"""["']packages["']\s*\)?\s*/\s*["']protocol["']"""
        r"""|packages/protocol/[\w.-]+\.json["']\s*\)"""
    )
    offenders = []
    for path in (API_ROOT / "app").rglob("*.py"):
        relative = path.relative_to(API_ROOT).as_posix()
        if relative in OWN_FALLBACK:
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{relative}:{number}")
    assert not offenders, (
        "these modules build a path into packages/, which the production image does not "
        f"contain - read it through app.protocol_files instead: {offenders}"
    )


def test_the_app_imports_from_a_tree_shaped_like_the_production_image(tmp_path: Path) -> None:
    """services/api/Dockerfile copies `app` to /srv/pagentos/app and nothing of packages/.
    The same shape here, far from the checkout, and the real import."""
    image_root = tmp_path / "srv" / "pagentos"
    shutil.copytree(
        API_ROOT / "app",
        image_root / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # (Windows has an unrelated AppData\Local\Packages; what matters is the contract dir.)
    assert not any((parent / "packages" / "protocol").exists() for parent in image_root.parents)

    env = {k: v for k, v in os.environ.items() if not k.startswith("PAGENTOS_")}
    env["PYTHONPATH"] = str(image_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=image_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert run.returncode == 0, run.stderr[-3000:]

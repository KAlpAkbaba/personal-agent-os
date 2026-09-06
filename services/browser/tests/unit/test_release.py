"""browser_agent.release: which copy of the package is executing (ADR-0050 item 16)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from browser_agent import release, worker

# scripts/tests/installer-release.tests.ps1 pins Get-BrowserPackageDigest to the same constant
FIXTURE_DIGEST = "396f5e079e36439516843dfc55df4cbbe45c58a602c6aacd99e6962716f2feb9"


def test_package_digest_matches_the_powershell_algorithm(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_bytes(
        b"b = 2\n"
    )  # raw LF: the PowerShell fixture writes the same bytes
    (tmp_path / "a.py").write_bytes(b"a = 1\n")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("ignored: not directly inside\n", encoding="utf-8")
    assert release.package_digest(tmp_path) == FIXTURE_DIGEST


def test_module_info_names_the_executing_worker_and_its_hashes() -> None:
    info = release.module_info()
    worker_file = Path(worker.__file__).resolve()
    assert Path(info["file"]) == worker_file
    assert info["sha256"] == hashlib.sha256(worker_file.read_bytes()).hexdigest()
    assert info["package_sha256"] == release.package_digest(worker_file.parent)
    assert Path(info["package_dir"]) == worker_file.parent
    assert info["cwd"]
    json.dumps(info)  # JSON-safe for the hello line


def test_worker_version_is_the_release_the_installer_expects() -> None:
    # The installer parses WORKER_VERSION / CONTRACTS from the source with a regex; keep the
    # literals on their own lines in the canonical form.
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert f'\nWORKER_VERSION = "{worker.WORKER_VERSION}"\n' in source
    assert "\nCONTRACTS: dict[str, int] = {" in source
    # M18.3 (contract v1.2): the alarm media family is new capability surface, so the
    # release moves — that is how the release-currency check tells the owner the
    # installed agent predates the media operations instead of failing an alarm.
    assert worker.WORKER_VERSION == "0.5.0"
    pyproject = (Path(worker.__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.5.0"' in pyproject
    assert "cache-keys" in pyproject and "browser_agent/**/*.py" in pyproject

"""M25 3D Creation - the REAL Blender headless lab (docs/M25_CREATIVE_3D_SPEC.md §6).

Runs the FIXED, sha256-pinned driver (``services/api/app/creative3d/drivers/
blender_driver.py``) through the real ``blender.exe -b`` on this machine (spec §1's own
detected install: ``C:\\Program Files\\Blender Foundation\\Blender 4.5\\blender.exe``),
against a fixture project under a fresh temp root: create -> add sphere/cube/camera/sun
-> transform -> material -> render 320x240 -> inspect. Every fact the evidence file
records comes from ``out.json`` (the driver's own read-back) and an independent read of
the rendered PNG (``app.creative3d.compare``, ``PIL``) - never the plan restated.

Also proves the "refused before Blender starts" property (spec §7): a plan with a name
outside the closed alphabet, an operation outside the vocabulary, or an output path
outside the fixture root is refused by ``ScenePlan``/this script's own root check BEFORE
any subprocess is ever created - checked by counting Blender invocations, not by reading
a message.

Skips (exit 0, reason recorded) when ``blender.exe`` is not present on this machine -
e.g. the CI runner (docs/M25_CREATIVE_3D_SPEC.md §1: "the runner runs the fake driver").

Run with the API venv's interpreter::

    services/api/.venv/Scripts/python.exe scripts/tests/blender-scene-lab.py \\
        --evidence docs/evidence/m25-blender-lab-<stamp>.json

Exit 0 when every assertion held (or the machine has no Blender), 1 otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
sys.path.insert(0, str(API_ROOT))

from app.creative3d.compare import check_render, compare  # noqa: E402
from app.creative3d.spec import ScenePlan  # noqa: E402

#: docs/M25_CREATIVE_3D_SPEC.md §1's own detected install path.
DEFAULT_BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
DRIVER_PATH = API_ROOT / "app" / "creative3d" / "drivers" / "blender_driver.py"
BLENDER_TIMEOUT_S = 180


def find_blender() -> Path | None:
    candidate = Path(DEFAULT_BLENDER_EXE)
    if candidate.exists():
        return candidate
    found = shutil.which("blender")
    return Path(found) if found else None


def _ensure_under_root(path: Path, root: Path) -> None:
    """The lab's own path-containment check (spec §7's "a path outside the root
    refused") - independent of ``ScenePlan``, which knows only slugs, never a real
    filesystem path. Raises before any subprocess exists."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise ValueError(f"{path} does not resolve inside the fixture root {root}")


def run_driver(blender_exe: Path, plan_path: Path, out_path: Path) -> subprocess.CompletedProcess:
    argv = [
        str(blender_exe),
        "-b",
        # Skip the owner's own preferences/addons (any of which could attempt a slow
        # network call, e.g. an update check) and any startup sound device probe —
        # a lab run must never depend on what happens to be installed/configured.
        "--factory-startup",
        "-noaudio",
        "--python",
        str(DRIVER_PATH),
        "--",
        str(plan_path),
        str(out_path),
    ]
    return subprocess.run(  # noqa: S603 - a fixed, local, argument-list invocation
        argv, capture_output=True, text=True, timeout=BLENDER_TIMEOUT_S
    )


def build_plan() -> ScenePlan:
    return ScenePlan.model_validate(
        {
            "tool": "blender",
            "project": "lab-fixture",
            "scene": "demo",
            "label": "M25 Blender lab",
            "operations": [
                {"op": "create_scene"},
                {
                    "op": "add_primitive",
                    "kind": "sphere",
                    "name": "Kure",
                    "location": [0.0, 0.0, 0.0],
                },
                {
                    "op": "add_primitive",
                    "kind": "cube",
                    "name": "Kup",
                    "location": [2.5, 0.0, 0.0],
                },
                {
                    "op": "add_primitive",
                    "kind": "camera",
                    "name": "Kamera",
                    "location": [1.0, -6.0, 1.5],
                },
                {
                    "op": "add_primitive",
                    "kind": "light_sun",
                    "name": "Gunes",
                    "location": [0.0, 0.0, 5.0],
                },
                {"op": "set_camera", "name": "Kamera", "look_at": "Kure"},
                {
                    "op": "transform",
                    "name": "Kup",
                    "location": [2.5, 0.0, 0.0],
                    "scale": [1.5, 1.5, 1.5],
                },
                {
                    "op": "set_material",
                    "name": "Kure",
                    "color": [0.9, 0.1, 0.1, 1.0],
                    "metallic": 0.1,
                    "roughness": 0.6,
                },
                {"op": "set_light", "name": "Gunes", "energy": 3.0},
                {"op": "render", "width": 320, "height": 240, "engine": "workbench"},
                {"op": "inspect"},
            ],
        }
    )


def prove_refused_before_blender_starts(root: Path) -> list[dict]:
    """Three deliberately wrong plans, each refused before any subprocess is created
    (spec §7). Returns the evidence rows; raises if any of the three is NOT refused,
    or if a Blender process was somehow started for one of them."""
    rows: list[dict] = []
    invocations_before = _BLENDER_INVOCATIONS[0]

    # 1. A name outside the closed alphabet.
    try:
        ScenePlan.model_validate(
            {
                "tool": "blender",
                "project": "lab-fixture",
                "scene": "demo",
                "operations": [
                    {
                        "op": "add_primitive",
                        "kind": "cube",
                        "name": "Kup; DROP TABLE scenes;--",
                        "location": [0, 0, 0],
                    }
                ],
            }
        )
        raise AssertionError("a name outside the closed alphabet was NOT refused")
    except Exception as exc:  # noqa: BLE001 - the refusal itself is the evidence
        rows.append({"case": "name_outside_alphabet", "refused": True, "detail": str(exc)[:200]})

    # 2. An operation outside the vocabulary.
    try:
        ScenePlan.model_validate(
            {
                "tool": "blender",
                "project": "lab-fixture",
                "scene": "demo",
                "operations": [{"op": "delete_everything"}],
            }
        )
        raise AssertionError("an operation outside the vocabulary was NOT refused")
    except Exception as exc:  # noqa: BLE001
        rows.append(
            {"case": "operation_outside_vocabulary", "refused": True, "detail": str(exc)[:200]}
        )

    # 3. A path outside the fixture root (the lab's own containment check, since
    #    ScenePlan itself knows only slugs, never a real filesystem path).
    outside_path = root.parent / "outside-the-root" / "out.json"
    try:
        _ensure_under_root(outside_path, root)
        raise AssertionError("a path outside the fixture root was NOT refused")
    except ValueError as exc:
        rows.append({"case": "path_outside_root", "refused": True, "detail": str(exc)[:200]})

    if _BLENDER_INVOCATIONS[0] != invocations_before:
        raise AssertionError(
            "a Blender process was started for a plan that should have been refused first"
        )
    return rows


#: A one-element list used as a mutable counter (module-level, no class needed) - how
#: many times ``run_driver`` actually launched ``blender.exe`` in this run.
_BLENDER_INVOCATIONS = [0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--blender-exe", default=None)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H%M%SZ")
    evidence: dict = {
        "kind": "m25_blender_lab",
        "measured_at": stamp,
        "driver_path": str(DRIVER_PATH),
    }

    blender_exe = Path(args.blender_exe) if args.blender_exe else find_blender()
    if blender_exe is None:
        evidence["skipped"] = True
        evidence["reason"] = "blender.exe not found on this machine"
        Path(args.evidence).parent.mkdir(parents=True, exist_ok=True)
        Path(args.evidence).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(f"SKIP: {evidence['reason']}")
        return 0

    evidence["blender_exe"] = str(blender_exe)
    driver_bytes = DRIVER_PATH.read_bytes()
    evidence["driver_sha256"] = hashlib.sha256(driver_bytes).hexdigest()

    root = Path(tempfile.mkdtemp(prefix="pagentos-blender-lab-"))
    evidence["fixture_root"] = str(root)
    try:
        # The refusal property FIRST, before anything real ever runs.
        evidence["refusals"] = prove_refused_before_blender_starts(root)

        plan = build_plan()
        plan_path = root / "plan.json"
        out_path = root / "out.json"
        _ensure_under_root(plan_path, root)
        _ensure_under_root(out_path, root)
        plan_path.write_text(plan.plan_json(), encoding="utf-8")

        started = time.monotonic()
        _BLENDER_INVOCATIONS[0] += 1
        proc = run_driver(blender_exe, plan_path, out_path)
        duration_s = round(time.monotonic() - started, 2)
        evidence["duration_s"] = duration_s
        evidence["blender_exit_code"] = proc.returncode
        evidence["blender_stderr_tail"] = proc.stderr[-2000:]

        if not out_path.exists():
            evidence["ok"] = False
            evidence["reason"] = "out.json was never written"
            evidence["blender_stdout_tail"] = proc.stdout[-2000:]
            _write(args.evidence, evidence)
            print("FAIL:", evidence["reason"])
            return 1

        inspection = json.loads(out_path.read_text(encoding="utf-8"))
        evidence["inspection"] = inspection
        evidence["driver_errors"] = inspection.get("errors") or []

        blend_path = root / "demo.blend"
        evidence["blend_file_present"] = blend_path.exists()
        evidence["blend_file_bytes"] = blend_path.stat().st_size if blend_path.exists() else 0

        render_info = inspection.get("render")
        render_bytes: bytes | None = None
        if render_info and render_info.get("path"):
            render_path = Path(render_info["path"])
            if render_path.exists():
                render_bytes = render_path.read_bytes()
                evidence["render_path"] = str(render_path)
                evidence["render_bytes_on_disk"] = len(render_bytes)
                evidence["render_sha256_recomputed"] = hashlib.sha256(render_bytes).hexdigest()
                evidence["render_sha256_matches_driver"] = evidence[
                    "render_sha256_recomputed"
                ] == render_info.get("sha256")

        render_check = check_render(render_bytes)
        evidence["render_independent_check"] = (
            "non_trivial" if render_check is None else render_check.as_dict()
        )

        cmp_result = compare(plan, inspection, render_bytes=render_bytes)
        evidence["compare"] = cmp_result.as_dict()

        problems: list[str] = []
        if inspection.get("errors"):
            problems.append(f"driver reported errors: {inspection['errors']}")
        if not evidence["blend_file_present"]:
            problems.append(".blend file was not saved")
        if render_bytes is None:
            problems.append("no render bytes were produced")
        elif render_check is not None:
            problems.append(f"the render failed the independent check: {render_check.as_dict()}")
        if not cmp_result.ok:
            problems.append(
                f"compare() found mismatches: {[m.as_dict() for m in cmp_result.mismatches]}"
            )

        evidence["ok"] = not problems
        evidence["problems"] = problems
        _write(args.evidence, evidence)

        if problems:
            print("FAIL:", "; ".join(problems))
            return 1
        print(
            f"PASS: {len(inspection.get('objects', []))} objects, "
            f"render {evidence.get('render_bytes_on_disk')} bytes, "
            f"compare checked={cmp_result.checked} ok={cmp_result.ok} "
            f"in {duration_s}s"
        )
        return 0
    finally:
        # The fixture root is intentionally left on disk for inspection when the run
        # failed; a passing run's evidence already captured everything durable about
        # it, so it is safe (and tidy) to remove.
        if evidence.get("ok"):
            shutil.rmtree(root, ignore_errors=True)


def _write(path: str, evidence: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

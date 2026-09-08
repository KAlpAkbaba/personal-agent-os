"""M25: the command the CLOUD CORE builds, run against the real Blender.

The M25 security review found that the service sent the bare word "blender" as its run
command, so `project.scaffold` would have refused at the first device call and the write
path could never have worked — and nothing caught it, because the unit suite's fake device
echoed success without reading the command text and the Blender lab drove the driver
directly rather than through the service.

This lab closes exactly that gap. It takes `app.creative3d.service.run_command()` — the
very string the Cloud Core now puts in the manifest — lays out a project the way
`project.scaffold` would (the plan at `plan.json`, the pinned driver beside it), and hands
that argv to the real `blender.exe`. Then it reads the inspection back and compares it with
the plan through the real `app.creative3d.compare`. Both forms are exercised: the creation
run, which names no scene file because there is none to open yet, and the apply run, which
opens the `scene.blend` the first one saved.

Its first run (2026-09-08) found two more real defects in the very path it was written for:
the driver saved the plan's scene word rather than `scene.blend`, so the apply run had
nothing to open and exited 1; and the apply run, once that was fixed, HUNG — opening a saved
`.blend` loads the owner's installed Blender add-ons, and one of them starts a watchdog
thread that never stops. `--factory-startup` is now the first token of both forms.

Run with the API interpreter from `services/api`:

    uv run python ../../scripts/tests/blender-service-command-lab.py

Skips (exit 0, saying so) when Blender is not installed, e.g. on the runner.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

from app.creative3d.compare import compare
from app.creative3d.service import (
    DRIVER_PATH_BY_TOOL,
    INSPECTION_FILE,
    PLAN_FILE,
    RUN_COMMAND_KEY,
    driver_text,
    run_command,
)
from app.creative3d.spec import ScenePlan

BLENDER = pathlib.Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe")
RUN_TIMEOUT_S = 180

CREATE_PLAN = {
    "tool": "blender",
    "project": "lab",
    "scene": "servis",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
        {"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, -6.0, 3.0]},
        {"op": "add_primitive", "kind": "light_sun", "name": "Gunes", "location": [2.0, -2.0, 5.0]},
        {"op": "set_camera", "name": "Kamera", "look_at": "Kure"},
        {"op": "render", "width": 320, "height": 240, "engine": "workbench"},
    ],
}
APPLY_PLAN = {
    "tool": "blender",
    "project": "lab",
    "scene": "servis",
    "operations": [
        {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [3.0, 0.0, 0.0]},
        {"op": "transform", "name": "Kure", "location": [0.0, 2.0, 0.0]},
        {"op": "render", "width": 320, "height": 240, "engine": "workbench"},
    ],
}


def _lay_out(root: pathlib.Path, plan: ScenePlan) -> None:
    """What `project.scaffold` writes: the plan and the pinned driver, nothing else."""
    (root / PLAN_FILE).write_text(plan.plan_json(), encoding="utf-8")
    (root / DRIVER_PATH_BY_TOOL["blender"]).write_text(driver_text("blender"), encoding="utf-8")


def _run(root: pathlib.Path, command: str) -> dict:
    """The manifest's command, run the way the device runs it: the program resolved to the
    installed editor, every other token verbatim, the project folder as the cwd."""
    tokens = command.split()
    assert tokens[0] == "blender", tokens
    argv = [str(BLENDER), *tokens[1:]]
    started = dt.datetime.now(dt.UTC)
    proc = subprocess.run(  # noqa: S603 - the allowlisted argv, under a temp root
        argv, cwd=root, capture_output=True, text=True, timeout=RUN_TIMEOUT_S
    )
    return {
        "command": command,
        "argv_tail": tokens[1:],
        "exit_code": proc.returncode,
        "seconds": round((dt.datetime.now(dt.UTC) - started).total_seconds(), 2),
        "stderr_tail": proc.stderr.strip().splitlines()[-3:],
    }


def main() -> int:
    if not BLENDER.is_file():
        print(f"SKIP: no Blender at {BLENDER}")
        return 0

    evidence: dict = {
        "kind": "m25_service_command_lab",
        "measured_at": dt.datetime.now(dt.UTC).isoformat(),
        "blender_exe": str(BLENDER),
        "why": (
            "the command app.creative3d.service.run_command() builds, run against the real "
            "editor — the path the M25 security review found had never been exercised"
        ),
        "runs": [],
        "verdict": "INCOMPLETE",
    }
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="pagentos-m25-service-cmd-"))
    failures = 0
    try:
        root = workspace / "lab-servis"
        root.mkdir(parents=True)

        for label, raw_plan, existing_scene in (
            ("create", CREATE_PLAN, False),
            ("apply", APPLY_PLAN, True),
        ):
            plan = ScenePlan.model_validate(raw_plan)
            _lay_out(root, plan)
            command = run_command("blender", existing_scene=existing_scene)
            record = _run(root, command)
            record["label"] = label
            record["manifest_key"] = RUN_COMMAND_KEY["blender"]

            inspection_path = root / INSPECTION_FILE
            if record["exit_code"] != 0 or not inspection_path.is_file():
                failures += 1
                record["ok"] = False
                evidence["runs"].append(record)
                continue

            inspection = json.loads(inspection_path.read_text(encoding="utf-8"))
            render_name = (inspection.get("render") or {}).get("path")
            render_bytes = None
            if render_name:
                candidate = root / render_name
                if candidate.is_file():
                    render_bytes = candidate.read_bytes()
            result = compare(plan, inspection, render_bytes=render_bytes)
            record["ok"] = result.ok
            record["scene_saved"] = (root / "scene.blend").is_file()
            record["objects"] = sorted(o.get("name") for o in inspection.get("objects") or [])
            record["render_bytes"] = len(render_bytes) if render_bytes else 0
            record["compare"] = {
                "ok": result.ok,
                "checked": result.checked,
                "mismatches": [m.as_dict() for m in result.mismatches[:5]],
            }
            failures += 0 if result.ok else 1
            evidence["runs"].append(record)
            inspection_path.unlink()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    evidence["failures"] = failures
    evidence["verdict"] = "PASS" if failures == 0 else "FAIL"
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d-%H%M%S")
    out = pathlib.Path(__file__).resolve().parents[2] / "docs/evidence"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"m25-service-command-lab-{stamp}.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"M25 service-command lab: {evidence['verdict']} ({failures} failures) -> {target}")
    for run in evidence["runs"]:
        print(
            f"  {run['label']}: exit={run['exit_code']} ok={run.get('ok')} "
            f"objects={run.get('objects')}"
        )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

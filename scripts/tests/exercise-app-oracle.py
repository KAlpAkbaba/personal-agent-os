"""M23 App Factory - the REAL headless DOM exercise of a rendered template (spec §4/§6/§7).

Serves a rendered project directory with the same command the device's manifest allowlist
carries (``python -m http.server <port> --bind 127.0.0.1``), opens it in headless Chromium
through Playwright (the browser worker's own runtime, ``services/browser/.venv``), and runs
the template's ``oracle.json`` - the initial assertions and the interaction steps - exactly as
written. Nothing here knows the template: every selector, value and expectation comes from the
oracle file, so a passing run is evidence that THIS rendered page satisfies THIS oracle.

Run with the browser worker's interpreter::

    services/browser/.venv/Scripts/python.exe scripts/tests/exercise-app-oracle.py \
        --project-dir <rendered project> --oracle services/api/tests/fixtures/apps/task-tracker/oracle.json \
        --evidence docs/evidence/m23-dom-exercise-<stamp>.json

Exit 0 when every assertion held, 1 otherwise; the evidence file records each step's outcome.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

STEP_TIMEOUT_MS = 5_000
SERVER_START_S = 15


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_http(url: str, deadline_s: float) -> None:
    end = time.monotonic() + deadline_s
    last: Exception | None = None
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - loopback only
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"the served project never answered at {url}: {last}")


def run_step(page, step: dict) -> dict:  # noqa: ANN001 - playwright Page
    action = step["action"]
    started = time.monotonic()
    if action == "fill":
        page.fill(step["selector"], step["value"], timeout=STEP_TIMEOUT_MS)
    elif action == "click":
        page.click(step["selector"], timeout=STEP_TIMEOUT_MS)
    elif action == "reload":
        page.reload(wait_until="load", timeout=STEP_TIMEOUT_MS)
    elif action == "assert_text":
        locator = page.locator(step["selector"]).first
        locator.wait_for(timeout=STEP_TIMEOUT_MS)
        text = locator.inner_text()
        if step["contains"] not in text:
            raise AssertionError(f"{step['selector']} text {text!r} lacks {step['contains']!r}")
    elif action == "assert_class":
        locator = page.locator(step["selector"]).first
        locator.wait_for(timeout=STEP_TIMEOUT_MS)
        classes = (locator.get_attribute("class") or "").split()
        if step["class"] not in classes:
            raise AssertionError(f"{step['selector']} classes {classes} lack {step['class']!r}")
    else:
        raise ValueError(f"unknown oracle action {action!r}")
    return {"step": step, "ok": True, "ms": round((time.monotonic() - started) * 1000)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--python", default=sys.executable, help="interpreter for http.server")
    args = parser.parse_args()

    project = pathlib.Path(args.project_dir).resolve()
    oracle = json.loads(pathlib.Path(args.oracle).read_text(encoding="utf-8"))
    port = free_port()
    url = f"http://127.0.0.1:{port}{oracle.get('url_path', '/')}"
    evidence: dict = {
        "kind": "m23_dom_exercise",
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "project_dir": str(project),
        "oracle": str(pathlib.Path(args.oracle).resolve()),
        "template": oracle.get("template"),
        "serve_command": f"{args.python} -m http.server {port} --bind 127.0.0.1",
        "url": url,
        "initial_assertions": [],
        "interaction_steps": [],
        "verdict": "INCOMPLETE",
    }

    server = subprocess.Popen(  # noqa: S603 - the allowlisted serve command, loopback only
        [args.python, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=project,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures = 0
    try:
        wait_for_http(url, SERVER_START_S)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            evidence["browser"] = f"chromium {browser.version} headless"
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=STEP_TIMEOUT_MS)
            evidence["title"] = page.title()
            for assertion in oracle.get("initial_assertions", []):
                count = page.locator(assertion["selector"]).count()
                ok = (count > 0) == bool(assertion.get("exists", True))
                failures += 0 if ok else 1
                evidence["initial_assertions"].append(
                    {"selector": assertion["selector"], "expected_exists": assertion.get("exists", True), "count": count, "ok": ok}
                )
            for step in oracle.get("interaction_steps", []):
                try:
                    evidence["interaction_steps"].append(run_step(page, step))
                except (AssertionError, PlaywrightTimeout, ValueError) as exc:
                    failures += 1
                    evidence["interaction_steps"].append({"step": step, "ok": False, "error": str(exc)[:300]})
                    break
            browser.close()
    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
        failures += 1
        evidence["error"] = str(exc)[:500]
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    evidence["failures"] = failures
    evidence["verdict"] = "PASS" if failures == 0 else "FAIL"
    evidence["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
    out = pathlib.Path(args.evidence)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"M23 DOM exercise: {evidence['verdict']} ({len(evidence['interaction_steps'])} steps, {failures} failures) -> {out}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

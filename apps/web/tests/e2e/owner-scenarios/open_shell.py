"""The test team's web step (scripts/testteam/run-scenario.ps1, a step with "web").

Opens the STAGING web shell with the staging owner session, as the owner would, waits until
the session check is over, and writes a screenshot. With --wav the browser gets that Turkish
TTS file as its microphone (Chrome's fake media stream): the free local voice mode.

Runs on the Playwright of services/browser's environment (no new dependency). Prints ONE JSON
line: {"ok": bool, "actual": str, "screenshot": str}. Refuses any url but staging's web shell.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

STAGING_WEB = {("127.0.0.1", 28000), ("localhost", 28000)}
STORAGE_KEY = "pagentos.owner_session"
CHECKING = "Oturum kontrol ediliyor"


def _say(ok: bool, actual: str, shot: str = "") -> int:
    # ASCII escapes: the console's code page cannot print every Turkish letter (cp1252 has no ı).
    print(json.dumps({"ok": ok, "actual": actual, "screenshot": shot}), flush=True)
    return 0 if ok else 1


def _is_staging(url: str, test_port: int) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.username or parts.password:
        return False
    target = ((parts.hostname or "").lower(), parts.port)
    return target in STAGING_WEB or (test_port > 0 and target == ("127.0.0.1", test_port))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--wav", default="")
    parser.add_argument("--expect", default="")
    parser.add_argument("--allow-test-port", type=int, default=0)
    args = parser.parse_args()
    if not _is_staging(args.url, args.allow_test_port):
        return _say(False, f"STAGING DEĞİL: {args.url}")

    token = ""
    if args.session and Path(args.session).is_file():
        token = str(json.loads(Path(args.session).read_text(encoding="utf-8")).get("session_token", ""))

    from playwright.sync_api import sync_playwright

    launch = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    if args.wav:
        launch.append(f"--use-file-for-fake-audio-capture={args.wav}")
    Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=launch)
        try:
            context = browser.new_context(locale="tr-TR", viewport={"width": 1280, "height": 800})
            if token:
                context.add_init_script(
                    f"window.localStorage.setItem({json.dumps(STORAGE_KEY)}, {json.dumps(token)});"
                )
            page = context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_function(
                    f"!document.body.innerText.includes({json.dumps(CHECKING)})", timeout=20_000
                )
            except Exception:  # noqa: BLE001 - a shell that never leaves the check is the finding
                page.screenshot(path=args.shot, full_page=True)
                return _say(False, f"'{CHECKING}' 20 sn sonra hâlâ ekranda", args.shot)
            text = page.inner_text("body")
            page.screenshot(path=args.shot, full_page=True)
            if args.expect and args.expect not in text:
                return _say(False, f"sayfada '{args.expect}' yok", args.shot)
            return _say(True, "kabuk açıldı, oturum kontrolü bitti", args.shot)
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())

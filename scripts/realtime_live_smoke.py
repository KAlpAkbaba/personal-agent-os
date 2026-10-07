"""Opt-in LIVE smoke for GPT-Live (gpt-live-provider, d20261004) - the Turkish first step.

The owner's / Danışman's step, never a worker's (a paid call with the owner's key). One
question only: does ONE Turkish turn come back? If it does not, the measurement ends there
(team/plans/gpt-live-provider-adr.md).

What it does, on the vendor's documented primary WebSocket (guides/realtime-websocket,
api=live, read 2026-10-04): connect to ``wss://api.openai.com/v1/live/sessions`` with the
key in the Authorization header, send ``session.start`` with the session object built by
the SAME adapter the server uses (``OpenAILiveProvider.build_session_config``) plus the
WebSocket-only audio format, seed one user message ("Merhaba", or ``--text``) - or stream
a raw PCM16/24 kHz mono file of the owner saying it (``--audio-file``) - and collect
``session.output_transcript.delta`` until ``--wait`` seconds pass, then ``session.close``
and wait for ``session.closed`` (usage).

stdout is exactly one JSON document: the session.start body as sent (no headers), the
server's own ``session.started`` object (scrubbed), the reply transcript, the final usage
and the per-minute estimate. Never printed: the API key.

Default is a DRY RUN that prints the request and connects to nothing; ``--live`` is the
opt-in. The key comes from the environment (PAGENTOS_VOICE_OPENAI_API_KEY), never from an
argument.

    cd services/api
    PAGENTOS_VOICE_OPENAI_API_KEY=... uv run python ../../scripts/realtime_live_smoke.py --live

Exit codes: 0 a reply transcript came back (or dry run); 1 the vendor refused/errored;
2 unexpected error; 3 key absent with --live (nothing attempted); 5 connected but no reply
within --wait (inconclusive - say so, do not call it a pass).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

EXIT_OK = 0
EXIT_PROVIDER_ERROR = 1
EXIT_UNEXPECTED = 2
EXIT_KEY_ABSENT = 3
EXIT_NO_REPLY = 5

WS_URL = "wss://api.openai.com/v1/live/sessions"
TURKISH_INSTRUCTIONS = (
    "Sen Türkçe konuşan bir kişisel asistansın. Yalnızca Türkçe, kısa ve doğal cevap ver."
)
PCM_CHUNK_BYTES = 4800  # 100 ms of 24 kHz mono PCM16


def build_start_event(provider: Any, *, text: str | None) -> dict[str, Any]:
    """``session.start``: the adapter's session object + the WebSocket audio format and,
    without an audio file, one seeded user message."""
    from app.voice.providers import RealtimeSessionConfig

    session = provider.build_session_config(
        RealtimeSessionConfig(instructions=TURKISH_INSTRUCTIONS)
    )
    session["audio"]["format"] = {"type": "audio/pcm", "rate": 24000}
    if text:
        session["input"] = [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
    return {"type": "session.start", "event_id": "event_start", "session": session}


def _scrub(obj: Any, key: str) -> Any:
    from app.voice.providers_openai_live import scrub_secrets

    return json.loads(json.dumps(scrub_secrets(obj), default=str).replace(key, "[redacted]"))


def run_live(args: argparse.Namespace, key: str, start: dict[str, Any]) -> tuple[int, dict]:
    from websockets.sync.client import connect

    from app.voice.providers_openai_live import USD_PER_MINUTE

    report: dict[str, Any] = {"session_started": None, "reply_transcript": "", "errors": []}
    with connect(WS_URL, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
        ws.send(json.dumps(start))
        deadline = time.monotonic() + args.wait
        audio_sent = False
        closing = False
        while time.monotonic() < deadline + 15:
            try:
                raw = ws.recv(timeout=1.0)
            except TimeoutError:
                raw = None
            if raw is not None:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.started":
                    report["session_started"] = _scrub(event.get("session"), key)
                    if args.audio_file and not audio_sent:
                        pcm = Path(args.audio_file).read_bytes()
                        for i in range(0, len(pcm), PCM_CHUNK_BYTES):
                            chunk = base64.b64encode(pcm[i : i + PCM_CHUNK_BYTES]).decode()
                            ws.send(
                                json.dumps({"type": "session.input_audio.append", "audio": chunk})
                            )
                        audio_sent = True
                elif kind == "session.output_transcript.delta":
                    report["reply_transcript"] += str(event.get("delta") or "")
                elif kind == "error":
                    report["errors"].append(_scrub(event.get("error"), key))
                elif kind == "session.closed":
                    report["usage"] = _scrub(event.get("usage"), key)
                    break
            if not closing and time.monotonic() >= deadline:
                ws.send(json.dumps({"type": "session.close"}))
                closing = True
    seconds = (report.get("usage") or {}).get("seconds")
    report["estimated_usd"] = (
        round(float(seconds) / 60 * USD_PER_MINUTE, 4) if isinstance(seconds, int | float) else None
    )
    if report["errors"] and not report["reply_transcript"]:
        return EXIT_PROVIDER_ERROR, report
    return (EXIT_OK if report["reply_transcript"].strip() else EXIT_NO_REPLY), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="opt-in: make the paid call")
    parser.add_argument("--text", default="Merhaba", help="seeded Turkish user message")
    parser.add_argument("--audio-file", help="raw PCM16 24 kHz mono file to stream instead")
    parser.add_argument("--wait", type=float, default=12.0, help="seconds to wait for a reply")
    parser.add_argument("--model", default=None, help="override the model (default gpt-live-1)")
    args = parser.parse_args(argv)

    from app.voice.providers_openai_live import DEFAULT_MODEL, OpenAILiveProvider

    key = os.environ.get("PAGENTOS_VOICE_OPENAI_API_KEY", "").strip()
    provider = OpenAILiveProvider(key or None, model=args.model or DEFAULT_MODEL)
    start = build_start_event(provider, text=None if args.audio_file else args.text)
    out: dict[str, Any] = {
        "kind": "gpt_live_turkish_smoke",
        "mode": "live" if args.live else "dry_run",
        "url": WS_URL,
        "request": start,
    }
    code = EXIT_OK
    if args.live:
        if not key:
            print("realtime_live_smoke: PAGENTOS_VOICE_OPENAI_API_KEY absent", file=sys.stderr)
            code = EXIT_KEY_ABSENT
        else:
            try:
                code, report = run_live(args, key, start)
                out.update(report)
            except Exception as exc:  # noqa: BLE001 - one JSON document whatever happens
                message = str(exc).replace(key, "[redacted]")
                out["unexpected"] = f"{type(exc).__name__}: {message}"[:500]
                code = EXIT_UNEXPECTED
    out["exit_code"] = code
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Turkish text on a cp1252 console
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

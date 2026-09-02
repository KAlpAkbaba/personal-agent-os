"""Opt-in LIVE smoke for the OpenAI Realtime adapter (M12 track B).

This is the one command the owner's credential step runs. It mints ephemeral,
single-session credentials through the adapter and prints what a client would
need to open the media leg, WITHOUT the values: the request body as sent (no
headers), the credential's expiry, the transport descriptor, and the vendor's
own echo of the session object (scrubbed of secrets).

Modes (``--mode``):

  probe (default)  Prove the MINIMAL current client_secrets contract first -
                   {"session": {"type": "realtime", "model": ..., "audio":
                   {"output": {"voice": ...}}}} - then add the M12 configuration
                   ONE layer at a time (expires_after, output_modalities,
                   audio_formats, transcription, turn_detection = semantic VAD +
                   interruption, instructions, tools). A refused layer is named
                   with the vendor's error object; the probe continues with the
                   layers that were accepted, so every incompatible option is
                   found in one run.
  minimal          Only the minimal contract.
  full             Only the full M12 configuration.

``--list-models`` prints the account's Realtime model ids (discovery) and exits.

Never printed: the standing API key, the ephemeral secret value.

Usage (from services/api; the key comes from the environment or .env, never
from an argument):

    PAGENTOS_VOICE_OPENAI_API_KEY=... uv run python scripts/realtime_smoke.py
        [--mode probe|minimal|full] [--transport webrtc|websocket] [--ttl 60]
        [--model gpt-realtime-2.1] [--voice marin|cedar] [--list-models]

Exit codes: 0 every requested layer minted; 1 the minimal contract itself was
refused (vendor error on stderr and in the report); 3 key absent (nothing
attempted); 4 minimal contract OK but at least one M12 layer refused (named);
2 unexpected error. stdout is exactly one JSON document; diagnostics go to
stderr.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXIT_OK = 0
EXIT_PROVIDER_ERROR = 1
EXIT_UNEXPECTED = 2
EXIT_KEY_ABSENT = 3
EXIT_LAYER_REFUSED = 4


def _sanitized_request(body: dict[str, Any]) -> dict[str, Any]:
    """The request body as sent, with the long persona text and tool schemas
    summarized so the document stays readable. It carries no secret: headers
    are not part of it."""
    out = copy.deepcopy(body)
    session = out.get("session")
    if isinstance(session, dict):
        instructions = session.get("instructions")
        if isinstance(instructions, str):
            session["instructions"] = f"<{len(instructions)} chars: {instructions[:100]!r}...>"
        tools = session.get("tools")
        if isinstance(tools, list):
            session["tools"] = f"<{len(tools)} function tools: {[t.get('name') for t in tools]}>"
    return out


def _vendor_error_line(layer: str, error: dict[str, Any]) -> str:
    details = error.get("details") or {}
    vendor = details.get("vendor_error") or {}
    return (f"realtime_smoke: {layer} refused: HTTP {details.get('http_status')} "
            f"type={vendor.get('type')} code={vendor.get('code')} "
            f"param={vendor.get('param')} message={vendor.get('message')!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", default="probe", choices=("probe", "minimal", "full"))
    parser.add_argument("--transport", default="webrtc", choices=("webrtc", "websocket"))
    parser.add_argument("--ttl", type=int, default=60, help="credential TTL in seconds")
    parser.add_argument("--model", default=None, help="override the configured Realtime model id")
    parser.add_argument("--voice", default=None,
                        help="override the configured voice (marin, cedar, ...)")
    parser.add_argument("--list-models", action="store_true",
                        help="print the account's Realtime model ids and exit")
    parser.add_argument("--out", default=None,
                        help="also write the JSON report to this path (UTF-8)")
    args = parser.parse_args()

    from app.config import Settings

    settings = Settings()
    if not settings.voice_openai_api_key:
        print(
            "realtime_smoke: PAGENTOS_VOICE_OPENAI_API_KEY is not set; nothing was attempted. "
            "This is the owner action from ADR-0034 item 7: provision the key in the environment "
            "(never in git) and re-run.",
            file=sys.stderr,
        )
        return EXIT_KEY_ABSENT

    from datetime import UTC, datetime

    from app.voice.errors import VoiceError
    from app.voice.providers import RealtimeSessionConfig
    from app.voice.providers_openai_realtime import (
        FULL_LAYERS,
        MINIMAL_LAYERS,
        SESSION_LAYERS,
        OpenAIRealtimeProvider,
    )
    from app.voice.realtime_sessions.persona import build_instructions
    from app.voice.realtime_sessions.tools import default_registry

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-tty stdout
        pass

    provider = OpenAIRealtimeProvider(
        settings.voice_openai_api_key,
        model=args.model or settings.voice_realtime_openai_model,
        voice=args.voice or settings.voice_realtime_openai_voice,
        eagerness=settings.voice_realtime_openai_eagerness,
        transcription_model=settings.voice_realtime_openai_transcription_model,
        base_url=settings.voice_realtime_openai_base_url,
        timeout_s=settings.voice_realtime_openai_timeout_s,
    )

    if args.list_models:
        try:
            models = provider.list_realtime_models()
        except VoiceError as exc:
            print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
            return EXIT_PROVIDER_ERROR
        print(json.dumps({"realtime_models": models, "configured_model": provider.model},
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    config = RealtimeSessionConfig(
        language="tr-TR",
        instructions=build_instructions(),
        tools=tuple(default_registry().manifest()),
    )

    def attempt(label: str, layers: tuple[str, ...]) -> dict[str, Any]:
        session_id = f"smoke-{uuid.uuid4()}"
        request_preview = _sanitized_request(provider.build_credential_request(
            session_id=session_id, ttl_s=args.ttl, transport=args.transport,
            session_config=config, layers=layers,
        ).json_body or {})
        try:
            result = provider.mint(session_id=session_id, ttl_s=args.ttl, transport=args.transport,
                                   session_config=config, layers=layers)
        except VoiceError as exc:
            error = exc.to_dict()
            print(_vendor_error_line(label, error), file=sys.stderr)
            return {"label": label, "layers": list(layers), "request": request_preview,
                    "ok": False, "error": error}
        cred = result.credential
        now = datetime.now(UTC)
        print(f"realtime_smoke: {label} OK (credential received; expires "
              f"{cred.expires_at.isoformat()})", file=sys.stderr)
        return {
            "label": label, "layers": list(layers), "request": request_preview, "ok": True,
            "session_ref": cred.session_ref,
            "credential_received": bool(cred.secret),
            "expires_at": cred.expires_at.isoformat().replace("+00:00", "Z"),
            "ttl_requested_s": args.ttl,
            "ttl_observed_s": round((cred.expires_at - now).total_seconds(), 1),
            "session_echo": result.session_echo,
        }

    attempts: list[dict[str, Any]] = []
    accepted: list[str] = []
    refused: dict[str, Any] = {}

    if args.mode == "full":
        attempts.append(attempt("full", FULL_LAYERS))
        if attempts[-1]["ok"]:
            accepted = list(FULL_LAYERS)
        else:
            refused["full"] = attempts[-1]["error"]
    else:
        minimal = attempt("minimal", MINIMAL_LAYERS)
        attempts.append(minimal)
        if minimal["ok"] and args.mode == "probe":
            for layer in SESSION_LAYERS:
                trial = attempt(f"+{layer}", tuple(accepted) + (layer,))
                attempts.append(trial)
                if trial["ok"]:
                    accepted.append(layer)
                else:
                    refused[layer] = trial["error"]

    minimal_ok = bool(attempts and attempts[0]["ok"]) if args.mode != "full" else None
    report = {
        "mode": args.mode,
        "provider": provider.name,
        "model": provider.model,
        "transport": args.transport,
        "minimal_contract_ok": minimal_ok,
        "accepted_layers": accepted,
        "refused_layers": refused,
        "m12_full_contract_ok": (set(accepted) == set(FULL_LAYERS)),
        "attempts": attempts,
        "transport_descriptor": provider.transport_descriptor(args.transport),
        "capabilities": provider.capabilities().to_dict(),
        "note": ("Turkish quality is UNVERIFIED by vendor docs; measure it with the "
                 "benchmark on the owner's machine (ADR-0034 §6)."),
    }
    document = json.dumps(report, ensure_ascii=False, indent=2)
    print(document)
    if args.out:
        Path(args.out).write_text(document + "\n", encoding="utf-8")

    if args.mode == "full":
        return EXIT_OK if not refused else EXIT_PROVIDER_ERROR
    if not minimal_ok:
        return EXIT_PROVIDER_ERROR
    if refused:
        return EXIT_LAYER_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a smoke script reports, never traces secrets
        print(f"realtime_smoke: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)

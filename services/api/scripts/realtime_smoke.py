"""Opt-in LIVE smoke for the OpenAI Realtime adapter (M12 track B).

This is the one command the owner's credential step runs. It performs exactly
one real call — mint one ephemeral, single-session credential through the
adapter — and prints what a client would need to open the media leg, WITHOUT
the values: the credential's expiry and the transport descriptor, plus the
vendor's own echo of the session object (scrubbed of secrets) so the GA field
names the client will be built on are confirmed live, not assumed
(docs/research/realtime-providers-2026-09.md §4 item 7).

Never printed: the standing API key, the ephemeral secret.

Usage (from services/api; the key comes from the environment or .env, never
from an argument):

    PAGENTOS_VOICE_OPENAI_API_KEY=... uv run python scripts/realtime_smoke.py \
        [--transport webrtc|websocket] [--ttl 60]

Exit codes: 0 credential minted; 3 key absent (nothing attempted); 1 the vendor
call failed (typed voice error on stderr, scrubbed); 2 unexpected error.
stdout is exactly one JSON document; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXIT_OK = 0
EXIT_PROVIDER_ERROR = 1
EXIT_UNEXPECTED = 2
EXIT_KEY_ABSENT = 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--transport", default="webrtc", choices=("webrtc", "websocket"))
    parser.add_argument("--ttl", type=int, default=60, help="credential TTL in seconds")
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
    from app.voice.providers_openai_realtime import OpenAIRealtimeProvider
    from app.voice.realtime_sessions.persona import build_instructions
    from app.voice.realtime_sessions.tools import default_registry

    provider = OpenAIRealtimeProvider.from_settings(settings)
    config = RealtimeSessionConfig(
        language="tr-TR",
        instructions=build_instructions(),
        tools=tuple(default_registry().manifest()),
    )
    session_id = f"smoke-{uuid.uuid4()}"
    try:
        result = provider.mint(session_id=session_id, ttl_s=args.ttl, transport=args.transport,
                               session_config=config)
    except VoiceError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return EXIT_PROVIDER_ERROR

    cred = result.credential
    now = datetime.now(UTC)
    report = {
        "provider": cred.provider,
        "model": provider.model,
        "session_id": session_id,
        "session_ref": cred.session_ref,
        "credential_received": bool(cred.secret),
        "expires_at": cred.expires_at.isoformat().replace("+00:00", "Z"),
        "ttl_requested_s": args.ttl,
        "ttl_observed_s": round((cred.expires_at - now).total_seconds(), 1),
        "transport": cred.transport,
        "transport_descriptor": cred.transport_descriptor,
        "session_echo": result.session_echo,
        "capabilities": provider.capabilities().to_dict(),
        "note": ("Turkish quality is UNVERIFIED by vendor docs; measure it with the "
                 "benchmark on the owner's machine (ADR-0034 §6)."),
    }
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-tty stdout
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a smoke script reports, never traces secrets
        print(f"realtime_smoke: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)

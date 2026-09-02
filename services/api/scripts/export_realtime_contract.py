"""Write the canonical realtime session contract to packages/protocol.

    uv run python scripts/export_realtime_contract.py          # write
    uv run python scripts/export_realtime_contract.py --check  # exit 1 on drift

The committed JSON is what the web client validates its requests against and what
``tests/unit/test_realtime_contract.py`` compares to the live Pydantic models; when a
request model changes, bump CONTRACT_VERSION in app/voice/realtime_sessions/contract.py
and re-run this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.voice.realtime_sessions.contract import realtime_contract  # noqa: E402

TARGET = (Path(__file__).resolve().parents[3] / "packages" / "protocol"
          / "realtime-session-contract.json")


def render() -> str:
    return json.dumps(realtime_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv[1:]:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"contract drift: {TARGET} differs from the live models; re-run without --check",
                  file=sys.stderr)
            return 1
        print(f"contract up to date: {TARGET}")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

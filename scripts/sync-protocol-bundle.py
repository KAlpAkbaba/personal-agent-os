"""Refresh services/api/app/protocol_bundle/ from packages/protocol/ (byte for byte).

    python scripts/sync-protocol-bundle.py          # copy
    python scripts/sync-protocol-bundle.py --check  # exit 1 when a copy differs

The Cloud Core image carries only services/api, so the shared contract files it reads at run
time travel as copies (app/protocol_files.py). tests/unit/test_protocol_bundle.py fails when a
copy drifts; this script is the fix it names.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "packages" / "protocol"
MODULE = REPO / "services" / "api" / "app" / "protocol_files.py"
TARGET = MODULE.parent / "protocol_bundle"


def bundled_names() -> tuple[str, ...]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "BUNDLED":
            return tuple(ast.literal_eval(node.value))
    raise SystemExit("BUNDLED not found in app/protocol_files.py")


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    TARGET.mkdir(exist_ok=True)
    for name in bundled_names():
        source = (SOURCE / name).read_bytes()
        target = TARGET / name
        if not target.exists() or target.read_bytes() != source:
            stale.append(name)
            if not check:
                target.write_bytes(source)
    for name in stale:
        print(("STALE " if check else "copied ") + name)
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    raise SystemExit(main())

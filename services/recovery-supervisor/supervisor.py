"""Recovery Supervisor entrypoint (see recovery_supervisor/cli.py for verbs).

Usage:
    python supervisor.py --workspace <dir> [--json] <verb> [options]

Stdlib-only by design: this process must survive a broken main application
release (CLAUDE.md Self-development rule / RECOVERY_AND_SELF_HEALING.md §2).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recovery_supervisor.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

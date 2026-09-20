"""Forget the machine's heartbeat rows that the Experience Engine wrote as memories.

ADR-0190 stopped WRITING them; this forgets the ones already in the store. Owner
authorised on 2026-09-20 after seeing them ("Uygula").

What it selects, and why that and not a text match: every episodic memory the engine wrote
carries a deterministic key, ``experience.episodic:<ledger event id>``. The rows to forget
are exactly the ones whose ledger event type is in
``app.experience.engine.TELEMETRY_EVENT_TYPES`` - so this deletes by PROVENANCE, never by
guessing at a sentence. A memory the owner wrote, pinned or marked explicit is never
touched, whatever its text says.

Nothing is lost: ``activity_events`` keeps every one of these events, which is where
"when was the owner at the machine" is actually answered from.

    python scripts/forget_telemetry_memories.py            # dry run: counts only
    python scripts/forget_telemetry_memories.py --apply    # hard delete + audit rows
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.experience.cleanup import select_telemetry_memories  # noqa: E402
from app.memory.models import Memory  # noqa: E402
from app.memory.service import forget_memory  # noqa: E402
from app.memory.types import Actor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="really delete (default: dry run)")
    parser.add_argument("--database-url", default=os.environ.get("PAGENTOS_DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        print("no database url: pass --database-url or set PAGENTOS_DATABASE_URL")
        return 2

    engine = create_engine(args.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        doomed = select_telemetry_memories(session)

        total = session.execute(select(Memory)).scalars().all()
        by_text = Counter((m.text or "")[:60] for m in doomed)
        print(f"memories in store : {len(total)}")
        print(f"telemetry rows    : {len(doomed)}")
        print(f"would remain      : {len(total) - len(doomed)}")
        for text, count in by_text.most_common(10):
            print(f"  {count:>5}  {text}")

        if not args.apply:
            print("\ndry run: nothing deleted (pass --apply)")
            return 0

        deleted = 0
        for memory in doomed:
            forget_memory(
                session,
                uuid.UUID(str(memory.id)),
                actor=Actor.OWNER,
                reason="adr-0190:telemetry_not_memory",
            )
            deleted += 1
        print(f"\ndeleted {deleted} memories (audit rows written, ledger untouched)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

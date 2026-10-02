"""Write the semantic engine's intent exemplars (ADR-0224 layer 2) from the voice corpus.

    uv run python scripts/export_understanding_exemplars.py          # write
    uv run python scripts/export_understanding_exemplars.py --check  # exit 1 on drift

Production cannot import ``tests/``, so the corpus (``tests/voice_corpus/corpus.py``) is
exported to ``app/voice/understanding/exemplars.json`` - the same ``exemplars_from_cases``
the tests build their engine from, one entry per distinct sentence, sorted, UTF-8, LF, and
nothing but the intent and the sentence. ``tests/unit/test_understanding_startup.py``
compares the committed file byte for byte with this output: when a corpus case is added,
removed or reworded, re-run this and commit the file with the corpus change.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.voice.understanding.exemplars import EXEMPLARS_FILE, EXEMPLARS_VERSION  # noqa: E402
from app.voice.understanding.semantic import exemplars_from_cases  # noqa: E402

TARGET = API_ROOT / "app" / "voice" / "understanding" / EXEMPLARS_FILE


def render(exemplars: Iterable[tuple[str, str]]) -> str:
    """The file's text: one entry per line, sorted by (intent, sentence)."""
    rows = [
        " " * 2 + json.dumps({"intent": intent, "sentence": sentence}, ensure_ascii=False)
        for intent, sentence in sorted(set(exemplars))
    ]
    body = ",\n".join(rows)
    return f'{{\n "version": {EXEMPLARS_VERSION},\n "exemplars": [\n{body}\n ]\n}}\n'


def corpus_exemplars() -> list[tuple[str, str]]:
    from tests.voice_corpus.corpus import all_cases  # the export side may read the corpus

    return exemplars_from_cases(all_cases())


def main(argv: Sequence[str] | None = None, *, target: Path = TARGET) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    exemplars = corpus_exemplars()
    data = render(exemplars).encode("utf-8")
    if "--check" in args:
        current = target.read_bytes() if target.exists() else b""
        if current != data:
            print(
                f"exemplar drift: {target} is not today's corpus; re-run "
                "scripts/export_understanding_exemplars.py without --check",
                file=sys.stderr,
            )
            return 1
        print(f"exemplars up to date: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)  # bytes: write_text would turn LF into CRLF on Windows
    print(f"wrote {target} ({len(exemplars)} exemplars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

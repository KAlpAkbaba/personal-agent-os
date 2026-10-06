"""The fill step of speaker-engine-measure: the ONLY networked run.

Downloads exactly the pinned files of ``measure.MODEL_FILES`` (team/plans/
speaker-engine-integration-plan.md section 2.2) into ``--models`` (the named volume
``pagentos-speaker-models``). Each file is written to ``<name>.part``, its byte size and sha256
are checked, and only then is it renamed into place. A mismatch deletes the ``.part`` file and
exits 3. A file already present with the pinned bytes is not downloaded again.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import EXIT_HASH_MISMATCH, MODEL_FILES, sha256_of  # noqa: E402


def _download(url: str, target: Path, timeout: int = 600) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - pinned https URL
        with target.open("wb") as handle:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                handle.write(chunk)


def fill(models: Path, *, opener: Callable[[str, Path], None] = _download) -> int:
    for key, entry in MODEL_FILES.items():
        target = models / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            target.is_file()
            and target.stat().st_size == entry.bytes
            and sha256_of(target) == entry.sha256
        ):
            print(f"fill: {key} already present and verified", file=sys.stderr)
            continue
        part = target.with_name(target.name + ".part")
        print(f"fill: downloading {entry.url}", file=sys.stderr)
        try:
            opener(entry.url, part)
            size = part.stat().st_size
            actual = sha256_of(part)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        if size != entry.bytes or actual != entry.sha256:
            part.unlink()
            print(
                f"fill: {key} is {size} bytes sha256 {actual}; pinned {entry.bytes} bytes "
                f"{entry.sha256}: deleted",
                file=sys.stderr,
            )
            print('{"kind": "error", "error": "weight_hash_mismatch", "files": ["' + key + '"]}')
            return EXIT_HASH_MISMATCH
        part.replace(target)
    print('{"kind": "fill", "ok": true}')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_models.py")
    parser.add_argument("--models", required=True, type=Path)
    args = parser.parse_args(argv)
    return fill(args.models)


if __name__ == "__main__":
    raise SystemExit(main())

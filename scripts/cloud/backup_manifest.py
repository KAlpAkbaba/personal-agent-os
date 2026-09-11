#!/usr/bin/env python3
"""What a Cloud Core backup contains, and whether a restore reproduced it (ADR-0122).

Standard library only: it runs on the production host's own python3, beside
backup-cloud-core.sh and restore-cloud-core.sh, never inside a container.

    fingerprint              read `pg_restore --data-only -f -` SQL on stdin; print, per
                             table, the row count and a sha256 of its rows SORTED - so two
                             databases holding the same rows agree whatever physical order
                             each one stores them in - plus every sequence position
    manifest DIR [K=V ...]   write DIR/MANIFEST.json: every file's size and sha256, every
                             postgres/<db>.fingerprint.json folded in, and the metadata given
    verify DIR               recompute every file in DIR against DIR/MANIFEST.json; exit 1
                             naming each file that is missing, extra or different
    compare A B              two fingerprints; exit 1 naming each table that differs

A restore drill is only as good as what it compares. Row counts taken from the live
database a moment after the dump would drift with every write in between; these
fingerprints are computed FROM THE DUMP at backup time and FROM A RE-DUMP of the restored
database at drill time, through the same pg_restore, so equality means the restored
database holds exactly the rows the backup holds.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

#: pg_restore's COPY terminator: a backslash and a dot, alone on a line.
COPY_END = "\\."
MANIFEST = "MANIFEST.json"


def fingerprint(lines) -> dict:  # noqa: ANN001 - any iterable of text lines
    tables: dict[str, dict] = {}
    sequences: list[str] = []
    current: str | None = None
    columns = ""
    rows: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if current is not None:
            if line == COPY_END:
                digest = hashlib.sha256()
                digest.update(columns.encode("utf-8"))
                for row in sorted(rows):
                    digest.update(b"\n")
                    digest.update(row.encode("utf-8"))
                tables[current] = {"rows": len(rows), "sha256": digest.hexdigest()}
                current, rows = None, []
            else:
                rows.append(line)
            continue
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            head = line[len("COPY ") : -len(" FROM stdin;")]
            name, _, cols = head.partition(" ")
            current, columns = name, cols
            continue
        if line.startswith("SELECT pg_catalog.setval("):
            sequences.append(line)
    if current is not None:
        raise ValueError(f"COPY block for {current} never ended: the dump is truncated")
    return {"tables": dict(sorted(tables.items())), "sequences": sorted(sequences)}


def _file_facts(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def _walk(root: Path) -> dict[str, dict]:
    files = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST:
            continue
        files[relative] = _file_facts(path)
    return files


def write_manifest(root: Path, metadata: dict[str, str]) -> dict:
    files = _walk(root)
    databases = {}
    for fp in sorted((root / "postgres").glob("*.fingerprint.json")):
        databases[fp.name[: -len(".fingerprint.json")]] = json.loads(fp.read_text("utf-8"))
    document = {
        "format": 1,
        "metadata": dict(sorted(metadata.items())),
        "files": files,
        "totals": {
            "files": len(files),
            "bytes": sum(f["size"] for f in files.values()),
            "objects": sum(1 for name in files if name.startswith("minio/")),
        },
        "databases": databases,
    }
    (root / MANIFEST).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
    return document


def verify(root: Path) -> list[str]:
    manifest = json.loads((root / MANIFEST).read_text("utf-8"))
    expected: dict[str, dict] = manifest["files"]
    found = _walk(root)
    problems = []
    for name, facts in expected.items():
        if name not in found:
            problems.append(f"missing: {name}")
        elif found[name] != facts:
            problems.append(f"different: {name}")
    problems.extend(f"extra: {name}" for name in found if name not in expected)
    return problems


def compare(left: dict, right: dict) -> list[str]:
    problems = []
    a, b = left.get("tables", {}), right.get("tables", {})
    for name in sorted(set(a) | set(b)):
        if name not in b:
            problems.append(f"table missing after restore: {name}")
        elif name not in a:
            problems.append(f"table not in the backup: {name}")
        elif a[name] != b[name]:
            problems.append(
                f"table differs: {name} (rows {a[name]['rows']} -> {b[name]['rows']})"
            )
    if left.get("sequences", []) != right.get("sequences", []):
        problems.append("sequence positions differ")
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "fingerprint":
        json.dump(fingerprint(sys.stdin), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if command == "manifest":
        metadata = dict(item.split("=", 1) for item in rest[1:])
        document = write_manifest(Path(rest[0]), metadata)
        print(json.dumps(document["totals"], sort_keys=True))
        return 0
    if command == "verify":
        problems = verify(Path(rest[0]))
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if command == "compare":
        left = json.loads(Path(rest[0]).read_text("utf-8"))
        right = json.loads(Path(rest[1]).read_text("utf-8"))
        problems = compare(left, right)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

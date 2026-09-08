"""M23 App Factory - render one built-in template into a directory the way the Cloud Core
does (``DeterministicAppGenerator`` + the ``ProjectFiles`` policy), for the real headless DOM
exercise in ``exercise-app-oracle.py`` (spec §4/§6/§7).

Run with the API interpreter from ``services/api``::

    uv run python ../../scripts/tests/render-app-template.py --template task-tracker \
        --name "Yapılacaklar" --out <dir>

Prints the rendered file list and the manifest; exits 1 if the policy refuses the render.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from app.appfactory.generator import DeterministicAppGenerator
from app.appfactory.spec import TEMPLATE_KIND, AppSpec
from app.appfactory.validation import AppValidationError, validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--template", required=True, choices=sorted(TEMPLATE_KIND))
    parser.add_argument("--name", default="Yapılacaklar")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    spec = AppSpec.model_validate(
        {"name": args.name, "kind": TEMPLATE_KIND[args.template], "template": args.template}
    )
    files = DeterministicAppGenerator().generate(spec)
    try:
        validate(files)
    except AppValidationError as exc:
        print(f"refused by the ProjectFiles policy: {exc}", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for entry in files.files:
        target = out / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.text, encoding="utf-8", newline="\n")
    print(json.dumps({"out": str(out), "files": sorted(files.path_set()), "manifest": files.manifest()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

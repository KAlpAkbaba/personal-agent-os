"""Build step of the pagentos-freya-measure image: fetch the FreyaTTS code at the PINNED commit,
file by file, and check every file's sha256 (team/plans/tts-freya-integration-plan.md section 2.1).
A mismatch stops the build. Only the package the measurement imports and the LICENSE are taken.
"""

from __future__ import annotations

import hashlib
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

COMMIT = "146d36c1cb6660646be57d31339db4eed9315de3"
BASE = f"https://raw.githubusercontent.com/freyavoiceai/FreyaTTS/{COMMIT}/"
FILES = {
    "freyatts/__init__.py": "acb2b9ee509c3d4010751fd329f7926b584b8bdaa4060cd2f3c80a07bb95f824",
    "freyatts/model.py": "f4bec78eaa413381e984f6f661333dd89480296fdc6b5d8e995451b1f1080cd8",
    "freyatts/pipeline.py": "bade3e25366321121826a7d19cc4ef352d0534e2255ebec721dfab118edb31b8",
    "freyatts/vae.py": "8aeff5ce4a27d7f0434b9323ab25b7fb1c83560128cb725d28e2126da60cd640",
    "freyatts/char_vocab.json": "f69a8f2abea4ec09535b92b1109d7dce743472b89e7cc6ab218cd3906781340c",
    "LICENSE": "e58b88a04502b355f2f01cd2e3935adca39e7523c91463175169c400729964b2",
}


def main(target: Path) -> int:
    context = ssl.create_default_context(cafile=certifi.where())
    for name, expected in FILES.items():
        with urllib.request.urlopen(BASE + name, timeout=120, context=context) as response:  # noqa: S310 - fixed https URL
            data = response.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            print(f"fetch_code: {name} sha256 {actual} != pinned {expected}", file=sys.stderr)
            return 3
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"fetch_code: {name} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

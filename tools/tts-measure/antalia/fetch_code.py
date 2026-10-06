"""Build step of the pagentos-antalia-measure image: fetch the Antalia 1 inference code and the
BigVGAN vocoder code at their PINNED commits, file by file, and check every file's sha256
(team/plans/tts-antalia-integration-plan.md section 2.1). A mismatch stops the build.

Only the five turkish_tts modules the synthesis path imports and the vocoder files it imports are
taken. Antalia's one-hunk BigVGAN patch (default values for ``proxies`` / ``resume_download``) is
fetched, hash-checked and applied as the exact text replacement it is; the patched ``bigvgan.py``
is hash-checked again. The image has no git and no patch tool.

Note on the patched hash: the plan's table gives ``fb0dee3e...`` for the patched file. That is the
CRLF form of the file (a Windows checkout); the LF bytes git produces from ``git apply -p4`` on the
pinned blob hash to ``a5ced3c6...`` (measured 2026-10-06), which is what this step checks.
"""

from __future__ import annotations

import hashlib
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

ANTALIA_COMMIT = "20f9bfeaaefefb3ef723c292fe2bb0306e08823d"
BIGVGAN_COMMIT = "7d2b454564a6c7d014227f635b7423881f14bdac"
ANTALIA_BASE = f"https://raw.githubusercontent.com/0daycloud/antalia/{ANTALIA_COMMIT}/"
BIGVGAN_BASE = f"https://raw.githubusercontent.com/NVIDIA/BigVGAN/{BIGVGAN_COMMIT}/"
#: source path in the repository -> (target path under the output root, sha256)
ANTALIA_FILES = {
    "src/turkish_tts/__init__.py": (
        "antalia/turkish_tts/__init__.py",
        "1b3261cde5f8f4b70577dd27a89880fda87f2ea75e0ffa8ec7efef45c81e5297",
    ),
    "src/turkish_tts/crossflow.py": (
        "antalia/turkish_tts/crossflow.py",
        "597bfc81f7ff23ec4ccf2da82f061293b3327926584fffcc61620b2a87710d38",
    ),
    "src/turkish_tts/crossflow_train.py": (
        "antalia/turkish_tts/crossflow_train.py",
        "4392208dbf7e0d20b1778c4ca0453d6f48ee33bd1f419fd115a4ff6acc889d5d",
    ),
    "src/turkish_tts/crossflow_release.py": (
        "antalia/turkish_tts/crossflow_release.py",
        "89cd6399a7f7a9154de0de5bdaa217ad530f8a5c691136cad5833227a1c84f9c",
    ),
    "src/turkish_tts/normalize.py": (
        "antalia/turkish_tts/normalize.py",
        "84956ca49521f1a52f3abcdf5dcc759e385d1d262f10483b05213562f0466957",
    ),
    "LICENSE": (
        "antalia/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
    "NOTICE": (
        "antalia/NOTICE",
        "e2db777defb70560a627e6c24959615b1fdb9c978ba32488f5452a0335f6a22d",
    ),
}
PATCH = (
    "scripts/patches/bigvgan-huggingface-hub-1.patch",
    "c65018be7ffc0e56117b5896a2c602e8b2361732bb64c4a39884d0dbbf376cf0",
)
BIGVGAN_FILES = {
    "bigvgan.py": "dc0a39917d3a6f720afd888afe585ecbb72211e808d692deb4e5d85608062acb",
    "activations.py": "ff2562e116399bca730929aeb07e029a61321660a4f07c3db0b8f9853c70470f",
    "utils.py": "04eee590ca04ca33a6b2b79802a6514bbe7d6867164c72d61f9404ea9b0101c4",
    "env.py": "0c458da3132ebcce272eb277f9af489d09f3cf3dd4378ceeeb915b467aa039a5",
    "meldataset.py": "8b9e35a4f62728fdf55003b93c7ab3d8bb982504e0366333842f0ffa3293e6db",
    "alias_free_activation/torch/__init__.py": (
        "2e3138e1052e377ba2e51ea59c7d5d255a519559001757af21dbec4cb9c22471"
    ),
    "alias_free_activation/torch/act.py": (
        "651448005dd5ae0c193da60d1a50c0aa53550a4a3050e29ed7d35cf86410e212"
    ),
    "alias_free_activation/torch/filter.py": (
        "acf2257276e617dd3161e53abc0a1582e586a3d2d618a43235c7f83818b4e179"
    ),
    "alias_free_activation/torch/resample.py": (
        "4d7e1bf4169d03f59360f34f33d029c04b99364150f6737563ebfbee0ab49d79"
    ),
    "LICENSE": "5c7f573db5f807a9adc2a755c4901e203ea067f73c7a20fa6b703da7e77d7b35",
}
PATCH_OLD = b"        proxies: Optional[Dict],\n        resume_download: bool,\n"
PATCH_NEW = b"        proxies: Optional[Dict] = None,\n        resume_download: bool = False,\n"
BIGVGAN_PATCHED_SHA256 = "a5ced3c62014a299538f4e9fc4d27a9e44f744e9feefd3bb5a1de6046e40459b"


class PinMismatch(Exception):
    pass


def _fetch(url: str, expected: str, context: ssl.SSLContext) -> bytes:
    with urllib.request.urlopen(url, timeout=120, context=context) as response:  # noqa: S310 - fixed https URL
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise PinMismatch(f"{url} sha256 {actual} != pinned {expected}")
    return data


def apply_patch(original: bytes, patch: bytes) -> bytes:
    """The pinned patch's one hunk, as text; refuses anything but exactly one match."""
    if b"-" + PATCH_OLD.splitlines(keepends=True)[0] not in patch:
        raise PinMismatch("the patch is not the pinned one-hunk change")
    if original.count(PATCH_OLD) != 1:
        raise PinMismatch("bigvgan.py does not hold the patched lines exactly once")
    patched = original.replace(PATCH_OLD, PATCH_NEW)
    actual = hashlib.sha256(patched).hexdigest()
    if actual != BIGVGAN_PATCHED_SHA256:
        raise PinMismatch(f"patched bigvgan.py sha256 {actual} != pinned {BIGVGAN_PATCHED_SHA256}")
    return patched


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"fetch_code: {target} ok")


def main(root: Path) -> int:
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        for source, (target, expected) in ANTALIA_FILES.items():
            _write(root / target, _fetch(ANTALIA_BASE + source, expected, context))
        patch = _fetch(ANTALIA_BASE + PATCH[0], PATCH[1], context)
        for name, expected in BIGVGAN_FILES.items():
            data = _fetch(BIGVGAN_BASE + name, expected, context)
            if name == "bigvgan.py":
                data = apply_patch(data, patch)
            _write(root / "bigvgan" / name, data)
    except PinMismatch as error:
        print(f"fetch_code: {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

"""The images the dev and production stacks run, as the compose files name them.

2026-09-11: CI's integration job could no longer pull ``minio/minio:RELEASE.2025-04-22T22-12-26Z``
from Docker Hub ("pull access denied") while every other image pulled; the production host
only kept running because the image was already in its cache - a fresh host, or a disaster
recovery onto a new VM, could not have started object storage at all. The same release is
served by quay.io under the SAME manifest-list digest (``sha256:a1ea29fa...``, checked with
``docker buildx imagetools inspect`` against the digest production runs), so the stacks now
name it there, pinned by that digest: identical bits, a registry that serves them, and no
tag that can be repointed underneath us.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE = {
    "dev": REPO_ROOT / "infra" / "docker" / "docker-compose.dev.yml",
    "prod": REPO_ROOT / "infra" / "docker" / "docker-compose.prod.yml",
}
MINIO_DIGEST = "sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"


def _images(path: Path) -> list[str]:
    return re.findall(r"^\s*image:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)


@pytest.mark.parametrize("stack", sorted(COMPOSE))
def test_object_storage_is_pulled_from_a_registry_that_serves_it_pinned_by_digest(
    stack: str,
) -> None:
    minio = [image for image in _images(COMPOSE[stack]) if "minio/minio" in image]
    assert minio == [f"quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@{MINIO_DIGEST}"]


def test_dev_and_production_run_the_same_object_storage_bits() -> None:
    def minio(stack: str) -> str:
        return next(i for i in _images(COMPOSE[stack]) if "minio/minio" in i)

    assert minio("dev") == minio("prod")

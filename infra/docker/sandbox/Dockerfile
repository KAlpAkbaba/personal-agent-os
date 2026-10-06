# compute.run sandbox image (team/plans/compute-run-sandbox-adr.md).
#
# The official python:3.12-slim (3.12.15-slim-trixie) by its multi-arch INDEX digest, the same
# string as app/compute/policy.py DEFAULT_IMAGE (a unit test reads both). Nothing is installed.
# The runtime does NOT need this build: the official digest run with `--user 65534:65534` is the
# same thing. It exists so a release can mirror the image into GHCR under its own name.
FROM python@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65534:65534

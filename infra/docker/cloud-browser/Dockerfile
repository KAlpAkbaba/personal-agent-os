# Cloud browser worker image (ADR-0213 PR 2). Build context: services/browser.
#   docker build -f ../../infra/docker/cloud-browser/Dockerfile -t pagentos-cloud-browser:local .
# Pin PLAYWRIGHT_TAG to the playwright version resolved in uv.lock before a release build.
# services/browser/uv.lock resolves playwright 1.62.0; test_pilot02_wiring holds the two equal.
# ONE number for the base image (which carries that Playwright's browsers) and for the
# package pip installs: a newer package on an older image cannot find its Chromium.
ARG PLAYWRIGHT_VERSION=1.62.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble
ARG PLAYWRIGHT_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY browser_agent ./browser_agent
# websockets + cryptography are named here, not in pyproject (the Windows agent venv does not carry them).
RUN pip install . "playwright==${PLAYWRIGHT_VERSION}" "websockets>=13" "cryptography>=43"

RUN useradd --create-home --uid 10001 pagentos \
 && mkdir -p /var/lib/pagentos-cloud/state /var/lib/pagentos-cloud/data \
 && chown -R pagentos:pagentos /var/lib/pagentos-cloud
USER pagentos

# --init and --shm-size come from the compose fragment; Chromium is the image's own.
ENTRYPOINT ["python", "-m", "browser_agent.cloud"]

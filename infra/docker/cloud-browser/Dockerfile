# Cloud browser worker image (ADR-0213 PR 2). Build context: services/browser.
#   docker build -f ../../infra/docker/cloud-browser/Dockerfile -t pagentos-cloud-browser:local .
# Pin PLAYWRIGHT_TAG to the playwright version resolved in uv.lock before a release build.
# services/browser/uv.lock resolves playwright 1.62.0; test_pilot02_wiring holds the two equal.
ARG PLAYWRIGHT_TAG=v1.62.0-noble
FROM mcr.microsoft.com/playwright/python:${PLAYWRIGHT_TAG}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY browser_agent ./browser_agent
# websockets + cryptography are named here, not in pyproject (the Windows agent venv does not carry them).
RUN pip install . "websockets>=13" "cryptography>=43"

RUN useradd --create-home --uid 10001 pagentos \
 && mkdir -p /var/lib/pagentos-cloud/state /var/lib/pagentos-cloud/data \
 && chown -R pagentos:pagentos /var/lib/pagentos-cloud
USER pagentos

# --init and --shm-size come from the compose fragment; Chromium is the image's own.
ENTRYPOINT ["python", "-m", "browser_agent.cloud"]

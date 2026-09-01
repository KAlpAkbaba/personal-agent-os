# Personal Agent OS — Cloud Core (API + device broker) production image.
#
# Built by the release process (initially on the VPS itself, later by CI into GHCR —
# ADR-0032). Runs uvicorn exactly as scripts/dev-broker.ps1 does locally, including the
# pre-auth WebSocket frame cap (M1 security review #2). The container has no secrets
# baked in: everything arrives as PAGENTOS_* environment variables, and the identity
# root directory is a mounted volume so the owner-credential hash lives on persistent
# storage OUTSIDE the image and outside the database (ADR-0027).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# curl is the container healthcheck; nothing else is added to the base.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/pagentos

# Dependency layer first so code edits do not re-resolve the environment.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

# Non-root: the app needs nothing the user does not own.
RUN useradd --create-home --uid 10001 pagentos \
    && mkdir -p /srv/pagentos/var/identity \
    && chown -R pagentos:pagentos /srv/pagentos
USER pagentos

ENV PAGENTOS_IDENTITY_ROOT_DIR=/srv/pagentos/var/identity

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=20s \
    CMD curl -fsS http://127.0.0.1:8001/v1/system/health >/dev/null || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--ws-max-size", "65536"]

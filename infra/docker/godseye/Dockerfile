# God's Eye View (https://github.com/bilawalsidhu/gods-eye-view, MIT) as a Cloud Core
# workload - owner addition 3 of 2026-09-21, docs/DECISIONS.md ADR-0197.
#
# The upstream project ships no Dockerfile and documents one way to run it: the Vite dev
# server, which also hosts its key-brokering backend ("the key broker runs inside the dev
# server"). So that is what runs here, pinned to one upstream commit, bound to the
# container's interfaces only - the compose service publishes it on the host's Tailscale
# address and nowhere else. No key is baked in: the optional provider keys come from the
# host's env file at run time (GODSEYE_ENV_FILE) and never from this image or the tree.
FROM node:24-bookworm-slim

ARG GEV_REPO=https://github.com/bilawalsidhu/gods-eye-view.git
# 2026-09-21 upstream main ("Merge pull request #284"). Bump deliberately, never "main".
ARG GEV_COMMIT=0dbde1e36c0177b7664b47702d77ba50f11ddadc

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/gods-eye-view
RUN git init -q . \
    && git remote add origin "$GEV_REPO" \
    && git fetch -q --depth 1 origin "$GEV_COMMIT" \
    && git checkout -q FETCH_HEAD \
    && rm -rf .git

# Puppeteer is a devDependency the tests use; its Chromium download is ~150 MB of
# nothing this container will ever run. Sharp's prebuilt binary is fine on linux-x64.
ENV PUPPETEER_SKIP_DOWNLOAD=1 \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1 \
    NODE_ENV=development
RUN npm ci --no-audit --no-fund

RUN useradd --create-home --uid 10002 godseye && chown -R godseye:godseye /srv/gods-eye-view
USER godseye

# The upstream's own documented remote-access invocation; HOST/PORT are also read by its
# server, and the "shared server" mode it enters disables the in-app key panel (keys are
# env-only when not on localhost - see its SECURITY.md).
ENV HOST=0.0.0.0 \
    PORT=4173
EXPOSE 4173
HEALTHCHECK --interval=30s --timeout=5s --retries=5 --start-period=90s \
    CMD curl -fsS http://127.0.0.1:4173/ >/dev/null || exit 1
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "4173"]

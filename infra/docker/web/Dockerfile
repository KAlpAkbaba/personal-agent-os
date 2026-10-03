# The owner's web shell (apps/web: Ofis, Onay Merkezi, voice) as a Cloud Core `aux`
# workload - docs/DECISIONS.md, the "web on the Cloud Core" ADR. The phone reaches it over
# the tailnet; the compose service publishes it on the host's LOOPBACK only and
# `tailscale serve` puts HTTPS in front (scripts/cloud/enable-web-tailnet-https.sh).
#
# Build context is the REPOSITORY ROOT (the pnpm workspace and its lockfile live there);
# infra/docker/web/Dockerfile.dockerignore keeps the context to the web package and the
# lockfile, and keeps every `.env*` and node_modules out of it.
#
# No secret is baked in or needed: the only build arguments are the public API base the
# browser calls (`/api`, same-origin) and the compose-internal name of the edge that the
# server rewrites it to. The owner's sign-in token lives in the BROWSER's localStorage.

# ----------------------------------------------------------------------------- build
FROM node:24-bookworm-slim AS build

ARG PNPM_VERSION=11.24.0
# The browser calls `/api/*` on this same origin; Next rewrites it server-side. A
# NEXT_PUBLIC_ value is inlined into the client bundle by `next build`.
ARG NEXT_PUBLIC_API_BASE=/api
# `next build` evaluates the rewrite and stores it in the routes manifest, so `next start`
# never re-reads this: it is a BUILD argument by necessity. The edge is the compose
# service that follows the blue/green switch - never a fixed api colour.
ARG PAGENTOS_API_UPSTREAM=http://edge:8001

ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} \
    PAGENTOS_API_UPSTREAM=${PAGENTOS_API_UPSTREAM} \
    PAGENTOS_WEB_STANDALONE=1 \
    CI=true

RUN npm install --global --no-audit --no-fund "pnpm@${PNPM_VERSION}"

WORKDIR /repo
# The lockfile and manifests first: the dependency layer is rebuilt only when they change.
COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @pagentos/web...

COPY packages/protocol/realtime-session-contract.json packages/protocol/realtime-session-contract.json
COPY apps/web apps/web
WORKDIR /repo/apps/web
RUN pnpm exec next build

# ----------------------------------------------------------------------------- run
# Only what the traced production server needs: `.next/standalone` (server + the files
# Next's output tracing found in node_modules), the static assets and `public/`. No pnpm,
# no source, no devDependencies, no build tools.
FROM node:24-bookworm-slim AS run

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN useradd --create-home --uid 10003 pagentosweb
WORKDIR /srv/web
COPY --from=build --chown=pagentosweb:pagentosweb /repo/apps/web/.next/standalone ./
COPY --from=build --chown=pagentosweb:pagentosweb /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=pagentosweb:pagentosweb /repo/apps/web/public ./apps/web/public

USER pagentosweb
EXPOSE 3000
# `/` is a statically prerendered page: it answers without the api being up, so a release
# or a rollback of the api never makes this container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "apps/web/server.js"]

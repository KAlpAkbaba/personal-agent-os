import path from "node:path";
import type { NextConfig } from "next";

/**
 * Same-origin API proxy for the owner's real sessions (M12 real-microphone qualification).
 *
 * The web shell calls the API from the browser. Against the Hetzner Cloud Core that would
 * need the browser's origin in the API's CORS allowlist, i.e. an env change and an api
 * recreate on the proven baseline for a client that runs on the owner's own PC. Instead,
 * when PAGENTOS_API_UPSTREAM is set (e.g. http://pagentos-core:8001, reached over the
 * tailnet), `/api/*` on this dev server is rewritten server-side to the upstream, and the
 * page is started with NEXT_PUBLIC_API_BASE=/api so every fetch stays same-origin. The
 * browser's WebRTC leg still goes straight to the realtime provider with the ephemeral
 * credential; only Cloud Core traffic goes through the rewrite. Unset -> no rewrite, the
 * loopback default applies (dev stack).
 *
 * In a PRODUCTION build the rewrite is evaluated by `next build` and stored in the routes
 * manifest - `next start` does not re-read PAGENTOS_API_UPSTREAM. The image
 * (infra/docker/web/Dockerfile, ADR "web on the Cloud Core") therefore takes the upstream
 * as a build argument; it is the compose-internal name of the edge, never a secret.
 */
const upstream = process.env.PAGENTOS_API_UPSTREAM?.replace(/\/+$/, "");

/**
 * The container image runs the traced "standalone" server: only the files the production
 * server needs, not the whole workspace's node_modules. Opt-in (the image sets it) so the
 * dev server and the quality gate's build are exactly what they were. The workspace's
 * node_modules lives two directories up (pnpm), so that is the tracing root.
 */
const standalone = process.env.PAGENTOS_WEB_STANDALONE === "1";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  ...(standalone
    ? { output: "standalone" as const, outputFileTracingRoot: path.join(process.cwd(), "..", "..") }
    : {}),
  async rewrites() {
    return upstream ? [{ source: "/api/:path*", destination: `${upstream}/:path*` }] : [];
  },
};

export default nextConfig;

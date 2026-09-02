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
 */
const upstream = process.env.PAGENTOS_API_UPSTREAM?.replace(/\/+$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return upstream ? [{ source: "/api/:path*", destination: `${upstream}/:path*` }] : [];
  },
};

export default nextConfig;

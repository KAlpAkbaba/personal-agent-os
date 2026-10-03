import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The web shell is also built into a container image that runs on the Cloud Core
 * (infra/docker/web/Dockerfile). Two switches in next.config.ts serve that image, and
 * neither may change what the dev server and the quality gate's `next build` already do:
 *
 *  - PAGENTOS_WEB_STANDALONE=1 -> the traced "standalone" output (opt-in, the image sets it);
 *  - PAGENTOS_API_UPSTREAM     -> the `/api/*` rewrite, evaluated at BUILD time in production
 *    (the routes manifest keeps it), which is why the image takes it as a build argument.
 */

const KEYS = ["PAGENTOS_WEB_STANDALONE", "PAGENTOS_API_UPSTREAM"] as const;
const saved: Record<string, string | undefined> = {};

async function loadConfig() {
  vi.resetModules();
  return (await import("../../next.config")).default;
}

beforeEach(() => {
  for (const key of KEYS) saved[key] = process.env[key];
  for (const key of KEYS) delete process.env[key];
});

afterEach(() => {
  for (const key of KEYS) {
    if (saved[key] === undefined) delete process.env[key];
    else process.env[key] = saved[key];
  }
});

describe("next.config.ts", () => {
  it("is exactly the dev/gate configuration when nothing is set: no standalone output, no rewrite", async () => {
    const config = await loadConfig();
    expect(config.output).toBeUndefined();
    expect(config.outputFileTracingRoot).toBeUndefined();
    expect(await config.rewrites!()).toEqual([]);
  });

  it("rewrites /api/* to the upstream, trailing slashes trimmed", async () => {
    process.env.PAGENTOS_API_UPSTREAM = "http://edge:8001//";
    const config = await loadConfig();
    expect(await config.rewrites!()).toEqual([{ source: "/api/:path*", destination: "http://edge:8001/:path*" }]);
  });

  it("emits the traced standalone output, rooted at the workspace, only when the image asks", async () => {
    process.env.PAGENTOS_WEB_STANDALONE = "1";
    const config = await loadConfig();
    expect(config.output).toBe("standalone");
    // pnpm keeps node_modules at the workspace root, two directories above apps/web.
    expect(config.outputFileTracingRoot).toBe(path.resolve(process.cwd(), "..", ".."));
  });

  it("treats any other value of the standalone switch as off", async () => {
    process.env.PAGENTOS_WEB_STANDALONE = "true";
    const config = await loadConfig();
    expect(config.output).toBeUndefined();
  });
});

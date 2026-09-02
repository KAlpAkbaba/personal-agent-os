import { defineConfig } from "vitest/config";

// Unit tests for the voice client run in Node: the controller, reporter,
// hesitation guard and dialects are pure, and the fakes stand in for the
// browser ports. Nothing here needs a DOM.
export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
});

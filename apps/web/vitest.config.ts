import { defineConfig } from "vitest/config";

// Unit tests for the voice client run in Node: the controller, reporter,
// hesitation guard and dialects are pure, and the fakes stand in for the
// browser ports. Nothing here needs a DOM.
//
// The research surface (M13) is tested the same way: its components are
// rendered to static markup with react-dom/server and asserted on as HTML,
// its API client is exercised against a mocked `apiFetch`. No jsdom; the
// tsconfig's `jsx: react-jsx` already gives the automatic runtime.
export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    environment: "node",
  },
});

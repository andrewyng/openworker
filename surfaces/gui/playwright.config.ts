import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// E2E harness for the GUI. Tests are hermetic: every /v1 request and the event WebSocket are mocked
// at the network layer (see e2e/fixtures.ts), so they run without the Python backend and never
// mutate real state — safe for CI and for asserting regressions in the interaction flows.
const PORT = 5199;
// The dev server reads the standalone server's token from the state dir on each page load;
// point it at a scratch dir so the suite never picks up a developer's real token, and so
// specs can write one of their own. Workers re-run this file but inherit the env, so `||=`
// keeps one dir for the whole run.
process.env.OPENWORKER_E2E_STATE_DIR ||= fs.mkdtempSync(path.join(os.tmpdir(), "openworker-e2e-state-"));

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Dev server on a dedicated port so it never collides with a running `npm run dev` (5173).
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    env: { COWORKER_STATE_DIR: process.env.OPENWORKER_E2E_STATE_DIR },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

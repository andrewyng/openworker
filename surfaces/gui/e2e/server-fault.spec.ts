// #382: a packaged desktop app whose sidecar died used to render a fully navigable UI
// where every backend call hung forever with no error surfaced. With the shell now
// reporting `get_server_status`, the SPA must fail fast to the fault screen — not the
// folder gate, and not an endless splash.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a dead sidecar surfaces the server-fault screen instead of a silent hang", async ({
  page,
}) => {
  // Simulate the desktop shell: __TAURI__ is present and reports the sidecar exited.
  await page.addInitScript(() => {
    (globalThis as any).__TAURI__ = {
      core: {
        invoke: async (cmd: string) => {
          if (cmd === "get_server_status") {
            return {
              status: "exited",
              detail: "exit status: 3",
              bin_path:
                "/Applications/OpenWorker.app/Contents/Resources/sidecar/openworker-server",
              log_path: "/Users/me/.config/coworker/logs/openworker-server.log",
            };
          }
          return null;
        },
      },
    };
  });
  // Nothing is listening: every health probe dies, exactly like the field report.
  await page.route("**/v1/health", (route) => route.abort());
  await page.goto("/");

  const fault = page.getByTestId("server-fault");
  await expect(fault).toBeVisible({ timeout: 10_000 });
  await expect(fault).toContainText("exited during startup");
  await expect(fault).toContainText("openworker-server.log");
  // The folder gate must NOT be offered — nothing behind it works.
  await expect(page.locator(".gate-input")).toHaveCount(0);
});

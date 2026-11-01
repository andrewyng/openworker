import { test, expect, seedMachines } from "./fixtures";

const machine = { id: "m1", name: "Remote box", connected: true, fingerprint: "test", app_version: "0.3.1", created_at: 1, last_seen: 1 };
const remote = { session_id: "remote-1", title: "Old remote conversation", workspace: "/work", agent: "cowork", model: "gpt-5.5", updated_at: "2026-08-01 00:00:00", messages: 2, mode: "interactive", pinned: true, archived: false };
const local = { session_id: "pinned-cowork-1", title: "Local refreshed", workspace: "/Users/test/OpenWorker/launch-note", agent: "cowork", model: "gpt-5.5", updated_at: "2026-07-01 00:00:00", messages: 2, mode: "interactive", pinned: true, archived: false };

test("local updates while remote refresh keeps old rows and shows progress, then replaces remote rows", async ({ page }) => {
  await seedMachines(page, [machine], { m1: [remote] });
  await page.goto("/");
  await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/cloud/machines", async route => {
    await gate;
    await route.fulfill({ json: { session: "signed_out", machines: [] } });
  });
  await page.route("**/v1/sessions", route => route.fulfill({ json: { sessions: [local] } }));
  await page.route("**/v1/machines/m1/sessions", route => route.fulfill({ json: { live: true, sessions: [{ ...remote, title: "Remote refreshed" }] } }));
  try {
    await expect(page.getByText(local.title, { exact: true }).first()).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("remote-sessions-loading")).toBeVisible();
    await expect(page.getByText("Remote refreshed", { exact: true })).toHaveCount(0);
    release();
    await expect(page.getByText("Remote refreshed", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("remote-sessions-loading")).toHaveCount(0);
  } finally { release(); }
});

test("remote failure preserves old rows, stops the spinner, and Retry accepts a successful empty list", async ({ page }) => {
  await seedMachines(page, [machine], { m1: [remote] });
  await page.goto("/");
  await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  await page.route("**/v1/machines/m1/sessions", route => route.fulfill({ status: 500, json: { detail: "Synthetic failure" } }));
  const error = page.getByTestId("sessions-refresh-error");
  await expect(error).toContainText("remote", { timeout: 8_000 });
  await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("remote-sessions-loading")).toHaveCount(0);
  await page.unroute("**/v1/machines/m1/sessions");
  await page.route("**/v1/machines/m1/sessions", route => route.fulfill({ json: { live: true, sessions: [] } }));
  await error.getByRole("button", { name: "Retry" }).click();
  await expect(error).toHaveCount(0);
  // The active title may still name the open transcript; the sidebar row must disappear.
  await expect(page.locator(".sidebar").getByText(remote.title, { exact: true })).toHaveCount(0);
  await expect(page.getByText("Weekly plan 1", { exact: true }).first()).toBeVisible();
});

for (const chooseLocal of [false, true]) {
  test(`cold boot displays local rows, preserves ${chooseLocal ? "a user's local selection" : "automatic newest-remote selection"}`, async ({ page }) => {
    await seedMachines(page, [machine], { m1: [remote] });
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/v1/machines/m1/sessions", async route => {
      await gate;
      await route.fulfill({ json: { live: true, sessions: [remote] } });
    });
    try {
      await page.goto("/");
      const row = page.getByText("Draft the launch note", { exact: true }).first();
      await expect(row).toBeVisible({ timeout: 5_000 });
      await expect(page.getByTestId("remote-sessions-loading")).toBeVisible();
      await expect(page.getByTestId("session-restore-pending")).toBeVisible();
      if (chooseLocal) {
        await row.click();
        await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
      }
      release();
      await expect(page.getByTestId("remote-sessions-loading")).toHaveCount(0);
      await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
      if (chooseLocal) await expect(page.getByTestId("runson-chip")).not.toContainText("Remote box");
      else await expect(page.getByTestId("runson-chip")).toContainText("Remote box");
    } finally { release(); }
  });
}

test("remote timeout stops progress and preserves old rows until retry succeeds", async ({ page }) => {
  test.setTimeout(40_000);
  await seedMachines(page, [machine], { m1: [remote] });
  await page.goto("/");
  await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/machines/m1/sessions", async route => {
    await gate;
    await route.fulfill({ json: { live: true, sessions: [] } });
  });
  try {
    await expect(page.getByTestId("remote-sessions-loading")).toBeVisible({ timeout: 8_000 });
    await expect(page.getByTestId("sessions-refresh-error")).toBeVisible({ timeout: 18_000 });
    await expect(page.getByTestId("remote-sessions-loading")).toHaveCount(0);
    await expect(page.locator(".sidebar").getByText(remote.title, { exact: true })).toBeVisible();
    await page.getByTestId("sessions-refresh-error").getByRole("button", { name: "Retry" }).click();
    release();
    await expect(page.locator(".sidebar").getByText(remote.title, { exact: true })).toHaveCount(0);
  } finally { release(); }
});

test("a delayed old refresh cannot replace a newer local list", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Weekly plan 1", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route("**/v1/sessions", async route => {
    const first = ++calls === 1;
    if (first) await gate;
    await route.fulfill({ json: { sessions: [{ ...local, title: first ? "Slow old result" : "Latest local result" }] } });
  });
  try {
    await expect(page.getByText("Latest local result", { exact: true }).first()).toBeVisible({ timeout: 13_000 });
    const response = page.waitForResponse(r => r.url().endsWith("/v1/sessions"));
    release();
    await response;
    // Let the old response reach its state-update callbacks.
    await page.waitForTimeout(150);
    await expect(page.getByText("Latest local result", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Slow old result", { exact: true })).toHaveCount(0);
  } finally { release(); }
});

test("local failure retains both sources and a successful empty local list removes only local rows", async ({ page }) => {
  await seedMachines(page, [machine], { m1: [remote] });
  await page.goto("/");
  await expect(page.getByText(remote.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  await page.route("**/v1/sessions", route => route.fulfill({ status: 503, json: { detail: "Synthetic failure" } }));
  const error = page.getByTestId("sessions-refresh-error");
  await expect(error).toContainText("local", { timeout: 8_000 });
  await expect(page.getByText("Weekly plan 1", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".sidebar").getByText(remote.title, { exact: true })).toBeVisible();
  await page.unroute("**/v1/sessions");
  await page.route("**/v1/sessions", route => route.fulfill({ json: { sessions: [] } }));
  await error.getByRole("button", { name: "Retry" }).click();
  await expect(error).toHaveCount(0);
  await expect(page.getByText("Weekly plan 1", { exact: true })).toHaveCount(0);
  await expect(page.locator(".sidebar").getByText(remote.title, { exact: true })).toBeVisible();
});

test("an explicit older remote deep link is still restored", async ({ page }) => {
  await seedMachines(page, [machine], { m1: [{ ...remote, updated_at: "2026-01-01 00:00:00" }] });
  await page.goto("/#/s/remote-1");
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  await expect(page.getByTestId("runson-chip")).toContainText("Remote box");
});

test("sign-out removes cloud rows and a pending old response cannot restore them", async ({ page }) => {
  let signedIn = true;
  await page.route("**/v1/cloud/machines", route => route.fulfill({ json: {
    session: signedIn ? "ok" : "signed_out", machines: signedIn ? [{ ...machine, id: "cloud-box" }] : [],
  } }));
  await page.route("**/v1/cloud/machines/cloud-box/sessions", route => route.fulfill({ json: { live: true, sessions: [{ ...remote, updated_at: "2026-01-01 00:00:00" }] } }));
  await page.goto("/");
  const cloudRow = page.locator(".sidebar").getByText(remote.title, { exact: true });
  await expect(cloudRow).toBeVisible();
  await expect(page.getByTestId("session-restore-pending")).toHaveCount(0);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/v1/cloud/machines/cloud-box/sessions", async route => {
    requested = true;
    await gate;
    await route.fulfill({ json: { live: true, sessions: [remote] } });
  });
  try {
    await expect.poll(() => requested, { timeout: 8_000 }).toBe(true);
    signedIn = false;
    await page.evaluate(async () => {
      const api = await import("/src/api.ts");
      api.announceCloudChanged();
    });
    await expect(cloudRow).toHaveCount(0);
    const response = page.waitForResponse(r => r.url().includes("/cloud-box/sessions"));
    release();
    await response;
    await page.waitForTimeout(150);
    await expect(cloudRow).toHaveCount(0);
  } finally { release(); }
});

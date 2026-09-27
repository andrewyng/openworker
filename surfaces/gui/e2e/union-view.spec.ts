import { test, expect, seedMachines } from "./fixtures";

// Union view (spec §"Union view on the signed-in desktop"): the desktop shows
// its local registry plus the hosted cloud's machines, proxied by the sidecar
// under /v1/cloud/machines. Cloud rows wear a `cloud:` id prefix in the GUI —
// one seam routes every consumer (rows, picker, scoped pages, sessions).

const LOCAL = {
  id: "m1",
  name: "hetzner-box",
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: 1_755_000_000,
  connected: true,
};

const CLOUD_ROW = {
  id: "c1",
  name: "cloud-vm",
  fingerprint: "8e74c8c42ec58dfa",
  app_version: "0.2.0",
  created_at: 1_755_100_000,
  last_seen: 1_755_100_000,
  connected: true,
};

async function seedCloud(
  page: import("@playwright/test").Page,
  body: Record<string, unknown> = {
    session: "ok",
    org: { id: "org:deeplearning-ai", name: "DeepLearning AI" },
    machines: [CLOUD_ROW],
  },
) {
  await page.route("**/v1/cloud/machines", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    }),
  );
}

test("Machines page: cloud section lists the org's machines beside the local registry", async ({
  page,
}) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page);
  await page.goto("/#/settings/machines");

  await expect(page.getByTestId("machine-hetzner-box")).toBeVisible();
  const section = page.getByTestId("cloud-machines-section");
  await expect(section).toContainText("OpenWorker Cloud · DeepLearning AI");
  await expect(section.getByTestId("machine-cloud-vm")).toBeVisible();
});

test("cloud rename routes through the sidecar's cloud proxy", async ({ page }) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page);
  let renamed = "";
  await page.route("**/v1/cloud/machines/c1", (route) => {
    renamed = route.request().postDataJSON()?.name ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ machine: { id: "c1", name: renamed } }),
    });
  });
  await page.goto("/#/settings/machines");

  const row = page.getByTestId("machine-cloud-vm");
  await row.hover();
  await row.getByText("Rename").click();
  await page.locator("input:focus").fill("prod-vm");
  await page.keyboard.press("Enter");
  await expect.poll(() => renamed).toBe("prod-vm");
});

test("expired cloud session degrades the section; the local list is untouched", async ({
  page,
}) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page, { session: "expired", machines: [] });
  await page.goto("/#/settings/machines");

  await expect(page.getByTestId("machine-hetzner-box")).toBeVisible();
  await expect(page.getByTestId("cloud-session-expired")).toContainText(
    "session expired",
  );
});

test("signed out: the cloud section does not exist at all", async ({ page }) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page, { session: "signed_out", machines: [] });
  await page.goto("/#/settings/machines");

  await expect(page.getByTestId("machine-hetzner-box")).toBeVisible();
  await expect(page.getByTestId("cloud-machines-section")).toHaveCount(0);
});

test("Settings picker offers cloud machines; scoped pages ride the cloud proxy", async ({
  page,
}) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page);
  await page.route("**/v1/cloud/machines/c1/p/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        skills: [
          {
            name: "cloud-only-skill",
            description: "Lives on the cloud VM",
            source: "workspace",
            enabled: true,
          },
        ],
      }),
    }),
  );
  await page.goto("/#/settings/skills");

  const picker = page.getByTestId("machine-scope-picker");
  await picker.selectOption("cloud:c1");
  await expect(page.getByText("cloud-only-skill")).toBeVisible();
  await expect(page).toHaveURL(/#\/settings\/skills\?m=cloud:c1$/);
});

test("runs-on menu groups cloud machines under their own header", async ({
  page,
}) => {
  await seedMachines(page, [LOCAL]);
  await seedCloud(page);
  await page.goto("/");

  await page.getByRole("button", { name: "New session" }).click();
  const chip = page.getByTestId("runson-chip");
  await chip.click();
  await expect(page.getByText("OpenWorker Cloud", { exact: true })).toBeVisible();
  await expect(page.getByText("⌂ cloud-vm")).toBeVisible();
  await expect(page.getByText("⌂ hetzner-box")).toBeVisible();
});

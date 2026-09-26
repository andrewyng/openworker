import { test, expect, seedMachines } from "./fixtures";

// Settings URL routes + the Account page (UX-046 tail, 2026-08-30):
// #/settings/{page} deep links open Settings on that page, tab browsing keeps
// the URL current (replaceState — no history pileup), and ?m= carries the
// machine scope for machine-scoped pages. The Account page is the APP-group
// home for identity: OpenWorker Cloud sign-in on desktop, the Auth0 identity
// + org switcher on the hosted dashboard (moved off the Machines page).

const HETZNER = {
  id: "m1",
  name: "hetzner-box",
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: 1_755_000_000,
  connected: true,
};

test("deep link #/settings/skills opens that page; tab clicks ride the URL", async ({
  page,
}) => {
  await page.goto("/#/settings/skills");
  await expect(page.getByRole("heading", { name: "Skills" })).toBeVisible();
  await expect(page).toHaveURL(/#\/settings\/skills$/);

  // Browsing to another page rewrites the hash — bookmarks always name the open page.
  await page.getByRole("button", { name: "General", exact: true }).click();
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page).toHaveURL(/#\/settings\/general$/);
});

test("?m= deep-links a machine scope; the picker adopts it and the URL keeps it", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.goto("/#/settings/models?m=m1");
  await expect(page.getByTestId("machine-models-panel")).toBeVisible();
  await expect(page.getByTestId("machine-scope-picker")).toHaveValue("m1");
  await expect(page).toHaveURL(/#\/settings\/models\?m=m1$/);

  // App-scope pages carry no machine scope — the param drops off.
  await page.getByRole("button", { name: "General", exact: true }).click();
  await expect(page).toHaveURL(/#\/settings\/general$/);
});

test("leaving Settings hands the URL back to the session route", async ({ page }) => {
  await page.goto("/#/settings/general");
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  // Settings owns the left column while open (the session sidebar is not mounted);
  // "Back to app" on the rail is the way out.
  await expect(page.getByTestId("settings-rail")).toBeVisible();
  await expect(page.getByRole("button", { name: "New session" })).toHaveCount(0);
  await page.getByTestId("settings-back").click();
  await expect(page).toHaveURL(/#\/s\/[\w-]+$/);
  await expect(page.getByRole("button", { name: "New session" })).toBeVisible();
});

test("Account page (desktop): sign in lands the identity; sign out returns the button", async ({
  page,
}) => {
  await page.goto("/#/settings/account");
  const card = page.getByTestId("cloud-signin-card");
  await expect(card).toBeVisible();

  // The fixture's /v1/cloud/login flips state instantly; the page polls it in.
  await card.getByTestId("account-page-sign-in").click();
  await expect(card.getByText("rohit@openworker.com")).toBeVisible({ timeout: 10000 });

  await card.getByRole("button", { name: "Sign out" }).click();
  await expect(card.getByTestId("account-page-sign-in")).toBeVisible();
});

test("Account page (cloud): identity and org name (display only), no desktop sign-in card", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route("**/v1/capabilities", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ mode: "cloud" }),
    }),
  );
  // One login, one org: /v1/me offers exactly the resolved org — the page
  // shows its name and offers no way to switch.
  await page.route("**/v1/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor: "dave@example.com",
        email: "dave@example.com",
        org_id: "org:deeplearning-ai",
        admin: false,
        orgs: [{ id: "org:deeplearning-ai", name: "DeepLearning AI", role: "admin" }],
        policies: { key_push: true },
      }),
    }),
  );
  await page.goto("/#/settings/account");

  const account = page.getByTestId("cloud-account-card");
  await expect(account.getByText("dave@example.com")).toBeVisible();
  await expect(account.getByTestId("org-name")).toHaveText("DeepLearning AI");
  await expect(account.locator("select")).toHaveCount(0);
  await expect(page.getByTestId("cloud-signin-card")).toHaveCount(0);
});

test("a spent approval deep link leaves the URL on Settings ▸ Machines", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/remote\/device\/[^/]+$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_code: "WXYZ-2345",
        name: "deep-vm",
        fingerprint: "0123456789abcdef",
        expires_at: Date.now() / 1000 + 600,
      }),
    }),
  );
  await page.goto("/#/approve/WXYZ-2345");
  await expect(page.getByTestId("approval-fingerprint")).toContainText("0123456789abcdef", {
    timeout: 10000,
  });
  // The code was consumed on landing — a refresh must not replay a decided
  // approval, so the URL has already moved to the page itself.
  await expect(page).toHaveURL(/#\/settings\/machines$/);
});

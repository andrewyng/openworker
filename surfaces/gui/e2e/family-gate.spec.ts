import { test, expect } from "./fixtures";

// Which personas belong to a PROJECT — a folder the user picks, enforced by the FolderGate, and
// the grouping the sidebar files their sessions under.
//
// §16 (2026-07-03) tied this to family: code gated, knowledge never did. Reversed by owner ask —
// with several personas installed, all of them except the chat-shaped Fast Chat do work that
// belongs to a project, and only the code persona had the structure to show it. The persona now
// DECLARES it (`projects:` in its manifest) instead of it being inferred from family, which also
// governs unrelated engine behaviour.
// (The mock's Ops persona has zero sessions, so picking it exercises the brand-new-session path
// rather than a resume.)

const personaMenu = (page: import("@playwright/test").Page) => page.locator(".newsplit-menu");

async function startAs(page: import("@playwright/test").Page, persona: RegExp) {
  await page.getByLabel("Choose a persona").click();
  await personaMenu(page).getByRole("button", { name: persona }).click();
}

test("a project persona gates, whatever its family", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();

  // Ops is knowledge-family but declares projects, so it gates exactly like Code does.
  await startAs(page, /Ops/);
  const gate = page.locator(".gate-overlay");
  await expect(gate).toBeVisible();
  await gate.getByPlaceholder("/path/to/your/project").fill("/tmp/e2e-ops-project");
  await gate.getByRole("button", { name: "Open", exact: true }).click();

  await expect(page.locator(".gate-overlay")).toHaveCount(0);
  // The composer is live — matched by role, not by copy: each persona writes its own
  // placeholder, so "Ask the coworker" is Coworker's line, not every session's.
  await expect(page.getByRole("textbox")).toBeVisible();
});

test("code persona: the folder gate blocks until a project is chosen", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();

  await startAs(page, /Code/);

  const gate = page.locator(".gate-overlay");
  await expect(gate).toBeVisible();
  await expect(gate.getByText("Choose a project folder")).toBeVisible();
  // No escape hatch: the gate offers pick-a-folder only (no "switch to Chat" — owner call, §16).
  await expect(gate.getByText(/chat/i)).toHaveCount(0);

  await gate.getByPlaceholder("/path/to/your/project").fill("/tmp/e2e-project");
  await gate.getByRole("button", { name: "Open", exact: true }).click();

  // Gate clears, the session is rooted in the chosen folder, and the code composer is live.
  await expect(page.locator(".gate-overlay")).toHaveCount(0);
  await expect(page.getByPlaceholder(/Ask the coder/)).toBeVisible();
});

import { test, expect } from "./fixtures";

// UX-044: the composer "+" menu's session section — project-memory/board bindings.
// Guards: the two labeled sections, the radio submenu (derived label rules, MRU,
// bound tag), swap-binding round trip, board's "none" row, and naming the current
// project. Bindings are PROJECT memory only — global memory never appears here.

async function openAttach(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByRole("button", { name: "Attach" }).click();
}

test("attach menu: two sections, memory submenu with derived + named rows", async ({ page }) => {
  await openAttach(page);

  await expect(page.getByText("This message")).toBeVisible();
  await expect(page.getByText("This session")).toBeVisible();
  await expect(page.getByRole("button", { name: "Photo or image" })).toBeVisible();

  await page.getByRole("button", { name: "Project memory" }).click();
  const menu = page.getByTestId("project-menu-memory");
  // Derived row: folder form, trimmed to the last 3 segments, tagged.
  await expect(menu.getByText("…/ro4d/demo-universe/notes")).toBeVisible();
  await expect(menu.getByText("this folder")).toBeVisible();
  // Named rows (MRU), no filter under 6, the two actions.
  await expect(menu.getByText("openworker")).toBeVisible();
  await expect(menu.getByText("personal-ops")).toBeVisible();
  await expect(menu.getByPlaceholder("Filter…")).toHaveCount(0);
  await expect(menu.getByText("Name current memory…")).toBeVisible();
  await expect(menu.getByText("View & edit…")).toBeVisible();
});

test("binding swap round-trips and closes the menu", async ({ page }) => {
  await openAttach(page);
  await page.getByRole("button", { name: "Project memory" }).click();
  await page.getByTestId("project-menu-memory").getByText("openworker").click();
  // Menu closed on success.
  await expect(page.getByTestId("project-menu-memory")).toHaveCount(0);

  // Reopen: the binding shows as bound.
  await page.getByRole("button", { name: "Attach" }).click();
  await page.getByRole("button", { name: "Project memory" }).click();
  const menu = page.getByTestId("project-menu-memory");
  await expect(menu.getByText("bound")).toBeVisible();
});

test("board submenu has a none row and its own names", async ({ page }) => {
  await openAttach(page);
  await page.getByRole("button", { name: "Board", exact: true }).click();
  const menu = page.getByTestId("project-menu-board");
  await expect(menu.getByText("none")).toBeVisible();
  await expect(menu.getByText("aicreator-ops")).toBeVisible();
  // Board naming exists; memory's View & edit does not.
  await expect(menu.getByText("Name current board…")).toBeVisible();
  await expect(menu.getByText("View & edit…")).toHaveCount(0);
});

test("naming the current project adds it to the named list", async ({ page }) => {
  await openAttach(page);
  await page.getByRole("button", { name: "Project memory" }).click();
  await page.getByTestId("project-menu-memory").getByText("Name current memory…").click();
  const input = page.getByPlaceholder("Name this memory…");
  await input.fill("my-notes");
  await input.press("Enter");
  await expect(page.getByTestId("project-menu-memory").getByText("my-notes")).toBeVisible();
});

import { test, expect } from "./fixtures";

// The persona's requires_folder trait decides workspace behavior
// (workspace-scratch-design.md), enforced at the SEND moment:
//   requires_folder → send with no folder → "Where should … work?" dialog (recents /
//                     native picker / "Start in a temporary folder", git-init'd, created
//                     only now). Exercised through Security Coworker — the enabled gated
//                     persona in the shipped lineup (Code ships disabled).
//   everything else → starts orphan on a transparent temporary dir — never gated
// The coworker pick lives in the setup chip row above the composer, only before the
// first message of a new session; afterwards the row leaves and the facts move to the
// session header.

async function newDraftAs(page: import("@playwright/test").Page, coworker: RegExp) {
  await page.getByText("New session").first().click();
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByRole("button", { name: coworker }).click();
}

test("scratch coworker: new session starts instantly, no gate, no dialog", async ({ page }) => {
  await page.goto("/");
  await newDraftAs(page, /Ops Coworker/);

  await expect(page.locator(".gate-overlay")).toHaveCount(0);
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello there");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello there/)).toBeVisible();
  await expect(page.getByTestId("send-folder-dialog")).toHaveCount(0);
});

test("gated coworker: send with no folder asks where to work; temp folder sends the message", async ({
  page,
}) => {
  await page.goto("/");
  await newDraftAs(page, /Security Coworker/);

  // No modal gate up front — the composer is live and the draft is composable.
  await expect(page.locator(".gate-overlay")).toHaveCount(0);
  await page.getByPlaceholder(/Ask the coworker/).fill("fix the tests");
  await page.getByRole("button", { name: "Send" }).click();

  const dlg = page.getByTestId("send-folder-dialog");
  await expect(dlg).toBeVisible();
  await expect(dlg.getByText("Where should Security Coworker work?")).toBeVisible();
  await dlg.getByTestId("start-temp-folder").click();

  // The message flies as soon as the choice lands — no second send click, and the local
  // echo isn't duplicated by turn_start (the notice sits between them).
  await expect(page.getByText(/Echo: fix the tests/)).toBeVisible();
  await expect(page.locator(".main-scroll").getByText("fix the tests", { exact: true })).toHaveCount(1);
  await expect(page.getByText("Temporary folder created · git initialized")).toBeVisible();

  // The raw temp path never shows: header says "Temporary folder" + Save as project….
  const sub = page.getByTestId("session-subtitle");
  await expect(sub).toContainText("Security Coworker");
  await expect(sub).toContainText("Temporary folder");
  await expect(sub).not.toContainText("ow-temp");
  await expect(page.getByTestId("save-as-project")).toBeVisible();

  // One-time pick: the setup row left with the first message.
  await expect(page.getByTestId("setup-row")).toHaveCount(0);

  // A NEW session never inherits the temporary dir — the folder chip starts fresh.
  await page.getByText("New session").first().click();
  await expect(page.getByTestId("folder-chip")).toContainText("Choose folder");
});

test("gated coworker: Choose a folder… binds the picked project and sends", async ({ page }) => {
  await page.goto("/");
  await newDraftAs(page, /Security Coworker/);

  await page.getByPlaceholder(/Ask the coworker/).fill("hello repo");
  await page.getByRole("button", { name: "Send" }).click();

  // Native pick is mocked server-side → /tmp/picked-folder.
  await page.getByTestId("send-folder-dialog").getByRole("button", { name: "Choose a folder…" }).click();
  await expect(page.getByText(/Echo: hello repo/)).toBeVisible();
  await expect(page.getByTestId("session-subtitle")).toContainText("picked-folder");
  await expect(page.getByTestId("save-as-project")).toHaveCount(0);
});

test("escape restores the draft instead of losing it", async ({ page }) => {
  await page.goto("/");
  await newDraftAs(page, /Security Coworker/);

  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("precious draft");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("send-folder-dialog")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("send-folder-dialog")).toHaveCount(0);
  await expect(box).toHaveValue("precious draft");
});

test("an explicit folder pick survives a coworker change; menu copy matches state", async ({
  page,
}) => {
  // Owner catch 2026-08-24 (v0.2.0 walkthrough): picking a folder and THEN picking the
  // coworker silently reset the folder. The user's own chip pick must survive; only
  // inherited folders (boot-resume, scratch) still clear on a coworker change.
  await page.goto("/");
  await page.getByText("New session").first().click();

  // No folder yet — the menu's browse action reads "Choose a folder…", not "another".
  await page.getByTestId("folder-chip").click();
  const browseBtn = page.locator(".setup-menu").getByRole("button", { name: /Choose a(nother)? folder…/ });
  await expect(browseBtn).toHaveText(/Choose a folder…/);
  await browseBtn.click(); // native pick is mocked server-side → /tmp/picked-folder
  await expect(page.getByTestId("folder-chip")).toContainText("picked-folder");

  // With a folder bound, the same action offers "another".
  await page.getByTestId("folder-chip").click();
  await expect(
    page.locator(".setup-menu").getByRole("button", { name: /Choose a(nother)? folder…/ }),
  ).toHaveText(/Choose another folder…/);
  await page.mouse.click(10, 10); // scrim click closes the menu

  // Re-target the draft to a folder-gated coworker — the pick survives…
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByRole("button", { name: /Security Coworker/ }).click();
  await expect(page.getByTestId("folder-chip")).toContainText("picked-folder");

  // …and the send goes straight through, no folder dialog.
  await page.getByPlaceholder(/Ask the coworker/).fill("scan here");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: scan here/)).toBeVisible();
  await expect(page.getByTestId("send-folder-dialog")).toHaveCount(0);
});

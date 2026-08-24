// OPE-85: a missing CLI becomes a visible decision, never a silently dropped check.
// The bug this guards (owner-hit 2026-08-13): with gitleaks absent, a security review
// quietly omitted its git-history secret scan — "we couldn't look" rendered as "clean".
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function ask(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("scan for secrets");
  await page.getByRole("button", { name: "Send" }).click();
}

test("request_tool surfaces a card naming the tool, the reason and the pinned version", async ({
  page,
}) => {
  await ask(page);
  const card = page.locator(".dirreq-card");
  await expect(card).toContainText("gitleaks");
  // The coworker's justification is labeled, not a bare floating quote.
  await expect(card).toContainText("Reason: “scan the git history for committed secrets”");
  // The fact strip is the product's voice: version, publisher, checksum — kept apart from
  // the coworker's quoted reason (mixing them is what made the card confusing, 2026-08-14).
  const facts = card.locator(".toolreq-facts");
  await expect(facts).toContainText("8.30.1");
  // Plain-language consent: who installs (OpenWorker), from where, and the self-install
  // alternative — no supply-chain jargon on the card (owner feedback 2026-08-15).
  await expect(facts).toContainText(
    "OpenWorker installs its own verified copy from github.com/gitleaks — or install it yourself and continue.",
  );
  // Declining must read as a normal choice that continues the run, not a failure.
  await expect(card.getByTestId("toolreq-skip")).toHaveText("Continue without it");
});

test("an event without install metadata fails CLOSED — Install disabled, skip offered", async ({
  page,
}) => {
  // Owner-hit 2026-08-14: the card offered "pinned build, checksum-verified" for a tool
  // with no pinned build; approval could only produce an error. Absence of metadata is NO.
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("request an unpinned tool");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.locator(".dirreq-card");
  await expect(card).toContainText("somescanner");
  await expect(card).toContainText(/no verified build/i);
  await expect(card.getByTestId("toolreq-install")).toBeDisabled();
  await expect(card.getByTestId("toolreq-skip")).toBeEnabled();
});

test("installing runs the check; skipping still reports coverage", async ({ page }) => {
  await ask(page);
  await page.getByTestId("toolreq-install").click();
  await expect(page.locator(".main-scroll")).toContainText("Installed gitleaks");

  await page.getByPlaceholder(/Ask the coworker/).fill("scan for secrets");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("toolreq-skip").click();
  // The whole point: the skipped check is disclosed, not invisible.
  await expect(page.locator(".main-scroll")).toContainText(/Coverage:/);
});

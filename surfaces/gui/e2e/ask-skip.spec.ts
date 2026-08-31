import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// OPE-153 — skipping a question. A question card used to have no exit: the only ways out were
// picking an option or typing an answer, so a user who couldn't answer had to invent one or
// abandon the session. Now every card carries Skip, and grouped cards carry both "Skip question"
// (this one, advance) and "Skip all" (resolve the rest). Seeded via a per-test inbox route
// override so the base fixtures' counts stay untouched.

// Mirrors SKIP in InboxItemCard.tsx / SKIP_SENTINEL in coworker/tools/ask.py.
const SKIP = "__ocw_skip__";

const BASE = {
  body: "",
  state: "pending",
  resolution: null as string | null,
  inbox: "default",
  created_at: "2026-08-31 08:00:00",
  resolved_at: null as string | null,
  session_title: "Investigate alerts",
  session_agent: "ops",
  session_workspace: "",
  session_exists: true,
};

const SINGLE_ITEM = {
  ...BASE,
  id: "inb-question-single",
  session_id: "ops-1",
  kind: "question",
  title: "Which environment should I deploy to?",
  header: "Environment",
  options: ["Staging", "Production"],
  allow_text: true,
  multi: false,
  questions: [],
};

// The card a user could previously get stuck on: exhaustive options, no free-text escape.
const NO_TEXT_ITEM = {
  ...SINGLE_ITEM,
  id: "inb-question-notext",
  allow_text: false,
};

const GROUPED_ITEM = {
  ...BASE,
  id: "inb-question-grouped",
  session_id: "ops-1",
  kind: "question",
  title: "Chart style?",
  header: "Chart style",
  options: ["Bar", "Line"],
  allow_text: false,
  multi: false,
  questions: [
    { question: "Chart style?", header: "Chart style", options: ["Bar", "Line"], allow_text: false, multi: false },
    { question: "Which distribution?", header: "Distribution", options: ["Stacked", "Grouped"], allow_text: false, multi: false },
    { question: "Which palette?", header: "Palette", options: ["Warm", "Cool"], allow_text: false, multi: false },
  ],
};

/** Replace the Inbox's seeded items for this test (resolve mutates the local copy). */
async function seedInbox(page: Page, items: Record<string, unknown>[]) {
  const inbox = items.map((i) => ({ ...i }));
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route(/\/v1\/inbox\/[^/]+\/resolve$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const id = decodeURIComponent(path.split("/").slice(-2)[0]);
    const it = inbox.find((x) => x.id === id);
    if (it) {
      it.state = "resolved";
      it.resolution = route.request().postDataJSON().resolution;
    }
    return route.fulfill(json({ ok: true }));
  });
  await page.route(/\/v1\/inbox(\?.*)?$/, (route) =>
    route.fulfill(json({ items: inbox.filter((i) => i.state === "pending") })),
  );
  return inbox;
}

async function openInbox(page: Page, expectTitle: string) {
  await page.goto("/");
  await page.getByTestId("inbox-chip").click();
  await expect(page.getByText(expectTitle)).toBeVisible();
}

test("a single question can be skipped, and resolves as skipped rather than blank", async ({
  page,
}) => {
  await seedInbox(page, [SINGLE_ITEM]);
  await openInbox(page, "Which environment should I deploy to?");

  // One control, labelled plainly — no stepper, so nothing to say "question" about.
  const skip = page.getByTestId("question-skip");
  await expect(skip.getByRole("button", { name: "Skip", exact: true })).toBeVisible();
  await expect(skip.getByRole("button", { name: "Skip all" })).toHaveCount(0);

  const resolved = page.waitForRequest((r) => r.url().includes("/resolve") && r.method() === "POST");
  await skip.getByRole("button", { name: "Skip", exact: true }).click();
  expect((await resolved).postDataJSON().resolution).toBe(SKIP);
  await expect(page.getByText("Nothing pending.")).toBeVisible();
});

test("Skip is offered even when the card has no free-text escape", async ({ page }) => {
  await seedInbox(page, [NO_TEXT_ITEM]);
  await openInbox(page, "Which environment should I deploy to?");

  // Exhaustive options and no "Or type your own answer…" — Skip is the ONLY way out.
  await expect(page.getByPlaceholder("Or type your own answer…")).toHaveCount(0);
  const resolved = page.waitForRequest((r) => r.url().includes("/resolve") && r.method() === "POST");
  await page.getByTestId("question-skip").getByRole("button", { name: "Skip", exact: true }).click();
  expect((await resolved).postDataJSON().resolution).toBe(SKIP);
});

test("one step of a grouped card can be skipped while the others are answered", async ({
  page,
}) => {
  await seedInbox(page, [GROUPED_ITEM]);
  await openInbox(page, "Chart style?");

  const stepper = page.getByTestId("question-stepper");
  const skip = page.getByTestId("question-skip");

  // Skipping step 1 advances exactly as answering would.
  await skip.getByRole("button", { name: "Skip question" }).click();
  await expect(stepper).toContainText("2 of 3");

  await page.getByRole("button", { name: "Stacked", exact: true }).click();
  await expect(stepper).toContainText("3 of 3");

  // Last step: nothing left to "skip all", so only the per-question control remains.
  await expect(skip.getByRole("button", { name: "Skip all" })).toHaveCount(0);

  const resolved = page.waitForRequest((r) => r.url().includes("/resolve") && r.method() === "POST");
  await page.getByRole("button", { name: "Warm", exact: true }).click();
  expect((await resolved).postDataJSON().resolution).toBe(
    JSON.stringify({ "Chart style": SKIP, Distribution: "Stacked", Palette: "Warm" }),
  );
});

test("Skip all resolves the rest of a grouped card but keeps answers already given", async ({
  page,
}) => {
  await seedInbox(page, [GROUPED_ITEM]);
  await openInbox(page, "Chart style?");

  await page.getByRole("button", { name: "Bar", exact: true }).click();
  await expect(page.getByTestId("question-stepper")).toContainText("2 of 3");

  const resolved = page.waitForRequest((r) => r.url().includes("/resolve") && r.method() === "POST");
  await page.getByTestId("question-skip").getByRole("button", { name: "Skip all" }).click();
  // Step 1's real answer survives; only the two the user never reached are skipped.
  expect((await resolved).postDataJSON().resolution).toBe(
    JSON.stringify({ "Chart style": "Bar", Distribution: SKIP, Palette: SKIP }),
  );
  await expect(page.getByText("Nothing pending.")).toBeVisible();
});

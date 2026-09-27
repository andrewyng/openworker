import { test, expect, seedMachines } from "./fixtures";

// Union v1.5: cloud machines join the two cross-machine aggregations —
// sidebar session groups and the Inbox. Motivated live: a cloud box's parked
// approval was invisible (a silent stall) until this.

const CLOUD_ROW = {
  id: "c1",
  name: "cloud-vm",
  fingerprint: "8e74c8c42ec58dfa",
  app_version: "0.2.0",
  created_at: 1_755_100_000,
  last_seen: 1_755_100_000,
  connected: true,
};

const CLOUD_SESSION = {
  session_id: "cloud-triage-1",
  title: "github mention triage",
  workspace: null,
  agent: "cowork",
  model: "anthropic:claude-sonnet-4-6",
  mode: "interactive",
  updated_at: "2026-09-01 20:00:00",
  messages: 5,
  pinned: false,
  archived: false,
};

async function seedCloud(page: import("@playwright/test").Page) {
  await page.route("**/v1/cloud/machines", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session: "ok",
        org: { id: "org:deeplearning-ai", name: "DeepLearning AI" },
        machines: [CLOUD_ROW],
      }),
    }),
  );
  await page.route("**/v1/cloud/machines/c1/sessions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ live: true, sessions: [CLOUD_SESSION] }),
    }),
  );
}

test("sidebar lists a cloud machine's sessions; Machine grouping shows its group", async ({
  page,
}) => {
  await seedMachines(page, []);
  await seedCloud(page);
  await page.route("**/v1/cloud/machines/c1/p/v1/inbox*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    }),
  );
  await page.goto("/");

  const row = page.getByText("github mention triage").first();
  await expect(row).toBeVisible();

  await page.getByRole("button", { name: "Group and filter conversations" }).click();
  await page.getByRole("menu").getByText("Machine", { exact: true }).click();
  // UX-048: group headers are the machine name, sentence case, no glyph.
  await expect(page.locator("div").filter({ hasText: /^cloud-vm$/ })).toBeVisible();
});

test("Inbox aggregates a cloud box's parked approval; resolve routes via the proxy", async ({
  page,
}) => {
  await seedMachines(page, []);
  await seedCloud(page);
  await page.route("**/v1/cloud/machines/c1/p/v1/inbox*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "cloud-appr-1",
            session_id: "cloud-triage-1",
            kind: "approval",
            title: "Run `send_message`?",
            body: "requires approval\ntarget: github:o/r#2",
            state: "pending",
            resolution: null,
            inbox: "default",
            created_at: "2026-09-01 20:00:00",
            resolved_at: null,
            session_title: "github mention triage",
            session_agent: "cowork",
            session_workspace: "",
            session_exists: true,
          },
        ],
      }),
    }),
  );
  let resolved = "";
  await page.route("**/v1/cloud/machines/c1/p/v1/inbox/cloud-appr-1/resolve", (route) => {
    resolved = route.request().postDataJSON()?.resolution ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Inbox" }).click();

  // The cloud approval lists beside local ones, wearing its machine tag —
  // the drill's invisible approval, now with a face.
  await expect(page.getByText("target: github:o/r#2")).toBeVisible();
  await expect(page.getByText("⌂ cloud-vm").first()).toBeVisible();

  await page
    .getByTestId("inbox-item-cloud-appr-1")
    .getByRole("button", { name: "Approve", exact: true })
    .click();
  await expect.poll(() => resolved).toBe("allow");
});

// Connectors-across-machines §6: team calls ride the machine of the session they
// belong to. A lead session on a cloud box opens its chat and journal through the
// same proxy its transcript uses — never the local sidecar.
test("team chat and journal of a cloud lead session route via the proxy", async ({ page }) => {
  await seedMachines(page, []);
  await page.route("**/v1/cloud/machines", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session: "ok",
        org: { id: "org:deeplearning-ai", name: "DeepLearning AI" },
        machines: [CLOUD_ROW],
      }),
    }),
  );
  const lead = {
    ...CLOUD_SESSION,
    session_id: "cloud-lead-1",
    title: "SWE team on the box",
    agent: "swe-lead",
    team: { role: "lead", team_id: "t-cloud", lead_session: "cloud-lead-1", chat_enabled: true },
  };
  // The Team panel renders for a lead WITH workers — one worker on the same box.
  const worker = {
    ...CLOUD_SESSION,
    session_id: "cloud-worker-1",
    title: "nia",
    agent: "swe-worker",
    team: { role: "worker", team_id: "t-cloud", lead_session: "cloud-lead-1", actor: "nia", status: "idle" },
  };
  await page.route("**/v1/cloud/machines/c1/sessions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ live: true, sessions: [lead, worker] }),
    }),
  );
  await page.route("**/v1/cloud/machines/c1/p/v1/inbox*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }),
  );
  await page.route("**/v1/cloud/machines/c1/p/v1/sessions/cloud-lead-1/board", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ space: "box-space", name: "Board", items: [] }),
    }),
  );
  const hits: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/v1/teams/")) hits.push(u);
  });
  await page.goto("/");
  await page.getByText("SWE team on the box").first().click();

  await expect(page.getByTestId("rail-toggle-team")).toBeVisible({ timeout: 12_000 });
  await page.getByTestId("rail-toggle-team").click();
  await page.getByTestId("team-chat-action").click();
  await expect(page.getByTestId("teamchat-view")).toBeVisible();

  await expect
    .poll(() => hits.filter((u) => u.includes("/v1/cloud/machines/c1/p/v1/teams/t-cloud/chat")).length)
    .toBeGreaterThan(0);
  await expect
    .poll(() => hits.filter((u) => u.includes("/v1/cloud/machines/c1/p/v1/teams/journal")).length)
    .toBeGreaterThan(0);
  // Nothing about this team asked the local sidecar.
  expect(hits.filter((u) => !u.includes("/p/v1/teams/"))).toEqual([]);
});

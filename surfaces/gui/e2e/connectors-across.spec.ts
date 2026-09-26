import { test, expect, seedMachines } from "./fixtures";

// UX-049 — connectors across machines: a machine's Connectors page shows what
// other machines hold (Enable / Move here, or the sign-in for a rotating
// provider), inbound rows link to the glance page, and the glance page under
// Settings ▸ App shows holders, the default machine, People, and who answers
// each channel. Cloud views are mocked at the sidecar's /v1/cloud routes.

const SEAL_PUB = "pbEWq+ooNkz0sfoIeaDCE/O+xvH+RD83HTD8rHTxSg4=";
const base = (id: string, name: string) => ({
  id,
  name,
  origin: "cloud",
  fingerprint: id,
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: 1_755_000_000,
  connected: true,
  seal_pubkey: SEAL_PUB,
  seal_fingerprint: id,
});
const CLOUD_VM = base("cloud:mA", "cloud-vm");
const SANDBOX = { ...base("cloud:mB", "sandbox-1"), provenance: "fly" };

const conn = (name: string, extra: Record<string, unknown>) => ({
  name,
  title: name === "outlook" ? "Outlook" : name[0].toUpperCase() + name.slice(1),
  icon: "•",
  blurb: `${name} blurb`,
  auth: "oauth",
  two_way: name === "slack",
  channels: name === "slack",
  available: true,
  fields: [],
  instructions: [],
  connected: false,
  account: null,
  enabled: true,
  brand_color: "#333",
  logo: name,
  allowed_users: [],
  tools: [],
  managed: true,
  ...extra,
});

const CLOUD_CONNECTIONS = [
  { connection_id: "c-slack", connector: "slack", provider: "slack", status: "connected", provider_account: "acme.slack.com", holders: [{ machine_id: "mB", delegated_at: "2026-09-03T10:00:00Z", events: true }, { machine_id: "mA", delegated_at: "2026-09-03T11:00:00Z", events: true }], default_machine_id: "", copyable: true, routing: {} },
  { connection_id: "c-notion", connector: "notion", provider: "notion", status: "connected", provider_account: "Rohit's Notion", holders: [{ machine_id: "mB", delegated_at: "2026-09-03T10:00:00Z", events: false }], default_machine_id: "", copyable: true, routing: {} },
  { connection_id: "c-outlook", connector: "outlook", provider: "microsoft", status: "connected", provider_account: "rohit@contoso.com", holders: [{ machine_id: "mB", delegated_at: "2026-09-03T10:00:00Z", events: false }], default_machine_id: "", copyable: false, routing: {} },
];

async function mockCloud(page: import("@playwright/test").Page) {
  const calls: { path: string; body: unknown }[] = [];
  await page.route(/\/v1\/cloud\/connections$/, (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connections: CLOUD_CONNECTIONS }) }));
  await page.route(/\/v1\/cloud\/connections\/[^/]+\/(default-machine|routing)$/, (r) => {
    calls.push({ path: new URL(r.request().url()).pathname, body: r.request().postDataJSON() });
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.route(/\/v1\/cloud\/subscriptions(\?.*)?$/, (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ subscriptions: [
      { source: "slack:T1/C1", connector: "slack", machine_id: "mA", session_id: "s-1", connection_id: "c-slack", team_id: "T1", title: "Release watcher", state: "active", created_at: "2026-09-03T12:00:00Z" },
      { source: "slack:T1/C2", connector: "slack", machine_id: "mB", session_id: "s-2", connection_id: "c-slack", team_id: "T1", title: "", state: "orphan", created_at: "2026-09-01T12:00:00Z" },
    ] }),
  }));
  await page.route(/\/v1\/cloud\/subscriptions\/remove$/, (r) => {
    calls.push({ path: "remove", body: r.request().postDataJSON() });
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, removed: true }) });
  });
  await page.route(/\/v1\/cloud\/people(\?.*)?$/, (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ people: [{ source: "slack:T1/U_BOB", connector: "slack", scope: "T1", member: "U_BOB", name: "Bob", added_at: "" }] }),
  }));
  return calls;
}

test("a machine's Connectors page: Enable for a grant held elsewhere, Configure on inbound rows, sign-in for a rotating provider", async ({ page }) => {
  await seedMachines(page, [CLOUD_VM, SANDBOX]);
  await mockCloud(page);
  await page.route(/\/v1\/cloud\/machines\/mA\/p\/v1\/connectors$/, (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ connectors: [
      conn("slack", { connected: true, account: "acme.slack.com", mode: "relay" }),
      conn("notion", {}),
      conn("outlook", {}),
      conn("gmail", {}),
    ] }),
  }));
  await page.goto("/#/settings/connectors?m=cloud:mA");

  // Connected here: Slack names its other holder and the default, links Configure.
  const slack = page.getByTestId("remote-connector-slack");
  await expect(slack.getByTestId("remote-holders-slack")).toContainText("also on sandbox-1");
  await expect(slack.getByTestId("remote-holders-slack")).toContainText("sandbox-1 answers by default");
  await expect(slack.getByTestId("remote-disconnect-slack")).toHaveText("Disconnect here");

  // Held on another machine: Notion offers Enable + Move here; Outlook (rotating) offers the sign-in.
  const notion = page.getByTestId("remote-held-notion");
  await expect(notion).toContainText("on sandbox-1");
  await expect(notion.getByTestId("remote-enable-notion")).toHaveAttribute("title", /already connected on another machine/);
  await expect(notion.getByTestId("remote-move-notion")).toHaveText("Move here");
  const outlook = page.getByTestId("remote-held-outlook");
  await expect(outlook).toContainText("signs in separately on each machine");
  await expect(outlook.getByTestId("remote-enable-outlook")).toHaveCount(0);
  // Held nowhere stays in Available with the sign-in.
  await expect(page.getByTestId("remote-available-gmail")).toBeVisible();

  // Configure → the glance page.
  await slack.getByTestId("remote-configure-slack").click();
  await expect(page.getByTestId("glance-slack")).toBeVisible();
});

test("Settings ▸ Slack: holders, default machine, People, subscribed channels; unsubscribe and default change go to the cloud", async ({ page }) => {
  await seedMachines(page, [CLOUD_VM, SANDBOX]);
  const calls = await mockCloud(page);
  await page.goto("/#/settings/appearance");
  // The tab appears because Slack is connected somewhere.
  await page.getByRole("button", { name: "Slack" }).click();
  const glance = page.getByTestId("glance-slack");
  await expect(glance.getByTestId("glance-holders")).toContainText("sandbox-1");
  await expect(glance.getByTestId("glance-holders")).toContainText("cloud-vm");
  // The T1 scope: the routing line (machine · coworker) and one People list.
  const scope = page.getByTestId("slack-workspace-T1");
  await expect(scope.getByTestId("glance-people-T1")).toContainText("Bob");
  const picker = scope.getByTestId("glance-machine-T1");
  // This Mac holds Slack too (the fixture's local relay), and held it first: preselected.
  await expect(glance.getByTestId("glance-holders")).toContainText("This Mac");
  await expect(picker).toHaveValue("desktop");
  await picker.selectOption("mA");
  await expect.poll(() => calls.find((c) => c.path.endsWith("/routing"))?.body).toEqual({ scope: "T1", machine_id: "mA" });
  await scope.getByTestId("glance-coworker-T1").selectOption("");
  // Subscribed channels across machines; the orphan offers Remove.
  await expect(page.getByTestId("glance-sub-slack:T1/C1")).toContainText("#C1");
  await expect(page.getByTestId("glance-sub-slack:T1/C1")).toContainText("cloud-vm");
  await expect(page.getByTestId("glance-sub-slack:T1/C2")).toContainText("session gone");
  await expect(page.getByTestId("glance-unsub-slack:T1/C2")).toHaveText("Remove");
  await page.getByTestId("glance-unsub-slack:T1/C1").click();
  await expect.poll(() => calls.find((c) => c.path === "remove")?.body).toEqual({ source: "slack:T1/C1" });
});

// UX-050 — the GitHub glance page IS its configurations (spec §10): the
// installation default is the first row; Add opens the dialog (repositories ×
// event × who × send to); Existing session uses the grouped picker; Delete and
// a broker conflict round-trip through the cloud proxy.
const GITHUB_CONN = { connection_id: "c-github", connector: "github", provider: "github", status: "connected", provider_account: "acme", holders: [{ machine_id: "mB", delegated_at: "2026-09-03T10:00:00Z", events: true }], default_machine_id: "", copyable: true, routing: {} };
const DEFAULT_ROW = {
  config_id: "cfg0", connector: "github", installation_id: "101", repos: ["acme"], event: "mention", name: "", who: ["nia"],
  target: { kind: "new", machine_id: "mB", persona: "security", models: [], base_dir: "", worktree: true, skills: [], instructions: "" },
  state: "active", created_at: "2026-09-03T10:00:00Z", created_by: "default",
};
const CFG_ROW = {
  config_id: "cfg1", connector: "github", installation_id: "101", repos: ["acme/site", "acme/api"], event: "pr_open", name: "", who: ["nia"],
  target: { kind: "new", machine_id: "mB", persona: "reviewer", models: ["anthropic:claude-sonnet-4-6"], base_dir: "~/work", worktree: true, skills: [], instructions: "" },
  state: "active", created_at: "2026-09-04T10:00:00Z",
};

async function mockConfigurations(page: import("@playwright/test").Page, opts: { conflict?: boolean } = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const rows = [DEFAULT_ROW, CFG_ROW];
  await page.route(/\/v1\/cloud\/connections$/, (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connections: [...CLOUD_CONNECTIONS, GITHUB_CONN] }) }));
  await page.route(/\/v1\/cloud\/configurations(\?.*)?$/, (r) => {
    const req = r.request();
    if (req.method() === "POST") {
      calls.push({ method: "POST", path: "configurations", body: req.postDataJSON() });
      if (opts.conflict) return r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { error: "held", held_by: { key: "github:acme/site@pr_open" } } }) });
      rows.push({ ...CFG_ROW, config_id: "cfg2", repos: (req.postDataJSON() as { repos: string[] }).repos });
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, configuration: rows[rows.length - 1] }) });
    }
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ configurations: rows }) });
  });
  await page.route(/\/v1\/cloud\/configurations\/[^/]+$/, (r) => {
    const req = r.request();
    if (req.method() === "DELETE") {
      calls.push({ method: "DELETE", path: new URL(req.url()).pathname, body: null });
      rows.splice(rows.findIndex((x) => req.url().endsWith(`/${x.config_id}`)), 1);
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, removed: 2 }) });
    }
    return r.continue();
  });
  await page.route(/\/v1\/cloud\/people(\?.*)?$/, (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ people: [] }) }));
  return calls;
}

test("Settings ▸ GitHub: the default row, a configuration row, Add with a new session, Delete", async ({ page }) => {
  await seedMachines(page, [CLOUD_VM, SANDBOX]);
  await mockCloud(page);
  const calls = await mockConfigurations(page);
  await page.goto("/#/settings/appearance");
  await page.getByRole("button", { name: "GitHub" }).click();
  const glance = page.getByTestId("glance-github");
  await expect(glance.getByTestId("glance-holders")).toContainText("sandbox-1");
  // The installation default is a row like any other (created at connect).
  const dflt = page.getByTestId("glance-cfg-cfg0");
  await expect(dflt).toContainText("All repositories");
  await expect(dflt).toContainText("default");
  await expect(dflt).toContainText("security");
  await expect(dflt.getByTestId("cfg-edit-cfg0")).toBeVisible();
  // No page-level routing line or People list for GitHub any more.
  await expect(page.getByTestId("glance-machine-101")).toHaveCount(0);
  await expect(page.getByTestId("glance-people-101")).toHaveCount(0);
  // A configuration row from the cloud.
  const row = page.getByTestId("glance-cfg-cfg1");
  await expect(row).toContainText("site, api");
  await expect(row).toContainText("PR opened");
  await expect(row).toContainText("reviewer");
  await expect(row).toContainText("you + 1");

  // Add: repositories × event × who × new session.
  await page.getByTestId("cfg-add").click();
  const dlg = page.getByTestId("cfg-dialog");
  await dlg.getByTestId("cfg-repo-input").fill("acme/site");
  await dlg.getByTestId("cfg-repo-input").press("Enter");
  await expect(dlg.getByTestId("cfg-repo-acme/site")).toBeVisible();
  await dlg.getByTestId("cfg-event").selectOption("pr_open");
  await dlg.getByTestId("cfg-who-input").fill("nia");
  await dlg.getByTestId("cfg-who-input").press("Enter");
  await dlg.getByTestId("cfg-machine").selectOption("desktop");
  await dlg.getByTestId("cfg-coworker").selectOption("security");
  // The coworker's own models list is offered; the Ollama one is marked not here.
  await expect(dlg.getByTestId("cfg-model-ollama:qwen3-coder:30b")).toContainText("not here");
  await dlg.getByTestId("cfg-base-dir").fill("~/work");
  await expect(dlg.getByTestId("cfg-worktree")).toHaveCount(0);
  await expect(dlg).toContainText("no checkout is created automatically");
  // Spec §11.5: Approval mode defaults to Auto-approve and approvals go to the Inbox;
  // both are the user's to change here.
  await expect(dlg.getByTestId("cfg-approval-mode")).toHaveValue("auto-approve");
  await expect(dlg.getByTestId("cfg-unattended")).toBeChecked();
  await dlg.getByTestId("cfg-approval-mode").selectOption("interactive");
  await dlg.getByTestId("cfg-unattended").uncheck();
  await dlg.getByTestId("cfg-save").click();
  await expect.poll(() => calls.find((c) => c.method === "POST")?.body).toMatchObject({
    repos: ["acme/site"], event: "pr_open", who: ["nia"],
    target: { kind: "new", machine_id: "desktop", persona: "security", base_dir: "~/work", models: ["anthropic:claude-opus-4-8"], approval_mode: "interactive", unattended: false },
  });
  await expect(page.getByTestId("cfg-dialog")).toHaveCount(0);
  await expect(page.getByTestId("glance-cfg-cfg2")).toBeVisible();

  // Delete goes to the cloud.
  await page.getByTestId("cfg-delete-cfg1").click();
  await expect.poll(() => calls.find((c) => c.method === "DELETE")?.path).toContain("/v1/cloud/configurations/cfg1");
});

test("Settings ▸ GitHub: Existing session uses the grouped picker; a broker conflict shows in the dialog", async ({ page }) => {
  await seedMachines(page, [CLOUD_VM, SANDBOX]);
  await mockCloud(page);
  const calls = await mockConfigurations(page, { conflict: true });
  await page.goto("/#/settings/appearance");
  await page.getByRole("button", { name: "GitHub" }).click();
  await page.getByTestId("cfg-add").click();
  const dlg = page.getByTestId("cfg-dialog");
  await dlg.getByTestId("cfg-repo-input").fill("acme/site");
  await dlg.getByTestId("cfg-repo-input").press("Enter");
  await dlg.getByTestId("cfg-event").selectOption("pr_merge");
  await dlg.getByTestId("cfg-target-existing").check();
  // This Mac's pinned session is listed under its machine group, pinned first.
  await dlg.getByTestId("cfg-session-search").fill("launch");
  await dlg.getByTestId("cfg-session-pinned-cowork-1").click();
  await dlg.getByTestId("cfg-save").click();
  await expect.poll(() => calls.find((c) => c.method === "POST")?.body).toMatchObject({
    event: "pr_merge", target: { kind: "existing", machine_id: "desktop", session_id: "pinned-cowork-1" },
  });
  await expect(dlg.getByTestId("cfg-error")).toContainText("already covers");
});

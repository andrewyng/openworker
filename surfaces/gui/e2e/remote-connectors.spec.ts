import { test, expect, seedMachines } from "./fixtures";

// Settings ▸ Connectors under a machine scope: the machine's own connector
// list via the proxy, sealed manual connect (fields encrypted in-tab to the
// machine's pinned key — the wire carries ciphertext only), and honest tags
// on connectors that need a browser sign-in.

const SEAL_PUB = "pbEWq+ooNkz0sfoIeaDCE/O+xvH+RD83HTD8rHTxSg4=";

const HETZNER = {
  id: "m1",
  name: "hetzner-box",
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: 1_755_000_000,
  connected: true,
  seal_pubkey: SEAL_PUB,
  seal_fingerprint: "8662e3d8968dca74",
};

const REMOTE_CONNECTORS = [
  {
    name: "linear",
    title: "Linear",
    icon: "◆",
    blurb: "Issues and projects",
    auth: "api_token",
    two_way: false,
    channels: false,
    available: true,
    fields: [
      {
        key: "api_key",
        label: "API key",
        secret: true,
        required: true,
        help: "",
        placeholder: "lin_api_…",
      },
    ],
    instructions: [],
    connected: false,
    account: null,
    enabled: true,
    brand_color: "#5E6AD2",
    logo: "linear",
    allowed_users: [],
    tools: [],
    managed: false,
  },
  {
    name: "gmail",
    title: "Gmail",
    icon: "✉",
    blurb: "Email",
    auth: "oauth",
    two_way: true,
    channels: false,
    available: true,
    fields: [],
    instructions: [],
    connected: false,
    account: null,
    enabled: true,
    brand_color: "#EA4335",
    logo: "gmail",
    allowed_users: [],
    tools: [],
    managed: true,
  },
  {
    name: "google_calendar",
    title: "Google Calendar",
    icon: "▤",
    blurb: "Events",
    auth: "oauth",
    two_way: false,
    channels: false,
    available: true,
    fields: [],
    instructions: [],
    connected: false,
    account: null,
    enabled: true,
    brand_color: "#4285F4",
    logo: "google_calendar",
    allowed_users: [],
    tools: [],
    managed: true,
    managed_paused: true,
  },
  {
    name: "slack",
    title: "Slack",
    icon: "▣",
    blurb: "Messages",
    auth: "api_token",
    two_way: true,
    channels: true,
    available: true,
    fields: [],
    instructions: [],
    connected: true,
    account: "quillvoice.slack.com",
    enabled: true,
    brand_color: "#611f69",
    logo: "slack",
    allowed_users: [],
    tools: [
      {
        name: "slack_send",
        label: "Send messages",
        kind: "write",
        description: "Post to channels",
        enabled: true,
        requires_approval: true,
      },
    ],
    managed: false,
  },
];

test("machine scope: the machine's own connectors, sealed connect, honest OAuth tag", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/machines\/m1\/p\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connectors: REMOTE_CONNECTORS }),
    }),
  );
  let sealedBody = "";
  await page.route("**/v1/machines/m1/connectors/linear/connect-sealed", (route) => {
    sealedBody = route.request().postData() ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, account: "rohit@quillvoice.dev" }),
    });
  });
  await page.goto("/#/settings/connectors?m=m1");

  // The machine's list: connected Slack with its account, available Linear.
  const slack = page.getByTestId("remote-connector-slack");
  await expect(slack).toContainText("quillvoice.slack.com");
  await expect(page.getByTestId("remote-available-linear")).toBeVisible();

  // Managed Gmail: connect-direct-to-machine — OAuth runs in this browser,
  // the grant lands on the machine (machines spec §Remote OAuth).
  const gmail = page.getByTestId("remote-available-gmail");
  await expect(gmail.getByTestId("remote-connect-browser-gmail")).toBeVisible();
  // A paused managed connector still gets the honest tag, not a dead button.
  const gcal = page.getByTestId("remote-available-google_calendar");
  await expect(gcal).toContainText("browser sign-in");
  await expect(gcal.getByRole("button", { name: /Connect/ })).toHaveCount(0);

  // Manual connect: the field seals in-tab; the wire never sees the token.
  await page.getByTestId("remote-connect-linear").click();
  await page.locator('input[type="password"]').fill("lin_api_SECRETVALUE");
  await page.getByTestId("remote-connect-submit-linear").click();
  await expect.poll(() => sealedBody).toContain("sealed_b64");
  expect(sealedBody).not.toContain("lin_api_SECRETVALUE");
});

test("This-Mac scope keeps the full Connectors page; account menu lands here", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors" }).click();
  // Settings shell, Connectors page selected, the familiar full list.
  await expect(page).toHaveURL(/#\/settings\/connectors$/);
  await expect(
    page.getByRole("heading", { name: "Connectors", exact: true }),
  ).toBeVisible();
});

test("grant handoff: Move from This Mac deploys sealed names, then This Mac forgets", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  // The machine doesn't have Notion; This Mac does (fixture's default local
  // list is overridden so notion shows connected locally).
  await page.route(/\/v1\/machines\/m1\/p\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connectors: [
          {
            ...REMOTE_CONNECTORS[1],
            name: "notion",
            title: "Notion",
            managed: true,
          },
        ],
      }),
    }),
  );
  await page.route(/^https?:\/\/[^/]+\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connectors: [
          { ...REMOTE_CONNECTORS[1], name: "notion", title: "Notion", connected: true },
        ],
      }),
    }),
  );
  await page.route("**/v1/connectors/notion/handoff-info", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        portable: true,
        profiles: ["notion:account:42", "notion:default"],
        needs_delegation: true,
      }),
    }),
  );
  let delegated = false;
  let delegateBody = "";
  await page.route("**/v1/connectors/notion/delegate", (route) => {
    delegated = true;
    delegateBody = route.request().postData() ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, delegated: ["notion:account:42"] }),
    });
  });
  let deployed = "";
  await page.route("**/v1/machines/m1/secrets", (route) => {
    deployed = route.request().postData() ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, deployed: ["notion:account:42", "notion:default"] }),
    });
  });
  let forgot = false;
  await page.route("**/v1/connectors/notion/forget-local", (route) => {
    forgot = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/#/settings/connectors?m=m1");

  await page.getByTestId("remote-move-notion").click();
  await expect.poll(() => delegated).toBe(true); // broker delegation precedes the move
  // The machine's seal key rides the delegation — the broker mints the
  // machine credential against it (managed events).
  expect(delegateBody).toContain(SEAL_PUB);
  await expect.poll(() => deployed).toContain("notion:account:42");
  await expect.poll(() => forgot).toBe(true); // local-only forget — never broker disconnect

});

test("grant handoff: a managed grant explains the refresh gate instead of moving", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/machines\/m1\/p\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connectors: [{ ...REMOTE_CONNECTORS[1], name: "gmail", title: "Gmail" }],
      }),
    }),
  );
  await page.route(/^https?:\/\/[^/]+\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connectors: [
          { ...REMOTE_CONNECTORS[1], name: "gmail", title: "Gmail", connected: true },
        ],
      }),
    }),
  );
  await page.route("**/v1/connectors/gmail/handoff-info", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, portable: false, reason: "refresh_binding", profiles: [] }),
    }),
  );
  await page.goto("/#/settings/connectors?m=m1");

  await page.getByTestId("remote-move-gmail").click();
  await expect(
    page.getByText(/renews its sign-in through OpenWorker Cloud/),
  ).toBeVisible();
});

test("connect-direct: the browser flow starts with the machine as target", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/machines\/m1\/p\/v1\/connectors$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connectors: REMOTE_CONNECTORS }),
    }),
  );
  let startBody = "";
  await page.route("**/v1/connectors/gmail/connect-managed", (route) => {
    startBody = route.request().postData() ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, authorize_url: "https://example.test/consent" }),
    });
  });
  await page.goto("/#/settings/connectors?m=m1");

  await page.getByTestId("remote-connect-browser-gmail").click();
  // The sidecar is told WHICH machine the grant is for — the callback ships
  // it there and stores nothing locally.
  await expect.poll(() => startBody).toContain('"machine_id":"m1"');
  expect(startBody).toContain('"machine_name":"hetzner-box"');
  await expect(page.getByTestId("remote-available-gmail")).toContainText(
    "land on ⌂ hetzner-box",
  );
});

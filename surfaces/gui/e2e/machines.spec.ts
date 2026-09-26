import { test, expect, seedMachines } from "./fixtures";

// Remote homes P1b (UX-045 frames C/D): Settings ▸ Machines — rows with
// presence, rename/remove round trips, and the enrollment card (arming, the
// join one-liner, and the joined state when a box appears mid-poll).

const HETZNER = {
  id: "m1",
  name: "hetzner-box",
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: 1_755_000_000,
  connected: true,
};
const MACPRO = {
  id: "m2",
  name: "old-macpro",
  fingerprint: "1234567890abcdef",
  app_version: "0.1.9",
  created_at: 1_754_000_000,
  last_seen: 1_754_100_000,
  connected: false,
};

async function openMachines(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Machines", exact: true }).click();
}

test("machine rows: This Mac + presence, offline machines keep their row", async ({ page }) => {
  await seedMachines(page, [HETZNER, MACPRO]);
  await openMachines(page);

  await expect(page.getByText("This Mac", { exact: true })).toBeVisible();
  const hetzner = page.getByTestId("machine-hetzner-box");
  await expect(hetzner.getByText("hetzner-box")).toBeVisible();
  await expect(hetzner.getByText(/online/)).toBeVisible();
  const macpro = page.getByTestId("machine-old-macpro");
  await expect(macpro.getByText(/last seen/)).toBeVisible();
  await expect(macpro.getByText(/v0\.1\.9/)).toBeVisible();
});

test("rename round-trips; removing a connected machine is refused", async ({ page }) => {
  await seedMachines(page, [HETZNER]);
  await openMachines(page);

  const row = page.getByTestId("machine-hetzner-box");
  await row.hover();
  await row.getByText("Rename").click();
  await page.locator("input:focus").fill("gpu-rig");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("machine-gpu-rig")).toBeVisible();

  page.on("dialog", (d) => d.accept());
  const renamed = page.getByTestId("machine-gpu-rig");
  await renamed.hover();
  await renamed.getByText("Remove").click();
  await expect(page.getByText(/must leave \(or go offline\)/)).toBeVisible();
  await expect(page.getByTestId("machine-gpu-rig")).toBeVisible();
});

test("enrollment: arming shows the one-liner, a joining box flips the card", async ({ page }) => {
  const seeded = await seedMachines(page, [MACPRO]);
  await openMachines(page);

  await page.getByText("Add a machine…").click();
  await expect(page.getByTestId("join-command")).toContainText(
    "openworker join http://127.0.0.1:9787/j/e2e-token",
  );
  await expect(page.getByText(/Waiting for the machine/)).toBeVisible();
  await expect(page.getByTestId("token-countdown")).toContainText("token expires in");

  // The reachability toggle only swaps hint text.
  await expect(page.getByText(/ssh -N -R/)).toBeVisible();
  await page.getByText("Tailscale / VPN").click();
  await expect(page.getByText(/tailnet address/)).toBeVisible();

  // Provision-with-defaults rides the join: on by default when the wallet
  // holds provider keys.
  await expect(page.getByTestId("provision-defaults")).toBeChecked();

  seeded.join({
    id: "m9",
    name: "my-box",
    fingerprint: "feedfacecafebeef",
    app_version: "0.2.0",
    created_at: Date.now() / 1000,
    last_seen: Date.now() / 1000,
    connected: true,
  });
  await expect(page.getByTestId("join-success")).toContainText("my-box joined");
  await expect(page.getByTestId("join-success")).toContainText("feedfacecafebeef");
  await expect(page.getByTestId("provisioned-note")).toContainText("Provisioned with 1 model key");
  await expect(page.getByText(/pick ⌂ my-box under/)).toBeVisible();
});

// -- P1c: runs-on chip, ⌂ badge, Machine grouping ------------------------------

const REMOTE_SESSION = {
  session_id: "remote-nightly-1",
  title: "Nightly triage watch",
  workspace: "/home/ow/scratch/nightly",
  agent: "cowork",
  model: "anthropic:claude-opus-4-8",
  mode: "interactive",
  updated_at: "2026-07-01 08:00:00",
  messages: 4,
  pinned: false,
  archived: false,
};

test("runs-on chip: appears with a machine enrolled, retargets the draft, hides the folder chip", async ({ page }) => {
  await seedMachines(page, [HETZNER]);
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).click();

  const chip = page.getByTestId("runson-chip");
  await expect(chip).toContainText("This Mac");
  await expect(page.getByTestId("folder-chip")).toBeVisible();

  await chip.click();
  await page.getByText("⌂ hetzner-box").click();
  await expect(chip).toContainText("hetzner-box");
  // A remote draft never carries a local folder.
  await expect(page.getByTestId("folder-chip")).toHaveCount(0);

  // And back: This Mac restores the folder chip.
  await chip.click();
  await page.getByRole("button", { name: "This Mac" }).click();
  await expect(chip).toContainText("This Mac");
  await expect(page.getByTestId("folder-chip")).toBeVisible();
});

test("offline machines are listed but not selectable in the runs-on menu", async ({ page }) => {
  await seedMachines(page, [HETZNER, MACPRO]);
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).click();
  await page.getByTestId("runson-chip").click();
  const offline = page.getByRole("button", { name: /old-macpro/ });
  await expect(offline).toBeDisabled();
});

test("remote sessions wear the ⌂ badge and group under their machine", async ({ page }) => {
  await seedMachines(page, [HETZNER], { m1: [REMOTE_SESSION] });
  await page.goto("/");

  // Flat list: the remote row is present with its quiet badge.
  const row = page.getByText("Nightly triage watch").first();
  await expect(row).toBeVisible();

  // Group by Machine (entry exists only because a machine is enrolled).
  await page.getByRole("button", { name: "Group and filter conversations" }).click();
  await page.getByRole("menu").getByText("Machine", { exact: true }).click();
  // UX-048: group headers are the machine name, sentence case, no glyph.
  await expect(page.locator("div").filter({ hasText: /^This Mac$/ })).toBeVisible();
  await expect(page.locator("div").filter({ hasText: /^hetzner-box$/ })).toBeVisible();
  await page.mouse.click(640, 400); // dismiss the popover (its backdrop covers the page)

  // Selecting the remote session adopts its machine — the proxied (blank) transcript
  // renders as a draft whose runs-on chip already points at the box.
  await page.getByText("Nightly triage watch").first().click();
  await expect(page.getByTestId("runson-chip")).toContainText("hetzner-box");
});

test("an offline machine's sessions stay listed — greyed, with an explainer on open", async ({ page }) => {
  await seedMachines(page, [MACPRO], { m2: [{ ...REMOTE_SESSION, session_id: "snap-1", title: "Snapshot watch" }] });
  await page.goto("/");

  // The snapshot row is present despite the machine being offline (it sorts
  // oldest, so expand the recents peek first)…
  await page.getByText(/Show (\d+ )?more/).first().click();
  const row = page.getByText("Snapshot watch").first();
  await expect(row).toBeVisible();
  // …and quiet: the row wrapper carries the offline treatment.
  await expect(
    page.locator("div.opacity-45", { hasText: "Snapshot watch" }).first(),
  ).toBeVisible();

  // No cached transcript → opening explains where the conversation lives.
  await row.click();
  await expect(page.getByText(/old-macpro is offline — the conversation lives there/)).toBeVisible();
});

test("a viewed transcript stays readable offline — read-only with a banner", async ({ page }) => {
  await seedMachines(
    page,
    [MACPRO],
    { m2: [{ ...REMOTE_SESSION, session_id: "snap-2", title: "Synced watch" }] },
    {
      "m2/snap-2": [
        { role: "user", content: "check the deploy" },
        { role: "assistant", content: "Deploy looks healthy; two warnings noted." },
      ],
    },
  );
  await page.goto("/");
  await page.getByText(/Show (\d+ )?more/).first().click();
  await page.getByText("Synced watch").first().click();

  // The last synced conversation renders…
  await expect(page.getByText("check the deploy").first()).toBeVisible();
  await expect(page.getByText(/Deploy looks healthy/).first()).toBeVisible();
  // …with the read-only banner…
  await expect(page.getByText(/showing the last synced view \(read-only\)/)).toBeVisible();
  // …and the composer can't send (the bridged socket never connects).
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
});

// -- Keys wallet W3: chips + machine-scoped model picker -----------------------

test("a remote draft offers the MACHINE's models, not this Mac's", async ({ page }) => {
  await seedMachines(page, [HETZNER]);
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).click();
  await page.getByTestId("runson-chip").click();
  await page.getByText("⌂ hetzner-box").click();
  // The composer's model chip flips to the box's default (proxied settings).
  await expect(page.getByRole("button", { name: /qwen3-coder:30b/ })).toBeVisible();
});

test("wallet chips: deploy and revoke a provider key on a machine", async ({ page }) => {
  await seedMachines(page, [HETZNER, MACPRO]);
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await page.getByTestId("set-provider-openai").click(); // configured + keyed in fixtures

  const chips = page.getByTestId("wallet-chips");
  await expect(chips.getByText("This Mac")).toBeVisible();
  const offline = page.getByTestId("wallet-chip-old-macpro");
  await expect(offline).toBeDisabled();

  const chip = page.getByTestId("wallet-chip-hetzner-box");
  await chip.click();
  await expect(chip).toContainText("✓");
  await chip.click();
  await expect(chip).not.toContainText("✓");
});


// -- Cloud-mode skeleton: same bundle, server-driven flag ----------------------

async function forceCloudMode(page: import("@playwright/test").Page) {
  // LATER routes win: flip the capabilities answer to cloud for this spec.
  await page.route("**/v1/capabilities", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ mode: "cloud" }),
    }),
  );
}

test("cloud mode: no This-Mac row, machine-first runs-on, no local folder chip", async ({ page }) => {
  await seedMachines(page, [HETZNER]);
  await forceCloudMode(page);
  await page.goto("/");

  await page.getByRole("button", { name: "New session" }).click();
  // The runs-on chip prompts for a machine (no local home to default to)…
  const chip = page.getByTestId("runson-chip");
  await expect(chip).toContainText("Choose machine");
  // …and with no machine there is no socket to reconnect — no strip on a
  // fresh draft (the controller has no engine; owner-hit 2026-09-02).
  await page.waitForTimeout(1500);
  await expect(page.getByTestId("session-reconnecting")).toHaveCount(0);
  await chip.click();
  // …and the menu offers only machines — This Mac is not a choice.
  await expect(page.getByText("⌂ hetzner-box")).toBeVisible();
  await expect(page.getByRole("button", { name: "This Mac" })).toHaveCount(0);
  await page.getByText("⌂ hetzner-box").click();
  // No local folder chip in cloud mode.
  await expect(page.getByTestId("folder-chip")).toHaveCount(0);

  // Settings ▸ Machines: the controller isn't a home — no This-Mac row.
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Machines", exact: true }).click();
  await expect(page.getByTestId("machine-hetzner-box")).toBeVisible();
  await expect(page.getByText("This Mac")).toHaveCount(0);
});

// -- Inbox aggregation across machines -----------------------------------------

test("the Inbox aggregates a connected machine's parked items; resolve routes back", async ({ page }) => {
  await seedMachines(
    page,
    [HETZNER],
    { m1: [REMOTE_SESSION] },
    {},
    {
      m1: [
        {
          id: "remote-appr-1",
          session_id: "remote-nightly-1",
          kind: "approval",
          title: "Approve: run_shell",
          body: "systemctl restart nginx",
          state: "pending",
          resolution: null,
          inbox: "default",
          created_at: "2026-07-02 03:00:00",
          resolved_at: null,
          session_title: "Nightly triage watch",
          session_agent: "cowork",
          session_workspace: "",
          session_exists: true,
        },
      ],
    },
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Inbox" }).click();

  // The remote approval lists beside local ones, wearing its machine tag.
  await expect(page.getByText("systemctl restart nginx")).toBeVisible();
  await expect(page.getByText("⌂ hetzner-box").first()).toBeVisible();

  // Resolving it posts to THAT machine (the stateful mock flips it) and the
  // item leaves the pending list.
  await page
    .getByTestId("inbox-item-remote-appr-1")
    .getByRole("button", { name: "Approve", exact: true })
    .click();
  await expect(page.getByText("systemctl restart nginx")).toHaveCount(0);
});

// -- Remote folder choice (send-time dialog) -----------------------------------

test("remote send-folder dialog: typed path on the machine, no local picker", async ({ page }) => {
  await seedMachines(page, [HETZNER]);
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).click();

  // A folder-gated coworker on a machine.
  await page.getByTestId("coworker-chip").click();
  await page.getByText("Security Coworker").click();
  await page.getByTestId("runson-chip").click();
  await page.getByText("⌂ hetzner-box").click();

  await page.getByPlaceholder(/Ask the coworker/).fill("scan the repo");
  await page.keyboard.press("Enter");

  const dialog = page.getByTestId("send-folder-dialog");
  await expect(dialog).toBeVisible();
  // Remote variant: machine-scoped copy, typed path, NO native picker.
  await expect(dialog.getByText(/on ⌂ hetzner-box/).first()).toBeVisible();
  await expect(dialog.getByText("Choose a folder…")).toHaveCount(0);
  await expect(dialog.getByTestId("start-temp-folder")).toContainText("on hetzner-box");

  // A bad path is rejected BY THE MACHINE with a real error…
  await page.getByTestId("remote-path-input").fill("not-a-path");
  await page.getByTestId("remote-path-open").click();
  await expect(dialog.getByText(/no such folder on this machine/)).toBeVisible();

  // …a good one validates and the stashed message flies.
  await page.getByTestId("remote-path-input").fill("/home/ow/project");
  await page.getByTestId("remote-path-open").click();
  await expect(page.getByTestId("send-folder-dialog")).toHaveCount(0);
  await expect(page.getByText("scan the repo").first()).toBeVisible();
});

test("typed-code approval: bad code dead-ends, good code shows fingerprint, approve decides", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  const decisions: string[] = [];
  await page.route(/\/v1\/remote\/device\/[^/]+\/(approve|deny)$/, (route) => {
    decisions.push(new URL(route.request().url()).pathname.split("/").pop()!);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
  await page.route(/\/v1\/remote\/device\/[^/]+$/, (route) => {
    const code = new URL(route.request().url()).pathname.split("/").pop();
    if (code === "WXYZ-2345")
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user_code: "WXYZ-2345",
          name: "auth-vm",
          fingerprint: "deadbeef00112233",
          expires_at: Date.now() / 1000 + 600,
        }),
      });
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: "unknown or expired code" }),
    });
  });
  await openMachines(page);
  await page.getByTestId("approve-machine-button").click();

  // Unknown code: a dead end, never a grant.
  await page.getByTestId("approval-code-input").fill("AAAA-0000");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("approval-unknown")).toBeVisible();

  // Lowercase input is normalized; the claim (name + fingerprint) shows first.
  await page.getByTestId("approval-code-input").fill("wxyz-2345");
  await page.keyboard.press("Enter");
  await expect(page.getByText("auth-vm")).toBeVisible();
  await expect(page.getByTestId("approval-fingerprint")).toContainText("deadbeef00112233");

  await page.getByTestId("approve-button").click();
  await expect(page.getByTestId("approval-result")).toContainText("Approved");
  expect(decisions).toEqual(["approve"]);
});

test("approval deep link: #/approve/CODE lands on the card with the claim shown", async ({
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
  // No clicks: the deep link opens Settings ▸ Machines with the lookup done.
  await expect(page.getByTestId("approval-fingerprint")).toContainText("0123456789abcdef", {
    timeout: 10000,
  });
  await expect(page.getByText("deep-vm")).toBeVisible();
});

test("settings shell (UX-046): scoped rail, machine picker, machine-scoped Models & Keys", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/machines\/m1\/p\/v1\/settings$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        model: "ollama:qwen3-coder:30b",
        models: ["ollama:qwen3-coder:30b", "anthropic:claude-sonnet-4-6"],
        model_labels: {},
        model_context_windows: {},
        model_ready: true,
      }),
    }),
  );
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();

  // Grouped rail with the picker on the MACHINE header, defaulting to This Mac.
  await expect(page.getByText("Inventory", { exact: true })).toBeVisible();
  const picker = page.getByTestId("machine-scope-picker");
  await expect(picker).toHaveValue("");

  // Models & Keys on This Mac = the local providers page.
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await expect(page.getByText("Keys are stored only on this computer")).toBeVisible();

  // Pick the machine: the panel flips to the machine's own report + ledger.
  await picker.selectOption("m1");
  await expect(page.getByTestId("machine-models-panel")).toBeVisible();
  await expect(page.getByTestId("active-ollama")).toContainText("qwen3-coder:30b");
  await expect(page.getByTestId("active-anthropic")).toContainText("claude-sonnet-4-6");
  await expect(page.getByText(/Send a key to this machine/i)).toBeVisible();

  // Skills under the machine scope: the list comes THROUGH THE PROXY — a
  // remote-only skill proves which backend answered.
  await page.route(/\/v1\/machines\/m1\/p\/v1\/skills$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        skills: [
          {
            name: "remote-only-skill",
            description: "Lives on the box",
            instructions: "x",
            scope: "global",
            source: "local",
            enabled: true,
            path: "/home/ow/.skills/remote-only-skill",
          },
        ],
      }),
    }),
  );
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(page.getByText("remote-only-skill")).toBeVisible();
  await expect(page.getByText(/On ⌂ hetzner-box — reusable instructions/)).toBeVisible();

  // Coworkers under the machine scope: same proxy proof, and the detail page
  // opens scoped (no local-export affordance for a remote coworker).
  await page.route(/\/v1\/machines\/m1\/p\/v1\/personas$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        internal: false,
        personas: [
          {
            id: "remote-cw",
            name: "Remote Coworker",
            icon: "wrench",
            tagline: "Only on the box",
            requires_folder: false,
            builtin: false,
            tools: ["files"],
            enabled: true,
            surfaced: true,
            default: false,
            ships: true,
            group: "general",
          },
        ],
      }),
    }),
  );
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
  await expect(page.getByText("Remote Coworker")).toBeVisible();

  // Context optimization reads THE MACHINE's settings (the same proxied
  // settings route registered above answers).
  await page.getByRole("button", { name: "Context optimization" }).click();
  await expect(page.getByText("On ⌂ hetzner-box — how its sessions spend tokens.")).toBeVisible();
  await expect(page.getByTestId("token-savings-card")).toBeVisible();

  // Memory is VIEW-ONLY on a machine: entries render, no edit/delete, and the
  // CTA starts a conversation ON the machine — the edit surface.
  await page.route(/\/v1\/machines\/m1\/p\/v1\/memory$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ memory: [{ id: 7, content: "Remote fact: deploys happen on Fridays" }] }),
    }),
  );
  await page.route(/\/v1\/machines\/m1\/p\/v1\/memory\/settings$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true, user_rules: "Remote rules" }),
    }),
  );
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await expect(page.getByTestId("memory-remote-cta")).toBeVisible();
  await expect(page.getByText("Remote fact: deploys happen on Fridays")).toBeVisible();
  await expect(page.getByTestId("memory-edit-btn-7")).toHaveCount(0);
  await expect(page.getByTestId("memory-delete-all")).toHaveCount(0);
  await page.getByTestId("memory-ask-worker").click();
  await expect(page.getByPlaceholder(/Ask the coworker/)).toHaveValue(/Update your memory/);
  await expect(page.getByTestId("runson-chip")).toContainText("hetzner-box");
});

// -- managed sandboxes (spec §Fly sandboxes, 2026-09-02) -----------------------

test("cloud mode: New sandbox creates, the row walks provisioning → joining → online, at-cap disables", async ({
  page,
}) => {
  await seedMachines(page, []);
  await forceCloudMode(page);
  const state: { sandboxes: any[]; cap: number; used: number } = { sandboxes: [], cap: 1, used: 0 };
  const machines: any[] = [];
  await page.route("**/v1/sandboxes", async (route) => {
    if (route.request().method() === "POST") {
      state.sandboxes = [
        { id: "sb-1", name: "sandbox-1", owner: "erin@c.com", mine: true, state: "provisioning", phase: "provisioning", error: "", machine_id: "", connected: false, created_at: 1, updated_at: 1 },
      ];
      state.used = 1;
      return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ sandbox: state.sandboxes[0], cap: 1, used: 1 }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state) });
  });
  await page.route("**/v1/machines", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ machines, armed: false }) }),
  );
  await openMachines(page);

  const button = page.getByTestId("new-sandbox-button");
  await expect(button).toHaveText(/New sandbox \(0 of 1\)/);
  await button.click();
  await expect(page.getByTestId("sandbox-phase-sandbox-1")).toContainText("creating the machine");
  await expect(button).toHaveText(/\(1 of 1\)/);
  await expect(button).toBeDisabled();

  // Fly machine started, box booting.
  state.sandboxes[0] = { ...state.sandboxes[0], state: "running", phase: "joining" };
  await expect(page.getByTestId("sandbox-phase-sandbox-1")).toContainText("starting up", { timeout: 10000 });

  // The box joined: the pending row becomes a machine row with the tag.
  state.sandboxes[0] = { ...state.sandboxes[0], phase: "online", machine_id: "m9", connected: true };
  machines.push({ id: "m9", name: "sandbox-1", fingerprint: "abcdefabcdef0000", app_version: "0.2.0", created_at: 2, last_seen: 2, connected: true, provenance: "fly", provenance_ref: "sb-1" });
  await expect(page.getByTestId("machine-sandbox-1")).toBeVisible({ timeout: 10000 });
  await expect(page.getByTestId("sandbox-tag-sandbox-1")).toBeVisible();
  await expect(page.getByTestId("sandbox-phase-sandbox-1")).toHaveCount(0);
  // The Settings rail's machine picker learns about it without a reload.
  await expect(page.getByRole("combobox")).toContainText("sandbox-1", { timeout: 10000 });
});

test("a keyless machine shows no active model — its default is not reported as working", async ({
  page,
}) => {
  await seedMachines(page, [HETZNER]);
  await page.route(/\/v1\/machines\/m1\/p\/v1\/settings$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        model: "gpt-5.6-sol",
        models: ["gpt-5.6-sol"], // the box keeps its default selectable…
        model_labels: {},
        model_context_windows: {},
        model_ready: false, // …but nothing can run it yet
      }),
    }),
  );
  await openMachines(page);
  await page.getByTestId("machine-scope-picker").selectOption("m1");
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await expect(page.getByTestId("machine-models-panel")).toBeVisible();
  await expect(page.getByText("No working models yet — send a key below.")).toBeVisible();
  await expect(page.getByTestId("active-openai")).toHaveCount(0);
});

// -- cloud-dashboard connect-direct (spec, 2026-09-02) --------------------------

test("cloud mode: Connect in browser on a machine's connector seals the broker's result to the machine", async ({
  page,
}) => {
  const SEAL_PUB = Buffer.from(new Uint8Array(32).fill(1)).toString("base64");
  await seedMachines(page, [{ ...HETZNER, seal_pubkey: SEAL_PUB, seal_fingerprint: "0101010101010101" }]);
  await page.route("**/v1/capabilities", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ mode: "cloud", machines: true, wallet: false, cloud: { base: "https://broker.test" } }),
    }),
  );
  const notion = {
    name: "notion", title: "Notion", icon: "notion", blurb: "", auth: "oauth", two_way: false, channels: false,
    available: true, fields: [], instructions: [], connected: false, account: null, enabled: false,
    brand_color: "#000", logo: "", allowed_users: [], tools: [], managed: true, managed_profile: false,
  };
  await page.route(/\/v1\/machines\/m1\/p\/v1\/connectors$/, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connectors: [notion] }) }),
  );
  const starts: any[] = [];
  await page.route("https://broker.test/v1/oauth/notion/start", async (route) => {
    starts.push({ body: route.request().postDataJSON(), auth: route.request().headers()["authorization"] });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authorize_url: "https://notion.test/authorize?state=x" }) });
  });
  const relays: any[] = [];
  await page.route(/\/v1\/machines\/m1\/connectors\/notion\/managed-grant-sealed$/, async (route) => {
    relays.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, account: "Acme" }) });
  });
  // The popup: a stub that records the URL it was pointed at. The hosted dashboard holds
  // a session token (the broker call is authorised with it); the test supplies its own
  // rather than leaning on a dev token file that only exists on a developer's machine.
  await page.addInitScript(() => {
    (globalThis as any).__COWORKER_API_TOKEN__ = "e2e-session-token";
    (window as any).__popup = { location: { href: "" }, close() {} };
    window.open = () => (window as any).__popup as any;
  });

  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await page.getByTestId("machine-scope-picker").selectOption("m1");
  const button = page.getByTestId("remote-connect-browser-notion");
  await expect(button).toBeVisible();
  await button.click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0].body).toMatchObject({ connector: "notion", mode: "browser", machine_id: "m1", seal_pubkey: SEAL_PUB });
  expect(starts[0].body.redirect).toBe(new URL(page.url()).origin);
  await expect.poll(() => page.evaluate(() => (window as any).__popup.location.href)).toBe("https://notion.test/authorize?state=x");
  await expect(button).toContainText("Waiting for browser");

  // A message from the WRONG origin is ignored…
  await page.evaluate(() => {
    window.dispatchEvent(new MessageEvent("message", { origin: "https://evil.test", data: { type: "openworker-managed-grant", connector: "notion", access_token: "x" } }));
  });
  await page.waitForTimeout(300);
  expect(relays.length).toBe(0);
  // …the broker's is sealed to the machine and relayed as ciphertext only.
  await page.evaluate(() => {
    window.dispatchEvent(new MessageEvent("message", {
      origin: "https://broker.test",
      data: { type: "openworker-managed-grant", provider: "notion", connector: "notion", connection_id: "conn_1", access_token: "SECRET-TOKEN", account: "Acme", account_id: "ws_1", machine_credential: "mc_1", broker_user_id: "usr_1", machine_id: "m1" },
    }));
  });
  await expect.poll(() => relays.length).toBe(1);
  expect(typeof relays[0].sealed_b64).toBe("string");
  expect(JSON.stringify(relays[0])).not.toContain("SECRET-TOKEN");
  await expect(button).toContainText("Connect in browser");
});

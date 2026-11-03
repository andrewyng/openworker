import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

// UX-057: on a Mac, OpenWorker sets llama.cpp up and runs it — get llama.cpp, pick a
// model, start it — and "Connect to it instead" keeps the plain form.

const GB = 1024 ** 3;

const provider = (managed: { phase: string; model: string | null }) => ({
  name: "llamacpp", title: "llama.cpp", kind: "local", needs_key: false, configured: false, values: {},
  suggested_models: [], recommended_model: null, alive: managed.phase === "running", setup_here: true, managed,
  fields: [
    { key: "base_url", label: "Server address", secret: false, required: false, help: "", placeholder: "http://localhost:8080" },
    { key: "api_key", label: "API key (only if the server was started with one)", secret: true, required: false, help: "", placeholder: "" },
  ],
});

function base(over: Record<string, unknown> = {}) {
  return {
    supported: true, brew: "/opt/homebrew/bin/brew", installed: null, install_command: "brew install llama.cpp",
    cache_dir: "~/.cache/huggingface/hub", cache_free_bytes: 56 * GB,
    suggested: [
      { id: "nemotron-3.5-lightning", name: "Nemotron 3.5 Lightning", vendor: "NVIDIA", quant: "Q8 + MTP", features: ["thinking", "tools"], size: 36e9, fit: "runs_well", downloaded: false,
        license: "OpenMDW-1.1", license_url: "https://openmdw.ai/license/1-1/" },
      { id: "gpt-oss-20b", name: "gpt-oss 20B", vendor: "OpenAI", quant: "MXFP4", features: ["reasoning", "tools"], size: 12e9, fit: "runs_well", downloaded: false,
        license: "Apache-2.0", license_url: "https://huggingface.co/openai/gpt-oss-20b/blob/main/LICENSE" },
    ],
    ollama: [{ name: "qwen3-coder:30b", path: "/Users/me/.ollama/models/blobs/sha256-1194", size: 18e9, fit: "runs_well" }],
    ollama_pull: [{ name: "gpt-oss:20b", size: 12e9, fit: "runs_well", command: "ollama pull gpt-oss:20b" }],
    phase: "idle", lines: [], progress: null, model: null, error: null, check: null, note: null, retry_in: null, address: null,
    autostart: false, last_choice: null,
    ...over,
  };
}

const RUNNING = {
  phase: "running",
  model: { name: "qwen3-coder:30b", detail: "from Ollama", alias: "qwen3-coder:30b", download: false, context: 131072, file: "~/.ollama/models/blobs/sha256-1194" },
  check: { answers: true, tools: true },
  address: "127.0.0.1:8080",
};

async function openPage(page: Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();
}

test("llama.cpp: install, pick a model Ollama has, start it, and it runs", async ({ page }) => {
  let state = base();
  const posts: { path: string; body: unknown }[] = [];
  await page.route("**/v1/providers", (route) =>
    route.fulfill({ json: [provider({ phase: state.phase, model: (state.model as { name: string } | null)?.name ?? null })] }),
  );
  await page.route("**/v1/providers/llamacpp/models", (route) => route.fulfill({ json: { provider: "llamacpp", alive: true, models: [] } }));
  await page.route("**/v1/llamacpp/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/v1/llamacpp/", "");
    if (route.request().method() === "GET") {
      // Each poll moves the work along, as the server's thread would.
      const now = state;
      if (state.phase === "installing") state = base({ installed: { path: "/opt/homebrew/bin/llama-server", version: "0.6.0" } });
      if (state.phase === "loading") state = base({ installed: { path: "/opt/homebrew/bin/llama-server", version: "0.6.0" }, ...RUNNING });
      return route.fulfill({ json: now });
    }
    posts.push({ path, body: route.request().postDataJSON() });
    if (path === "install") state = base({ phase: "installing", lines: ["==> Fetching downloads for: llama.cpp", "==> Pouring llama.cpp--0.6.0.arm64_tahoe.bottle.tar.gz"] });
    if (path === "start") state = base({ installed: { path: "/opt/homebrew/bin/llama-server", version: "0.6.0" }, phase: "loading", model: { ...RUNNING.model } });
    if (path === "autostart") state = { ...state, autostart: (route.request().postDataJSON() as { on: boolean }).on };
    return route.fulfill({ json: { ok: true, ...state } });
  });

  await openPage(page);
  await expect(page.getByTestId("set-provider-llamacpp")).toContainText("Not set up · Set up on this Mac");
  await page.getByTestId("set-provider-llamacpp").click();
  const main = page.locator("main");
  await expect(main).toContainText("Runs open models on this Mac");
  await expect(page.getByTestId("llamacpp-step-get")).toHaveAttribute("data-state", "now");
  await expect(page.getByTestId("llamacpp-step-get")).toContainText("brew install llama.cpp");
  // The guided card replaces the address form until the user asks for it.
  await expect(page.getByTestId("set-field-api_key")).toHaveCount(0);
  await expect(page.getByTestId("set-endpoint-link")).toHaveCount(0);

  await page.getByTestId("llamacpp-install").click();
  await expect(page.getByTestId("llamacpp-install-log")).toContainText("Pouring llama.cpp");
  await expect(page.getByTestId("llamacpp-step-get")).toHaveAttribute("data-state", "done");
  await expect(main).toContainText("Installed · 0.6.0");

  // Pick: suggested models, what Ollama has, the pull command, and where models go.
  const pick = page.getByTestId("llamacpp-step-pick");
  await expect(pick).toHaveAttribute("data-state", "now");
  await expect(pick).toContainText("Nemotron 3.5 Lightning");
  await expect(page.getByTestId("llamacpp-pull")).toContainText("ollama pull gpt-oss:20b");
  await expect(page.getByTestId("llamacpp-cache")).toContainText("~/.cache/huggingface/hub");
  // Each model that downloads names its licence, linked, before Download.
  await expect(page.getByTestId("llamacpp-license-nemotron-3.5-lightning")).toContainText("Licence: OpenMDW-1.1");
  await expect(page.getByTestId("llamacpp-license-gpt-oss-20b")).toContainText("Licence: Apache-2.0");
  await expect(page.getByTestId("llamacpp-start")).toHaveText("Download and start");
  await page.getByTestId("llamacpp-model-o:qwen3-coder:30b").click();
  await expect(page.getByTestId("llamacpp-start")).toHaveText("Start");
  await page.getByTestId("llamacpp-start").click();

  const running = page.getByTestId("llamacpp-running");
  await expect(running).toContainText("qwen3-coder:30b");
  await expect(running).toContainText("128K");
  await expect(running).toContainText("127.0.0.1:8080");
  await expect(page.getByTestId("llamacpp-no-tools")).toHaveCount(0);
  await expect(main).toContainText("✓ Running · 1 model");
  // "Start with OpenWorker" is off until the user turns it on.
  const auto = page.getByTestId("llamacpp-autostart");
  await expect(auto).toHaveAttribute("aria-checked", "false");
  await auto.click();
  await expect(auto).toHaveAttribute("aria-checked", "true");

  expect(posts.map((p) => p.path)).toEqual(["install", "start", "autostart"]);
  expect(posts[1].body).toEqual({ choice: { kind: "ollama", name: "qwen3-coder:30b" } });
  expect(posts[2].body).toEqual({ on: true });

  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-llamacpp")).toContainText("✓ Running · qwen3-coder:30b");
});

test("llama.cpp: a model that cannot call tools runs with a warning", async ({ page }) => {
  const state = base({ installed: { path: "/opt/homebrew/bin/llama-server", version: "0.6.0" }, ...RUNNING, check: { answers: true, tools: false } });
  await page.route("**/v1/providers", (route) => route.fulfill({ json: [provider({ phase: "running", model: "qwen3-coder:30b" })] }));
  await page.route("**/v1/providers/llamacpp/models", (route) => route.fulfill({ json: { provider: "llamacpp", alive: true, models: [] } }));
  await page.route("**/v1/llamacpp/**", (route) => route.fulfill({ json: state }));
  await openPage(page);
  await page.getByTestId("set-provider-llamacpp").click();
  await expect(page.getByTestId("llamacpp-no-tools")).toContainText("did not call a tool");
});

test("llama.cpp: Connect to it instead shows the address form, and back", async ({ page }) => {
  await page.route("**/v1/providers", (route) => route.fulfill({ json: [provider({ phase: "idle", model: null })] }));
  await page.route("**/v1/providers/llamacpp/models", (route) => route.fulfill({ json: { provider: "llamacpp", alive: false, models: [] } }));
  await page.route("**/v1/llamacpp/**", (route) => route.fulfill({ json: base({ brew: null }) }));
  await openPage(page);
  await page.getByTestId("set-provider-llamacpp").click();
  // No Homebrew: it says so instead of offering Install.
  await expect(page.getByTestId("llamacpp-no-brew")).toBeVisible();
  await expect(page.getByTestId("llamacpp-install")).toHaveCount(0);
  await page.getByTestId("llamacpp-connect-instead").click();
  // Today's form: the key field, the address behind "Custom endpoint", the help line.
  await expect(page.getByTestId("set-field-api_key")).toBeVisible();
  await expect(page.getByTestId("set-endpoint-link")).toBeVisible();
  await expect(page.locator("main")).toContainText("llama-server");
  await page.getByTestId("llamacpp-setup-here").click();
  await expect(page.getByTestId("llamacpp-setup")).toBeVisible();
});

const INSTALLED = { installed: { path: "/opt/homebrew/bin/llama-server", version: "0.6.0" } };
const DOWNLOADING = {
  ...INSTALLED,
  phase: "downloading",
  model: { name: "Nemotron 3.5 Lightning", detail: "Q8 + MTP", alias: "nemotron-3.5-lightning", download: true },
  progress: { done: 12.4e9, total: 35.8e9, seconds_left: 840 },
};

test("llama.cpp: a download waits out a rate limit, verifies the file, and says when it could not", async ({ page }) => {
  // Each poll moves the download along: rate-limited, then verifying with the API gone.
  const states = [
    base({ ...DOWNLOADING, retry_in: 42 }),
    base({ ...DOWNLOADING, retry_in: 42 }),
    base({ ...DOWNLOADING, progress: { done: 35.8e9, total: 35.8e9, seconds_left: 0, verifying: true }, note: "unverified" }),
  ];
  let poll = 0;
  await page.route("**/v1/providers", (route) => route.fulfill({ json: [provider({ phase: "downloading", model: "Nemotron 3.5 Lightning" })] }));
  await page.route("**/v1/providers/llamacpp/models", (route) => route.fulfill({ json: { provider: "llamacpp", alive: false, models: [] } }));
  await page.route("**/v1/llamacpp/**", (route) => route.fulfill({ json: states[Math.min(poll++, states.length - 1)] }));
  await openPage(page);
  await expect(page.getByTestId("set-provider-llamacpp")).toContainText("Starting…");
  await page.getByTestId("set-provider-llamacpp").click();
  await expect(page.getByTestId("llamacpp-step-start")).toHaveAttribute("data-state", "now");
  const progress = page.getByTestId("llamacpp-progress");
  await expect(progress).toHaveText("Hugging Face is rate-limiting downloads, retrying in 42s");
  await expect(progress).toHaveText("Verifying the download…");
  await expect(page.getByTestId("llamacpp-note")).toContainText("checksum was not checked");
  await expect(page.getByTestId("llamacpp-cancel")).toBeVisible();
});

// The screenshots in docs/assets (light and dark): the three-step card before anything is
// installed, the model picker, and the running card.
for (const theme of ["light", "dark"] as const) {
  test(`llama.cpp: screenshots (${theme})`, async ({ page }) => {
    let state = base();
    await page.addInitScript((value) => localStorage.setItem("openwork-theme", value), theme);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.route("**/v1/providers", (route) =>
      route.fulfill({ json: [provider({ phase: state.phase, model: (state.model as { name: string } | null)?.name ?? null })] }),
    );
    await page.route("**/v1/providers/llamacpp/models", (route) =>
      route.fulfill({
        json: {
          provider: "llamacpp", alive: state.phase === "running",
          models: state.phase === "running"
            ? [{ model: "llamacpp:qwen3-coder:30b", name: "qwen3-coder:30b", size_bytes: 18e9, tools: true, thinking: false, vision: null, remote: false,
                parameter_size: null, quantization: null, context_max: 262144, context: 131072, context_from: "server", fit: "runs_well", recommendation: null }]
            : [],
        },
      }),
    );
    await page.route("**/v1/llamacpp/**", (route) => route.fulfill({ json: state }));
    // "Your system", as a 96 GB Mac reports it.
    await page.route("**/v1/system", (route) =>
      route.fulfill({
        json: { processor: "Apple M4 Max", graphics: "Apple M4 Max (40-core GPU)", kind: "apple_silicon", memory_bytes: 96 * GB, gpu_memory_bytes: null,
          storage_free_bytes: 420 * GB, storage_total_bytes: 926 * GB, model_memory_bytes: 72 * GB, runs_well_up_to_bytes: 64 * GB },
      }),
    );
    const suffix = theme === "dark" ? "-dark" : "";

    await openPage(page);
    await page.getByTestId("set-provider-llamacpp").click();
    await expect(page.getByTestId("llamacpp-step-get")).toHaveAttribute("data-state", "now");
    await page.screenshot({ path: `test-results/models-llamacpp-setup${suffix}.png` });

    state = base(INSTALLED);
    await page.getByTestId("set-back").click();
    await page.getByTestId("set-provider-llamacpp").click();
    await expect(page.getByTestId("llamacpp-step-pick")).toHaveAttribute("data-state", "now");
    await expect(page.getByTestId("llamacpp-license-nemotron-3.5-lightning")).toBeVisible();
    await page.screenshot({ path: `test-results/models-llamacpp-pick${suffix}.png` });

    state = base({ ...INSTALLED, ...RUNNING });
    await page.getByTestId("set-back").click();
    await page.getByTestId("set-provider-llamacpp").click();
    await expect(page.getByTestId("llamacpp-running")).toContainText("qwen3-coder:30b");
    await page.screenshot({ path: `test-results/models-llamacpp-running${suffix}.png` });
  });
}

import { expect } from "@playwright/test";
import { test } from "./fixtures";

// Each keyless local server explains itself; llama.cpp and vLLM never say "Ollama".
const server = (name: string, title: string) => ({
  name, title, kind: "local", needs_key: false, configured: false, values: {}, suggested_models: [], recommended_model: null,
  fields: [{ key: "api_key", label: "API key (only if the server was started with one)", secret: true, required: false, help: "", placeholder: "" }],
});

for (const [name, title, says, link] of [
  ["llamacpp", "llama.cpp", "llama-server", "Install llama.cpp"],
  ["vllm", "vLLM", "NVIDIA GPU", "Install vLLM"],
] as const) {
  test(`${title}: its own help line, not Ollama's`, async ({ page }) => {
    await page.route("**/v1/providers", (route) => route.fulfill({ json: [server(name, title)] }));
    await page.goto("/");
    await page.getByTestId("account-row").click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Models & Keys" }).click();
    await page.getByTestId(`set-provider-${name}`).click();
    const form = page.locator("main");
    await expect(form).toContainText(says);
    await expect(form.getByRole("button", { name: `${link} ↗` })).toBeVisible();
    await expect(form).not.toContainText("Ollama");
  });
}

// A models request that fails must say so, with Retry, never leave the page blank
// (owner-hit 2026-10-06: the table vanished and an unticked model could not be re-ticked).
test("llama.cpp: a failed model list says so, and Retry brings the table back", async ({ page }) => {
  let fail = true;
  await page.route("**/v1/providers", (route) => route.fulfill({ json: [server("llamacpp", "llama.cpp")] }));
  await page.route("**/v1/providers/llamacpp/models", (route) =>
    fail
      ? route.abort()
      : route.fulfill({
          json: {
            provider: "llamacpp", alive: true,
            models: [{ model: "llamacpp:nemotron-3.5-lightning", name: "nemotron-3.5-lightning", size_bytes: 36e9, tools: true, thinking: true,
              vision: null, remote: false, parameter_size: null, quantization: null, context_max: 1048576, context: 65536,
              context_from: "server", fit: "runs_well", recommendation: "NVIDIA Nemotron 3.5 Lightning" }],
          },
        }),
  );
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await page.getByTestId("set-provider-llamacpp").click();
  await expect(page.getByTestId("local-models-status")).toContainText("Couldn't reach the server");
  fail = false;
  await page.getByTestId("local-models-retry").click();
  // The model is back, unticked, and ticking it puts it back in the picker.
  const box = page.getByTestId("local-model-nemotron-3.5-lightning").getByRole("checkbox");
  await expect(box).not.toBeChecked();
  const added = page.waitForRequest((r) => r.url().endsWith("/v1/settings/models/add") && r.method() === "POST");
  await box.click();
  expect((await added).postDataJSON()).toEqual({ model: "llamacpp:nemotron-3.5-lightning" });
});

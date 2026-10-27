// Screenshots for docs/assets and PR bodies, light and dark, from the hermetic mock.
// Opt-in: `DOCS_SHOTS=1 npx playwright test e2e/docs-screenshots.spec.ts` writes the PNGs;
// a normal run skips this file.
import path from "node:path";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const OUT = process.env.DOCS_SHOTS_DIR || path.resolve(process.cwd(), "../../docs/assets");
test.skip(!process.env.DOCS_SHOTS, "set DOCS_SHOTS=1 to refresh the docs screenshots");

const localServer = (name: string, title: string, placeholder: string) => ({
  name, title, kind: "local", needs_key: false, configured: false, values: {}, suggested_models: [], recommended_model: null,
  fields: [
    { key: "base_url", label: "Server address", secret: false, required: false, help: "", placeholder },
    { key: "api_key", label: "API key (only if the server was started with one)", secret: true, required: false, help: "", placeholder: "" },
  ],
});

/** Every image decoded and the fonts in: a card's SVG mark can otherwise be captured
 * before it paints (the vLLM mark came out blank once). */
async function painted(page) {
  await page.evaluate(async () => {
    await Promise.all(
      Array.from(document.images).map((img) =>
        img.complete ? img.decode().catch(() => {}) : new Promise<void>((done) => { img.onload = img.onerror = () => done(); }),
      ),
    );
    await (document as any).fonts?.ready;
  });
}

async function openModels(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await expect(page.getByTestId("provider-groups")).toBeVisible();
}

for (const scheme of ["light", "dark"] as const) {
  const suffix = scheme === "dark" ? "-dark" : "";

  test(`custom provider: add box, saved card, form (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width: 1280, height: 900 });
    await openModels(page);
    await page.getByTestId("set-provider-custom-add").click();
    await page.getByTestId("custom-provider-title").fill("Acme Gateway");
    await page.getByTestId("custom-provider-endpoint").fill("https://llm.acme.example/v1");
    await page.getByTestId("custom-provider-key").fill("acme-test");
    await painted(page);
    await page.screenshot({ path: path.join(OUT, `models-custom-provider-form${suffix}.png`) });
    await page.getByTestId("custom-provider-add").click();
    await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://llm.acme.example/v1");
    await page.getByTestId("set-back").click();
    await expect(page.getByTestId("set-provider-acme-gateway")).toBeVisible();
    await painted(page);
    await page.screenshot({ path: path.join(OUT, `models-custom-provider${suffix}.png`) });
  });

  test(`local servers group (${scheme})`, async ({ page }) => {
    await page.route("**/v1/providers", (route) =>
      route.fulfill({
        json: [
          // The cloud rows the default fixture carries, so the picker card below names
          // the vendors the same way as in the other scene.
          { name: "openai", title: "OpenAI", needs_key: true, configured: true, values: {}, suggested_models: ["gpt-5.5"], recommended_model: null, key_set_at: "2026-06-12", last_used_at: null, fields: [{ key: "api_key", label: "OpenAI API key", secret: true, required: true, help: "", placeholder: "sk-…" }] },
          { name: "anthropic", title: "Claude (Anthropic)", needs_key: true, configured: true, values: {}, suggested_models: ["claude-opus-4-8"], recommended_model: null, key_set_at: null, last_used_at: null, fields: [{ key: "api_key", label: "API key", secret: true, required: true, help: "", placeholder: "sk-…" }] },
          { name: "ollama", title: "Ollama", kind: "local", needs_key: false, configured: true, values: {}, suggested_models: [], recommended_model: null, alive: true, fields: [] },
          localServer("llamacpp", "llama.cpp", "http://localhost:8080"),
          localServer("vllm", "vLLM", "http://localhost:8000"),
          localServer("lmstudio", "LM Studio", "http://localhost:1234"),
          localServer("jan", "Jan", "http://127.0.0.1:1337"),
          localServer("docker", "Docker Model Runner", "http://localhost:12434/engines"),
          localServer("mlx", "mlx-lm", "http://127.0.0.1:8080"),
        ],
      }),
    );
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width: 1280, height: 900 });
    await openModels(page);
    await expect(page.getByTestId("set-provider-mlx")).toBeVisible();
    // Seven local servers: open the fold so every row is in the picture.
    const more = page.getByTestId("provider-more-local");
    if (await more.isVisible()) await more.click();
    await expect(page.getByTestId("set-provider-vllm")).toBeVisible();
    await painted(page);
    await page.screenshot({ path: path.join(OUT, `models-local-servers${suffix}.png`) });
  });
}

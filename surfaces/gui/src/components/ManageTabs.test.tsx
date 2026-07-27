import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelsTab } from "./ManageTabs";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates a custom provider and opens its manual-model configuration", async () => {
  let created = false;
  const calls: Array<{ url: string; method: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.includes("/v1/providers/custom") && method === "POST") {
        created = true;
        return { json: async () => ({ ok: true, provider: "openrouter" }) } as Response;
      }
      if (url.includes("/v1/providers")) {
        const providers: any[] = [
          {
            name: "openai",
            title: "OpenAI",
            needs_key: true,
            fields: [{ key: "api_key", label: "OpenAI API key", secret: true, required: true }],
            configured: false,
            values: {},
            suggested_models: [],
            recommended_model: "gpt-5.6-sol",
          },
        ];
        if (created) {
          providers.push({
            name: "openrouter",
            title: "OpenRouter",
            needs_key: true,
            fields: [
              { key: "api_key", label: "OpenRouter API key", secret: true, required: true },
              { key: "base_url", label: "Endpoint", secret: false, required: true, placeholder: "https://…/v1" },
            ],
            configured: true,
            values: { base_url: "https://openrouter.ai/api/v1" },
            suggested_models: [],
            recommended_model: null,
            is_custom: true,
          });
        }
        return { json: async () => providers } as Response;
      }
      if (url.includes("/v1/settings")) {
        return {
          json: async () => ({
            model: "gpt-5.6-sol",
            models: ["gpt-5.6-sol"],
            model_labels: {},
            source: null,
          }),
        } as Response;
      }
      return { json: async () => ({ ok: true }) } as Response;
    }),
  );

  render(<ModelsTab />);
  fireEvent.click(await screen.findByTestId("set-provider-custom-add"));
  fireEvent.change(screen.getByLabelText("Provider name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("Route prefix"), { target: { value: "openrouter" } });
  fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "https://openrouter.ai/api/v1" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "or-test" } });
  fireEvent.click(screen.getByRole("button", { name: "Add provider" }));

  await waitFor(() => {
    expect(calls.find((c) => c.url.includes("/v1/providers/custom")))?.toMatchObject({
      method: "POST",
      body: {
        name: "openrouter",
        title: "OpenRouter",
        fields: { base_url: "https://openrouter.ai/api/v1", api_key: "or-test" },
      },
    });
  });
  expect((await screen.findByTestId("set-field-base_url") as HTMLInputElement).value).toBe(
    "https://openrouter.ai/api/v1",
  );
});

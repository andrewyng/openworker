// The "Add an OpenAI-compatible provider" box (PR #224 design, owner follow-up): the route
// prefix follows the name until edited, saving creates a card named by the user and opens
// it, and "Fetch from server" offers the ids the server lists.
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelsTab, routePrefixFor } from "./ManageTabs";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("derives the route prefix from the name", () => {
  expect(routePrefixFor("Acme Gateway")).toBe("acme-gateway");
  expect(routePrefixFor("  My LLM proxy #2 ")).toBe("my-llm-proxy-2");
  expect(routePrefixFor("42 Labs")).toBe("labs");
  expect(routePrefixFor("x".repeat(40))).toHaveLength(32);
});

function stubApi() {
  let created = false;
  const calls: Array<{ url: string; method: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url.includes("/v1/providers/custom") && method === "POST") {
        created = true;
        return { json: async () => ({ ok: true, provider: "acme-gateway" }) } as Response;
      }
      if (url.includes("/v1/providers/acme-gateway/models")) {
        return {
          ok: true,
          json: async () => ({
            provider: "acme-gateway",
            alive: true,
            models: [
              { model: "acme-gateway:acme-large", name: "acme-large" },
              { model: "acme-gateway:acme-small", name: "acme-small" },
            ],
          }),
        } as Response;
      }
      if (url.includes("/v1/providers")) {
        const providers: any[] = [
          {
            name: "openai", title: "OpenAI", needs_key: true, configured: false, values: {}, suggested_models: [],
            recommended_model: "gpt-5.6-sol",
            fields: [{ key: "api_key", label: "OpenAI API key", secret: true, required: true }],
          },
        ];
        if (created) {
          providers.push({
            name: "acme-gateway", title: "Acme Gateway", needs_key: true, is_custom: true, configured: true,
            values: { base_url: "https://llm.acme.example/v1" }, suggested_models: [], recommended_model: null,
            fields: [
              { key: "api_key", label: "Acme Gateway API key", secret: true, required: true },
              { key: "base_url", label: "Endpoint", secret: false, required: true, placeholder: "https://…/v1" },
            ],
          });
        }
        return { json: async () => providers } as Response;
      }
      if (url.includes("/v1/settings")) {
        return { json: async () => ({ ok: true, model: "gpt-5.6-sol", models: ["gpt-5.6-sol"], model_labels: {}, source: null }) } as Response;
      }
      return { json: async () => ({ ok: true }) } as Response;
    }),
  );
  return calls;
}

it("creates a named provider from the add box and opens its card", async () => {
  const calls = stubApi();
  render(<ModelsTab />);
  fireEvent.click(await screen.findByTestId("set-provider-custom-add"));
  fireEvent.change(screen.getByTestId("custom-provider-title"), { target: { value: "Acme Gateway" } });
  // The prefix follows the name until the user edits it.
  expect((screen.getByTestId("custom-provider-route") as HTMLInputElement).value).toBe("acme-gateway");
  fireEvent.change(screen.getByTestId("custom-provider-endpoint"), { target: { value: "https://llm.acme.example/v1" } });
  fireEvent.change(screen.getByTestId("custom-provider-key"), { target: { value: "acme-test" } });
  fireEvent.click(screen.getByTestId("custom-provider-add"));

  await waitFor(() => {
    expect(calls.find((c) => c.url.includes("/v1/providers/custom"))).toMatchObject({
      method: "POST",
      body: { name: "acme-gateway", title: "Acme Gateway", fields: { base_url: "https://llm.acme.example/v1", api_key: "acme-test" } },
    });
  });
  // The new card opens: its endpoint is shown (required, so never behind the disclosure).
  expect(((await screen.findByTestId("set-field-base_url")) as HTMLInputElement).value).toBe("https://llm.acme.example/v1");
  expect(screen.getByTestId("set-remove-key").textContent).toContain("Remove provider");

  // Fetch from server lists the ids as rows to tick.
  fireEvent.click(screen.getByTestId("custom-fetch-models"));
  await waitFor(() => expect(screen.getByTestId("custom-fetch-note").textContent).toContain("2 models listed"));
  expect(screen.getByTitle("acme-gateway:acme-large")).toBeTruthy();
  expect(screen.getByTitle("acme-gateway:acme-small")).toBeTruthy();
});

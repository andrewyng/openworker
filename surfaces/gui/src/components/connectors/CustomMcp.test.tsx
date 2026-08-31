import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import zh from "../../locales/zh.json";
import { CustomMcpGroup } from "./CustomMcp";

type Call = { url: string; method: string; body: unknown };

function stubMcpApi() {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const json = method === "GET" && url.endsWith("/v1/mcp") ? { servers: [] } : { ok: true };
      return { ok: true, json: async () => json } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CustomMcpGroup presets", () => {
  it("adds Parallel Search through the existing no-auth MCP connection path", async () => {
    const calls = stubMcpApi();
    const onChanged = vi.fn();
    render(<CustomMcpGroup servers={[]} onOpen={vi.fn()} onChanged={onChanged} />);

    expect(await screen.findByText("Granola")).toBeTruthy();
    const preset = screen.getByTestId("mcp-preset-parallel-search");
    expect(within(preset).getByText("Parallel Search")).toBeTruthy();
    expect(within(preset).getByText(/no account or API key required/i)).toBeTruthy();

    fireEvent.click(within(preset).getByRole("button", { name: "Connect" }));

    await waitFor(() => {
      const addIndex = calls.findIndex(
        (call) => call.method === "POST" && call.url.endsWith("/v1/mcp"),
      );
      const connectIndex = calls.findIndex(
        (call) =>
          call.method === "POST" && call.url.endsWith("/v1/mcp/parallel-search/connect"),
      );
      expect(addIndex).toBeGreaterThan(-1);
      expect(connectIndex).toBeGreaterThan(addIndex);
      expect(calls[addIndex].body).toEqual({
        name: "parallel-search",
        config: { type: "http", url: "https://search.parallel.ai/mcp" },
      });
      expect(onChanged).toHaveBeenCalledOnce();
    });
  });

  it("renders the Parallel Search description in Chinese", async () => {
    const i18n = createInstance();
    await i18n.init({
      resources: { zh: { translation: zh } },
      lng: "zh",
      fallbackLng: false,
    });
    render(
      <I18nextProvider i18n={i18n}>
        <CustomMcpGroup servers={[]} onOpen={vi.fn()} onChanged={vi.fn()} />
      </I18nextProvider>,
    );

    const preset = screen.getByTestId("mcp-preset-parallel-search");
    expect(within(preset).getByText("实时网页搜索与 URL 内容获取。无需账户或 API 密钥。")).toBeTruthy();
  });
});

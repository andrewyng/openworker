// Settings ▸ Customization (UX-058): the Web search card lists the providers as radio rows
// from GET /v1/web-search; a keyed provider without a key cannot be chosen; the right-hand
// control reads Add key / Update key / In use; "Test and save" sends the key; the fetch
// option sits under the active provider only while it offers it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const row = (name: string, display: string, key_mode: string, extra: Record<string, unknown> = {}) => ({
  name,
  display_name: display,
  key_mode,
  key_url: key_mode === "none" ? "" : `https://${name}.example`,
  has_key: false,
  key_source: null,
  key_env: "",
  supports_fetch: ["exa", "firecrawl", "tavily"].includes(name),
  ...extra,
});

const base = {
  provider: "duckduckgo",
  fetch_via_provider: false,
  providers: [
    row("duckduckgo", "DuckDuckGo", "none"),
    row("you", "You.com", "optional"),
    row("tavily", "Tavily", "required", { has_key: true, key_source: "store" }),
    row("brave", "Brave Search", "required", { has_key: true, key_source: "env", key_env: "BRAVE_API_KEY" }),
    row("serper", "Serper", "required"),
    row("exa", "Exa", "required"),
    row("firecrawl", "Firecrawl", "required"),
  ],
};
let snapshot: any = structuredClone(base);

const reply = (patch: any = {}) => ({ ok: true, ...snapshot, ...patch });
const getWebSearch = vi.fn(async () => snapshot);
const setWebSearchProvider = vi.fn(async (name: string) => {
  snapshot = { ...snapshot, provider: name };
  return reply();
});
const setWebSearchKey = vi.fn(async (name: string, key: string) => {
  if (key.startsWith("bad")) return { ok: false, error: "Client error '401 Unauthorized'" };
  snapshot = {
    ...snapshot,
    providers: snapshot.providers.map((p: any) => (p.name === name ? { ...p, has_key: true, key_source: "store", key_env: "" } : p)),
  };
  return reply();
});
const clearWebSearchKey = vi.fn(async (name: string) => {
  snapshot = {
    ...snapshot,
    provider: snapshot.provider === name ? "duckduckgo" : snapshot.provider,
    providers: snapshot.providers.map((p: any) => (p.name === name ? { ...p, has_key: false, key_source: null } : p)),
  };
  return reply();
});
const setFetchViaProvider = vi.fn(async (enabled: boolean) => {
  snapshot = { ...snapshot, fetch_via_provider: enabled };
  return reply();
});
const openExternal = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getWebSearch: () => getWebSearch(),
    setWebSearchProvider: (name: string) => setWebSearchProvider(name),
    setWebSearchKey: (name: string, key: string) => setWebSearchKey(name, key),
    clearWebSearchKey: (name: string) => clearWebSearchKey(name),
    setFetchViaProvider: (enabled: boolean) => setFetchViaProvider(enabled),
  };
});
vi.mock("../tauri", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../tauri")>();
  return { ...actual, openExternal: (url: string) => openExternal(url) };
});

import { CustomizationTab } from "./CustomizationTab";

const radio = (name: string) => screen.getByTestId(`websearch-radio-${name}`) as HTMLInputElement;
const fetchBox = () => screen.getByTestId("websearch-fetch-toggle") as HTMLInputElement;

describe("Settings ▸ Customization ▸ Web search", () => {
  beforeEach(() => {
    snapshot = structuredClone(base);
    for (const fn of [getWebSearch, setWebSearchProvider, setWebSearchKey, clearWebSearchKey, setFetchViaProvider, openExternal]) fn.mockClear();
  });
  afterEach(cleanup);

  it("lists the providers in server order with their status lines and controls", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    expect(screen.getByText("Customization")).toBeTruthy();
    expect(screen.getByText("Pick the services OpenWorker uses behind its tools. Each choice applies to new sessions on this machine.")).toBeTruthy();
    const names = within(screen.getByTestId("websearch-providers")).getAllByRole("radio").map((r) => r.getAttribute("data-testid"));
    expect(names).toEqual(base.providers.map((p) => `websearch-radio-${p.name}`));

    // Status lines only where they say something.
    expect(screen.getByTestId("websearch-status-duckduckgo").textContent).toBe("No key. Rate-limited, fine for occasional searches.");
    expect(screen.getByTestId("websearch-status-you").textContent).toBe("Works without a key, with a daily limit. A key lifts it.");
    expect(screen.getByTestId("websearch-status-tavily").textContent).toBe("Key saved");
    expect(screen.getByTestId("websearch-status-brave").textContent).toBe("Key from environment BRAVE_API_KEY");
    expect(screen.queryByTestId("websearch-status-exa")).toBeNull();

    // The active row wears the pill and, being keyless, no key button.
    expect(screen.getByTestId("websearch-in-use-duckduckgo").textContent).toBe("In use");
    expect(screen.queryByTestId("websearch-key-button-duckduckgo")).toBeNull();
    // Add key without a key, Update key with one (stored or from the environment).
    expect(screen.getByTestId("websearch-key-button-exa").textContent).toBe("Add key");
    expect(screen.getByTestId("websearch-key-button-you").textContent).toBe("Add key");
    expect(screen.getByTestId("websearch-key-button-tavily").textContent).toBe("Update key");
    expect(screen.getByTestId("websearch-key-button-brave").textContent).toBe("Update key");
    expect(screen.getByText("A service with no key can't be chosen until one is added. The agent's approval card names the service in use.")).toBeTruthy();
  });

  it("a keyed provider without a key cannot be chosen; keyless and keyed-with-key can", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    expect(radio("exa").disabled).toBe(true);
    expect(radio("serper").disabled).toBe(true);
    expect(radio("you").disabled).toBe(false);
    expect(radio("tavily").disabled).toBe(false);
    expect(radio("brave").disabled).toBe(false);
    expect(radio("duckduckgo").checked).toBe(true);

    fireEvent.click(radio("tavily"));
    await waitFor(() => expect(setWebSearchProvider).toHaveBeenCalledWith("tavily"));
    await waitFor(() => expect(radio("tavily").checked).toBe(true));
    expect(screen.getByTestId("websearch-in-use-tavily")).toBeTruthy();
    expect(screen.queryByTestId("websearch-in-use-duckduckgo")).toBeNull();
    // The active keyed provider still offers Update key next to the pill.
    expect(screen.getByTestId("websearch-key-button-tavily").textContent).toBe("Update key");
  });

  it("Add key opens the inline row; Test and save stores the key and the row reads Update key", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    expect(screen.queryByTestId("websearch-key-input")).toBeNull();
    fireEvent.click(screen.getByTestId("websearch-key-button-exa"));
    const input = screen.getByTestId("websearch-key-input") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.placeholder).toBe("Exa API key");
    expect(screen.getByText("Checked with one search.")).toBeTruthy();
    expect(screen.queryByTestId("websearch-key-remove")).toBeNull(); // nothing stored yet
    const save = screen.getByTestId("websearch-key-save") as HTMLButtonElement;
    expect(save.textContent).toBe("Test and save");
    expect(save.disabled).toBe(true); // nothing typed

    fireEvent.click(screen.getByTestId("websearch-key-url-exa"));
    expect(openExternal).toHaveBeenCalledWith("https://exa.example");

    fireEvent.change(input, { target: { value: "exa-123" } });
    fireEvent.click(save);
    await waitFor(() => expect(setWebSearchKey).toHaveBeenCalledWith("exa", "exa-123"));
    await waitFor(() => expect(screen.queryByTestId("websearch-key-input")).toBeNull());
    expect(screen.getByTestId("websearch-key-button-exa").textContent).toBe("Update key");
    expect(screen.getByTestId("websearch-status-exa").textContent).toBe("Key saved");
    expect(radio("exa").disabled).toBe(false);
  });

  it("a failed check shows the server's message and keeps the row open", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    fireEvent.click(screen.getByTestId("websearch-key-button-serper"));
    fireEvent.change(screen.getByTestId("websearch-key-input"), { target: { value: "bad-key" } });
    fireEvent.click(screen.getByTestId("websearch-key-save"));
    await screen.findByTestId("websearch-error");
    expect(screen.getByTestId("websearch-error").textContent).toBe("Client error '401 Unauthorized'");
    expect(screen.getByTestId("websearch-key-input")).toBeTruthy();
    expect(screen.getByTestId("websearch-key-cancel").textContent).toBe("Cancel");
    expect(radio("serper").disabled).toBe(true);
    fireEvent.click(screen.getByTestId("websearch-key-cancel"));
    expect(screen.queryByTestId("websearch-key-input")).toBeNull();
  });

  it("Remove key is offered for a stored key and forgets it", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    fireEvent.click(screen.getByTestId("websearch-key-button-tavily"));
    fireEvent.click(screen.getByTestId("websearch-key-remove"));
    await waitFor(() => expect(clearWebSearchKey).toHaveBeenCalledWith("tavily"));
    await waitFor(() => expect(screen.getByTestId("websearch-key-button-tavily").textContent).toBe("Add key"));
    expect(screen.queryByTestId("websearch-status-tavily")).toBeNull();
    expect(radio("tavily").disabled).toBe(true);
    // An environment key is not removable from here.
    fireEvent.click(screen.getByTestId("websearch-key-button-brave"));
    expect(screen.queryByTestId("websearch-key-remove")).toBeNull();
  });

  it("the fetch option appears under the active provider only when it offers it", async () => {
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-providers");
    // DuckDuckGo is active and does not fetch pages; Exa would, but is not active.
    expect(screen.queryByTestId("websearch-fetch-row")).toBeNull();
    expect(screen.queryByTestId("websearch-fetch-toggle")).toBeNull();

    fireEvent.click(radio("tavily"));
    await screen.findByTestId("websearch-fetch-row");
    const row = screen.getByTestId("websearch-row-tavily");
    expect(within(row).getByTestId("websearch-fetch-row")).toBeTruthy();
    expect(within(row).getByText("Also fetch pages through Tavily")).toBeTruthy();
    expect(within(row).getByText("Tavily then sees the address of every page the agent opens.")).toBeTruthy();
    expect(fetchBox().checked).toBe(false);
    fireEvent.click(fetchBox());
    await waitFor(() => expect(setFetchViaProvider).toHaveBeenCalledWith(true));
    await waitFor(() => expect(fetchBox().checked).toBe(true));

    // Moving to a provider without page fetching hides the option; the flag stays stored
    // and comes back when a fetching provider is active again.
    fireEvent.click(radio("brave"));
    await waitFor(() => expect(radio("brave").checked).toBe(true));
    expect(screen.queryByTestId("websearch-fetch-row")).toBeNull();
    fireEvent.click(radio("tavily"));
    await screen.findByTestId("websearch-fetch-row");
    expect(fetchBox().checked).toBe(true);
    expect(setFetchViaProvider).toHaveBeenCalledTimes(1);
  });

  it("an unreachable server shows one line instead of an empty page", async () => {
    getWebSearch.mockRejectedValueOnce(new Error("down"));
    render(<CustomizationTab />);
    await screen.findByTestId("websearch-load-error");
    expect(screen.getByTestId("websearch-load-error").textContent).toBe("Couldn't load the web search settings.");
  });
});

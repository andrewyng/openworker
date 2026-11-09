// OPE-252 layer 1: a keyed provider's page lists what its own model list says the key can
// call, merged with the curated rows — New since last check, Recommended, All models —
// with tick-to-add, the facts' provenance, "not on this key", Refresh, and a typed id the
// key cannot call refused with the closest ids. A provider without a list falls back to
// the curated checklist.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  getProviderCatalog: vi.fn(),
  addModel: vi.fn(),
  removeModel: vi.fn(),
  setDefaultModel: vi.fn(async () => ({ ok: true })),
  getSettings: vi.fn(async () => ({ models: [], model: "" })),
}));

import { addModel, getProviderCatalog, removeModel, type ModelSettings, type ProviderCatalog, type ProviderCatalogRow } from "../../api";
import { ProviderModels } from "./ProviderModels";

const row = (over: Partial<ProviderCatalogRow>): ProviderCatalogRow => ({
  id: "x", model: "openrouter:x", label: "X", context: null, tools: null, vision: null, released: null,
  pdf: null, in_picker: false, recommended: false, new: false, on_key: true, verified: "unverified", ...over,
});

const LIVE: ProviderCatalog = {
  ok: true, provider: "openrouter", configured: true, listing: "live", fetched_at: Math.floor(Date.now() / 1000) - 30, count: 4,
  rows: [
    row({ id: "lab/brand-new", model: "openrouter:lab/brand-new", label: "Brand New", new: true, context: 262144 }),
    row({ id: "z-ai/glm-5.2", model: "openrouter:z-ai/glm-5.2", label: "GLM-5.2 · via OpenRouter", verified: "catalog", recommended: true, in_picker: true, context: 1048576, vision: true, pdf: true }),
    row({ id: "openai/gpt-6-astra", model: "openrouter:openai/gpt-6-astra", label: "OpenAI: GPT-6 Astra", verified: "provider", context: 922000, vision: true }),
    row({ id: "lab/mystery", model: "openrouter:lab/mystery", label: "Mystery" }),
    row({ id: "moonshotai/kimi-k3", model: "openrouter:moonshotai/kimi-k3", label: "Kimi K3 · via OpenRouter", verified: "catalog", in_picker: true, on_key: false }),
  ],
  new: ["openrouter:lab/brand-new"],
  not_on_key: ["openrouter:moonshotai/kimi-k3"],
};

const SETTINGS = {
  models: ["openrouter:z-ai/glm-5.2", "openrouter:moonshotai/kimi-k3"],
  model: "openrouter:z-ai/glm-5.2",
  model_labels: {},
} as unknown as ModelSettings;

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function renderPanel(onChanged = vi.fn()) {
  render(<ProviderModels provider="openrouter" knownProviders={["openai", "openrouter"]} suggested={["z-ai/glm-5.2"]} settings={SETTINGS} onChanged={onChanged} />);
  return onChanged;
}

afterEach(() => {
  cleanup();
  vi.mocked(getProviderCatalog).mockReset();
  vi.mocked(addModel).mockReset();
  vi.mocked(removeModel).mockReset();
});

describe("ProviderModels with a live listing", () => {
  it("asks the provider once on open and lays the rows out in three sections with their facts", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    renderPanel();
    expect(screen.getByTestId("provider-models-loading").textContent).toContain("Checking which models");
    await flush();
    expect(getProviderCatalog).toHaveBeenCalledWith("openrouter", false);
    expect(screen.getByTestId("catalog-status").textContent).toContain("4 models on this key");
    expect(screen.getByTestId("catalog-new").textContent).toContain("Brand New");
    const list = screen.getByTestId("catalog-list");
    const names = Array.from(list.querySelectorAll(".mlist-name")).map((n) => n.textContent);
    expect(names).toEqual(["GLM-5.2 · via OpenRouter", "OpenAI: GPT-6 Astra", "Mystery", "Kimi K3 · via OpenRouter"]); // the backend's order, one list
    expect(list.textContent).not.toContain("Brand New");
    expect(screen.queryByText("Reviewed by OpenWorker")).toBeNull();
    // the facts and where they came from
    expect(screen.getByTestId("catalog-tags-lab/brand-new").textContent).toContain("new");
    expect(screen.getByTestId("catalog-tags-openai/gpt-6-astra").textContent).toContain("922K context");
    expect(screen.getByTestId("catalog-tags-lab/brand-new").textContent).toContain("262K context");
    expect(screen.getByTestId("catalog-tags-z-ai/glm-5.2").textContent).toContain("1M context");
    expect(screen.getByTestId("catalog-tags-openai/gpt-6-astra").textContent).toContain("reads images");
    expect(screen.getByTestId("catalog-tags-openai/gpt-6-astra").textContent).not.toContain("PDF");
    expect(screen.getByTestId("catalog-tags-z-ai/glm-5.2").textContent).toContain("reads images and PDFs");
    expect(screen.getByTestId("catalog-tags-lab/mystery").textContent).toContain("unverified");
    expect(screen.getByTestId("catalog-tags-moonshotai/kimi-k3").textContent).toContain("not on this key");
    expect(screen.getByTestId("catalog-not-on-key").textContent).toContain("1 ticked models are not on this key");
    // the default row is ticked and locked
    const glm = screen.getByTestId("catalog-row-z-ai/glm-5.2").querySelector("input") as HTMLInputElement;
    expect(glm.checked && glm.disabled).toBe(true);
  });

  it("tick adds the routed id, untick removes it, and the page learns the new picker", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    vi.mocked(addModel).mockResolvedValue({ ok: true, models: [...SETTINGS.models, "openrouter:openai/gpt-6-astra"], model: SETTINGS.model } as any);
    vi.mocked(removeModel).mockResolvedValue({ ok: true, models: [SETTINGS.models[0]], model: SETTINGS.model } as any);
    const onChanged = renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-row-openai/gpt-6-astra").querySelector("input")!);
    await waitFor(() => expect(addModel).toHaveBeenCalledWith("openrouter:openai/gpt-6-astra"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ models: [...SETTINGS.models, "openrouter:openai/gpt-6-astra"], model: SETTINGS.model }));
    expect((screen.getByTestId("catalog-row-openai/gpt-6-astra").querySelector("input") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("catalog-row-moonshotai/kimi-k3").querySelector("input")!);
    await waitFor(() => expect(removeModel).toHaveBeenCalledWith("openrouter:moonshotai/kimi-k3"));
  });

  it("a typed id the key cannot call is refused with the closest ids, and one click adds the right one", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    vi.mocked(addModel)
      .mockResolvedValueOnce({ ok: false, error: "Not available on this key: openai/gpt-6-astro", reason: "not_on_key", suggestions: ["openai/gpt-6-astra"] } as any)
      .mockResolvedValueOnce({ ok: true, models: [...SETTINGS.models, "openrouter:openai/gpt-6-astra"], model: SETTINGS.model } as any);
    const onChanged = renderPanel();
    await flush();
    fireEvent.change(screen.getByTestId("catalog-add-input"), { target: { value: "openai/gpt-6-astro" } });
    fireEvent.click(screen.getByTestId("catalog-add"));
    await waitFor(() => expect(screen.getByTestId("catalog-add-error").textContent).toContain("Not available on this key: openai/gpt-6-astro"));
    expect(addModel).toHaveBeenCalledWith("openrouter:openai/gpt-6-astro");
    expect(screen.getByTestId("catalog-add-error").textContent).toContain("Did you mean:");
    fireEvent.click(screen.getByTestId("catalog-suggest-openai/gpt-6-astra"));
    await waitFor(() => expect(addModel).toHaveBeenLastCalledWith("openrouter:openai/gpt-6-astra"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.queryByTestId("catalog-add-error")).toBeNull();
  });

  it("Refresh asks the provider again, and a failed re-ask keeps the last list with a note", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValueOnce(LIVE).mockResolvedValueOnce({ ...LIVE, error: "HTTP 503 from the provider's model list." });
    renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-refresh"));
    await waitFor(() => expect(getProviderCatalog).toHaveBeenLastCalledWith("openrouter", true));
    await waitFor(() => expect(screen.getByTestId("catalog-error").textContent).toContain("HTTP 503"));
    expect(screen.getByTestId("catalog-list").textContent).toContain("GLM-5.2");
  });

  it("search narrows every section and long lists fold", async () => {
    const many = Array.from({ length: 20 }, (_, i) => row({ id: `lab/m${i}`, model: `openrouter:lab/m${i}`, label: `Model ${i}` }));
    vi.mocked(getProviderCatalog).mockResolvedValue({ ...LIVE, rows: [...LIVE.rows, ...many] });
    renderPanel();
    await flush();
    expect(screen.getByTestId("catalog-list").querySelectorAll(".mlist-row").length).toBe(12);
    expect(screen.getByTestId("catalog-show-all").textContent).toContain("Show all 24");
    fireEvent.click(screen.getByTestId("catalog-show-all"));
    expect(screen.getByTestId("catalog-list").querySelectorAll(".mlist-row").length).toBe(24);
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "astra" } });
    expect(screen.getByTestId("catalog-list").querySelectorAll(".mlist-row").length).toBe(1);
    expect(screen.queryByTestId("catalog-new")).toBeNull();
  });
});

describe("ProviderModels without a live listing", () => {
  it("a provider with no list shows the reason and the curated checklist", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue({
      ok: true, provider: "bedrock", configured: true, listing: "none",
      reason: "Bedrock lists models per region with AWS credentials; the curated rows are shown.",
      rows: [], new: [], not_on_key: [],
    });
    render(<ProviderModels provider="bedrock" knownProviders={["openai", "bedrock"]} suggested={["claude/anthropic.claude-sonnet-4-6-v1:0"]} settings={SETTINGS} onChanged={vi.fn()} />);
    await flush();
    expect(screen.getByTestId("provider-models-reason").textContent).toContain("per region");
    expect(screen.getByTestId("provider-models-fallback").querySelectorAll(".mlist-row").length).toBe(1);
    expect(screen.queryByTestId("catalog-status")).toBeNull();
  });

  it("a refused key on the first ask says so, offers Retry, and still shows the curated rows", async () => {
    vi.mocked(getProviderCatalog)
      .mockResolvedValueOnce({ ok: true, provider: "openrouter", configured: true, listing: "error", error: "The provider refused the key.", rows: [], new: [], not_on_key: [] })
      .mockResolvedValueOnce(LIVE);
    renderPanel();
    await flush();
    expect(screen.getByTestId("provider-models-reason").textContent).toContain("refused the key");
    fireEvent.click(screen.getByTestId("provider-models-retry"));
    await waitFor(() => expect(getProviderCatalog).toHaveBeenLastCalledWith("openrouter", true));
    await waitFor(() => expect(screen.getByTestId("catalog-status")).toBeTruthy());
  });

  it("an unreachable backend says so with Retry", async () => {
    vi.mocked(getProviderCatalog).mockRejectedValueOnce(new Error("HTTP 500")).mockResolvedValueOnce(LIVE);
    renderPanel();
    await flush();
    expect(screen.getByTestId("provider-models-failed").textContent).toContain("Could not ask the provider");
    fireEvent.click(screen.getByTestId("provider-models-retry"));
    await waitFor(() => expect(screen.getByTestId("catalog-status")).toBeTruthy());
  });
});

// OPE-252 layer 1 (design of 2026-11-10): a keyed provider's page offers exactly what its own
// list says the key can call — one list of chat models with a line of plain facts under each
// name, the other kinds behind one collapsed line with "Use anyway", a "Your pick · untested"
// badge with Undo, the name as the one way into a model's settings, Refresh, search, and a
// typed id the key cannot call refused with the closest ids. A provider without a list falls
// back to the curated checklist; a first ask that fails shows the error and Retry only.
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
  id: "x", model: "cohere:x", label: "X · Cohere", kind: "chat", kind_source: "provider", detail: null, reason: null,
  context: null, max_output: null, input_image: null, input_pdf: null, tools: null, reasoning: null, retires_at: null, released: null,
  in_picker: false, recommended: false, new: false, on_key: true, picked_anyway: false, ...over,
});

const LIVE: ProviderCatalog = {
  ok: true, provider: "cohere", configured: true, listing: "live", fetched_at: Math.floor(Date.now() / 1000) - 7200, count: 4,
  rows: [
    row({ id: "command-a-plus-05-2026", model: "cohere:command-a-plus-05-2026", label: "Command A+ · Cohere", recommended: true, in_picker: true, context: 436000, input_image: true, tools: true, reasoning: true }),
    row({ id: "command-a-fresh", model: "cohere:command-a-fresh", label: "command-a-fresh · Cohere", new: true, context: 288000, tools: true }),
    row({ id: "command-a-translate-08-2025", model: "cohere:command-a-translate-08-2025", label: "command-a-translate-08-2025 · Cohere", context: 8992, tools: false, retires_at: "2026-12-11" }),
    row({ id: "command-plain", model: "cohere:command-plain", label: "command-plain · Cohere", kind_source: "none" }),
    row({ id: "command-r-old", model: "cohere:command-r-old", label: "Command R · Cohere", kind_source: "catalog", in_picker: true, on_key: false, context: 128000 }),
  ],
  set_aside: [
    row({ id: "embed-v4", model: "cohere:embed-v4", label: "embed-v4 · Cohere", kind: "embedding", reason: "Built for embeddings", context: 512 }),
    row({ id: "whisper-ish", model: "cohere:whisper-ish", label: "whisper-ish · Cohere", kind: "speech_to_text", kind_source: "name", detail: "whisper", reason: "Likely built for speech to text" }),
  ],
  new: ["cohere:command-a-fresh"],
  not_on_key: ["cohere:command-r-old"],
};

const SETTINGS = {
  models: ["cohere:command-a-plus-05-2026", "cohere:command-r-old"],
  model: "cohere:command-a-plus-05-2026",
  model_labels: {},
} as unknown as ModelSettings;

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function renderPanel(onChanged = vi.fn(), onConfigure?: (model: string) => void) {
  render(<ProviderModels provider="cohere" providerTitle="Cohere" knownProviders={["openai", "cohere"]} suggested={["command-a-plus-05-2026"]} settings={SETTINGS} onChanged={onChanged} onConfigure={onConfigure} />);
  return onChanged;
}

afterEach(() => {
  cleanup();
  vi.mocked(getProviderCatalog).mockReset();
  vi.mocked(addModel).mockReset();
  vi.mocked(removeModel).mockReset();
});

describe("ProviderModels with a live listing", () => {
  it("asks the provider once on open and shows the chat models with their facts as plain text", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    renderPanel();
    expect(screen.getByTestId("provider-models-loading").textContent).toContain("Checking which models");
    await flush();
    expect(getProviderCatalog).toHaveBeenCalledWith("cohere", false);
    expect(screen.getByTestId("catalog-status").textContent).toContain("4 models on this key · from Cohere's list, checked 2 h ago");
    const names = Array.from(screen.getByTestId("catalog-list").querySelectorAll(".mlist-name")).map((n) => n.textContent);
    expect(names).toEqual(["Command A+", "command-a-fresh", "command-a-translate-08-2025", "command-plain", "Command R"]); // the backend's order, one list, no headings
    expect(screen.queryByText("New since last check")).toBeNull();
    expect(screen.queryByText(/From Cohere/)).toBeNull(); // no legend, no source marking anywhere
    expect(screen.queryByText(/unverified/i)).toBeNull();
    // the facts line: plain words separated by middle dots
    expect(screen.getByTestId("catalog-facts-command-a-plus-05-2026").textContent).toBe("Context 436K·Images in·Tools·Reasoning");
    expect(screen.getByTestId("catalog-facts-command-a-fresh").textContent).toBe("New·Context 288K·Tools");
    // a row whose vendor gave nothing has no facts line at all
    expect(screen.queryByTestId("catalog-facts-command-plain")).toBeNull();
    // the id in grey when the display name differs from it
    expect(screen.getByTestId("catalog-row-command-a-plus-05-2026").querySelector(".mlist-id")!.textContent).toBe("command-a-plus-05-2026");
    expect(screen.getByTestId("catalog-row-command-plain").querySelector(".mlist-id")).toBeNull();
    // the default row is ticked and locked
    const plus = screen.getByTestId("catalog-row-command-a-plus-05-2026").querySelector("input") as HTMLInputElement;
    expect(plus.checked && plus.disabled).toBe(true);
  });

  it("second-look facts are amber: a short window, no tools, a retirement date; a ticked model the key lacks says so", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    renderPanel();
    await flush();
    const facts = screen.getByTestId("catalog-facts-command-a-translate-08-2025");
    const amber = Array.from(facts.querySelectorAll(".text-warnInk")).map((n) => n.textContent);
    expect(amber).toEqual(["Context 9K — short for long tasks", "No tool support — coworkers cannot act", "Deprecated · ends 11 Dec 2026"]);
    expect(screen.getByTestId("catalog-facts-command-r-old").querySelector(".text-danger")!.textContent).toBe("Not on this key");
    expect(screen.getByTestId("catalog-not-on-key").textContent).toContain("1 ticked model is not on this key; the provider will refuse it.");
  });

  it("every model name opens the model's settings without toggling the tick", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    const onConfigure = vi.fn();
    renderPanel(vi.fn(), onConfigure);
    await flush();
    const ticked = screen.getByTestId("catalog-configure-command-r-old");
    expect(ticked.tagName).toBe("BUTTON");
    expect(ticked.getAttribute("title")).toBe("Settings for this model");
    fireEvent.click(ticked);
    expect(onConfigure).toHaveBeenCalledWith("cohere:command-r-old");
    expect(removeModel).not.toHaveBeenCalled();
    expect((screen.getByTestId("catalog-row-command-r-old").querySelector("input") as HTMLInputElement).checked).toBe(true);
    // an unticked row's name too: the dialog adds the model when its settings are saved
    fireEvent.click(screen.getByTestId("catalog-configure-command-plain"));
    expect(onConfigure).toHaveBeenLastCalledWith("cohere:command-plain");
    expect(addModel).not.toHaveBeenCalled();
    // no Edit button anywhere: the name is the one way in
    expect(screen.queryByText("Edit")).toBeNull();
  });

  it("without a settings opener every name is plain text", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    renderPanel();
    await flush();
    expect(screen.queryByTestId("catalog-configure-command-plain")).toBeNull();
    expect(screen.getByTestId("catalog-row-command-plain").querySelector(".mlist-name")!.tagName).toBe("SPAN");
  });

  it("tick adds the routed id, untick removes it, and the page re-reads the list", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    vi.mocked(addModel).mockResolvedValue({ ok: true, models: [...SETTINGS.models, "cohere:command-plain"], model: SETTINGS.model } as any);
    vi.mocked(removeModel).mockResolvedValue({ ok: true, models: [SETTINGS.models[0]], model: SETTINGS.model } as any);
    const onChanged = renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-row-command-plain").querySelector("input")!);
    await waitFor(() => expect(addModel).toHaveBeenCalledWith("cohere:command-plain"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ models: [...SETTINGS.models, "cohere:command-plain"], model: SETTINGS.model }));
    await waitFor(() => expect(getProviderCatalog).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByTestId("catalog-row-command-r-old").querySelector("input")!);
    await waitFor(() => expect(removeModel).toHaveBeenCalledWith("cohere:command-r-old"));
  });

  it("the set-aside models sit behind one line; Show lists them with the reason and Use anyway; the explanation then adds the model", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValueOnce(LIVE).mockResolvedValue({
      ...LIVE,
      rows: [...LIVE.rows, row({ id: "embed-v4", model: "cohere:embed-v4", label: "embed-v4 · Cohere", kind: "embedding", reason: "Built for embeddings", context: 512, in_picker: true, picked_anyway: true })],
      set_aside: [LIVE.set_aside[1]],
    });
    vi.mocked(addModel).mockResolvedValue({ ok: true, models: [...SETTINGS.models, "cohere:embed-v4"], model: SETTINGS.model } as any);
    const onChanged = renderPanel();
    await flush();
    expect(screen.getByTestId("catalog-aside-line").textContent).toContain("2 more models on this key are for images, speech, embeddings and other tasks.");
    expect(screen.queryByTestId("catalog-aside")).toBeNull();
    fireEvent.click(screen.getByTestId("catalog-aside-toggle"));
    expect(screen.getByTestId("catalog-aside-toggle").textContent).toBe("Hide");
    expect(screen.getByTestId("catalog-aside").querySelectorAll(".mlist-row").length).toBe(2);
    expect(screen.getByTestId("catalog-aside-embed-v4").querySelector("input")).toBeNull(); // no checkbox
    expect(screen.getByTestId("catalog-reason-embed-v4").textContent).toBe("Built for embeddings"); // the vendor said: definite
    expect(screen.getByTestId("catalog-reason-whisper-ish").textContent).toBe("Likely built for speech to text"); // our name rule: hedged
    expect(screen.queryByText(/by its name|naming convention/)).toBeNull();
    // Use anyway opens the explanation under the row, with the two templates
    fireEvent.click(screen.getByTestId("catalog-use-embed-v4"));
    expect(screen.getByTestId("catalog-why-embed-v4").textContent).toContain("Cohere's API lists it as embeddings, not chat. Coworkers send conversations and tool calls");
    expect(screen.getByTestId("catalog-why-embed-v4").textContent).toContain("If you use it anyway. It is ticked and shows in the picker marked “Your pick · untested”. Undo any time.");
    fireEvent.click(screen.getByTestId("catalog-use-whisper-ish"));
    expect(screen.queryByTestId("catalog-why-embed-v4")).toBeNull(); // one explanation open at a time
    expect(screen.getByTestId("catalog-why-whisper-ish").textContent).toContain("Cohere's API gives no details for this model. Its name suggests speech to text. If you know it chats, use it.");
    fireEvent.click(screen.getByTestId("catalog-keep-whisper-ish"));
    expect(screen.queryByTestId("catalog-why-whisper-ish")).toBeNull();
    expect(addModel).not.toHaveBeenCalled();
    // confirm: the model is added "anyway" and the page re-reads the list
    fireEvent.click(screen.getByTestId("catalog-use-embed-v4"));
    fireEvent.click(screen.getByTestId("catalog-use-confirm-embed-v4"));
    await waitFor(() => expect(addModel).toHaveBeenCalledWith("cohere:embed-v4", true));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("catalog-row-embed-v4")).toBeTruthy());
    // now in the list, ticked, with the badge, the reason and Undo
    expect((screen.getByTestId("catalog-row-embed-v4").querySelector("input") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("catalog-your-pick-embed-v4").textContent).toBe("Your pick · untested");
    expect(screen.getByTestId("catalog-added-embed-v4").textContent).toContain("Added by you · Built for embeddings");
    expect(screen.getByTestId("catalog-aside-line").textContent).toContain("1 more model on this key is for images");
  });

  it("a model the provider refused for this key sits aside with the provider's own words", async () => {
    const gone = row({ id: "gemini-2.5-flash", model: "gemini:gemini-2.5-flash", label: "Gemini 2.5 Flash · Google", kind: "unavailable", detail: "This model models/gemini-2.5-flash is no longer available to new users" });
    vi.mocked(getProviderCatalog).mockResolvedValue({ ...LIVE, set_aside: [gone] });
    renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-aside-toggle"));
    expect(screen.getByTestId("catalog-reason-gemini-2.5-flash").textContent).toBe("Not available to this key: “This model models/gemini-2.5-flash is no longer available to new users”");
    fireEvent.click(screen.getByTestId("catalog-use-gemini-2.5-flash"));
    expect(screen.getByTestId("catalog-why-gemini-2.5-flash").textContent).toContain("When the app checked this model with your key, Cohere refused it");
  });

  it("Undo on a Your-pick row removes the model", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue({
      ...LIVE,
      rows: [...LIVE.rows, row({ id: "embed-v4", model: "cohere:embed-v4", label: "embed-v4 · Cohere", kind: "embedding", reason: "Built for embeddings", in_picker: true, picked_anyway: true })],
    });
    vi.mocked(removeModel).mockResolvedValue({ ok: true, models: SETTINGS.models, model: SETTINGS.model } as any);
    renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-undo-embed-v4"));
    await waitFor(() => expect(removeModel).toHaveBeenCalledWith("cohere:embed-v4"));
  });

  it("the Add box names the provider, and a typed id the key cannot call is refused with the closest ids", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    vi.mocked(addModel)
      .mockResolvedValueOnce({ ok: false, error: "Not available on this key: command-a-plux", reason: "not_on_key", suggestions: ["command-a-plus-05-2026"] } as any)
      .mockResolvedValueOnce({ ok: true, models: SETTINGS.models, model: SETTINGS.model } as any);
    const onChanged = renderPanel();
    await flush();
    expect(screen.getByTestId("catalog-add-box").textContent).toContain("Not in the list? Add a Cohere model by its id.");
    expect(screen.getByTestId("catalog-add-input").getAttribute("placeholder")).toBe("Model id…");
    fireEvent.change(screen.getByTestId("catalog-add-input"), { target: { value: "command-a-plux" } });
    fireEvent.click(screen.getByTestId("catalog-add"));
    await waitFor(() => expect(screen.getByTestId("catalog-add-error").textContent).toContain("Not available on this key: command-a-plux"));
    expect(addModel).toHaveBeenCalledWith("cohere:command-a-plux");
    expect(screen.getByTestId("catalog-add-error").textContent).toContain("Did you mean:");
    fireEvent.click(screen.getByTestId("catalog-suggest-command-a-plus-05-2026"));
    await waitFor(() => expect(addModel).toHaveBeenLastCalledWith("cohere:command-a-plus-05-2026"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.queryByTestId("catalog-add-error")).toBeNull();
  });

  it("Refresh asks the provider again, and a failed re-ask keeps the last list with a note", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValueOnce(LIVE).mockResolvedValueOnce({ ...LIVE, error: "HTTP 503 from the provider's model list." });
    renderPanel();
    await flush();
    fireEvent.click(screen.getByTestId("catalog-refresh"));
    await waitFor(() => expect(getProviderCatalog).toHaveBeenLastCalledWith("cohere", true));
    await waitFor(() => expect(screen.getByTestId("catalog-error").textContent).toContain("HTTP 503"));
    expect(screen.getByTestId("catalog-list").textContent).toContain("Command A+");
  });

  it("search narrows the list and reaches the set-aside rows too", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue(LIVE);
    renderPanel();
    await flush();
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "embed" } });
    expect(screen.getByTestId("catalog-list").querySelectorAll(".mlist-row").length).toBe(0);
    expect(screen.getByTestId("catalog-aside").querySelectorAll(".mlist-row").length).toBe(1);
    expect(screen.queryByTestId("catalog-aside-line")).toBeNull();
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "command-a" } });
    expect(screen.getByTestId("catalog-list").querySelectorAll(".mlist-row").length).toBe(3);
  });
});

describe("ProviderModels without a live listing", () => {
  it("a provider with no list shows the reason and the curated checklist", async () => {
    vi.mocked(getProviderCatalog).mockResolvedValue({
      ok: true, provider: "bedrock", configured: true, listing: "none",
      reason: "Bedrock lists models per region with AWS credentials the app does not sign yet; the curated rows are shown.",
      rows: [], set_aside: [], new: [], not_on_key: [],
    });
    render(<ProviderModels provider="bedrock" knownProviders={["openai", "bedrock"]} suggested={["claude/anthropic.claude-sonnet-4-6-v1:0"]} settings={SETTINGS} onChanged={vi.fn()} />);
    await flush();
    expect(screen.getByTestId("provider-models-reason").textContent).toContain("per region");
    expect(screen.getByTestId("provider-models-fallback").querySelectorAll(".mlist-row").length).toBe(1);
    expect(screen.queryByTestId("catalog-status")).toBeNull();
  });

  it("a refused key on the first ask shows the error, Retry and the Add box, never the curated rows", async () => {
    vi.mocked(getProviderCatalog)
      .mockResolvedValueOnce({ ok: true, provider: "cohere", configured: true, listing: "error", error: "The provider refused the key.", rows: [], set_aside: [], new: [], not_on_key: [] })
      .mockResolvedValueOnce(LIVE);
    renderPanel();
    await flush();
    expect(screen.getByTestId("provider-models-reason").textContent).toContain("refused the key");
    expect(screen.getByTestId("provider-models-fallback").querySelectorAll(".mlist-row").length).toBe(0);
    expect(screen.getByTestId("catalog-add-input")).toBeTruthy();
    fireEvent.click(screen.getByTestId("provider-models-retry"));
    await waitFor(() => expect(getProviderCatalog).toHaveBeenLastCalledWith("cohere", true));
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

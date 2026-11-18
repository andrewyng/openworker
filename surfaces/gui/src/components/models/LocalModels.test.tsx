// OPE-260: the local-server model table can make a row the default, and says which row
// is the default. Before, with every key removed, this was the only live list on the page
// and offered no way to choose a default (the picker list's button was hover-only).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LocalModelsTable } from "./LocalModels";

const row = (name: string, extra: Record<string, unknown> = {}) => ({
  model: `ollama:${name}`,
  name,
  size_bytes: 1024 ** 3,
  tools: true,
  thinking: false,
  vision: false,
  remote: false,
  parameter_size: null,
  quantization: null,
  context_max: null,
  context: 16384,
  context_from: "machine",
  fit: "runs_well",
  recommendation: null,
  ...extra,
});

const ROWS = [row("qwen3:0.6b"), row("llama3.2:3b"), row("nomic-embed", { tools: false })];

vi.mock("../../api", () => ({
  getLocalModels: vi.fn(async () => ({ provider: "ollama", models: ROWS, alive: true })),
  getSystemFacts: vi.fn(async () => ({})),
  addModel: vi.fn(async (id: string) => ({ ok: true, models: [id], model: id })),
  removeModel: vi.fn(async () => ({ ok: true, models: [], model: "" })),
  setDefaultModel: vi.fn(async () => ({ ok: true })),
}));

import { addModel, removeModel, setDefaultModel } from "../../api";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderTable(opts: { curated?: string[]; defaultModel?: string } = {}) {
  const onChanged = vi.fn();
  const onConfigure = vi.fn();
  render(
    <LocalModelsTable
      provider="ollama"
      curated={opts.curated ?? ["ollama:qwen3:0.6b", "ollama:llama3.2:3b"]}
      defaultModel={opts.defaultModel ?? "ollama:qwen3:0.6b"}
      onChanged={onChanged}
      onConfigure={onConfigure}
    />,
  );
  await screen.findByTestId("local-model-qwen3:0.6b");
  return { onChanged, onConfigure };
}

describe("LocalModelsTable: Make default (OPE-260)", () => {
  it("marks the default row and offers Make default on every other usable row", async () => {
    await renderTable();
    expect(screen.getByTestId("local-model-default-qwen3:0.6b").textContent).toBe("default");
    expect(screen.queryByTestId("local-model-make-default-qwen3:0.6b")).toBeNull();
    expect(screen.getByTestId("local-model-make-default-llama3.2:3b").textContent).toBe("Make default");
    // A model without tool calling cannot be a coworker's model: no button.
    expect(screen.queryByTestId("local-model-make-default-nomic-embed")).toBeNull();
    expect(screen.queryByTestId("local-model-default-nomic-embed")).toBeNull();
  });

  it("the button is visible markup, not hover-only", async () => {
    await renderTable();
    const btn = screen.getByTestId("local-model-make-default-llama3.2:3b");
    expect(btn.tagName).toBe("BUTTON");
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it("makes a ticked row the default and refreshes", async () => {
    const { onChanged } = await renderTable();
    fireEvent.click(screen.getByTestId("local-model-make-default-llama3.2:3b"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(setDefaultModel).toHaveBeenCalledWith("ollama:llama3.2:3b");
    expect(addModel).not.toHaveBeenCalled(); // already in the picker
  });

  it("defaulting an unticked row ticks it first", async () => {
    const { onChanged } = await renderTable({ curated: ["ollama:qwen3:0.6b"] });
    fireEvent.click(screen.getByTestId("local-model-make-default-llama3.2:3b"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(addModel).toHaveBeenCalledWith("ollama:llama3.2:3b");
    expect(setDefaultModel).toHaveBeenCalledWith("ollama:llama3.2:3b");
    const order = [
      ...(addModel as any).mock.invocationCallOrder,
      ...(setDefaultModel as any).mock.invocationCallOrder,
    ];
    expect(order[0]).toBeLessThan(order[1]);
  });

  it("the default row's checkbox is locked and says why", async () => {
    await renderTable();
    const rowEl = screen.getByTestId("local-model-qwen3:0.6b");
    const box = rowEl.querySelector("input[type=checkbox]") as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.title).toContain("default");
  });

  it("the tick is the one in/out control: no ✕ beside it, and the help text says untick", async () => {
    await renderTable();
    expect(screen.queryByTitle("Remove from the picker")).toBeNull();
    expect(screen.queryByText("✕")).toBeNull();
    expect(screen.getByText(/untick one to take it out/)).toBeTruthy();
  });

  it("Make default stays on one line", async () => {
    await renderTable();
    const btn = screen.getByTestId("local-model-make-default-llama3.2:3b");
    expect(btn.className).toContain("whitespace-nowrap");
  });

  it("unticking another row calls removeModel and refreshes", async () => {
    const { onChanged } = await renderTable();
    const rowEl = screen.getByTestId("local-model-llama3.2:3b");
    const box = rowEl.querySelector("input[type=checkbox]") as HTMLInputElement;
    expect(box.disabled).toBe(false);
    fireEvent.click(box);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(removeModel).toHaveBeenCalledWith("ollama:llama3.2:3b");
  });
});

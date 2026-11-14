// Add-model family dropdown for the cloud-account providers: the family choice folds
// into the model id (`bedrock:claude/…`, `vertex:openweight/…`); plain providers keep
// the bare add-model row.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModelChecklist } from "./ModelChecklist";

vi.mock("../api", () => ({
  addModel: vi.fn(async (id: string) => ({ ok: true, models: [id], model: id })),
  removeModel: vi.fn(async () => ({ ok: true, models: [], model: "" })),
  setDefaultModel: vi.fn(async () => ({ ok: true })),
  getSettings: vi.fn(async () => ({ models: [], model: "" })),
}));

import { addModel } from "../api";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const KNOWN = ["openai", "anthropic", "bedrock", "vertex", "openrouter"];

function renderList(provider: string) {
  return render(
    <ModelChecklist
      provider={provider}
      knownProviders={KNOWN}
      suggested={[]}
      curated={[]}
      defaultModel=""
      onChanged={() => {}}
    />,
  );
}

// Types the id and clicks Add; resolves once the save has finished (the button reads
// "Adding…" meanwhile and ignores clicks).
async function addTyped(id: string) {
  fireEvent.change(screen.getByPlaceholderText("Add another model…"), {
    target: { value: id },
  });
  fireEvent.click(screen.getByText("Add"));
  await screen.findByText("Add");
}

describe("ModelChecklist add-model family dropdown", () => {
  it("folds the selected vertex family into the id", async () => {
    renderList("vertex");
    fireEvent.change(screen.getByTestId("mlist-family"), {
      target: { value: "openweight" },
    });
    await addTyped("meta/llama-4-maverick-17b-128e-instruct-maas");
    expect(addModel).toHaveBeenCalledWith(
      "vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas",
    );
  });

  it("defaults bedrock to the Claude family and keeps a typed family verbatim", async () => {
    renderList("bedrock");
    await addTyped("anthropic.claude-sonnet-4-6-v1:0");
    expect(addModel).toHaveBeenCalledWith(
      "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0",
    );
    await addTyped("other/amazon.nova-2-pro-v1:0");
    expect(addModel).toHaveBeenLastCalledWith("bedrock:other/amazon.nova-2-pro-v1:0");
  });

  it("shows no family dropdown for plain providers", async () => {
    renderList("openrouter");
    expect(screen.queryByTestId("mlist-family")).toBeNull();
    await addTyped("z-ai/glm-5.2");
    expect(addModel).toHaveBeenCalledWith("openrouter:z-ai/glm-5.2");
  });
});

describe("ModelChecklist Add button feedback (OPE-250)", () => {
  it("says it is working while the save runs, then clears the box", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let finish: (v: any) => void = () => {};
    vi.mocked(addModel).mockImplementationOnce(
      () => new Promise((resolve) => { finish = resolve; }),
    );
    renderList("openrouter");
    void addTyped("z-ai/glm-5.3");
    const button = (await screen.findByText("Adding…")) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    finish({ ok: true, models: ["openrouter:z-ai/glm-5.3"], model: "" });
    expect(await screen.findByText("Add")).toBeTruthy();
    expect((screen.getByPlaceholderText("Add another model…") as HTMLInputElement).value).toBe("");
  });

  it("shows the server's reason when the save fails, and the typed id stays", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(addModel).mockResolvedValueOnce({ ok: false, error: "empty model", models: [], model: "" } as any);
    renderList("openrouter");
    await addTyped("nonsense");
    expect((await screen.findByTestId("mlist-add-error")).textContent).toContain("empty model");
    expect((screen.getByPlaceholderText("Add another model…") as HTMLInputElement).value).toBe("nonsense");
    expect((screen.getByText("Add") as HTMLButtonElement).disabled).toBe(false);
  });
});

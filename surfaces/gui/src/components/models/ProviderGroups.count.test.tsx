// OPE-260: a local server's card counts the models the server holds, not the picker's
// share of them (unticking one of five installed models said "4 models"). A keyed
// provider's card still counts the picker, which is what its key is used for.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

vi.mock("../../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  getProviders: vi.fn(),
}));

import { getProviders, type ProviderInfo } from "../../api";
import { useProviderSetup } from "../../providers/ProviderSetup";
import { ProviderGroups } from "./ProviderGroups";

function Page({ models }: { models: string[] }) {
  const ps = useProviderSetup();
  return <ProviderGroups ps={ps} tp="set" models={models} />;
}

const ollama = {
  name: "ollama",
  title: "Ollama",
  kind: "local",
  needs_key: false,
  configured: true,
  alive: true,
  fields: [],
  suggested_models: ["qwen3:0.6b-q8_0", "qwen3:0.6b", "llama3.2:3b", "llama3.3:latest", "gpt-oss:20b"],
} as unknown as ProviderInfo;

const openai = {
  name: "openai",
  title: "OpenAI",
  kind: "api_key",
  needs_key: true,
  configured: true,
  fields: [],
  suggested_models: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
} as unknown as ProviderInfo;

afterEach(() => {
  cleanup();
  vi.mocked(getProviders).mockReset();
});

const cardText = (name: string) => screen.getByTestId(`set-provider-${name}`).textContent || "";

describe("provider card model counts (OPE-260)", () => {
  it("a local server's card counts installed models even when some are unticked", async () => {
    vi.mocked(getProviders).mockResolvedValue([ollama]);
    // Four of the five are in the picker.
    render(<Page models={["ollama:qwen3:0.6b", "ollama:llama3.2:3b", "ollama:llama3.3:latest", "ollama:gpt-oss:20b"]} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(cardText("ollama")).toContain("Running · 5 models");
  });

  it("a keyed provider's card still counts the picker's models", async () => {
    vi.mocked(getProviders).mockResolvedValue([openai]);
    render(<Page models={["gpt-5.6-sol"]} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(cardText("openai")).toContain("Connected · 1 model");
    expect(cardText("openai")).not.toContain("4 models");
  });

  it("an older backend without a live list falls back to the picker count", async () => {
    vi.mocked(getProviders).mockResolvedValue([{ ...ollama, suggested_models: [] } as ProviderInfo]);
    render(<Page models={["ollama:qwen3:0.6b", "ollama:llama3.2:3b"]} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(cardText("ollama")).toContain("Running · 2 models");
  });
});

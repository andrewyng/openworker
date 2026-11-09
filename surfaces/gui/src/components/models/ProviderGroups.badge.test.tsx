// OPE-252: a connected provider's card says how many models its own list gained since the
// last check; a card with nothing new, or one that is not connected, shows no badge.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

vi.mock("../../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  getProviders: vi.fn(),
}));

import { getProviders } from "../../api";
import { useProviderSetup } from "../../providers/ProviderSetup";
import { ProviderGroups } from "./ProviderGroups";

function Page() {
  const ps = useProviderSetup();
  return <ProviderGroups ps={ps} tp="set" models={["gpt-5.5"]} />;
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

afterEach(() => {
  cleanup();
  vi.mocked(getProviders).mockReset();
});

describe("ProviderGroups new-model badge (OPE-252)", () => {
  it("shows the count on a connected card and nothing elsewhere", async () => {
    vi.mocked(getProviders).mockResolvedValue([
      { name: "openai", title: "OpenAI", kind: "api_key", needs_key: true, configured: true, fields: [], suggested_models: [], new_models: 3 },
      { name: "anthropic", title: "Claude (Anthropic)", kind: "api_key", needs_key: true, configured: true, fields: [], suggested_models: [], new_models: 0 },
      { name: "groq", title: "Groq", kind: "api_key", needs_key: true, configured: false, fields: [], suggested_models: [], new_models: 5 },
    ] as any);
    render(<Page />);
    await flush();
    expect(screen.getByTestId("set-new-openai").textContent).toBe("3 new");
    expect(screen.queryByTestId("set-new-anthropic")).toBeNull();
    expect(screen.queryByTestId("set-new-groq")).toBeNull(); // not connected: nothing was asked
  });
});

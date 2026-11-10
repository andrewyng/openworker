// T3-2: while /v1/providers is on its way the page says "Loading providers…", and when it
// fails it says so with Try again, never "No providers match." with an empty search box.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  getProviders: vi.fn(),
}));

import { getProviders, type ProviderInfo } from "../../api";
import { useProviderSetup } from "../../providers/ProviderSetup";
import { ProviderGroups } from "./ProviderGroups";

function Page() {
  const ps = useProviderSetup();
  return <ProviderGroups ps={ps} tp="set" models={[]} />;
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

afterEach(() => {
  cleanup();
  vi.mocked(getProviders).mockReset();
});

describe("ProviderGroups while the providers load (T3-2)", () => {
  it("says Loading providers… until the answer comes", async () => {
    let answer: (p: ProviderInfo[]) => void = () => {};
    vi.mocked(getProviders).mockReturnValue(new Promise((r) => { answer = r; }));
    render(<Page />);
    expect(screen.getByTestId("provider-loading").textContent).toBe("Loading providers…");
    expect(screen.queryByTestId("provider-search-empty")).toBeNull();
    await act(async () => answer([{ name: "openai", title: "OpenAI", kind: "api_key", needs_key: true, configured: false, fields: [] } as any]));
    expect(screen.queryByTestId("provider-loading")).toBeNull();
    expect(screen.getByTestId("set-provider-openai")).toBeTruthy();
  });

  it("says it could not load, and Try again asks again", async () => {
    vi.mocked(getProviders).mockRejectedValueOnce(new Error("offline"));
    render(<Page />);
    await flush();
    expect(screen.getByTestId("provider-load-failed").textContent).toContain("Could not load the providers.");
    expect(screen.queryByTestId("provider-search-empty")).toBeNull();
    vi.mocked(getProviders).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByTestId("provider-load-retry"));
    await flush();
    expect(getProviders).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId("provider-load-failed")).toBeNull();
  });
});

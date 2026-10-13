// Models & Keys provider cards: while the first provider list is still on its way (the server
// can take seconds probing local model servers) the page says it is loading — "No providers
// match." is only for a search that matches nothing.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ProviderInfo } from "../../api";
import type { ProviderSetupState } from "../../providers/ProviderSetup";
import { ProviderGroups } from "./ProviderGroups";

vi.mock("../../tauri", () => ({ openExternal: vi.fn() }));

afterEach(cleanup);

const OPENAI: ProviderInfo = {
  name: "openai",
  title: "OpenAI",
  needs_key: true,
  fields: [],
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: null,
  kind: "api_key",
};

function makePs(providers: ProviderInfo[], loaded: boolean): ProviderSetupState {
  return {
    providers,
    loaded,
    ordered: providers,
    keylessOk: new Set(),
    openProvider: () => {},
    statusFor: () => null,
  } as unknown as ProviderSetupState;
}

describe("ProviderGroups loading state", () => {
  it("says it is loading, not that nothing matches, before the list arrives", () => {
    render(<ProviderGroups ps={makePs([], false)} tp="set" models={[]} />);
    expect(screen.getByTestId("provider-loading").textContent).toBe("Loading providers…");
    expect(screen.queryByTestId("provider-search-empty")).toBeNull();
  });

  it("shows the cards once loaded", () => {
    render(<ProviderGroups ps={makePs([OPENAI], true)} tp="set" models={[]} />);
    expect(screen.queryByTestId("provider-loading")).toBeNull();
    expect(screen.getByTestId("set-provider-openai")).toBeTruthy();
  });

  it("says nothing matches only for a search with no hits", () => {
    render(<ProviderGroups ps={makePs([OPENAI], true)} tp="set" models={[]} />);
    fireEvent.change(screen.getByTestId("provider-search"), { target: { value: "zzz" } });
    expect(screen.getByTestId("provider-search-empty").textContent).toBe("No providers match.");
    expect(screen.queryByTestId("provider-loading")).toBeNull();
  });
});

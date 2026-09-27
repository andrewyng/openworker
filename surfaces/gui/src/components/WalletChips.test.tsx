// Keys wallet W3: the "Available on" chips — deploy/revoke/stale-redeploy.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WalletChips } from "./WalletChips";

afterEach(cleanup);

let deployCalls: string[][] = [];
let revokeCalls: string[][] = [];
let secretRows: { profile: string; deployed_at: number; stale: boolean; missing_from_wallet: boolean }[] = [];

const machinesPayload = {
  machines: [
    { id: "m1", name: "demo-vm", fingerprint: "f", app_version: "0.2.0", created_at: 1, last_seen: 1, connected: true },
    { id: "m2", name: "old-macpro", fingerprint: "g", app_version: "0.1.9", created_at: 1, last_seen: 1, connected: false },
  ],
  armed: false,
};

beforeEach(() => {
  deployCalls = [];
  revokeCalls = [];
  secretRows = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (/\/v1\/machines$/.test(url)) return { json: async () => machinesPayload } as Response;
      if (/\/v1\/machines\/[^/]+\/secrets$/.test(url)) {
        if (init?.method === "POST") {
          deployCalls.push(JSON.parse(String(init.body)).profiles);
          return { json: async () => ({ ok: true }) } as Response;
        }
        if (init?.method === "DELETE") {
          revokeCalls.push(JSON.parse(String(init.body)).profiles);
          return { json: async () => ({ ok: true }) } as Response;
        }
        return { json: async () => ({ secrets: secretRows }) } as Response;
      }
      return { json: async () => ({}) } as Response;
    }),
  );
});

describe("WalletChips", () => {
  it("renders This Mac + machine chips; offline machines are disabled", async () => {
    render(<WalletChips profiles={["provider:openai"]} />);
    await waitFor(() => expect(screen.getByTestId("wallet-chip-demo-vm")).toBeTruthy());
    expect(screen.getByText("This Mac")).toBeTruthy();
    expect((screen.getByTestId("wallet-chip-old-macpro") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("wallet-chip-demo-vm") as HTMLButtonElement).disabled).toBe(false);
  });

  it("clicking an undeployed chip deploys; a deployed chip revokes", async () => {
    render(<WalletChips profiles={["provider:openai"]} />);
    await waitFor(() => expect(screen.getByTestId("wallet-chip-demo-vm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wallet-chip-demo-vm"));
    await waitFor(() => expect(deployCalls).toEqual([["provider:openai"]]));

    cleanup();
    secretRows = [{ profile: "provider:openai", deployed_at: 1, stale: false, missing_from_wallet: false }];
    render(<WalletChips profiles={["provider:openai"]} />);
    await waitFor(() =>
      expect(screen.getByTestId("wallet-chip-demo-vm").textContent).toContain("✓"),
    );
    fireEvent.click(screen.getByTestId("wallet-chip-demo-vm"));
    await waitFor(() => expect(revokeCalls).toEqual([["provider:openai"]]));
  });

  it("a stale chip shows the rotate marker and re-deploys on click", async () => {
    secretRows = [{ profile: "provider:openai", deployed_at: 1, stale: true, missing_from_wallet: false }];
    render(<WalletChips profiles={["provider:openai"]} />);
    await waitFor(() =>
      expect(screen.getByTestId("wallet-chip-demo-vm").textContent).toContain("↻"),
    );
    fireEvent.click(screen.getByTestId("wallet-chip-demo-vm"));
    await waitFor(() => expect(deployCalls).toEqual([["provider:openai"]]));
    expect(revokeCalls).toEqual([]);
  });
});

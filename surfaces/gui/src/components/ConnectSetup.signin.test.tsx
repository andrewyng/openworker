import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Connector } from "../api";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  connectManaged: vi.fn(),
}));
vi.mock("./connectors/CloudSignIn", () => ({
  CloudSignInInline: ({ onSignedIn }: { onSignedIn?: () => void }) => (
    <button data-testid="inline-cloud-sign-in" onClick={onSignedIn}>Sign in</button>
  ),
  CloudStatusPending: () => <span>Checking</span>,
}));

import { connectManaged } from "../api";
import { ConnectSetup } from "./ManageTabs";

const connector = {
  name: "slack", title: "Slack", managed: true, fields: [], instructions: [],
} as unknown as Connector;
const cloud = { signed_in: true, account: "test@example.com", user_id: "u1" };

afterEach(() => { cleanup(); vi.mocked(connectManaged).mockReset(); });

it.each([
  { ok: false, error: "not signed in", signed_in: false },
  { ok: false, error: "not signed in" },
])("recovers an expired cloud session in the inline connector panel: %j", async (failure) => {
  vi.mocked(connectManaged).mockResolvedValueOnce(failure).mockResolvedValueOnce({ ok: true });
  render(<ConnectSetup c={connector} cloud={cloud} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /connect.*slack/i }));
  fireEvent.click(await screen.findByTestId("inline-cloud-sign-in"));
  await waitFor(() => expect(connectManaged).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: /browser/i })).toBeTruthy());
  expect(screen.queryByText("not signed in")).toBeNull();
});

it("does not misclassify a broker failure as signed out", async () => {
  vi.mocked(connectManaged).mockResolvedValueOnce({ ok: false, error: "start failed (503)" });
  render(<ConnectSetup c={connector} cloud={cloud} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /connect.*slack/i }));
  expect(await screen.findByText("start failed (503)")).toBeTruthy();
  expect(screen.queryByTestId("inline-cloud-sign-in")).toBeNull();
});
